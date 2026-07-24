#!/usr/bin/env python
"""Validate a strategy_spec.json against the QuantStudio contract.

PR6b-1: converged to contracts.py single-source-of-truth. Validation now
includes the 5 cross-field timing rules from
quantstudio.strategy_compiler.contracts.validate_strategy_spec (bar/tick
frequency alignment, frequency rank monotonicity, next_open clock consistency,
proxy approximation records) ON TOP of the JSON-Schema check. The Skill-local
extras (spec_version consistency, capability warnings) are preserved.

Usage:
    python validate_strategy_spec.py <strategy_spec.json>
    python validate_strategy_spec.py <spec.json> --schema <custom.schema.json>

Exit code: 0 = valid, 1 = invalid (violations printed to stderr).

Note: requires the quantstudio package importable (run from project root or
project venv). If import fails, a clear error is printed (not a raw ImportError).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _here() -> Path:
    return Path(__file__).resolve().parent


def _default_schema_path() -> Path:
    return _here().parent / "schemas" / "strategy_spec.schema.json"


def _import_contracts_validate():
    """Import contracts.validate_strategy_spec with defensive error handling.

    Returns the callable, or raises SystemExit with a clear message if the
    quantstudio package is not importable (the Skill must run from project root
    or project venv — "no quantstudio package" is not a real scenario per the
    Skill's deployment context).
    """
    try:
        from quantstudio.strategy_compiler.contracts import (
            validate_strategy_spec as _contracts_validate,
            ContractValidationError,
        )
        return _contracts_validate, ContractValidationError
    except ImportError as e:
        raise SystemExit(
            "validate_strategy_spec 需要 quantstudio 包（含 5 条 timing 规则的单一真源）。\n"
            "请在项目根目录或项目 venv 中运行：\n"
            "  cd D:\\miniQMT策略实盘\\QuantStudio && python -m pip install -e .\n"
            f"原始 ImportError: {e}"
        )


def _validate_jsonschema(spec: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """Run JSON-Schema validation. Returns list of violation messages (empty = pass).

    The schema's allOf rules enforce: tick execution_status, bar bar_frequency,
    daily_open_proxy/close_proxy pairings, stock hard_filters, T-close+current_bar
    lookahead reject. We rely on these — do not duplicate them here.
    """
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema library not installed (pip install jsonschema)"]
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(spec), key=lambda e: list(e.path))
    return [_format_schema_error(e) for e in errors]


def _format_schema_error(err) -> str:
    loc = "/".join(str(p) for p in err.path) or "<root>"
    return f"[schema] {loc}: {err.message}"


def _check_spec_version_consistency(spec: Dict[str, Any]) -> List[str]:
    """spec_version (top-level, const '1.0') should align with
    contract_versions.strategy_spec_version (semver like '1.0.0').

    The schema treats these as independent fields (different shapes), so it
    cannot catch a mismatch like spec_version='1.0' but strategy_spec_version='2.0.0'.
    """
    violations = []
    top = spec.get("spec_version")
    cv = spec.get("contract_versions", {})
    ssv = cv.get("strategy_spec_version") if isinstance(cv, dict) else None
    if top and ssv:
        # spec_version '1.0' should correspond to strategy_spec_version major '1'
        try:
            major = str(ssv).split(".")[0]
            top_major = str(top).split(".")[0]
            if major != top_major:
                violations.append(
                    f"[consistency] spec_version='{top}' (major {top_major}) conflicts "
                    f"with contract_versions.strategy_spec_version='{ssv}' (major {major})"
                )
        except Exception:
            violations.append(
                f"[consistency] cannot parse spec_version='{top}' or "
                f"strategy_spec_version='{ssv}' for major-version alignment"
            )
    return violations


# Known capability IDs in the current capability matrix (capability-model.md).
# Capability IDs are functional-layer abstractions (e.g. 'stock_daily_backtest' =
# the ability to run daily-bar backtests on stocks), not raw table names — a Spec
# declares "I need the daily-backtest capability", not "I need the stock_daily table"
# (the latter is a data_status dimension inspect_capabilities reports).
# This set is illustrative, not exhaustive — the matrix evolves. Unknown IDs trigger
# a WARNING (printed to stdout), not a validation failure, to avoid blocking
# legitimate new capabilities or higher-level abstractions.
KNOWN_CAPABILITY_IDS = {
    # engine-layer capabilities
    "stock_daily_backtest", "etf_daily_backtest",
    "stock_minute_backtest", "etf_minute_backtest",
    "tick_backtest",
    # filter / data capabilities
    "stock_status_filter", "etf_status_filter",
    "stock_daily", "etf_daily", "stock_minutes", "etf_minutes", "tick_data",
    "index_daily", "fin_indicator", "stock_float_share", "stock_dividend", "stock_basic",
    "stock_daily_valuation", "scheduled_intraday_execution", "position_batch_state",
    "sw_industry", "index_constituents", "stock_namechange",
    # platform capabilities
    "ptrade_default_public_api", "quantstudio_public_api",
}


def _check_capability_requirements_exist(spec: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """capability_requirements entries should be recognizable capability IDs.

    The schema only requires non-empty unique strings — it cannot know which
    IDs the capability matrix actually defines. Unknown IDs are returned as
    WARNINGS (not hard errors), surfaced so typos are caught early without
    blocking legitimate new capabilities.

    Returns (violations, warnings).
    """
    reqs = spec.get("capability_requirements", []) or []
    unknown = [r for r in reqs if r not in KNOWN_CAPABILITY_IDS]
    warnings = []
    if unknown:
        warnings.append(
            f"[capability] capability_requirements references IDs not in the known set: {unknown}. "
            f"If these are new capabilities, add them to KNOWN_CAPABILITY_IDS. "
            f"(This is a warning, not a validation failure.)"
        )
    return ([], warnings)


def validate_spec(spec: Dict[str, Any], schema: Dict[str, Any]) -> Tuple[bool, List[str], List[str]]:
    """Full validation: schema + contracts.py timing rules + Skill-local extras.

    PR6b-1: converged to contracts.validate_strategy_spec as the single source
    of truth for the 5 cross-field timing rules (bar/tick frequency alignment,
    frequency rank monotonicity, next_open clock, proxy approximation records).
    The Skill-local extras (spec_version consistency, capability warnings) run
    on top.

    Returns (is_valid, violations, warnings).
    - violations: hard errors (schema + contracts timing + spec_version) → invalid
    - warnings: soft notices (unknown capability IDs) → valid but printed
    """
    violations: List[str] = []
    warnings: List[str] = []
    violations.extend(_validate_jsonschema(spec, schema))

    # PR6b-1: contracts.py timing rules (single source of truth).
    _contracts_validate, _ContractValidationError = _import_contracts_validate()
    try:
        _contracts_validate(spec)
    except _ContractValidationError as e:
        violations.append(f"[timing] {e}")

    violations.extend(_check_spec_version_consistency(spec))
    cap_violations, cap_warnings = _check_capability_requirements_exist(spec)
    violations.extend(cap_violations)
    warnings.extend(cap_warnings)
    return (len(violations) == 0, violations, warnings)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate a strategy_spec.json")
    parser.add_argument("spec", help="Path to strategy_spec.json")
    parser.add_argument("--schema", default=None,
                        help="Path to strategy_spec.schema.json (default: bundled)")
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"ERROR: spec file not found: {spec_path}", file=sys.stderr)
        return 1
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {spec_path}: {e}", file=sys.stderr)
        return 1

    schema_path = Path(args.schema) if args.schema else _default_schema_path()
    if not schema_path.exists():
        print(f"ERROR: schema file not found: {schema_path}", file=sys.stderr)
        return 1
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    ok, violations, warnings = validate_spec(spec, schema)
    if ok:
        print(f"VALID: {spec_path} passes schema + consistency checks.")
        for w in warnings:
            print(f"  WARNING: {w}")
        return 0
    else:
        print(f"INVALID: {spec_path} has {len(violations)} violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        for w in warnings:
            print(f"  WARNING: {w}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
