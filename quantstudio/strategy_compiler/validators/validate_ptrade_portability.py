"""validate_ptrade_portability validator (PR6b-1).

Derived from `docs/strategy-compiler/ptrade-profile-contract.md` §3 + IR contract §5.

PTrade-specific portability checks that go BEYOND validate_local_strategy:
the validate_local_strategy API whitelist reads ALL of ptrade_import.py (which
INCLUDES the batch/local-extension APIs). PTrade's public profile forbids those,
so this validator applies a stricter DENYLIST on top.

DENYLIST (ptrade-profile-contract.md §2 + IR contract §5): APIs that are
local-only optimizations or local file/DB access, forbidden in PTrade output:
  - get_fundamentals_batch, get_history_batch  (B1 batch perf APIs)
  - create_dir, get_trades_file, get_research_path  (local file-system access)
  - convert_position_from_csv  (local position import)

Returns (ok, violations, warnings) — same shape as PR6a validators.
"""

from __future__ import annotations

import ast
from typing import Any

from ..ir_nodes import StrategyIR
from ..portability_rules import (
    DENY_SHIM,
    INJECTED_WRAPPER_NAMES,
    SHIM_CONTRACT_REGISTRY,
    denylist,
)
from .scan_lookahead import Violation

# T3 修订：DENYLIST 单一来源——并集引用 portability_rules.denylist()，
# 转换器（source_import）与校验器共用同一清单，杜绝双边漂移。
# 消息分类集合仅用于生成 rule_id（不改变判定本身）：
_BATCH_APIS: frozenset[str] = frozenset(DENY_SHIM)  # get_fundamentals_batch / get_history_batch
_FILE_DB_APIS: frozenset[str] = frozenset({
    "create_dir", "get_trades_file", "get_research_path", "convert_position_from_csv",
})
# 其余 DENYLIST 项（本地自创 API / 外部数据源 / 成本模型）→ PORTABILITY-LOCAL-API


def validate_ptrade_portability(
    code: str,
    ir: StrategyIR | None = None,
    spec: dict[str, Any] | None = None,
) -> tuple[bool, list[Violation], list[str]]:
    """Validate that rendered PTrade .py contains no forbidden local-extension APIs.

    Returns (ok, violations, warnings). ok=False iff any BLOCK violation.
    """
    violations: list[Violation] = []
    warnings: list[str] = []

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        violations.append(Violation(
            rule_id="PORTABILITY-SYNTAX",
            severity="BLOCK",
            message=f"PTrade code has SyntaxError: {e}",
            location=f"line {e.lineno}",
        ))
        return False, violations, warnings

    # 收集产物中定义的函数名（含注入的 shim：def get_history_batch 等）。
    # 若 DENY_SHIM 类 API 在产物中有同名 def 定义，说明转换器已注入 shim
    # （Python 模块级 LEGB 查找，调用绑定到注入的 shim 而非本地扩展）→ 放行。
    # 修复（2026-08-12，zcode）：etf_theme_rotation 转换产物含注入 shim 仍被误 BLOCK。
    _defined_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    _defined_locs: dict[str, int] = {}
    for _node in ast.walk(tree):
        if isinstance(_node, ast.FunctionDef):
            _defined_locs.setdefault(_node.name, _node.lineno)

    # P-D10 三道防线①（机器门禁）：注入 shim/wrapper def 必须登记于
    # SHIM_CONTRACT_REGISTRY（本地契约四要素唯一真相），防第四例漏形状。
    _registered_injected = frozenset(SHIM_CONTRACT_REGISTRY)
    _injectable_names = frozenset(INJECTED_WRAPPER_NAMES) | frozenset(DENY_SHIM)
    for _n in sorted(_defined_names):
        if _n in _injectable_names and _n not in _registered_injected:
            violations.append(Violation(
                rule_id="PORTABILITY-UNREGISTERED-SHIM",
                severity="BLOCK",
                message=(f"PTrade code defines injected shim/wrapper {_n}() not registered in "
                         f"SHIM_CONTRACT_REGISTRY (portability_rules.py). P-D10: every injected "
                         f"shim/wrapper must register its local-contract four elements "
                         f"(type/index/columns/empty) before injection."),
                location=f"line {_defined_locs.get(_n, '?')}",
            ))

    # AST scan for DENYLIST calls
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        f = node.func
        if isinstance(f, ast.Name):
            name = f.id
        elif isinstance(f, ast.Attribute):
            name = f.attr
        if name in denylist():
            # 例外：DENY_SHIM 类 API（get_history_batch / get_fundamentals_batch）
            # 若产物中已有同名 def（注入的 shim），调用绑定到 shim 函数（shim 内部
            # 循环调 get_history/get_fundamentals 公共 API），语义已降级 → 放行。
            # DENY_REMOVE / DENY_BLOCK 类不适用此例外（产物中不会有其 def 定义）。
            if name in _BATCH_APIS and name in _defined_names:
                continue
            # 分类：batch API vs 本地文件/DB 访问 vs 本地自创 API
            if name in _BATCH_APIS:
                rule_id = "PORTABILITY-LOCAL-EXTENSION-BAN"
                msg = (f"PTrade code calls {name}() — batch perf API is local-only, "
                       f"forbidden in PTrade public Profile (ptrade-profile-contract.md §2). "
                       f"Must degrade to per-stock loop get_history/get_fundamentals.")
            elif name in _FILE_DB_APIS:
                rule_id = "PORTABILITY-FILE-DB-ACCESS"
                msg = (f"PTrade code calls {name}() — local file/DB/position access "
                       f"forbidden in PTrade public Profile (ptrade-profile-contract.md §2: "
                       f"不得访问 DuckDB/Provider/本地文件).")
            else:
                rule_id = "PORTABILITY-LOCAL-API"
                msg = (f"PTrade code calls {name}() — local-only API (portability_rules "
                       f"DENY_REMOVE/DENY_BLOCK), forbidden in PTrade public Profile.")
            violations.append(Violation(
                rule_id=rule_id,
                severity="BLOCK",
                message=msg,
                location=f"line {node.lineno}",
            ))

    # Header check: PTrade output should declare the profile
    if "ptrade-default" not in code[:500]:
        warnings.append("PTrade code header does not declare 'ptrade-default' profile")

    ok = not any(v.severity == "BLOCK" for v in violations)
    return ok, violations, warnings


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m quantstudio.strategy_compiler.validators.validate_ptrade_portability <ptrade.py>"""
    import sys
    from pathlib import Path
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) < 1:
        print("Usage: validate_ptrade_portability <ptrade.py>", file=sys.stderr)
        return 2
    code = Path(argv[0]).read_text(encoding="utf-8")
    ok, violations, warnings = validate_ptrade_portability(code)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    if ok:
        print(f"VALID: PTrade code passes portability checks ({len(violations)} non-block)")
        return 0
    print(f"INVALID: {len(violations)} portability violation(s):", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
