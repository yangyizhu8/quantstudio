"""check_hard_filters validator (PR6b-1).

Derived from `docs/strategy-compiler/ashare-filter-contract.md` §1 + IR contract §6.

Verifies HardFilterNode correctness against the 13-filter stock default:
  - HARDFILTER-STOCK-13: selection-stage lists ALL 13 (no implicit trimming)
  - HARDFILTER-EXECUTION-STAGE: execution-stage HardFilterNode exists (stock)
  - HARDFILTER-EXECUTION-SUBSET: execution-stage uses the 4 order-time subset
  - HARDFILTER-ASSET-MATCH: ETF/CB/futures use own Profile (not blind stock inherit)

Input: IR (primary) + Spec (for asset_class + hard_filters override detection).
Returns (ok, violations, warnings).
"""

from __future__ import annotations

from typing import Any

from ..ir_nodes import StrategyIR
from .scan_lookahead import Violation

# The 13 stock default filters (ashare-filter-contract.md §1).
_STOCK_13: frozenset[str] = frozenset({
    "exclude_st", "exclude_suspended", "exclude_delisted", "exclude_delisting_sorting",
    "exclude_star_market", "exclude_bse", "min_listing_trade_days",
    "exclude_invalid_price", "exclude_zero_volume",
    "block_limit_up_buy", "block_limit_down_sell", "enforce_t1", "round_lot",
})

# Execution-stage subset (contract §6 note: order-time dynamic checks only).
_EXECUTION_4: frozenset[str] = frozenset({
    "block_limit_up_buy", "block_limit_down_sell", "enforce_t1", "round_lot",
})


def check_hard_filters(
    ir: StrategyIR,
    spec: dict[str, Any] | None = None,
) -> tuple[bool, list[Violation], list[str]]:
    """Verify HardFilterNode correctness.

    Returns (ok, violations, warnings).
    """
    violations: list[Violation] = []
    warnings: list[str] = []

    asset_class = (spec or {}).get("asset_class", "stock")
    selection_nodes = [n for n in ir.nodes
                       if n.node_type == "HardFilterNode"
                       and n.parameters.get("stage") == "selection"]
    execution_nodes = [n for n in ir.nodes
                       if n.node_type == "HardFilterNode"
                       and n.parameters.get("stage") == "execution"]

    if asset_class == "stock":
        # HARDFILTER-STOCK-13: selection must list all 13
        if not selection_nodes:
            violations.append(Violation(
                rule_id="HARDFILTER-STOCK-13",
                severity="BLOCK",
                message="stock asset_class requires a selection-stage HardFilterNode; none found",
            ))
        else:
            for n in selection_nodes:
                filters = set(n.parameters.get("filters", []))
                missing = _STOCK_13 - filters
                extra = filters - _STOCK_13
                if missing:
                    violations.append(Violation(
                        rule_id="HARDFILTER-STOCK-13",
                        severity="BLOCK",
                        message=f"selection HardFilterNode {n.node_id!r} missing filters: {sorted(missing)}. "
                                f"Stock asset_class must list ALL 13 (contract §6: no implicit trimming).",
                        location=f"node_id={n.node_id}",
                    ))
                if extra:
                    warnings.append(f"selection HardFilterNode {n.node_id} has unknown filters: {sorted(extra)}")

        # HARDFILTER-EXECUTION-STAGE: execution-stage must exist
        if not execution_nodes:
            violations.append(Violation(
                rule_id="HARDFILTER-EXECUTION-STAGE",
                severity="BLOCK",
                message="stock asset_class requires an execution-stage HardFilterNode to re-check "
                        "order-time filters (ashare-filter-contract.md §2); none found",
            ))
        else:
            # HARDFILTER-EXECUTION-SUBSET: execution uses the 4-subset
            for n in execution_nodes:
                filters = set(n.parameters.get("filters", []))
                if filters != _EXECUTION_4:
                    violations.append(Violation(
                        rule_id="HARDFILTER-EXECUTION-SUBSET",
                        severity="BLOCK",
                        message=f"execution HardFilterNode {n.node_id!r} filters={sorted(filters)} "
                                f"must be exactly the 4 order-time subset {sorted(_EXECUTION_4)}",
                        location=f"node_id={n.node_id}",
                    ))

        # HARDFILTER-EXPLICIT-OVERRIDE: if Spec turns a filter off, flag (PR6b-1 detection)
        if spec:
            spec_hf = spec.get("hard_filters", {})
            for k, v in spec_hf.items():
                if k in _STOCK_13 and v is False:
                    warnings.append(
                        f"Spec hard_filters.{k}=false — explicit override. IR node should carry "
                        f"HARDFILTER-EXPLICIT-OVERRIDE rule (PR6b-1 detects; full enforcement PR6b-2)."
                    )
    else:
        # HARDFILTER-ASSET-MATCH: non-stock must not blindly use stock 13-filter set
        for n in selection_nodes:
            filters = set(n.parameters.get("filters", []))
            if filters == _STOCK_13:
                violations.append(Violation(
                    rule_id="HARDFILTER-ASSET-MATCH",
                    severity="BLOCK",
                    message=f"asset_class={asset_class} selection HardFilterNode uses stock 13-filter set; "
                            f"ETF/CB/futures must use own Profile T+0/T+1/lot/limit (ashare-filter-contract.md §3)",
                    location=f"node_id={n.node_id}",
                ))

    ok = not any(v.severity == "BLOCK" for v in violations)
    return ok, violations, warnings


def main(argv: list[str] | None = None) -> int:
    import sys, json
    from pathlib import Path
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) < 1:
        print("Usage: check_hard_filters <ir.json> [spec.json]", file=sys.stderr)
        return 2
    ir = StrategyIR.from_dict(json.loads(Path(argv[0]).read_text(encoding="utf-8")))
    spec = json.loads(Path(argv[1]).read_text(encoding="utf-8")) if len(argv) > 1 else None
    ok, violations, warnings = check_hard_filters(ir, spec)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    if ok:
        print(f"VALID: hard filters pass ({len(violations)} non-block)")
        return 0
    print(f"INVALID: {len(violations)} hard-filter violation(s):", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
