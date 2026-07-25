#!/usr/bin/env python3
"""Validate agent-authored QuantStudio/PTrade strategy code against real profiles."""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

from agent_skill_common import (
    call_name, confirmation_errors, constant_value, function_map,
    is_placeholder_function, load_catalog, load_json, skill_root,
    validate_design, write_json,
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


def _load_ptrade_profile() -> dict[str, Any]:
    return load_json(skill_root() / "references" / "ptrade-api-signatures.json")


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

    if name in set(profile.get("local_only_symbols", [])) and name not in defined_names:
        issues.append(_issue(
            "PTRADE-LOCAL-SYMBOL", "BLOCK",
            f"{name} is a QuantStudio-local injected symbol. Define a portable helper in source "
            f"or use a documented PTrade public API.", call.lineno))


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
        if strict_ptrade and name:
            _validate_ptrade_call(node, name, ptrade_profile, defined_names, issues)

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
    return _report(design, source_name, target_profile, issues)


def _report(design: dict[str, Any], source_name: str, target_profile: str,
            issues: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = [item for item in issues if item["severity"] == "BLOCK"]
    warnings = [item for item in issues if item["severity"] == "WARN"]
    return {
        "report_version": "2.0",
        "strategy_id": design.get("strategy_id"),
        "source": source_name,
        "target_profile": target_profile,
        "status": "BLOCKED" if blocks else "PASS",
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

