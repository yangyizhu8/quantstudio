"""Strategy IR node definitions (PR6a).

Derived from `docs/strategy-compiler/strategy-ir-contract.md` (权威源).
Each IRNode has 9 fields = 2 identifiers (node_id, node_type) + 7 attribute
categories from master plan §7.33 (input/output/parameters/required_capabilities/
timing/platform_mapping/validation_rules). The 2-identifier expansion is documented
in the contract §2.1 as implementation-required, not a deviation.

This module is pure data: it defines the 11 node types as dataclasses and a
StrategyIR container. Building Spec->IR lives in build_strategy_ir.py; rendering
IR->.py lives in render.py; validating IR invariants lives in contracts.py
(validate_strategy_ir) and validators/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Contract reference: strategy-ir-contract.md §2.2 (11 node types, matches
# master-implementation-plan-v1.0.md §7.33 lines 1027-1039).
NODE_TYPES: tuple[str, ...] = (
    "UniverseNode",
    "HardFilterNode",
    "DataLoadNode",
    "IndicatorNode",
    "FactorNode",
    "SignalNode",
    "RankingNode",
    "PortfolioNode",
    "RiskNode",
    "ExecutionNode",
    "DiagnosticNode",
)

# Contract reference: §2.1 field "timing" enum.
TIMINGS: tuple[str, ...] = (
    "pre_open",   # before_trading_start
    "bar",        # handle_data per bar (daily or minute)
    "tick",       # tick event (PR9, never READY in v1)
    "reference",  # reference-data load (status/listing)
    "fundamental",  # fundamental-data load (PIT by announcement_date)
    "post_close",  # after_trading_end
)


@dataclass
class IRNode:
    """Base IR node. All 11 node types share this 9-field shape (contract §2.1).

    The 7 attribute categories from master plan §7.33 (input/output/parameters/
    required_capabilities/timing/platform_mapping/validation_rules) plus 2
    identifier fields (node_id for upstream reference, node_type for dispatch).
    """

    node_id: str
    node_type: str
    input: list[str]
    output: str
    parameters: dict[str, Any]
    required_capabilities: list[str]
    timing: str
    platform_mapping: dict[str, str]
    validation_rules: list[str]

    def __post_init__(self) -> None:
        if self.node_type not in NODE_TYPES:
            raise ValueError(
                f"Unknown node_type {self.node_type!r}; expected one of {NODE_TYPES}"
            )
        if self.timing not in TIMINGS:
            raise ValueError(
                f"Unknown timing {self.timing!r}; expected one of {TIMINGS}"
            )
        # platform_mapping keys must be exactly {"quantstudio", "ptrade-default"}
        expected_keys = {"quantstudio", "ptrade-default"}
        if set(self.platform_mapping) != expected_keys:
            raise ValueError(
                f"platform_mapping keys must be {expected_keys}, got {set(self.platform_mapping)}"
            )


# Per-node-type sub-dataclasses are intentionally thin: they carry only the
# type-discriminator and inherit IRNode's 9 fields. parameters sub-fields are
# validated by schema (strategy_ir.schema.json) + build_strategy_ir, not by
# Python type, because the parameters schema is operation-dependent (contract §2.3).

@dataclass
class UniverseNode(IRNode):
    """Stock pool construction (contract §2.3).

    parameters.kind ∈ {index_constituents, single_stock, etf_list, manual_list}.
    Securities use QMT-suffix output (.SH/.SZ/.BJ) per security-code-rules §1.
    """


@dataclass
class HardFilterNode(IRNode):
    """A-share hard filters (contract §2.3, ashare-filter-contract.md §1).

    parameters.stage ∈ {selection, execution}. stock asset_class requires all 13
    default filters; ETF/CB/futures use their own Profile (HARDFILTER-ASSET-MATCH).
    """


@dataclass
class DataLoadNode(IRNode):
    """Market/fundamental/reference data load (contract §2.3).

    parameters.pit_anchor ∈ {previous_date, announcement_date} when pit_required.
    Non-bar datasets (stock_status/valuation) record frequency="1d" as
    "per-trading-day query", NOT "daily bar" (contract §2.3 note).
    """


@dataclass
class IndicatorNode(IRNode):
    """Time-series indicator (contract §2.3). Pure single-instrument纵向.

    parameters.operation ∈ {ma, ema, std, pct_change, max, min, sum, ref, ...}.
    PR6a supports `ma` only; others raise in build_strategy_ir (PR6b).
    """


@dataclass
class FactorNode(IRNode):
    """Cross-sectional factor (contract §2.3). PR6a placeholder, PR6b impl."""


@dataclass
class SignalNode(IRNode):
    """Boolean/directional signal (contract §2.3).

    parameters.operation ∈ {cross, threshold, compare, and, or, not}.
    PR6a supports `cross` only; others raise (PR6b).
    """


@dataclass
class RankingNode(IRNode):
    """Score/rank universe (contract §2.3). PR6a placeholder, PR6b impl."""


@dataclass
class PortfolioNode(IRNode):
    """Position sizing + rebalancing (contract §2.3).

    parameters.kind ∈ {single_position, equal_weight_top_n, signal_weighted, ...}.
    PORTFOLIO-POSITIONS-EXACT-MATCH aligns strategy_fidelity_gates.json semantics.
    """


@dataclass
class RiskNode(IRNode):
    """Risk checks (contract §2.3). stop_loss/take_profit fields in PR6b."""


@dataclass
class ExecutionNode(IRNode):
    """Order placement (contract §2.3).

    parameters.order_api ∈ {order_target_value, order_value, order_target, order}.
    match_price_mode is透传 Spec.execution.match_price_mode (EXEC-MATCH-PRICE-CONSISTENT).
    """


@dataclass
class DiagnosticNode(IRNode):
    """Logging/metrics/run-card evidence (contract §2.3). Must be nodes[-1]."""


# Registry: node_type -> dataclass, for build_strategy_ir dispatch.
NODE_TYPE_REGISTRY: dict[str, type[IRNode]] = {
    "UniverseNode": UniverseNode,
    "HardFilterNode": HardFilterNode,
    "DataLoadNode": DataLoadNode,
    "IndicatorNode": IndicatorNode,
    "FactorNode": FactorNode,
    "SignalNode": SignalNode,
    "RankingNode": RankingNode,
    "PortfolioNode": PortfolioNode,
    "RiskNode": RiskNode,
    "ExecutionNode": ExecutionNode,
    "DiagnosticNode": DiagnosticNode,
}


@dataclass
class StrategyIR:
    """Strategy IR container (contract §1).

    nodes is an ordered list; order = execution order (contract §4 pipeline).
    Top-level fields except nodes are transparently passed through from Spec
    (architecture.md invariant 1: no independent business-parameter source).
    """

    strategy_id: str
    nodes: list[IRNode]
    source_spec_sha256: str | None = None
    contract_versions: dict[str, str] = field(default_factory=dict)
    engine_profile: dict[str, Any] = field(default_factory=dict)
    time_model: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the strategy_ir.schema.json-conformant dict."""
        return {
            "strategy_ir_version": "1.0",
            "strategy_id": self.strategy_id,
            "source_spec_sha256": self.source_spec_sha256,
            "contract_versions": self.contract_versions,
            "engine_profile": self.engine_profile,
            "time_model": self.time_model,
            "nodes": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "input": n.input,
                    "output": n.output,
                    "parameters": n.parameters,
                    "required_capabilities": n.required_capabilities,
                    "timing": n.timing,
                    "platform_mapping": n.platform_mapping,
                    "validation_rules": n.validation_rules,
                }
                for n in self.nodes
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StrategyIR":
        """Deserialize from a strategy_ir.schema.json-conformant dict."""
        nodes = []
        for n in payload.get("nodes", []):
            node_cls = NODE_TYPE_REGISTRY.get(n["node_type"])
            if node_cls is None:
                raise ValueError(f"Unknown node_type in payload: {n['node_type']!r}")
            nodes.append(
                node_cls(
                    node_id=n["node_id"],
                    node_type=n["node_type"],
                    input=n["input"],
                    output=n["output"],
                    parameters=n["parameters"],
                    required_capabilities=n["required_capabilities"],
                    timing=n["timing"],
                    platform_mapping=n["platform_mapping"],
                    validation_rules=n["validation_rules"],
                )
            )
        return cls(
            strategy_id=payload["strategy_id"],
            nodes=nodes,
            source_spec_sha256=payload.get("source_spec_sha256"),
            contract_versions=payload.get("contract_versions", {}),
            engine_profile=payload.get("engine_profile", {}),
            time_model=payload.get("time_model", {}),
        )
