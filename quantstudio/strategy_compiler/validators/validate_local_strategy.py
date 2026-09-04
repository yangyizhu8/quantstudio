"""validate_local_strategy validator (PR6a).

Derived from `docs/strategy-compiler/strategy-ir-contract.md` §6 + master plan
§7.35 + `ptrade-profile-contract.md` §3 + `config/strategy_fidelity_gates.json`
semantics block.

Static validation of a rendered local (.py) strategy:
  1. Python syntax (compile())
  2. Lifecycle completeness (initialize required; others optional)
  3. API whitelist (every called name must be in ptrade_import injected set,
     or a stdlib/strategy-local helper)
  4. StrategyIsolationGuard (delegates to strategy_runner; forbidden imports/calls)
  5. Semantics contract (portfolio_positions_container=builtins.dict,
     portfolio_membership=exact_key_match, alias_aware_apis usage)

Returns (ok, violations, warnings). Same (ok, violations, warnings) shape as
scan_lookahead for uniform handling.
"""

from __future__ import annotations

import ast
import builtins as _builtins
import json
import re
from dataclasses import dataclass
from pathlib import Path

# 动态 builtin 集合（2026-08-11 修复：硬编码白名单缺 all/any 等常见内置函数，
# 导致合法策略（如 ETF动量.py 的 all(...)）被误 BLOCK）
_BUILTIN_NAMES = frozenset(dir(_builtins))
from typing import Any

from ..ir_nodes import StrategyIR

# Re-use Violation shape from scan_lookahead for uniformity.
from .scan_lookahead import Violation


# Standard-library / common modules whose imports are allowed in strategy files
# (mirrors 双均线策略.py which only imports numpy). Strategy files must NOT
# import quantstudio internals, duckdb, sqlite3, etc. — those are caught by
# StrategyIsolationGuard (delegated below).
_ALLOWED_IMPORT_MODULES: frozenset[str] = frozenset({
    "numpy", "np", "pandas", "pd", "math", "datetime", "decimal",
    "collections", "itertools", "functools", "logging",
})

# Lifecycle function names (strategy_runner.StrategySpec REQUIRED + OPTIONAL)
_LIFECYCLE_REQUIRED = ("initialize",)
_LIFECYCLE_OPTIONAL = ("before_trading_start", "handle_data", "after_trading_end", "set_backtest")
_LIFECYCLE_KNOWN = frozenset(_LIFECYCLE_REQUIRED + _LIFECYCLE_OPTIONAL)


