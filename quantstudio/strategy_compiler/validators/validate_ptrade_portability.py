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
from .scan_lookahead import Violation

# PTrade-forbidden APIs (ptrade_import.py "第3批" + "第5批" local extensions).
# These ARE in ptrade_import.py's injected set (so validate_local_strategy
# whitelists them), but PTrade's public Profile forbids them.
_PTRADE_DENYLIST: frozenset[str] = frozenset({
    # Batch APIs (B1 perf optimization — local only)
    "get_fundamentals_batch",
    "get_history_batch",
    # Local file-system / position import
    "create_dir",
    "get_trades_file",
    "get_research_path",
    "convert_position_from_csv",
})


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
        if name in _PTRADE_DENYLIST:
            # Classify: batch API vs file/DB access
            if name in ("get_fundamentals_batch", "get_history_batch"):
                rule_id = "PORTABILITY-LOCAL-EXTENSION-BAN"
                msg = (f"PTrade code calls {name}() — batch perf API is local-only, "
                       f"forbidden in PTrade public Profile (ptrade-profile-contract.md §2). "
                       f"Must degrade to per-stock loop get_history/get_fundamentals.")
            else:
                rule_id = "PORTABILITY-FILE-DB-ACCESS"
                msg = (f"PTrade code calls {name}() — local file/DB/position access "
                       f"forbidden in PTrade public Profile (ptrade-profile-contract.md §2: "
                       f"不得访问 DuckDB/Provider/本地文件).")
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
