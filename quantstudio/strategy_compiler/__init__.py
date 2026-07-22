"""QuantStudio Strategy Compiler contracts and implementation (PR0 + PR6a).

PR0: contract validators (strategy_spec / capability_report / run_card).
PR6a: IR builder, dual renderer, IR validator, 2 validators.
PR6b: 5 more validators, install_skill, templates, 9 more golden cases.
"""

from .contracts import (
    ContractValidationError,
    load_example,
    load_schema,
    validate_capability_report,
    validate_run_card,
    validate_strategy_ir,
    validate_strategy_spec,
)
from .ir_nodes import IRNode, StrategyIR
from .build_strategy_ir import build_strategy_ir
from .render import render_ptrade, render_quantstudio, render_strategy

__all__ = [
    # PR0 contracts
    "ContractValidationError",
    "load_example",
    "load_schema",
    "validate_capability_report",
    "validate_run_card",
    "validate_strategy_spec",
    # PR6a IR + render
    "validate_strategy_ir",
    "IRNode",
    "StrategyIR",
    "build_strategy_ir",
    "render_strategy",
    "render_quantstudio",
    "render_ptrade",
]
