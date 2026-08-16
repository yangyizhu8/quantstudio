#!/usr/bin/env python3
"""Validate agent-authored QuantStudio/PTrade strategy code against real profiles."""
from __future__ import annotations

import argparse
import ast
import builtins
import json
import re
from pathlib import Path
from typing import Any

from agent_skill_common import (
    call_name, confirmation_errors, confirmation_evidence_errors, constant_value,
    etf_t0_contract_errors, execution_funding_errors, function_map,
    is_placeholder_function, load_catalog, load_json, portfolio_contract_errors,
    skill_root, validate_design, write_json,
)


def _issue(rule: str, severity: str, message: str, line: int | None = None) -> dict[str, Any]:
    item = {"rule_id": rule, "severity": severity, "message": message}
    if line is not None:
        item["line"] = line
    return item


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _literal_schedule(call: ast.Call) -> tuple[str | None, str | None]:
    callback = call.args[1].id if len(call.args) >= 2 and isinstance(call.args[1], ast.Name) else None
    time_node = _keyword(call, "time")
    schedule_time = constant_value(time_node) if time_node is not None else None
    return callback, schedule_time if isinstance(schedule_time, str) else None


def _data_field_access(node: ast.AST) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Attribute) or item.attr not in {"open", "high", "low", "close", "volume"}:
            continue
        value = item.value
        if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name) and value.value.id == "data":
            return True
    return False


def _first_executable(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.stmt | None:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return body[0] if body else None


def _is_guard_call(stmt: ast.stmt | None) -> bool:
    return bool(
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and call_name(stmt.value) == "_ensure_runtime_state"
    )


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _g_assignment_fields(node: ast.AST) -> list[tuple[str, ast.AST]]:
    result: list[tuple[str, ast.AST]] = []
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    for target in targets:
        for item in ast.walk(target):
            if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) \
                    and item.value.id == "g":
                result.append((item.attr, node))
    return result


def _missing_hasattr_guard(test: ast.AST, field: str) -> bool:
    """Return True for an explicit ``not hasattr(g, '<field>')`` check."""
    if not isinstance(test, ast.UnaryOp) or not isinstance(test.op, ast.Not):
        return False
    call = test.operand
    return bool(
        isinstance(call, ast.Call)
        and call_name(call) == "hasattr"
        and len(call.args) == 2
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "g"
        and constant_value(call.args[1]) == field
    )


