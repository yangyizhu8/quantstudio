"""PR0 Strategy Spec and artifact schema contract tests."""

from copy import deepcopy

import pytest

from quantstudio.strategy_compiler import (
    ContractValidationError,
    load_example,
    validate_capability_report,
    validate_run_card,
    validate_strategy_spec,
)


def test_all_frozen_schema_examples_validate():
    validate_strategy_spec(load_example("strategy_spec.example.json"))
    validate_capability_report(load_example("capability_report.example.json"))
    validate_run_card(load_example("run_card.example.json"))


def test_strategy_spec_rejects_unknown_top_level_fields():
    spec = load_example("strategy_spec.example.json")
    spec["frequency"] = "1d"
    with pytest.raises(ContractValidationError):
        validate_strategy_spec(spec)


def test_strategy_spec_requires_all_contract_versions():
    spec = load_example("strategy_spec.example.json")
    del spec["contract_versions"]["engine_semantics_version"]
    with pytest.raises(ContractValidationError):
        validate_strategy_spec(spec)


def test_stock_hard_filters_cannot_disable_realism_guards():
    spec = load_example("strategy_spec.example.json")
    spec["hard_filters"]["enforce_t1"] = False
    with pytest.raises(ContractValidationError):
        validate_strategy_spec(spec)


def test_examples_are_returned_as_independent_objects():
    one = load_example("strategy_spec.example.json")
    two = load_example("strategy_spec.example.json")
    one["strategy_name"] = "changed"
    assert two["strategy_name"] == "示例动量策略"
