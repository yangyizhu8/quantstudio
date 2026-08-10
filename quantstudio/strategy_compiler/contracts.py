"""PR0 contract validators for QuantStudio Strategy Compiler."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import fastjsonschema

SCHEMA_DIR = Path(__file__).with_name("schemas")
EXAMPLE_DIR = Path(__file__).with_name("examples")


class ContractValidationError(ValueError):
    """Raised when an artifact violates a frozen compiler contract."""


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Unknown Strategy Compiler schema: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=None)
def _compiled_schema(name: str):
    return fastjsonschema.compile(load_schema(name))


def load_example(name: str) -> dict[str, Any]:
    path = EXAMPLE_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Unknown Strategy Compiler example: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _validate_schema(schema_name: str, payload: Mapping[str, Any]) -> None:
    try:
        _compiled_schema(schema_name)(dict(payload))
    except fastjsonschema.JsonSchemaException as exc:
        raise ContractValidationError(str(exc)) from exc


def validate_strategy_spec(payload: Mapping[str, Any]) -> None:
    """Validate Strategy Spec v1 plus cross-field timing semantics."""
    _validate_schema("strategy_spec.schema.json", payload)
    time_model = payload["time_model"]
    engine = payload["engine_profile"]
    execution = payload["execution"]

    if engine["event_type"] == "bar":
        if time_model["market_data_frequency"] != engine["bar_frequency"]:
            raise ContractValidationError(
                "market_data_frequency must equal bar_frequency for bar profiles"
            )
    elif time_model["market_data_frequency"] != "tick":
        raise ContractValidationError(
            "tick profiles require market_data_frequency='tick'"
        )

    ranks = {"tick": 0, "1m": 1, "5m": 2, "15m": 3,
             "30m": 4, "60m": 5, "1d": 6}
    market_rank = ranks[time_model["market_data_frequency"]]
    for field in ("factor_frequency", "signal_frequency",
                  "portfolio_valuation_frequency"):
        if ranks[time_model[field]] < market_rank:
            raise ContractValidationError(
                f"{field} cannot be finer than market_data_frequency"
            )

    if (execution["match_price_mode"] == "next_open"
            and time_model["execution_clock"] != "next_open"):
        raise ContractValidationError(
            "next_open matching requires execution_clock='next_open'"
        )

    mode = execution["mode"]
    if mode != "native" and not any(
        item["mode"] == mode for item in payload["approximations"]
    ):
        raise ContractValidationError(
            f"{mode} requires a confirmed approximation record"
        )

    scheduled_fields = (
        "entry_clock", "exit_clock", "exit_day_offset", "overlap_policy",
        "max_concurrent_positions", "new_buy_cash_policy",
    )
    present = [field for field in scheduled_fields if field in time_model]
    if present and len(present) != len(scheduled_fields):
        missing = [field for field in scheduled_fields if field not in time_model]
        raise ContractValidationError(
            f"scheduled entry/exit time model is incomplete; missing {missing}"
        )
    if present:
        if engine.get("bar_frequency") not in ("1m", "5m", "15m", "30m", "60m"):
            raise ContractValidationError(
                "scheduled intraday entry/exit requires a minute-bar engine profile"
            )
        if time_model["exit_day_offset"] < 1:
            raise ContractValidationError(
                "overnight timed exit requires exit_day_offset >= 1"
            )
        if time_model["overlap_policy"] == "allow_previous_day_overlap":
            portfolio_max = payload.get("portfolio", {}).get("parameters", {}).get("max_positions", 1)
            if time_model["max_concurrent_positions"] < portfolio_max * 2:
                raise ContractValidationError(
                    "overlap policy requires max_concurrent_positions >= 2 * portfolio.max_positions"
                )


def validate_source_import_report(payload: Mapping[str, Any]) -> None:
    """Validate Source Import Report v1 (fail-closed on BLOCK actions/errors).

    T4: source entry 的转换报告契约（schemas/source_import_report.schema.json）。
    """
    _validate_schema("source_import_report.schema.json", payload)
    if payload.get("errors") or any(
        a["severity"] == "BLOCK" for a in payload.get("actions", [])
    ):
        raise ContractValidationError(
            "source import report contains BLOCK-level actions/errors; "
            "conversion must not be treated as PASS"
        )


def validate_capability_report(payload: Mapping[str, Any]) -> None:
    """Validate capability dimensions and forbid false READY claims."""
    _validate_schema("capability_report.schema.json", payload)
    ready_values = {"AVAILABLE", "READY"}
    required_blockers: list[str] = []
    dimensions = ("schema_status", "data_status", "adapter_status",
                  "provider_status", "engine_status", "platform_status")
    for item in payload["capabilities"]:
        if item["execution_status"] == "READY":
            bad = [name for name in dimensions if item[name] not in ready_values]
            if bad:
                raise ContractValidationError(
                    f"{item['capability']} cannot be READY; non-ready: {bad}"
                )
        if item["required"] and item["execution_status"] != "READY":
            required_blockers.append(item["capability"])

    overall = payload["overall_execution_status"]
    if required_blockers and overall == "READY":
        raise ContractValidationError(
            "overall status cannot be READY with required blockers: "
            + ", ".join(required_blockers)
        )
    if not required_blockers and overall != "READY":
        raise ContractValidationError(
            "overall status must be READY when all required capabilities are READY"
        )


def validate_run_card(payload: Mapping[str, Any]) -> None:
    """Validate Run Card v1."""
    _validate_schema("run_card.schema.json", payload)


# ---------------------------------------------------------------------------
# Strategy IR validation (PR6a).
#
# Strategy IR has TWO validation layers (contract §4.1):
#   1. Schema shape (strategy_ir.schema.json) — field types/enums/regex.
#   2. Cross-node semantics (12 invariants IR-ORDER/DEP/CAP/TIMING/EXEC) —
#      pipeline order, dependency acyclicity, capability ID validity, timing
#      consistency, execution match-price passthrough.
# Both layers are独立 implemented here; schema alone cannot catch §4.1 rules.
# ---------------------------------------------------------------------------

# Contract §4 pipeline allowed positions for each node_type (for IR-ORDER checks).
# Each value = set of node_types that MUST appear before this type.
_IR_PIPELINE_PREDECESSORS: dict[str, set[str]] = {
    "UniverseNode": set(),
    "HardFilterNode": {"UniverseNode"},
    "DataLoadNode": {"UniverseNode"},
    "IndicatorNode": {"DataLoadNode"},
    "FactorNode": {"DataLoadNode"},
    "SignalNode": {"IndicatorNode", "FactorNode"},
    # RankingNode + PortfolioNode have "either-of" predecessors handled by
    # _EITHER_PREDECESSORS below (Ranking <- Indicator|Factor|Signal;
    # Portfolio <- Ranking|Signal). Their hard-required set here is empty.
    "RankingNode": set(),
    "PortfolioNode": set(),
    "RiskNode": {"PortfolioNode"},
    "ExecutionNode": {"PortfolioNode", "RiskNode"},
    "DiagnosticNode": {"ExecutionNode"},
}


def validate_strategy_ir(payload: Mapping[str, Any]) -> None:
    """Validate Strategy IR v1: schema shape + 12 cross-node invariants (§4.1).

    Raises ContractValidationError on any violation. The 12 invariants
    (IR-ORDER-1..6, IR-DEP-1/2, IR-CAP-1, IR-TIMING-1, IR-EXEC-1/2) are
    independent of schema — schema validates field shape, this function
    validates pipeline semantics.
    """
    _validate_schema("strategy_ir.schema.json", payload)
    nodes = list(payload.get("nodes", []))
    if not nodes:
        raise ContractValidationError("IR nodes array is empty")

    node_ids = [n["node_id"] for n in nodes]
    node_types = [n["node_type"] for n in nodes]
    node_by_id = {n["node_id"]: n for n in nodes}

    # --- IR-ORDER-1: UniverseNode is nodes[0] ---
    if node_types[0] != "UniverseNode":
        raise ContractValidationError(
            f"IR-ORDER-1: first node must be UniverseNode, got {node_types[0]!r}"
        )
    # --- IR-ORDER-2: DiagnosticNode is nodes[-1] ---
    if node_types[-1] != "DiagnosticNode":
        raise ContractValidationError(
            f"IR-ORDER-2: last node must be DiagnosticNode, got {node_types[-1]!r}"
        )
    # --- IR-ORDER-3: exactly 1 UniverseNode and 1 DiagnosticNode ---
    if node_types.count("UniverseNode") != 1:
        raise ContractValidationError(
            f"IR-ORDER-3: exactly 1 UniverseNode required, got {node_types.count('UniverseNode')}"
        )
    if node_types.count("DiagnosticNode") != 1:
        raise ContractValidationError(
            f"IR-ORDER-3: exactly 1 DiagnosticNode required, got {node_types.count('DiagnosticNode')}"
        )

    # --- IR-ORDER-4/5/6: pipeline predecessor ordering ---
    # For each node, every required predecessor type must appear earlier.
    # Some types accept "either of" predecessors (set union), not "all of":
    #   SignalNode   <- IndicatorNode OR FactorNode
    #   RankingNode  <- IndicatorNode OR FactorNode OR SignalNode (rank can act on raw indicator OR post-signal)
    #   PortfolioNode <- RankingNode OR SignalNode
    _EITHER_PREDECESSORS: dict[str, set[str]] = {
        "SignalNode": {"IndicatorNode", "FactorNode"},
        "RankingNode": {"IndicatorNode", "FactorNode", "SignalNode"},
        "PortfolioNode": {"RankingNode", "SignalNode"},
    }
    seen_types: set[str] = set()
    for idx, n in enumerate(nodes):
        nt = n["node_type"]
        required_before = _IR_PIPELINE_PREDECESSORS.get(nt, set())
        if nt in _EITHER_PREDECESSORS:
            # "either of" semantics: at least one of the union members must be seen
            either_set = _EITHER_PREDECESSORS[nt]
            if not (either_set & seen_types):
                raise ContractValidationError(
                    f"IR-ORDER-4/5/6: {nt} (node_id={n['node_id']!r}) at index {idx} "
                    f"requires at least one of {either_set} before it; seen: {seen_types}"
                )
            # Remove the either-set from required_before since we've handled it
            required_before = required_before - either_set
        missing = required_before - seen_types
        if missing:
            raise ContractValidationError(
                f"IR-ORDER-4/5/6: {nt} (node_id={n['node_id']!r}) at index {idx} "
                f"requires predecessor types {missing} before it; seen so far: {seen_types}"
            )
        seen_types.add(nt)

    # --- IR-DEP-1: every input ref exists as a前序 node's output (contract §2.1:
    # input references upstream output names, not node_ids) ---
    for idx, n in enumerate(nodes):
        for ref in n.get("input", []):
            ref_idx = next((i for i, x in enumerate(nodes[:idx]) if x["output"] == ref), None)
            if ref_idx is None:
                raise ContractValidationError(
                    f"IR-DEP-1: node {n['node_id']!r} at index {idx} references unknown "
                    f"or后置 input {ref!r} (input must reference an前序 node's output)"
                )

    # --- IR-DEP-2: no cycles. Since IR-DEP-1 enforces "inputs reference前序
    # outputs only", cycles are impossible by construction. We still assert
    # node_id uniqueness AND output uniqueness as cycle-prevention preconditions.
    if len(set(node_ids)) != len(node_ids):
        dupes = [nid for nid in node_ids if node_ids.count(nid) > 1]
        raise ContractValidationError(
            f"IR-DEP-2: duplicate node_id(s) break DAG invariant: {sorted(set(dupes))}"
        )
    outputs = [n["output"] for n in nodes]
    if len(set(outputs)) != len(outputs):
        dupes = [o for o in outputs if outputs.count(o) > 1]
        raise ContractValidationError(
            f"IR-DEP-2: duplicate output name(s) break DAG invariant: {sorted(set(dupes))}"
        )

    # --- IR-CAP-1: required_capabilities reference valid capability IDs ---
    # (loose check: non-empty strings; full capability-model alignment in PR6b
    # when capability registry is built out)
    for n in nodes:
        for cap in n.get("required_capabilities", []):
            if not isinstance(cap, str) or not cap.strip():
                raise ContractValidationError(
                    f"IR-CAP-1: node {n['node_id']!r} has invalid capability ref {cap!r}"
                )

    # --- IR-TIMING-1: DataLoadNode timing/pit_anchor consistency ---
    for n in nodes:
        if n["node_type"] == "DataLoadNode":
            params = n.get("parameters", {})
            if params.get("pit_required"):
                anchor = params.get("pit_anchor")
                if anchor not in ("previous_date", "announcement_date"):
                    raise ContractValidationError(
                        f"IR-TIMING-1: DataLoadNode {n['node_id']!r} pit_required=true "
                        f"requires pit_anchor in (previous_date, announcement_date), got {anchor!r}"
                    )
                if anchor == "announcement_date" and n["timing"] != "fundamental":
                    raise ContractValidationError(
                        f"IR-TIMING-1: DataLoadNode {n['node_id']!r} pit_anchor=announcement_date "
                        f"requires timing=fundamental, got {n['timing']!r}"
                    )
                if anchor == "previous_date" and n["timing"] not in ("pre_open", "reference", "bar"):
                    raise ContractValidationError(
                        f"IR-TIMING-1: DataLoadNode {n['node_id']!r} pit_anchor=previous_date "
                        f"requires timing in (pre_open, reference, bar), got {n['timing']!r}"
                    )

    # --- IR-EXEC-1: ExecutionNode.match_price_mode == Spec.execution.match_price_mode ---
    # (passthrough consistency; Spec value lives in payload.time_model.execution_clock
    # and we cross-check via execution_clock vs match_price_mode contract)
    execution_nodes = [n for n in nodes if n["node_type"] == "ExecutionNode"]
    if execution_nodes:
        exec_node = execution_nodes[0]
        exec_match = exec_node["parameters"].get("match_price_mode")
        # next_open match_price requires execution_clock=next_open (contracts.py
        # validate_strategy_spec already enforces this on the Spec side; here we
        # verify the IR didn't drift)
        time_model = payload.get("time_model", {})
        exec_clock = time_model.get("execution_clock")
        if exec_match == "next_open" and exec_clock != "next_open":
            raise ContractValidationError(
                f"IR-EXEC-1: ExecutionNode match_price_mode=next_open but time_model "
                f"execution_clock={exec_clock!r}; must be next_open"
            )

    # --- IR-EXEC-2: stock asset_class requires execution-stage HardFilterNode ---
    # This is the only conditional invariant (contract §4.1 IR-EXEC-2).
    # We infer asset_class from the selection-stage HardFilterNode's
    # validation_rules (HARDFILTER-STOCK-13 implies stock) since IR does not
    # carry asset_class directly.
    has_stock_filter = any(
        n["node_type"] == "HardFilterNode"
        and n["parameters"].get("stage") == "selection"
        and "HARDFILTER-STOCK-13" in n.get("validation_rules", [])
        for n in nodes
    )
    if has_stock_filter:
        exec_stage_hf = [
            n for n in nodes
            if n["node_type"] == "HardFilterNode"
            and n["parameters"].get("stage") == "execution"
        ]
        if not exec_stage_hf:
            raise ContractValidationError(
                "IR-EXEC-2: stock asset_class (HARDFILTER-STOCK-13 present) requires "
                "an execution-stage HardFilterNode to re-check order-time filters; "
                "none found (ashare-filter-contract.md §2)"
            )

