"""PR0 Time Model and engine-profile cross-field tests."""

import pytest

from quantstudio.strategy_compiler import (
    ContractValidationError,
    load_example,
    validate_strategy_spec,
)


def test_time_model_keeps_distinct_frequency_fields():
    spec = load_example("strategy_spec.example.json")
    fields = spec["time_model"]
    assert {"market_data_frequency", "factor_frequency", "signal_frequency",
            "portfolio_valuation_frequency"}.issubset(fields)
    assert "frequency" not in fields


def test_factor_frequency_cannot_be_finer_than_market_data():
    spec = load_example("strategy_spec.example.json")
    spec["time_model"]["factor_frequency"] = "1m"
    with pytest.raises(ContractValidationError, match="factor_frequency"):
        validate_strategy_spec(spec)


def test_bar_engine_frequency_must_match_market_data_frequency():
    spec = load_example("strategy_spec.example.json")
    spec["engine_profile"]["bar_frequency"] = "1m"
    with pytest.raises(ContractValidationError, match="market_data_frequency"):
        validate_strategy_spec(spec)


def test_next_open_matching_requires_next_open_execution_clock():
    spec = load_example("strategy_spec.example.json")
    spec["time_model"]["execution_clock"] = "current_bar"
    with pytest.raises(ContractValidationError, match="next_open"):
        validate_strategy_spec(spec)


def test_invalid_decision_clock_is_rejected():
    spec = load_example("strategy_spec.example.json")
    spec["time_model"]["decision_clock"] = "25:61"
    with pytest.raises(ContractValidationError):
        validate_strategy_spec(spec)