def _load_injected_names() -> set[str]:
    """Load the set of names ptrade_import.py injects into strategy modules.

    This is the API whitelist. Rendered strategy code may call any of these
    without import (they are injected at load time).
    """
    import ast as _ast
    # validators/validate_local_strategy.py
    # parents[0]=validators, [1]=strategy_compiler, [2]=quantstudio
    pi_path = Path(__file__).resolve().parents[2] / "backtest" / "ptrade_import.py"
    tree = _ast.parse(pi_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, _ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def _load_semantics(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load the semantics block from strategy_fidelity_gates.json."""
    path = Path(config_path) if config_path else Path("config/strategy_fidelity_gates.json")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
        return cfg.get("semantics", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def validate_local_strategy(
    spec: dict[str, Any],
    ir: StrategyIR,
    code: str,
    profile: str,
    config_path: str | Path | None = None,
) -> tuple[bool, list[Violation], list[str]]:
    """Statically validate a rendered local strategy .py.

    Returns (ok, violations, warnings). ok=False iff any BLOCK violation.
    """
    violations: list[Violation] = []
    warnings: list[str] = []

    # 1. Python syntax
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        violations.append(Violation(
            rule_id="LOCAL-SYNTAX",
            severity="BLOCK",
            message=f"Python SyntaxError: {e}",
            location=f"line {e.lineno}",
        ))
        return False, violations, warnings

    # 2. Lifecycle completeness
    # 顶层函数集（生命周期语义：required 生命周期必须是顶层定义）
    top_level_funcs = {
        n.name for n in ast.iter_child_nodes(tree)
        if isinstance(n, ast.FunctionDef)
    }
    for req in _LIFECYCLE_REQUIRED:
        if req not in top_level_funcs:
            violations.append(Violation(
                rule_id="LOCAL-LIFECYCLE",
                severity="BLOCK",
                message=f"required lifecycle function {req!r} missing",
            ))
    unknown = top_level_funcs - _LIFECYCLE_KNOWN
    # Helper functions (non-lifecycle) are allowed; only flag if a name looks
    # like a lifecycle misspelling. For PR6a we allow any non-lifecycle helper.
    # (strategy_runner.StrategySpec.validate actually rejects unknown top-level
    #  names, but that's enforced at load time; here we just warn.)
    if unknown:
        warnings.append(f"non-lifecycle top-level functions present (allowed as helpers): {sorted(unknown)}")

    # 3. API whitelist
    injected = _load_injected_names()
    # Local helper functions defined in the strategy are allowed to be called
    # 2026-09-03 修复：全深度收集（ast.walk）——嵌套 def（注入模板的 helper，如
    # position-view 的 `def _attr`）同样是文件内本地 helper，仅收顶层会误 BLOCK。
    # 纯增益：只把「文件内真实 def」加入白名单，不新增任何外部 API 放行面。
    local_funcs = {
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    }
    # P-D11（2026-08-26）：本地定义的 class 实例化调用与本地函数同安全层级
    # （白名单目的是拦截未注入的平台 API，非本地构造）。此前仅收 FunctionDef，
    # 导致注入模板的视图类实例化被误 BLOCK（既有模板被迫用 type() 体操规避）。
    defined_classes = {
        n.name for n in ast.iter_child_nodes(tree)
        if isinstance(n, ast.ClassDef)
    }
    local_calls_ok = local_funcs | defined_classes
    # Identify imported module aliases (e.g. `import numpy as np` -> np) so that
    # attribute calls like np.concatenate / pd.DataFrame / log.info are recognized
    # as calls on allowed modules, not unknown bare names.
    imported_modules: set[str] = set()
    for node in ast.walk(tree):  # 2026-09-03：全深度（嵌套 import 别名同被识别，纯增益）
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_modules.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # For Attribute calls (X.method), check the root object X:
            # if X is an imported module (np/pd/log/etc.), allow the call.
            # if X is a local expression (e.g. result of get_history), the method
            #   is on a runtime object we can't statically resolve — allow it
            #   (the injected APIs return typed objects whose methods are safe).
            func = node.func
            if isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name) and func.value.id in imported_modules:
                    continue  # np.concatenate, pd.DataFrame, log.info — allowed
                # Attribute call on a non-module (e.g. df.sort_values, h[code]['close'])
                # — runtime object method, can't whitelist statically; allow.
                continue
            name = _call_name(node)
            if not name:
                continue
            if name in injected or name in local_calls_ok:
                continue
            # Builtins / common safe names
            # 修复（2026-08-11）：硬编码白名单缺 all/any/filter/map 等常见 builtin，
            # 导致合法策略（如 ETF动量.py 的 all(...)）被误 BLOCK。改为动态判断。
            if name in _BUILTIN_NAMES:
                continue
            violations.append(Violation(
                rule_id="LOCAL-API-WHITELIST",
                severity="BLOCK",
                message=f"calls unknown API {name!r} — not in ptrade_import injected set, "
                        f"not a local helper, not a builtin, not an imported-module method. "
                        f"Strategy may only use injected API.",
                location=f"line {node.lineno}",
            ))

    # 4. StrategyIsolationGuard (delegate)
    try:
        from ...backtest.strategy_runner import StrategyIsolationGuard
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp = f.name
        try:
            StrategyIsolationGuard.validate_path(tmp)
        finally:
            os.unlink(tmp)
    except Exception as e:
        violations.append(Violation(
            rule_id="LOCAL-ISOLATION-GUARD",
            severity="BLOCK",
            message=f"StrategyIsolationGuard rejected: {e}",
        ))

    # 5. Semantics contract (portfolio membership exact_match)
    semantics = _load_semantics(config_path)
    if semantics:
        _check_semantics_contract(tree, semantics, violations)

    ok = not any(v.severity == "BLOCK" for v in violations)
    return ok, violations, warnings


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _check_semantics_contract(
    tree: ast.AST, semantics: dict[str, Any], violations: list[Violation]
) -> None:
    """Check portfolio membership semantics (strategy_fidelity_gates.json).

    semantics.portfolio_membership = "exact_key_match": strategy code must use
    `code in context.portfolio.positions` as a plain dict membership test, not
    wrap positions in an alias-aware container.

    semantics.forbidden behaviors must not appear.
    """
    forbidden_behaviors = semantics.get("forbidden", [])
    alias_aware_apis = set(semantics.get("alias_aware_apis", []))

    # Detect: wrapping context.portfolio.positions in a constructor call
    # e.g. AliasDict(context.portfolio.positions) or dict alias conversion
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            # If a call takes context.portfolio.positions as arg and the called
            # name is NOT a plain builtin container or a read-only builtin, flag it
            # (alias-aware wrapping). len() is read-only and cannot wrap/alias the
            # container, so it must not trip this gate.
            # 修复（2026-08-22）：白名单补 len —— 只读内置 len(context.portfolio.positions)
            # 被误 BLOCK，属校验器误报（证据 docs/evidence/validator-len-false-positive-20260822.md）。
            if name in ("dict", "list", "set", "tuple", "len"):
                continue  # plain builtin re-wrap / read-only builtin is fine
            for arg in node.args:
                if _is_positions_attr(arg):
                    # Calling a non-builtin on positions = potential alias wrapping
                    if name not in alias_aware_apis and name not in ("get_position", "get_positions"):
                        violations.append(Violation(
                            rule_id="PORTFOLIO-POSITIONS-EXACT-MATCH",
                            severity="BLOCK",
                            message=f"call {name}(...) on context.portfolio.positions — wrapping the "
                                    f"public positions container breaks exact_key_match semantics "
                                    f"(strategy_fidelity_gates.json). Use positions as a plain dict.",
                            location=f"line {node.lineno}",
                        ))

    # Detect explicit alias-key usage in positions access (XSHG/XSHE keys).
    # semantics.forbidden lists "XSHG/XSHE keys in the public portfolio container".
    # 修复（2026-08-11，zcode）：子串匹配误伤 docstring/注释中的 ".XSHG" 文字
    # （如 tech_etf_mvo_rotation docstring "跨 .SS/.SZ/.XSHG/.SH 后缀比较"）。
    # 改为精确代码形态正则（6 位数字.XSHG/.XSHE），与转换器 _normalize_code_suffixes
    # 的匹配口径一致。.SH 不在此拦（QMT 风格，由转换器规范化为 .SS）。
    _XSHG_CODE_RE = re.compile(r"\b\d{6}\.(XSHG|XSHE)\b")
    forbidden_str = str(forbidden_behaviors).lower()
    xshg_forbidden = "xshg" in forbidden_str or "xshe" in forbidden_str
    if xshg_forbidden:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _XSHG_CODE_RE.search(node.value):
                    violations.append(Violation(
                        rule_id="PORTFOLIO-POSITIONS-EXACT-MATCH",
                        severity="BLOCK",
                        message=f"string {node.value!r} contains XSHG/XSHE suffix — public portfolio "
                                f"container uses exact_key_match on .SS/.SZ/.BJ, not XSHG/XSHE aliases "
                                f"(semantics.forbidden).",
                        location=f"line {node.lineno}",
                    ))


def _is_positions_attr(node: ast.AST) -> bool:
    """True if node is context.portfolio.positions (Attribute chain)."""
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr != "positions":
        return False
    val = node.value
    if not isinstance(val, ast.Attribute) or val.attr != "portfolio":
        return False
    return isinstance(val.value, ast.Name) and val.value.id == "context"


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m quantstudio.strategy_compiler.validators.validate_local_strategy <rendered.py> <spec.json> <ir.json> [profile]"""
    import sys, json
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) < 3:
        print("Usage: validate_local_strategy <rendered.py> <spec.json> <ir.json> [profile]", file=sys.stderr)
        return 2
    code = Path(argv[0]).read_text(encoding="utf-8")
    spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    ir = StrategyIR.from_dict(json.loads(Path(argv[2]).read_text(encoding="utf-8")))
    profile = argv[3] if len(argv) > 3 else "quantstudio"

    ok, violations, warnings = validate_local_strategy(spec, ir, code, profile)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    if ok:
        print(f"VALID: local strategy passes static checks ({len(violations)} non-block violations)")
        return 0
    print(f"INVALID: {len(violations)} violation(s):", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
