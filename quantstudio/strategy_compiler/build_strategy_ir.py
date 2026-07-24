"""Build Strategy IR from Spec (PR6a).

Derived from `docs/strategy-compiler/strategy-ir-contract.md` §3 (signals.steps
mapping) + §4 (pipeline order).

This module converts a validated strategy_spec.json into a StrategyIR. The node
emit order is固定 by the contract §4 pipeline (UniverseNode -> selection
HardFilterNode -> DataLoadNode -> Indicator/Factor -> Signal -> Ranking ->
Portfolio -> Risk -> Execution -> execution HardFilterNode -> DiagnosticNode),
NOT derived from the order of fields in the Spec.

PR6a operation coverage (contract §7.1):
  - IndicatorNode: `ma` only
  - SignalNode: `cross` only
Any other operation raises ContractValidationError with the step id and a
"PR6b 扩展" hint — never silently downgrades (framework-contract invariant).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import ContractValidationError
from .ir_nodes import IRNode, StrategyIR, NODE_TYPE_REGISTRY

# Contract §6 HARDFILTER-STOCK-13: stock default full set (matches
# ashare-filter-contract.md §1). build_strategy_ir must list ALL 13 — no
# implicit trimming (contract §6 note).
_STOCK_FILTER_FULL_SET: tuple[str, ...] = (
    "exclude_st", "exclude_suspended", "exclude_delisted", "exclude_delisting_sorting",
    "exclude_star_market", "exclude_bse", "min_listing_trade_days",
    "exclude_invalid_price", "exclude_zero_volume",
    "block_limit_up_buy", "block_limit_down_sell", "enforce_t1", "round_lot",
)

# Contract §6 note: execution-stage HardFilterNode re-checks only the
# order-time dynamic subset (limit/T1/lot), not the static market/sector
# exclusions already fixed at selection stage.
_EXECUTION_FILTER_SUBSET: tuple[str, ...] = (
    "block_limit_up_buy", "block_limit_down_sell", "enforce_t1", "round_lot",
)

# PR6b-1-supported operations (contract §7.1/§7.2). Others raise.
# PR6b-1 adds pct_change (Indicator) + rank/top_n (Ranking) on top of PR6a's ma/cross.
# Factor ops (zscore/winsorize/neutralize/combine) + threshold/compare Signal → PR6b-2.
_PR6B1_INDICATOR_OPS: frozenset[str] = frozenset({"ma", "pct_change", "ema", "rolling_amplitude"})
_PR6B1_SIGNAL_OPS: frozenset[str] = frozenset({"cross", "compare", "open_below_previous_low", "and"})
_PR6B1_RANKING_OPS: frozenset[str] = frozenset({"rank", "top_n", "bottom_n"})
# Mapping: operation -> IR node_type (contract §3 table).
_OPERATION_TO_NODE_TYPE: dict[str, str] = {
    # IndicatorNode
    "ma": "IndicatorNode",
    "pct_change": "IndicatorNode",
    "ema": "IndicatorNode",
    "rolling_amplitude": "IndicatorNode",
    # SignalNode
    "cross": "SignalNode",
    "compare": "SignalNode",
    "open_below_previous_low": "SignalNode",
    "and": "SignalNode",
    # RankingNode
    "rank": "RankingNode",
    "top_n": "RankingNode",
    "bottom_n": "RankingNode",
    # The following are recognized as valid operations but NOT supported in PR6b-1;
    # they raise with a clear "PR6b-2" message. Listed here so unknown operations
    # can be distinguished from "valid-but-deferred" operations in the error.
}


def _spec_sha256(spec: dict[str, Any]) -> str:
    """Stable sha256 of the canonical JSON serialization of spec."""
    canonical = json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _make_node(
    node_id: str,
    node_type: str,
    input_refs: list[str],
    output: str,
    parameters: dict[str, Any],
    required_capabilities: list[str],
    timing: str,
    platform_mapping_qs: str,
    platform_mapping_ptrade: str,
    validation_rules: list[str],
) -> IRNode:
    """Construct an IRNode via the registry-dispatched dataclass."""
    cls = NODE_TYPE_REGISTRY[node_type]
    return cls(
        node_id=node_id,
        node_type=node_type,
        input=list(input_refs),
        output=output,
        parameters=dict(parameters),
        required_capabilities=list(required_capabilities),
        timing=timing,
        platform_mapping={
            "quantstudio": platform_mapping_qs,
            "ptrade-default": platform_mapping_ptrade,
        },
        validation_rules=list(validation_rules),
    )


def _build_universe_node(spec: dict[str, Any]) -> IRNode:
    universe = spec["universe"]
    kind = universe["kind"]
    params = dict(universe.get("parameters", {}))
    if kind == "single_stock":
        if "code" not in params:
            raise ContractValidationError(
                "universe.kind=single_stock requires parameters.code"
            )
        qs = f"g.security = '{params['code']}'; universe is the single stock itself"
    elif kind == "index_constituents":
        qs = f"g.stock_list = get_index_stocks('{params.get('index', '')}')"
    elif kind in ("etf_list", "manual_list"):
        codes = params.get("codes", [])
        if not codes:
            raise ContractValidationError(
                f"universe.kind={kind} requires non-empty parameters.codes"
            )
        codes_repr = ", ".join(repr(c) for c in codes)
        qs = f"g.stock_list = [{codes_repr}]"
    elif kind == "smallest_market_cap":
        pool_size = int(params.get("pool_size", 0))
        field = params.get("field", "circulating_market_cap")
        if pool_size <= 0:
            raise ContractValidationError(
                "universe.kind=smallest_market_cap requires positive parameters.pool_size"
            )
        if field not in ("circulating_market_cap", "market_cap", "float_value"):
            raise ContractValidationError(
                f"unsupported smallest_market_cap field: {field!r}"
            )
        qs = f"PIT valuation ascending by {field}; take smallest {pool_size}"
    else:
        raise ContractValidationError(f"Unknown universe.kind: {kind!r}")
    return _make_node(
        node_id="universe",
        node_type="UniverseNode",
        input_refs=[],
        output="universe",
        parameters={"kind": kind, **params},
        required_capabilities=[],
        timing="pre_open",
        platform_mapping_qs=qs,
        platform_mapping_ptrade="同 QS（注入 API 一致）",
        validation_rules=["UNIVERSE-NONEMPTY"],
    )


def _build_selection_hard_filter_node(spec: dict[str, Any]) -> IRNode:
    """Selection-stage HardFilterNode lists ALL 13 filters for stock asset_class
    (contract §6 HARDFILTER-STOCK-13: no implicit trimming).
    """
    asset_class = spec.get("asset_class", "stock")
    if asset_class != "stock":
        raise ContractValidationError(
            f"PR6b-1 build_strategy_ir only supports asset_class=stock; got {asset_class!r} (ETF/CB/futures进PR6b-2)"
        )
    # Contract §6 note: list all 13 regardless of which the Spec sets true/false.
    # The Spec's hard_filters values are recorded in parameters.spec_filters
    # so the renderer can honor explicit overrides, but the node's active
    # filter set is the full 13.
    spec_filters = dict(spec.get("hard_filters", {}))
    return _make_node(
        node_id="hard_filter_selection",
        node_type="HardFilterNode",
        input_refs=["universe"],
        output="filtered_universe",
        parameters={
            "filters": list(_STOCK_FILTER_FULL_SET),
            "stage": "selection",
            "spec_filters": spec_filters,  # 透传 Spec 原值供 renderer 按需 honor override
        },
        required_capabilities=["stock_status_filter"],
        timing="pre_open",
        platform_mapping_qs=(
            "selection stage: filter_stock_by_status for ST/HALT/DELISTING + "
            "listing-day/price/volume checks; for single_stock universe most "
            "filters are no-op (the stock either passes or gets fully rejected) "
            "but all 13 are evaluated so the rule set is uniform across universe kinds"
        ),
        platform_mapping_ptrade="同 QS",
        validation_rules=["HARDFILTER-STOCK-13", "HARDFILTER-ASSET-MATCH"],
    )


def _build_dataload_node(spec: dict[str, Any]) -> IRNode:
    """DataLoadNode for the primary market dataset (PR6a: single dataset case).

    fields = union of fields actually referenced by signals.steps (e.g. ma's
    `field` param), not the full dataset OHLCV — this keeps the load minimal
    and makes the IR reflect真实 data dependency.
    lookback = max(ma lookbacks) doubled for a safety margin (matches the
    双均线策略.py pattern: ma10 needs 10 bars, loads 20).
    """
    datasets = spec.get("data_requirements", {}).get("datasets", [])
    bar_ds = next((d for d in datasets if d["dataset"] in ("stock_daily", "etf_daily", "stock_minutes", "etf_minutes")), None)
    if bar_ds is None:
        raise ContractValidationError(
            "data_requirements must contain a bar dataset (stock_daily/etf_daily/stock_minutes/etf_minutes)"
        )
    freq = bar_ds["frequency"]
    # Collect fields actually used by signal steps (indicator `field` param).
    used_fields: list[str] = []
    for s in spec["signals"]["steps"]:
        f = s.get("parameters", {}).get("field")
        if f and f not in used_fields:
            used_fields.append(f)
    # Fallback to dataset fields if no step declares a field.
    if not used_fields:
        used_fields = list(bar_ds["fields"])
    # Lookback: max(indicator lookback) * 2 for safety margin (双均线策略.py pattern).
    # PR6b-1: covers ma AND pct_change (both IndicatorNode with lookback).
    lookbacks = [
        s["parameters"]["lookback"]
        for s in spec["signals"]["steps"]
        if s["operation"] in _PR6B1_INDICATOR_OPS and "lookback" in s.get("parameters", {})
    ]
    lookback = (max(lookbacks) * 2) if lookbacks else 20
    is_minute = freq in ("1m", "5m", "15m", "30m", "60m")
    pit_anchor = "previous_date"
    qs = (
        f"get_history({lookback}, '{freq}', field={used_fields!r}, "
        f"security_list=g.security, fq='dypre', include=False, is_dict=True)"
    )
    ptrade = qs + "；禁止 get_history_batch"
    return _make_node(
        node_id="dataload_market",
        node_type="DataLoadNode",
        input_refs=["filtered_universe"],
        output="close_history",
        parameters={
            "dataset": bar_ds["dataset"],
            "frequency": freq,
            "fields": list(used_fields),
            "pit_required": True,
            "pit_anchor": pit_anchor,
            "lookback": lookback,
        },
        required_capabilities=["stock_daily_backtest"] if not is_minute else ["stock_1m_backtest"],
        timing="pre_open",
        platform_mapping_qs=qs,
        platform_mapping_ptrade=ptrade,
        validation_rules=["DATALOAD-PIT-PREVIOUS-DATE"] + (["DATALOAD-NO-INCLUDE-TRUE"] if is_minute else []),
    )


def _build_signal_chain_nodes(spec: dict[str, Any]) -> list[IRNode]:
    """Build Indicator/Signal nodes from signals.steps (contract §3 mapping).

    Returns nodes in step order. Each node's input references upstream step
    outputs (step id == output name, contract §3).
    """
    nodes: list[IRNode] = []
    step_id_to_output: dict[str, str] = {}
    for step in spec["signals"]["steps"]:
        step_id = step["id"]
        op = step["operation"]
        params = dict(step.get("parameters", {}))
        output = step_id  # contract §3: output name == step id for traceability

        if op in _PR6B1_INDICATOR_OPS:
            node_type = "IndicatorNode"
            # Resolve source: uses close_history (DataLoad output) by default
            # unless parameters.source points to another indicator.
            source = params.get("source", "close_history")
            input_refs = [source] if source in step_id_to_output or source == "close_history" else ["close_history"]
            field = params.get("field", "close")
            lookback_val = params.get("lookback")
            if lookback_val is None or lookback_val <= 0:
                raise ContractValidationError(
                    f"indicator step {step_id!r} (operation={op}) requires positive parameters.lookback"
                )
            if op == "ma":
                qs = f"simple moving average of {field}, lookback={lookback_val}"
            elif op == "pct_change":
                qs = f"pct_change({field}, lookback={lookback_val})"
            elif op == "ema":
                qs = f"EMA({field}, lookback={lookback_val}, adjust=False)"
            else:
                high_field = params.get("high_field", "high")
                low_field = params.get("low_field", "low")
                qs = f"(max({high_field})-min({low_field}))/min({low_field}), lookback={lookback_val}"
            validation_rules = ["INDICATOR-LOOKBACK-POSITIVE", "INDICATOR-NO-FUTURE-BAR"]
            required_caps: list[str] = []
            timing = "bar"
            parameters = {"operation": op, "field": field, "lookback": lookback_val}
            if op == "rolling_amplitude":
                parameters.update({
                    "high_field": params.get("high_field", "high"),
                    "low_field": params.get("low_field", "low"),
                    "denominator": params.get("denominator", "min_low"),
                })
        elif op == "cross":
            node_type = "SignalNode"
            sources = params.get("sources", [])
            if not sources:
                raise ContractValidationError(
                    f"signal step {step_id!r} (operation=cross) requires parameters.sources"
                )
            # Validate sources are前序 step ids
            missing = [s for s in sources if s not in step_id_to_output]
            if missing:
                raise ContractValidationError(
                    f"signal step {step_id!r} references unknown source(s) {missing}; "
                    f"known upstream: {list(step_id_to_output)}"
                )
            input_refs = [step_id_to_output[s] for s in sources]
            direction = params.get("direction", "golden")
            qs = (
                f"if {sources[0]} > {sources[1]} and position empty -> buy; "
                f"elif {sources[0]} < {sources[1]} and position held -> sell"
            )
            validation_rules = ["SIGNAL-TIMING-CONSISTENT", "SIGNAL-NO-SAME-CLOSE-TRADE"]
            required_caps = []
            timing = "bar"
            parameters = {"operation": op, "sources": list(sources), "direction": direction}
        elif op in ("compare", "open_below_previous_low", "and"):
            node_type = "SignalNode"
            if op == "and":
                sources = params.get("sources", [])
                if len(sources) < 2:
                    raise ContractValidationError(
                        f"signal step {step_id!r} operation=and requires at least two sources"
                    )
                missing = [source for source in sources if source not in step_id_to_output]
                if missing:
                    raise ContractValidationError(
                        f"signal step {step_id!r} references unknown source(s) {missing}"
                    )
                input_refs = [step_id_to_output[source] for source in sources]
                parameters = {"operation": op, "sources": list(sources)}
            elif op == "compare":
                left = params.get("left")
                right = params.get("right")
                right_value = params.get("right_value")
                comparator = params.get("comparator")
                if not left or (not right and right_value is None) or comparator not in (">", ">=", "<", "<=", "=="):
                    raise ContractValidationError(
                        f"signal step {step_id!r} compare requires left and right/right_value plus a supported comparator"
                    )
                refs = []
                for source in (left, right):
                    if source is None:
                        continue
                    if source == "close_history":
                        refs.append(source)
                    elif source in step_id_to_output:
                        refs.append(step_id_to_output[source])
                    else:
                        raise ContractValidationError(
                            f"signal step {step_id!r} references unknown compare source {source!r}"
                        )
                input_refs = refs
                parameters = {
                    "operation": op, "left": left, "right": right,
                    "right_value": right_value,
                    "comparator": comparator,
                    "left_offset": params.get("left_offset", -1),
                }
            else:
                input_refs = ["close_history"]
                parameters = {
                    "operation": op,
                    "open_field": params.get("open_field", "open"),
                    "low_field": params.get("low_field", "low"),
                    "low_offset": params.get("low_offset", -1),
                }
            qs = f"signal operation={op}"
            validation_rules = ["SIGNAL-TIMING-CONSISTENT", "SIGNAL-NO-FUTURE-DATA"]
            required_caps = []
            timing = "bar"
        elif op in _PR6B1_RANKING_OPS:
            node_type = "RankingNode"
            source = params.get("source")
            if not source:
                raise ContractValidationError(
                    f"ranking step {step_id!r} (operation={op}) requires parameters.source"
                )
            if source not in step_id_to_output and source != "close_history":
                raise ContractValidationError(
                    f"ranking step {step_id!r} references unknown source {source!r}; "
                    f"known upstream: {list(step_id_to_output)}"
                )
            input_refs = [source if source == "close_history" else step_id_to_output[source]]
            ascending = params.get("ascending", False)
            top_n_val = params.get("top_n") or params.get("n")
            qs = (
                f"sorted_codes = sorted(scores.keys(), key=lambda c: scores[c], reverse={not ascending}); "
                f"selected = sorted_codes[:{top_n_val}]"
            )
            validation_rules = ["RANK-SOURCE-EXISTS"]
            required_caps = []
            timing = "bar"
            parameters: dict[str, Any] = {"operation": op, "source": source, "ascending": ascending}
            if top_n_val is not None:
                parameters["top_n"] = top_n_val
        else:
            # Contract §3: unknown/deferred operation must raise (no silent downgrade).
            raise ContractValidationError(
                f"signals.steps step id={step_id!r} operation={op!r} is not supported in PR6b-1 "
                f"(supported: ma, pct_change, ema, rolling_amplitude, cross, compare, open_below_previous_low, and, rank, top_n, bottom_n). Extend in PR6b-2. "
                f"See strategy-ir-contract.md §3 mapping table."
            )

        node = _make_node(
            node_id=step_id,
            node_type=node_type,
            input_refs=input_refs,
            output=output,
            parameters=parameters,
            required_capabilities=required_caps,
            timing=timing,
            platform_mapping_qs=qs,
            platform_mapping_ptrade="同 QS",
            validation_rules=validation_rules,
        )
        nodes.append(node)
        step_id_to_output[step_id] = output
    return nodes


def _build_portfolio_node(spec: dict[str, Any], signal_output: str) -> IRNode:
    portfolio = spec["portfolio"]
    params = dict(portfolio.get("parameters", {}))
    kind = portfolio["kind"]
    if kind == "single_position":
        qs = "order_value(code, cash) when buy signal; order_target(code, 0) when sell signal"
    elif kind == "equal_weight_top_n":
        qs = "equal-weight: cash / max_positions per selected stock"
    else:
        qs = f"portfolio kind={kind}"
    return _make_node(
        node_id=f"portfolio_{kind}",
        node_type="PortfolioNode",
        input_refs=[signal_output],
        output="target_weights",
        parameters={
            "kind": kind,
            "max_positions": params.get("max_positions", 1),
            "rebalance": params.get("rebalance", "signal_triggered"),
            "target_weight": params.get("target_weight", 1.0),
        },
        required_capabilities=[],
        timing="bar",
        platform_mapping_qs=qs,
        platform_mapping_ptrade="同 QS",
        validation_rules=["PORTFOLIO-WEIGHT-VALID", "PORTFOLIO-POSITIONS-EXACT-MATCH"],
    )


def _build_risk_node(spec: dict[str, Any]) -> IRNode:
    risk = spec["risk"]
    params = dict(risk.get("parameters", {}))
    risk_kind = risk["kind"]
    if risk_kind == "position_limits":
        qs = f"position-limit check: max_single_weight={params.get('max_single_weight', 1.0)} before execution"
    else:
        qs = f"risk check (kind={risk_kind}) before execution"
    return _make_node(
        node_id=f"risk_{risk_kind.split('_')[-1]}",
        node_type="RiskNode",
        input_refs=["target_weights"],
        output="risk_checked_weights",
        parameters={
            "kind": risk_kind,
            "max_single_weight": params.get("max_single_weight", 1.0),
            "cash_buffer": params.get("cash_buffer", 0.0),
        },
        required_capabilities=[],
        timing="bar",
        platform_mapping_qs=qs,
        platform_mapping_ptrade="同 QS",
        validation_rules=["RISK-BEFORE-EXECUTION"],
    )


def _build_execution_node(spec: dict[str, Any]) -> IRNode:
    execution = spec["execution"]
    order_api_map = {
        "single_position": "order_value",
        "equal_weight_top_n": "order_value",
        "overlap_timed_equal_weight": "order_target_value",
    }
    portfolio_kind = spec["portfolio"]["kind"]
    order_api = order_api_map.get(portfolio_kind, "order_value")
    match_price_mode = execution["match_price_mode"]
    order_type = execution.get("order_type", "market")
    qs = f"{order_api}(g.security, cash) on buy; order_target(g.security, 0) on sell (match_price_mode={match_price_mode}, order_type={order_type})"
    return _make_node(
        node_id=f"execution_{order_type}",
        node_type="ExecutionNode",
        input_refs=["risk_checked_weights"],
        output="orders",
        parameters={
            "order_api": order_api,
            "match_price_mode": match_price_mode,
            "order_type": order_type,
            "allow_partial_fill": execution.get("allow_partial_fill", False),
        },
        required_capabilities=[],
        timing="bar",
        platform_mapping_qs=qs,
        platform_mapping_ptrade="同 QS；无本地撮合扩展",
        validation_rules=["EXEC-MATCH-PRICE-CONSISTENT", "EXEC-T1-ENFORCED"],
    )


def _build_execution_hard_filter_node(spec: dict[str, Any]) -> IRNode:
    """execution-stage HardFilterNode re-checks only the order-time subset
    (contract §6 note: block_limit_up_buy/block_limit_down_sell/enforce_t1/round_lot).
    """
    return _make_node(
        node_id="hard_filter_execution",
        node_type="HardFilterNode",
        input_refs=["orders"],
        output="filtered_orders",
        parameters={
            "filters": list(_EXECUTION_FILTER_SUBSET),
            "stage": "execution",
        },
        required_capabilities=["stock_status_filter"],
        timing="bar",
        platform_mapping_qs="check_limit(code)[code] + is_t1_blocked + round_to_lot (shared_ashare_rules) at order time",
        platform_mapping_ptrade="同 QS；禁止本地批量校验",
        validation_rules=["HARDFILTER-EXECUTION-STAGE"],
    )


def _build_diagnostic_node(last_output: str) -> IRNode:
    return _make_node(
        node_id="diagnostic",
        node_type="DiagnosticNode",
        input_refs=[last_output],
        output="diagnostics",
        parameters={"kind": "log", "fields": ["entry_signal", "exit_signal", "fill_price"]},
        required_capabilities=[],
        timing="post_close",
        platform_mapping_qs="log.info('Buying %s' / 'Selling %s') + g state recording",
        platform_mapping_ptrade="同 QS",
        validation_rules=["DIAG-NON-EMPTY"],
    )


def build_strategy_ir(spec: dict[str, Any]) -> StrategyIR:
    """Convert a validated strategy_spec dict into a StrategyIR.

    The Spec is assumed to have already passed `contracts.validate_strategy_spec`.
    This function emits nodes in the fixed contract §4 pipeline order; it does
    NOT derive node order from the order of fields in the Spec.

    Raises ContractValidationError on:
      - unsupported operation (0.3.3: ma, pct_change, ema, rolling_amplitude, cross, compare, open_below_previous_low, and, rank, top_n, bottom_n)
      - missing required Spec fields
      - signal source referencing unknown upstream step
      - asset_class != stock (PR6a scope; ETF/CB/futures进PR6b)
    """
    # Pipeline assembly (contract §4 order, fixed):
    universe_node = _build_universe_node(spec)
    selection_hf = _build_selection_hard_filter_node(spec)
    dataload = _build_dataload_node(spec)
    signal_chain = _build_signal_chain_nodes(spec)
    if not signal_chain:
        raise ContractValidationError(
            "signals.steps produced no IR nodes; at least one signal node is required"
        )
    # The last signal-chain node feeds Portfolio.
    last_signal_output = signal_chain[-1].output
    portfolio = _build_portfolio_node(spec, last_signal_output)
    risk = _build_risk_node(spec)
    execution = _build_execution_node(spec)
    execution_hf = _build_execution_hard_filter_node(spec)
    diagnostic = _build_diagnostic_node(execution_hf.output)

    nodes = [
        universe_node,
        selection_hf,
        dataload,
        *signal_chain,
        portfolio,
        risk,
        execution,
        execution_hf,
        diagnostic,
    ]

    return StrategyIR(
        strategy_id=spec["strategy_id"],
        nodes=nodes,
        source_spec_sha256=_spec_sha256(spec),
        contract_versions=dict(spec.get("contract_versions", {})),
        engine_profile=dict(spec.get("engine_profile", {})),
        time_model=dict(spec.get("time_model", {})),
        metadata={
            "benchmark": spec.get("benchmark"),
            "initial_capital": spec.get("initial_capital"),
        },
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m quantstudio.strategy_compiler.build_strategy_ir <spec.json> [--out ir.json]"""
    import argparse
    parser = argparse.ArgumentParser(description="Build Strategy IR from Spec")
    parser.add_argument("spec", help="Path to strategy_spec.json")
    parser.add_argument("--out", help="Path to write strategy_ir.json (default: stdout)")
    args = parser.parse_args(argv)

    from pathlib import Path
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    try:
        ir = build_strategy_ir(spec)
    except ContractValidationError as e:
        import sys
        print(f"INVALID: {e}", file=sys.stderr)
        return 1
    payload = ir.to_dict()
    out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(out_text, encoding="utf-8")
        print(f"Wrote IR to {args.out}")
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