def _assignment_is_missing_guarded(node: ast.AST, field: str, guard: ast.AST,
                                   parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    while current in parents:
        parent = parents[current]
        if parent is guard:
            break
        if isinstance(parent, ast.If) and _missing_hasattr_guard(parent.test, field):
            # Only the ``if`` body is the missing-field branch.
            if any(current is child or current in set(ast.walk(child)) for child in parent.body):
                return True
        current = parent
    return False


def _validate_idempotent_state_guard(guard: ast.FunctionDef | ast.AsyncFunctionDef,
                                     parents: dict[ast.AST, ast.AST],
                                     issues: list[dict[str, Any]]) -> None:
    reported: set[str] = set()
    for node in ast.walk(guard):
        for field, assignment in _g_assignment_fields(node):
            if field in reported:
                continue
            if not _assignment_is_missing_guarded(assignment, field, guard, parents):
                issues.append(_issue(
                    "RUNTIME-STATE-IDEMPOTENCE", "BLOCK",
                    f"_ensure_runtime_state() assigns g.{field} without an explicit "
                    f"if not hasattr(g, {field!r}) guard; repeated callbacks could reset state.",
                    getattr(assignment, "lineno", guard.lineno)))
                reported.add(field)


def _owner_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return None


def _literal_frequency(call: ast.Call) -> str | None:
    for key in ("frequency", "unit"):
        node = _keyword(call, key)
        value = constant_value(node) if node is not None else None
        if isinstance(value, str):
            return value
    if len(call.args) >= 2:
        value = constant_value(call.args[1])
        if isinstance(value, str):
            return value
    return None


SIGNAL_PRICE_APIS = {"get_history", "get_history_batch", "get_price"}


def _validate_signal_price_adjustment(
    call: ast.Call,
    name: str,
    issues: list[dict[str, Any]],
) -> None:
    """Enforce one explicit front-adjusted price basis for generated signals."""
    if name == "attribute_history":
        issues.append(_issue(
            "SIGNAL-PRICE-ADJUSTMENT", "BLOCK",
            "attribute_history() cannot prove the required front-adjusted price basis; "
            "use get_history(..., fq='pre') instead.", call.lineno))
        return
    if name not in SIGNAL_PRICE_APIS:
        return
    fq_node = _keyword(call, "fq")
    if fq_node is None:
        issues.append(_issue(
            "SIGNAL-PRICE-ADJUSTMENT", "BLOCK",
            f"{name}() must include the explicit literal keyword fq='pre'; "
            "omitted or positional adjustment modes are not accepted.", call.lineno))
        return
    fq_value = constant_value(fq_node)
    if fq_value != "pre":
        rendered = repr(fq_value) if fq_value is not None else (
            ast.unparse(fq_node) if hasattr(ast, "unparse") else "<dynamic>")
        issues.append(_issue(
            "SIGNAL-PRICE-ADJUSTMENT", "BLOCK",
            f"{name}() must use literal fq='pre', got {rendered}; "
            "None, dypre, post-adjustment, and dynamic values are forbidden.", call.lineno))


def _defined_or_imported_names(tree: ast.Module) -> set[str]:
    names = set(function_map(tree))
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _import_alias_map(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                aliases[alias.asname or alias.name] = module
    return aliases


def _validate_ptrade_runtime_imports(
    tree: ast.Module,
    profile: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    """Require explicit imports for calculation modules not injected by PTrade."""
    required = profile.get("runtime_module_imports", {})
    if not required:
        return
    imports = _import_alias_map(tree)
    used_lines: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                and node.id in required:
            used_lines.setdefault(node.id, node.lineno)
    for alias, line in sorted(used_lines.items()):
        module = required[alias]
        if imports.get(alias) != module:
            issues.append(_issue(
                "PTRADE-RUNTIME-IMPORT", "BLOCK",
                f"PTrade does not inject {alias!r}; add explicit `import {module}"
                f"{' as ' + alias if alias != module else ''}` before using it.",
                line))


def _load_ptrade_profile() -> dict[str, Any]:
    return load_json(skill_root() / "references" / "ptrade-api-signatures.json")


# --- PTrade get_history(is_dict=True) return-shape safety (profile 1.8.0) ---
#
# The real platform may return a mapping whose per-security item is a pandas
# DataFrame, a NumPy structured array or a recarray, and item[field] may be a
# Series or an ndarray. Generated code must therefore normalize extracted
# fields with np.asarray (optionally behind a hasattr(values, 'values') guard)
# instead of assuming pandas-only attributes exist.

HISTORY_ITEM_NUMERIC_FUNCS = {
    "mean", "std", "sum", "min", "max", "median", "percentile", "var",
    "average", "polyfit", "corrcoef", "diff", "argmax", "argmin", "cumsum",
    "nanmean", "nanstd", "nansum", "nanmedian",
}


def _is_dict_history_call(node: ast.AST) -> bool:
    return bool(
        isinstance(node, ast.Call)
        and call_name(node) == "get_history"
        and constant_value(_keyword(node, "is_dict")) is True
    )


def _is_history_mapping(node: ast.AST, mapping_names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in mapping_names
    return _is_dict_history_call(node)


def _history_shape_symbols(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Track names bound to is_dict=True history mapping items and fields."""
    mappings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_dict_history_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    mappings.add(target.id)

    items: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            iter_call = node.iter
            if isinstance(iter_call, ast.Call) and isinstance(iter_call.func, ast.Attribute) \
                    and iter_call.func.attr in {"items", "values"} \
                    and _is_history_mapping(iter_call.func.value, mappings):
                if iter_call.func.attr == "items" and isinstance(node.target, ast.Tuple) \
                        and len(node.target.elts) == 2 \
                        and isinstance(node.target.elts[1], ast.Name):
                    items.add(node.target.elts[1].id)
                elif iter_call.func.attr == "values" and isinstance(node.target, ast.Name):
                    items.add(node.target.id)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Subscript) \
                and _is_history_mapping(node.value.value, mappings):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    items.add(target.id)

    fields: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Subscript) \
                and isinstance(node.value.value, ast.Name) \
                and node.value.value.id in items:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    fields.add(target.id)
    return items, fields


def _root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, (ast.Subscript, ast.Attribute)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _hasattr_guarded(node: ast.AST, parents: dict[ast.AST, ast.AST],
                     items: set[str], fields: set[str]) -> bool:
    """True when an enclosing ``if hasattr(<root>, ...)`` guards the access."""
    tracked = items | fields
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.If):
            for part in ast.walk(current.test):
                if isinstance(part, ast.Call) and call_name(part) == "hasattr" \
                        and part.args and _root_name(part.args[0]) in tracked:
                    return True
    return False


def _history_base_kind(node: ast.AST, items: set[str], fields: set[str]) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in items:
            return "item"
        if node.id in fields:
            return "field"
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
            and node.value.id in items:
        return "field"  # item[field]
    return None


def _validate_history_shape_safety(
    tree: ast.AST,
    parents: dict[ast.AST, ast.AST],
    issues: list[dict[str, Any]],
) -> None:
    items, fields = _history_shape_symbols(tree)
    if not items:
        return

    normalized: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else "")
            if func_name in {"asarray", "array"}:
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id in fields:
                        normalized.add(arg.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            base = node.value
            kind = _history_base_kind(base, items, fields)
            if kind is None:
                continue
            rendered = ast.unparse(node) if hasattr(ast, "unparse") else node.attr
            if node.attr == "values":
                if not _hasattr_guarded(node, parents, items, fields):
                    issues.append(_issue(
                        "PTRADE-HISTORY-SHAPE-UNSAFE", "BLOCK",
                        f"{rendered} assumes a pandas object from "
                        "get_history(..., is_dict=True); the platform may return a NumPy "
                        "structured/recarray item. Normalize with "
                        "np.asarray(item[field], dtype=float) or a hasattr(values, 'values') "
                        "guarded helper instead.", node.lineno))
            elif node.attr in {"iloc", "loc", "empty", "columns", "index"}:
                if not _hasattr_guarded(node, parents, items, fields):
                    issues.append(_issue(
                        "PTRADE-HISTORY-PANDAS-ONLY", "BLOCK",
                        f"{rendered} is a pandas-only attribute on a "
                        "get_history(..., is_dict=True) item/field; structured arrays and "
                        "recarrays do not expose it.", node.lineno))
            elif node.attr == "to_numpy":
                parent = parents.get(node)
                if isinstance(parent, ast.Call) and parent.func is node \
                        and not _hasattr_guarded(node, parents, items, fields):
                    issues.append(_issue(
                        "PTRADE-HISTORY-PANDAS-ONLY", "BLOCK",
                        f"{rendered}() is a pandas-only method on a "
                        "get_history(..., is_dict=True) item/field; use np.asarray(...).",
                        node.lineno))

    # Numerical use of an extracted field without np.asarray normalization.
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id in items and isinstance(node.slice, ast.Constant):
            parent = parents.get(node)
            if isinstance(parent, ast.Call):
                func_name = parent.func.attr if isinstance(parent.func, ast.Attribute) else (
                    parent.func.id if isinstance(parent.func, ast.Name) else "")
                if func_name in HISTORY_ITEM_NUMERIC_FUNCS:
                    issues.append(_issue(
                        "PTRADE-HISTORY-NORMALIZATION-MISSING", "BLOCK",
                        "history field is consumed numerically without np.asarray "
                        "normalization; wrap it as np.asarray(item[field], dtype=float).",
                        node.lineno))
            elif isinstance(parent, (ast.BinOp, ast.Compare)):
                issues.append(_issue(
                    "PTRADE-HISTORY-NORMALIZATION-MISSING", "BLOCK",
                    "history field is used in arithmetic/comparison without np.asarray "
                    "normalization; wrap it as np.asarray(item[field], dtype=float).",
                    node.lineno))
        elif isinstance(node, ast.Name) and node.id in fields \
                and isinstance(node.ctx, ast.Load) and node.id not in normalized:
            parent = parents.get(node)
            if isinstance(parent, ast.Call):
                func_name = parent.func.attr if isinstance(parent.func, ast.Attribute) else (
                    parent.func.id if isinstance(parent.func, ast.Name) else "")
                if func_name in HISTORY_ITEM_NUMERIC_FUNCS:
                    issues.append(_issue(
                        "PTRADE-HISTORY-NORMALIZATION-MISSING", "BLOCK",
                        f"history field {node.id!r} is consumed numerically without "
                        "np.asarray normalization.", node.lineno))
            elif isinstance(parent, (ast.BinOp, ast.Compare)):
                issues.append(_issue(
                    "PTRADE-HISTORY-NORMALIZATION-MISSING", "BLOCK",
                    f"history field {node.id!r} is used in arithmetic/comparison without "
                    "np.asarray normalization.", node.lineno))


# --- Standard history-field helper contract (design 2.2, dual targets) ---

STANDARD_HISTORY_HELPER = "_extract_history_field"


def _validate_history_helper_required(
    design: dict[str, Any],
    tree: ast.AST,
    functions: dict[str, Any],
    parents: dict[ast.AST, ast.AST],
    issues: list[dict[str, Any]],
) -> None:
    """Design 2.2 dual strategies using get_history(is_dict=True) must route
    every field extraction through the standard _extract_history_field helper.

    This keeps the static validator and the agent-first runtime-shape fixture
    on one identical contract: the fixture executes exactly the helper the
    strategy actually uses.
    """
    if design.get("design_version") != "2.2":
        return
    items, _fields = _history_shape_symbols(tree)
    if not items:
        return
    helper = functions.get(STANDARD_HISTORY_HELPER)
    if helper is None or is_placeholder_function(helper):
        issues.append(_issue(
            "PTRADE-HISTORY-HELPER-REQUIRED", "BLOCK",
            f"design 2.2 dual strategies using get_history(is_dict=True) must define "
            f"the standard {STANDARD_HISTORY_HELPER}(history_item, field, dtype=float) "
            "helper so the runtime-shape fixture can execute the real extraction code."))
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id in items:
            owner = _owner_function(node, parents)
            if owner != STANDARD_HISTORY_HELPER:
                issues.append(_issue(
                    "PTRADE-HISTORY-HELPER-REQUIRED", "BLOCK",
                    f"history item field extraction happens outside "
                    f"{STANDARD_HISTORY_HELPER}; call "
                    f"{STANDARD_HISTORY_HELPER}(df, 'field', float) instead of direct "
                    "item[field] extraction.", node.lineno))


# --- Machine-checkable portfolio/capital contract (design 2.2) ---

RUNTIME_PORTFOLIO_VALUE_ATTRS = {"total_value", "portfolio_value", "total_asset"}


def _validate_portfolio_contract_code(
    design: dict[str, Any],
    tree: ast.AST,
    functions: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    contract = design.get("portfolio_contract")
    if not isinstance(contract, dict):
        return
    sizing_mode = contract.get("sizing_mode")

    for node in ast.walk(tree):
        for field, assignment in _g_assignment_fields(node):
            value = getattr(assignment, "value", None)
            if not isinstance(value, ast.Constant) or not isinstance(value.value, (int, float)) \
                    or isinstance(value.value, bool):
                continue
            if sizing_mode == "runtime_total_value" and field in {"capital", "per_target"}:
                issues.append(_issue(
                    "PORTFOLIO-HARDCODED-CAPITAL", "BLOCK",
                    f"sizing_mode=runtime_total_value forbids hardcoded g.{field} = "
                    f"{value.value!r}; derive position targets from the runtime portfolio "
                    "total value.", getattr(assignment, "lineno", None)))
            elif sizing_mode == "fixed_notional" and field == "capital":
                required_cash = contract.get("required_initial_cash")
                if isinstance(required_cash, (int, float)) and value.value != required_cash:
                    issues.append(_issue(
                        "PORTFOLIO-FIXED-NOTIONAL-MISMATCH", "BLOCK",
                        f"g.capital = {value.value!r} contradicts "
                        f"portfolio_contract.required_initial_cash = {required_cash!r}.",
                        getattr(assignment, "lineno", None)))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or call_name(node) != "order_target_value":
            continue
        value_node = _keyword(node, "value")
        if value_node is None and len(node.args) >= 2:
            value_node = node.args[1]
        number = constant_value(value_node) if value_node is not None else None
        if not isinstance(number, (int, float)) or isinstance(number, bool):
            continue
        if number <= 0:
            continue  # order_target_value(code, 0) is a liquidation, always allowed
        if sizing_mode == "runtime_total_value":
            issues.append(_issue(
                "PORTFOLIO-HARDCODED-CAPITAL", "BLOCK",
                f"sizing_mode=runtime_total_value forbids a hardcoded positive "
                f"order_target_value amount ({number!r}); size buys from the runtime "
                "portfolio total value.", node.lineno))
        elif sizing_mode == "fixed_notional":
            fixed_target = contract.get("fixed_target_value")
            if isinstance(fixed_target, (int, float)) and number != fixed_target:
                issues.append(_issue(
                    "PORTFOLIO-FIXED-NOTIONAL-MISMATCH", "BLOCK",
                    f"order_target_value amount {number!r} contradicts "
                    f"portfolio_contract.fixed_target_value = {fixed_target!r}.",
                    node.lineno))

    if sizing_mode == "runtime_total_value":
        has_helper = "_portfolio_total_value" in functions
        has_runtime_field = any(
            isinstance(node, ast.Attribute) and node.attr in RUNTIME_PORTFOLIO_VALUE_ATTRS
            for node in ast.walk(tree)
        )
        if not has_helper and not has_runtime_field:
            issues.append(_issue(
                "PORTFOLIO-RUNTIME-VALUE-MISSING", "BLOCK",
                "sizing_mode=runtime_total_value requires a _portfolio_total_value(context) "
                "helper or equivalent reads of runtime portfolio fields "
                f"{sorted(RUNTIME_PORTFOLIO_VALUE_ATTRS)} (cash + positions market value)."))


# --- Standardized R5 rebalance/portfolio audit logs (design 2.2) ---

LOG_METHODS = {"debug", "info", "warning", "error", "critical"}
LIFECYCLE_CALLBACKS = {"initialize", "before_trading_start", "handle_data", "after_trading_end"}


def _static_text(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            value.value for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _static_text(node.left)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "format":
        return _static_text(node.func.value)
    return ""


def _log_call_texts(tree: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[tuple[str, str | None, int]]:
    texts: list[tuple[str, str | None, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "log" \
                and node.func.attr in LOG_METHODS and node.args:
            text = _static_text(node.args[0])
            if text:
                texts.append((text, _owner_function(node, parents), node.lineno))
    return texts


def _reachable_callbacks(tree: ast.AST, functions: dict[str, Any],
                         parents: dict[ast.AST, ast.AST]) -> set[str]:
    roots = set(functions) & LIFECYCLE_CALLBACKS
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == "run_daily":
            callback, _schedule = _literal_schedule(node)
            if callback:
                roots.add(callback)
    local_graph: dict[str, set[str]] = {name: set() for name in functions}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            owner = _owner_function(node, parents)
            name = call_name(node)
            if owner in local_graph and name in local_graph:
                local_graph[owner].add(name)
    reachable: set[str] = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        stack.extend(local_graph.get(current, ()))
    return reachable


def _validate_r5_audit_logs(
    design: dict[str, Any],
    tree: ast.AST,
    functions: dict[str, Any],
    parents: dict[ast.AST, ast.AST],
    issues: list[dict[str, Any]],
) -> None:
    """AST-level audit-log verification: the QS_*_AUDIT markers must appear in
    real log.* calls carrying the required keys, emitted from a callback that
    is actually reachable from the strategy lifecycle. Comments, docstrings
    and dead helpers cannot satisfy this gate.
    """
    if not isinstance(design.get("r5_deployment_invariants"), dict):
        return
    texts = _log_call_texts(tree, parents)
    reachable = _reachable_callbacks(tree, functions, parents)
    rebalance_logs = [entry for entry in texts if "QS_REBALANCE_AUDIT" in entry[0]]
    portfolio_logs = [entry for entry in texts if "QS_PORTFOLIO_AUDIT" in entry[0]]
    if not rebalance_logs and not portfolio_logs:
        issues.append(_issue(
            "R5-AUDIT-LOG-MISSING", "BLOCK",
            "designs with r5_deployment_invariants must emit machine-parseable "
            "QS_REBALANCE_AUDIT and QS_PORTFOLIO_AUDIT log lines so R5 can verify "
            "selected/submitted/actual deployment."))
        return
    if rebalance_logs:
        for text, owner, line in rebalance_logs:
            missing = [key for key in ("rebalance_id", "date", "selected", "buy_submitted")
                       if f"{key}=" not in text]
            if missing:
                issues.append(_issue(
                    "R5-REBALANCE-AUDIT-INCOMPLETE", "BLOCK",
                    f"QS_REBALANCE_AUDIT log call must record keys {missing} "
                    "(fixed key=value format; rebalance_id uniquely binds this "
                    "rebalance to its QS_PORTFOLIO_AUDIT).", line))
            if owner not in reachable:
                issues.append(_issue(
                    "R5-REBALANCE-AUDIT-INCOMPLETE", "BLOCK",
                    f"QS_REBALANCE_AUDIT is emitted from {owner or '<module>'}, which is "
                    "not reachable from any lifecycle/scheduled callback; the audit "
                    "must fire from the rebalance execution path.", line))
    else:
        issues.append(_issue(
            "R5-REBALANCE-AUDIT-INCOMPLETE", "BLOCK",
            "QS_REBALANCE_AUDIT log line is missing; each rebalance must record "
            "date/selected/tradable/sell_submitted/buy_submitted."))
    if portfolio_logs:
        for text, owner, line in portfolio_logs:
            missing = [key for key in ("rebalance_id", "date", "positions") if f"{key}=" not in text]
            if missing:
                issues.append(_issue(
                    "R5-PORTFOLIO-AUDIT-INCOMPLETE", "BLOCK",
                    f"QS_PORTFOLIO_AUDIT log call must record keys {missing} "
                    "(fixed key=value format; rebalance_id must equal the "
                    "corresponding QS_REBALANCE_AUDIT id).", line))
            if owner not in reachable:
                issues.append(_issue(
                    "R5-PORTFOLIO-AUDIT-INCOMPLETE", "BLOCK",
                    f"QS_PORTFOLIO_AUDIT is emitted from {owner or '<module>'}, which is "
                    "not reachable from any lifecycle/scheduled callback; record actual "
                    "positions from after_trading_end or the next trading day.", line))
    else:
        issues.append(_issue(
            "R5-PORTFOLIO-AUDIT-INCOMPLETE", "BLOCK",
            "QS_PORTFOLIO_AUDIT log line is missing; after_trading_end (or the next "
            "trading day) must record actual positions/cash_ratio/gross_exposure."))


def _validate_ptrade_logger_call(
    call: ast.Call,
    profile: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    """Enforce the logger methods exposed by the real PTrade IQEngine runtime."""
    if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name):
        return
    if call.func.value.id != "log":
        return
    method = call.func.attr
    logger_profile = profile.get("logger_methods", {})
    blocked = logger_profile.get("blocked_aliases", {})
    allowed = set(logger_profile.get("allowed", []))
    if method in blocked:
        issues.append(_issue(
            "PTRADE-LOG-METHOD", "BLOCK", blocked[method], call.lineno))
    elif allowed and method not in allowed:
        issues.append(_issue(
            "PTRADE-LOG-METHOD", "BLOCK",
            f"log.{method}() is not in the verified PTrade logger profile; "
            f"allowed methods are {sorted(allowed)}.", call.lineno))


def _validate_ptrade_call(
    call: ast.Call,
    name: str,
    profile: dict[str, Any],
    defined_names: set[str],
    issues: list[dict[str, Any]],
) -> None:
    specs = profile.get("signatures", {})
    spec = specs.get(name, {})
    if spec.get("unsupported_on_ptrade"):
        replacement = spec.get("replacement_for") or "a documented PTrade public API"
        issues.append(_issue(
            "PTRADE-API-UNSUPPORTED", "BLOCK",
            f"{name}() is not in the PTrade public profile; use {replacement}.", call.lineno))
    if spec.get("forbidden_in_backtest"):
        issues.append(_issue(
            "PTRADE-CONTEXT-API", "BLOCK",
            f"{name}() is trading-context only and cannot be used in a PTrade backtest strategy.", call.lineno))

    # Validate every profiled public call, not only the API that most recently
    # failed on a broker runtime. This prevents local ``**kwargs`` permissiveness
    # from hiding the next incompatible keyword until upload time.
    if spec:
        allowed = set(spec.get("allowed_keywords", []))
        dynamic_keywords = [kw for kw in call.keywords if kw.arg is None]
        if dynamic_keywords:
            issues.append(_issue(
                "PTRADE-API-SIGNATURE", "BLOCK",
                f"{name}() uses dynamic **kwargs; exact PTrade keyword compatibility cannot be verified.",
                call.lineno))
        if "allowed_keywords" in spec:
            bad = sorted(kw.arg for kw in call.keywords if kw.arg and kw.arg not in allowed)
            if bad:
                issues.append(_issue(
                    "PTRADE-API-SIGNATURE", "BLOCK",
                    f"{name}() unsupported keyword(s) {bad}; allowed keywords are {sorted(allowed)}.",
                    call.lineno))
        if "max_positional" in spec:
            max_positional = int(spec["max_positional"])
            if len(call.args) > max_positional:
                issues.append(_issue(
                    "PTRADE-API-SIGNATURE", "BLOCK",
                    f"{name}() accepts at most {max_positional} positional argument(s).", call.lineno))
        for keyword, values in spec.get("canonical_keyword_values", {}).items():
            value_node = _keyword(call, keyword)
            if value_node is None:
                continue
            value = constant_value(value_node)
            if value is not None and value not in values:
                issues.append(_issue(
                    "PTRADE-API-SIGNATURE", "BLOCK",
                    f"{name}() keyword {keyword!r} must use one of {values}; got {value!r}.",
                    call.lineno))

    if name in set(profile.get("local_only_symbols", [])) and name not in defined_names:
        issues.append(_issue(
            "PTRADE-LOCAL-SYMBOL", "BLOCK",
            f"{name} is a QuantStudio-local injected symbol. Define a portable helper in source "
            f"or use a documented PTrade public API.", call.lineno))


# --- A4 (PHASE1): dual-target field-name + is_dict=True hard blocks ---
_LOCAL_ONLY_COLUMNS = {"amount", "close_front", "volume_front", "open_front"}
_LOCAL_TO_CANONICAL = {
    "amount": "money",
    "close_front": "close",
    "volume_front": "volume",
    "open_front": "open",
}


def _validate_dual_target_field_names(tree, issues, parents):
    """Block dual-target code referencing QuantStudio-only column names.

    Direct attribute/subscript access (df.amount, df['amount'], item.close_front)
    is a hard break on PTrade. The sanctioned _extract_series(df, 'amount', 'money')
    helper is allowed when a canonical PTrade name ('money'/'close'/...) is also
    requested, because it degrades gracefully after the B1 reverse-mapping.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _LOCAL_ONLY_COLUMNS:
            rendered = ast.unparse(node) if hasattr(ast, "unparse") else node.attr
            canon = _LOCAL_TO_CANONICAL[node.attr]
            issues.append(_issue(
                "PTRADE-LOCAL-COLUMN", "BLOCK",
                f"{rendered} uses QuantStudio-only column {node.attr!r}; PTrade returns "
                f"{canon!r}. Use {canon!r} (or the _extract_series helper with {canon!r} "
                f"in the requested names).", node.lineno))
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str) and node.slice.value in _LOCAL_ONLY_COLUMNS:
            rendered = ast.unparse(node) if hasattr(ast, "unparse") else node.slice.value
            canon = _LOCAL_TO_CANONICAL[node.slice.value]
            issues.append(_issue(
                "PTRADE-LOCAL-COLUMN", "BLOCK",
                f"{rendered} indexes QuantStudio-only column {node.slice.value!r}; PTrade "
                f"returns {canon!r}. Use {canon!r}.", node.lineno))
    # _extract_series(df, 'amount', 'money') style: block only if no canonical name present.
    canonical_names = set(_LOCAL_TO_CANONICAL.values())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) in {"_extract_series", "extract_series"}:
            requested: set[str] = set()
            for arg in list(node.args)[1:]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    requested.add(arg.value)
            for kw in node.keywords:
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    requested.add(kw.value.value)
            if requested & _LOCAL_ONLY_COLUMNS and not (requested & canonical_names):
                issues.append(_issue(
                    "PTRADE-LOCAL-COLUMN", "BLOCK",
                    f"{call_name(node)}(...) requests QuantStudio-only column(s) "
                    f"{sorted(requested & _LOCAL_ONLY_COLUMNS)} without a canonical PTrade "
                    f"name; pass 'money'/'close'/... so it degrades after B1.",
                    node.lineno))


def _validate_is_dict_usage(tree, issues):
    """Block dual-target code that calls get_history/get_price with is_dict=True.

    is_dict=True yields a divergent return shape across QuantStudio (DataFrame map)
    and PTrade (array/recarray map). Dual-target code must use the default DataFrame
    path (omit is_dict or set is_dict=False) which is portable to both engines.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if name in {"get_history", "get_price"} and constant_value(_keyword(node, "is_dict")) is True:
            issues.append(_issue(
                "PTRADE-IS-DICT-BAN", "BLOCK",
                f"{name}(..., is_dict=True) returns a divergent mapping shape across "
                f"QuantStudio/PTrade; dual-target code must omit is_dict or use is_dict=False "
                f"(default DataFrame path is portable).", node.lineno))


def validate_strategy(
    design: dict[str, Any],
    source: str,
    source_name: str = "strategy.py",
    target_profile: str = "canonical",
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for message in validate_design(design):
        issues.append(_issue("DESIGN-SCHEMA", "BLOCK", message))
    for message in confirmation_errors(design):
        issues.append(_issue("DESIGN-CONFIRMATION", "BLOCK", message))
    for item in confirmation_evidence_errors(design):
        issues.append(_issue(item["rule_id"], "BLOCK", item["message"]))
    for item in portfolio_contract_errors(design):
        issues.append(_issue(item["rule_id"], "BLOCK", item["message"]))
    for item in execution_funding_errors(design):
        issues.append(_issue(item["rule_id"], "BLOCK", item["message"]))
    for item in etf_t0_contract_errors(design):
        issues.append(_issue(item["rule_id"], "BLOCK", item["message"]))
    if target_profile not in {"canonical", "quantstudio", "ptrade"}:
        issues.append(_issue("TARGET-PROFILE", "BLOCK", f"unknown target profile {target_profile!r}"))

    # Keep auditing source when an older design only misses a newly introduced
    # nested constraint. Runtime evidence should reveal every portability defect
    # in one report instead of stopping at the first schema migration issue.
    structural_keys = {"engine_profile", "components", "constraints", "timing"}
    fatal_design = target_profile not in {"canonical", "quantstudio", "ptrade"} \
        or not structural_keys.issubset(design)
    if fatal_design:
        return _report(design, source_name, target_profile, issues)
    if target_profile == "ptrade" and "ptrade" not in design.get("targets", []):
        if any(item["severity"] == "BLOCK" for item in issues):
            return _report(design, source_name, target_profile, issues)
        return {
            "report_version": "2.1",
            "strategy_id": design.get("strategy_id"),
            "source": source_name,
            "target_profile": target_profile,
            "status": "NOT_APPLICABLE",
            "profile_validation_status": "NOT_APPLICABLE",
            "runtime_validation_status": "NOT_APPLICABLE",
            "deployment_status": "NOT_APPLICABLE",
            "block_count": 0,
            "warning_count": 0,
            "reason": "design targets exclude ptrade",
            "issues": [],
        }

    try:
        tree = ast.parse(source, filename=source_name)
    except SyntaxError as exc:
        issues.append(_issue("PYTHON-SYNTAX", "BLOCK", str(exc), exc.lineno))
        return _report(design, source_name, target_profile, issues)

    catalog = load_catalog()
    ptrade_profile = _load_ptrade_profile()
    strict_ptrade = target_profile in {"canonical", "ptrade"} and "ptrade" in design.get("targets", [])
    functions = function_map(tree)
    parents = _parent_map(tree)
    defined_names = _defined_or_imported_names(tree)
    if strict_ptrade:
        _validate_ptrade_runtime_imports(tree, ptrade_profile, issues)
    required_hooks = set(design["components"].get("lifecycle_hooks", [])) | {"initialize"}

    for hook in sorted(required_hooks):
        node = functions.get(hook)
        if node is None:
            issues.append(_issue("LIFECYCLE-MISSING", "BLOCK", f"required lifecycle function {hook}() is missing"))
        elif is_placeholder_function(node):
            issues.append(_issue("AGENT-IMPLEMENTATION-MISSING", "BLOCK", f"{hook}() is still an unimplemented scaffold", node.lineno))

    forbidden_prefixes = tuple(catalog["forbidden_import_prefixes"])
    forbidden_io = set(catalog["forbidden_direct_io"])
    schedules: list[tuple[str | None, str | None, int]] = []
    called: set[str] = set()
    calls: list[tuple[ast.Call, str, str | None]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports = [node.module or ""]
        else:
            imports = []
        for imported in imports:
            if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden_prefixes):
                issues.append(_issue("STRATEGY-ISOLATION", "BLOCK", f"forbidden internal/storage import: {imported}", node.lineno))

        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        owner = _owner_function(node, parents)
        calls.append((node, name, owner))
        if name:
            called.add(name)
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            called.add(node.func.value.id)
        if name in forbidden_io:
            issues.append(_issue("STRATEGY-ISOLATION", "BLOCK", f"forbidden direct I/O call: {name}()", node.lineno))
        if name == "run_daily":
            callback, schedule_time = _literal_schedule(node)
            schedules.append((callback, schedule_time, node.lineno))
        _validate_signal_price_adjustment(node, name, issues)
        if strict_ptrade:
            _validate_ptrade_logger_call(node, ptrade_profile, issues)
            if name:
                _validate_ptrade_call(node, name, ptrade_profile, defined_names, issues)

    if strict_ptrade:
        _validate_dual_target_field_names(tree, issues, parents)
        _validate_is_dict_usage(tree, issues)

    target_set = set(design.get("targets", []))
    local_only_symbols = set(ptrade_profile.get("local_only_symbols", []))
    profiled_apis = set(ptrade_profile.get("signatures", {}))
    external_calls = [(call, name) for call, name, _ in calls
                      if name and name not in defined_names]
    if "ptrade" in target_set:
        required_apis = set(design["components"].get("required_apis", []))
        for api in sorted(required_apis - {"log"}):
            if api in local_only_symbols:
                issues.append(_issue(
                    "PTRADE-DESIGN-LOCAL-API", "BLOCK",
                    f"dual/PTrade design declares QuantStudio-local API {api}()."))
            elif api not in profiled_apis:
                issues.append(_issue(
                    "PTRADE-DESIGN-UNPROFILED-API", "BLOCK",
                    f"dual/PTrade design declares {api}(), but no verified PTrade signature/profile entry exists."))
        python_builtins = set(dir(builtins))
        for call, name in external_calls:
            if name in local_only_symbols:
                issues.append(_issue(
                    "TARGET-LOCAL-EXTENSION-BAN", "BLOCK",
                    f"dual/PTrade target cannot call QuantStudio-local API {name}().",
                    call.lineno))
            elif isinstance(call.func, ast.Name) and name not in python_builtins \
                    and name not in profiled_apis:
                issues.append(_issue(
                    "PTRADE-API-UNPROFILED", "BLOCK",
                    f"{name}() is an external top-level call without a verified PTrade signature/profile entry.",
                    call.lineno))
    for call, name in external_calls:
        if name == "get_etf_list":
            issues.append(_issue(
                "PTRADE-GET-ETF-LIST-BACKTEST-BAN", "BLOCK",
                "get_etf_list() is unavailable in the PTrade backtest profile; "
                "use a confirmed static whitelist for dual targets or "
                "get_etf_list_local() for QuantStudio-only targets.",
                call.lineno))

    universe_contract = design.get("universe_contract", {})
    if universe_contract.get("mode") == "dynamic_local" \
            and "get_etf_list_local" not in called:
        issues.append(_issue(
            "LOCAL-DYNAMIC-ETF-API-REQUIRED", "BLOCK",
            "dynamic_local universe contract requires get_etf_list_local()."))
    if universe_contract.get("mode") == "static_whitelist":
        confirmed_whitelist = set(universe_contract.get("static_etf_whitelist", []))
        source_literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        missing_whitelist = sorted(confirmed_whitelist - source_literals)
        if missing_whitelist:
            issues.append(_issue(
                "STATIC-ETF-WHITELIST-MISMATCH", "BLOCK",
                f"strategy source is missing confirmed ETF whitelist entries: {missing_whitelist}"))
    if "ptrade" not in target_set and re.search(
            r"PTrade\s+(?:validation\s*[:=]\s*)?PASS", source, re.IGNORECASE):
        issues.append(_issue(
            "LOCAL-ONLY-PTRADE-CLAIM", "BLOCK",
            "QuantStudio-only source must not claim PTrade validation PASS."))

    scheduled_times = {callback: schedule for callback, schedule, _ in schedules if callback}

    # Propagate scheduled callback times through top-level helper calls. Portable
    # strategies commonly isolate current-minute retrieval in a helper; checking
    # only the immediate AST owner would incorrectly reject that safe pattern.
    local_names = set(functions)
    local_graph: dict[str, set[str]] = {name: set() for name in local_names}
    for call, name, owner in calls:
        if owner in local_graph and name in local_names:
            local_graph[owner].add(name)
    reachable_schedule_times: dict[str, set[str]] = {}
    for callback, schedule_time in scheduled_times.items():
        if not schedule_time:
            continue
        stack = [callback]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            reachable_schedule_times.setdefault(current, set()).add(schedule_time)
            stack.extend(local_graph.get(current, ()))

    # PTrade documents filter_stock_by_status as a before_trading_start-only API.
    # Direct use from lifecycle/scheduled callbacks is therefore a platform block.
    runtime_callbacks = set(required_hooks) | set(scheduled_times)
    if strict_ptrade:
        for call, name, owner in calls:
            if name == "filter_stock_by_status" and owner in runtime_callbacks \
                    and owner != "before_trading_start":
                issues.append(_issue(
                    "PTRADE-CALLBACK-CONTEXT", "BLOCK",
                    "filter_stock_by_status() may only be called from "
                    "before_trading_start in PTrade; scheduled callbacks must use "
                    "get_stock_status().", call.lineno))

    # Initialization-failure safety: real PTrade may continue later lifecycle calls
    # after initialize raises, so state construction must be idempotent and first.
    guard_required = design.get("constraints", {}).get("runtime_state_guard_required", True)
    if guard_required:
        guard = functions.get("_ensure_runtime_state")
        if guard is None or is_placeholder_function(guard):
            issues.append(_issue(
                "RUNTIME-STATE-GUARD", "BLOCK",
                "define a non-placeholder idempotent _ensure_runtime_state() helper"))
        else:
            _validate_idempotent_state_guard(guard, parents, issues)
        callbacks = set(required_hooks) | set(scheduled_times)
        for callback in sorted(callbacks):
            node = functions.get(callback)
            if node is not None and not _is_guard_call(_first_executable(node)):
                issues.append(_issue(
                    "RUNTIME-STATE-GUARD", "BLOCK",
                    f"{callback}() must call _ensure_runtime_state() as its first executable statement",
                    node.lineno))

    before_open = functions.get("before_trading_start")
    if before_open is not None and _data_field_access(before_open):
        issues.append(_issue(
            "NO-LOOKAHEAD-PREOPEN", "BLOCK",
            "before_trading_start must not read same-day data[code].open/high/low/close/volume",
            before_open.lineno))

    cutoff_text = str(design.get("timing", {}).get("signal_data_cutoff", "")).lower()
    for call, name, owner in calls:
        if name in {"get_history", "attribute_history"}:
            include = _keyword(call, "include")
            if constant_value(include) is True:
                owner_times = reachable_schedule_times.get(owner or "", set())
                schedule_time = sorted(owner_times)[0] if len(owner_times) == 1 else None
                allowed_current_minute = (
                    design["engine_profile"]["profile_id"] in ("minute-bar-v1", "daily-open-close-proxy-v1")
                    and schedule_time is not None
                    and schedule_time >= "09:31"
                    and _literal_frequency(call) in {"1m", "1min"}
                    and ("current" in cutoff_text or schedule_time in cutoff_text)
                )
                if not allowed_current_minute:
                    issues.append(_issue(
                        "NO-LOOKAHEAD-INCLUDE", "BLOCK",
                        f"{name}(..., include=True) is allowed only in a confirmed scheduled "
                        f"completed-minute callback with an explicit current-bar cutoff.", call.lineno))
        if name == "get_fundamentals":
            date_node = _keyword(call, "date")
            text = ast.unparse(date_node) if date_node is not None and hasattr(ast, "unparse") else ""
            if "current_dt" in text or "current_date" in text:
                issues.append(_issue(
                    "FUNDAMENTAL-PIT", "BLOCK",
                    "get_fundamentals date must be a confirmed PIT/as-of date, not current_dt/current_date",
                    call.lineno))

    for callback, schedule_time, line in schedules:
        if callback is None:
            issues.append(_issue("SCHEDULE-CALLBACK", "BLOCK", "run_daily callback must be a named top-level function", line))
        elif callback not in functions:
            issues.append(_issue("SCHEDULE-CALLBACK", "BLOCK", f"run_daily callback {callback}() is not defined", line))
        elif is_placeholder_function(functions[callback]):
            issues.append(_issue("AGENT-IMPLEMENTATION-MISSING", "BLOCK", f"scheduled callback {callback}() is still an unimplemented scaffold", functions[callback].lineno))
        if schedule_time is None:
            issues.append(_issue("SCHEDULE-TIME", "BLOCK", "run_daily requires a literal confirmed time='HH:MM'", line))
        elif not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", schedule_time):
            issues.append(_issue("SCHEDULE-TIME", "BLOCK", f"invalid run_daily time: {schedule_time!r}", line))
        elif schedule_time == "09:30" and design["engine_profile"]["profile_id"] in ("minute-bar-v1", "daily-open-close-proxy-v1"):
            issues.append(_issue("AUCTION-BAR-UNAVAILABLE", "BLOCK", "QuantStudio minute events start at 09:31; use a confirmed 09:31 approximation or another model", line))

    if design["engine_profile"]["profile_id"] == "daily-bar-v1":
        precise = [event for event in design["timing"].get("decision_events", [])
                   if event.get("lifecycle") == "run_daily" and event.get("time") not in (None, "15:00")]
        if precise:
            issues.append(_issue("PROFILE-SCHEDULE-MISMATCH", "BLOCK", "intraday run_daily times require minute-bar-v1"))

    scheduled_by_name = {(cb, tm) for cb, tm, _ in schedules}
    for event in design["timing"].get("decision_events", []):
        lifecycle = event.get("lifecycle")
        if lifecycle == "run_daily":
            expected = (event["name"], event.get("time"))
            if expected not in scheduled_by_name:
                issues.append(_issue("DESIGN-CODE-SCHEDULE", "BLOCK", f"design event {expected} is not registered in code"))
        elif lifecycle in {"before_trading_start", "handle_data", "after_trading_end"} and lifecycle not in functions:
            issues.append(_issue("DESIGN-CODE-LIFECYCLE", "BLOCK", f"design event requires {lifecycle}(), but code does not define it"))

    required_apis = set(design["components"].get("required_apis", []))
    for api in sorted(required_apis - called):
        issues.append(_issue("DESIGN-CODE-API", "WARN", f"design declares required API {api}(), but code does not call it"))

    hard_filters = " ".join(design["constraints"].get("hard_filters", [])).lower()
    if design.get("asset_class") == "stock" and any(token in hard_filters for token in ("st", "halt", "suspend")):
        if "filter_stock_by_status" not in called and "get_stock_status" not in called:
            issues.append(_issue("HARDFILTER-STATUS", "BLOCK", "confirmed status filters require a public status API"))
    if any(token in hard_filters for token in ("limit", "??", "??")) and "check_limit" not in called:
        issues.append(_issue(
            "HARDFILTER-LIMIT", "WARN",
            "PTrade backtest has no public check_limit(); document that order rejection/price fields enforce limit behavior."))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.upper()
            if re.fullmatch(r"\d{6}\.(SH|XSHG|XSHE)", value):
                issues.append(_issue("SECURITY-CODE-PORTABILITY", "BLOCK", f"use PTrade suffix .SS/.SZ/.BJ, not {node.value!r}", node.lineno))

    if "AGENT_IMPLEMENTATION_REQUIRED" in source:
        issues.append(_issue("AGENT-IMPLEMENTATION-MISSING", "BLOCK", "strategy source still contains scaffold implementation markers"))
    if "strategy_pattern" in source:
        issues.append(_issue("NO-STRATEGY-PATTERN", "BLOCK", "strategy source must not depend on strategy_pattern dispatch"))

    # ---- per-code ETF T+0 契约（docs/etf-t0-per-code-design.md §6.3 / §7 / §8.2）----
    # 1) 订单返回值本地字段读取禁令（仅限 skill 生成的 PTrade 可移植策略；本地专用策略
    #    不受限——引擎 Order docstring 允许检查 status，边界以 README/strategy_toolbox 为准）
    for m in re.finditer(r"\.(?:status|reason)\b", source):
        if m:
            issues.append(_issue(
                "ORDER-RETURN-FIELD-READ", "BLOCK",
                "skill 生成的 PTrade 可移植策略不得读取订单返回值的本地字段 "
                "(.status/.reason)——本地返回 Order 对象、PTrade 返回 order_id/None，"
                "仅允许真值判断 + get_position 持仓对账（references/etf-t0-rules.md §3）",
                source[:m.start()].count("\n") + 1))
            break
    # 2) 非确定性迭代禁令（T3，G2 回归发现存量策略 dict/set 迭代顺序跨进程不稳定）
    #    盲区说明：set 字面量迭代（for x in {'a','b'}）本规则拦不住（哈希顺序同样不稳定），
    #    由契约文本约束（references/etf-t0-rules.md §3-5）+ R5 复现性双跑门禁（G3.5）兜底；
    #    .status/.reason 正则会误伤同名属性（不限于订单返回值）——宁可误拦，申诉走人工 review。
    for m in re.finditer(r"\b(?:set|frozenset)\s*\(", source):
        issues.append(_issue(
            "NONDETERMINISTIC-ITERATION", "BLOCK",
            "禁止在生成策略中使用 set()/frozenset()：哈希迭代顺序随进程随机（PYTHONHASHSEED），"
            "会导致决策与回测结果跨进程不稳定；使用 list + sorted() 等确定性容器",
            source[:m.start()].count("\n") + 1))

    if strict_ptrade:
        _validate_history_shape_safety(tree, parents, issues)
        _validate_history_helper_required(design, tree, functions, parents, issues)
    _validate_portfolio_contract_code(design, tree, functions, issues)
    _validate_r5_audit_logs(design, tree, functions, parents, issues)
    return _report(design, source_name, target_profile, issues)


def _report(design: dict[str, Any], source_name: str, target_profile: str,
            issues: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = [item for item in issues if item["severity"] == "BLOCK"]
    warnings = [item for item in issues if item["severity"] == "WARN"]
    status = "BLOCKED" if blocks else "PASS"
    return {
        "report_version": "2.1",
        "strategy_id": design.get("strategy_id"),
        "source": source_name,
        "target_profile": target_profile,
        "status": status,
        # Static profile validation terminology (Skill 0.6.0): a static PASS is
        # portability evidence only; it never means broker-runtime verified or
        # deployable.
        "profile_validation_status": status,
        "runtime_validation_status": "NOT_VERIFIED",
        "deployment_status": "NOT_DEPLOYABLE",
        "block_count": len(blocks),
        "warning_count": len(warnings),
        "issues": issues,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate agent-authored strategy code")
    parser.add_argument("strategy", help="Strategy Python source")
    parser.add_argument("--design", required=True, help="agent_strategy_design.json")
    parser.add_argument("--target-profile", choices=["canonical", "quantstudio", "ptrade"], default="canonical")
    parser.add_argument("--out", help="Write validation report JSON")
    args = parser.parse_args(argv)
    design = load_json(args.design)
    source = Path(args.strategy).read_text(encoding="utf-8-sig")
    report = validate_strategy(design, source, str(args.strategy), args.target_profile)
    if args.out:
        write_json(args.out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

