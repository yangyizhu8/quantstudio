"""PR0 daily proxy-mode contract tests."""

import pytest

from quantstudio.strategy_compiler import (
    ContractValidationError,
    load_example,
    validate_strategy_spec,
)


def _confirmed_proxy(spec, mode):
    spec["approximations"] = [{
        "mode": mode,
        "original_execution_clock": "09:35:00",
        "approximation": True,
        "approximation_reason": "minute engine is not READY",
        "user_confirmed": True,
        "risk_disclosure": "The fill is a daily proxy, not the exact requested time."
    }]


def test_daily_open_proxy_requires_open_matching():
    spec = load_example("strategy_spec.example.json")
    spec["execution"].update(mode="daily_open_proxy", match_price_mode="close")
    spec["time_model"]["execution_clock"] = "market_open"
    _confirmed_proxy(spec, "daily_open_proxy")
    with pytest.raises(ContractValidationError):
        validate_strategy_spec(spec)


def test_daily_open_proxy_with_open_and_confirmation_is_valid():
    spec = load_example("strategy_spec.example.json")
    spec["execution"].update(mode="daily_open_proxy", match_price_mode="open")
    spec["time_model"]["execution_clock"] = "market_open"
    _confirmed_proxy(spec, "daily_open_proxy")
    validate_strategy_spec(spec)


def test_daily_close_proxy_requires_close_matching():
    spec = load_example("strategy_spec.example.json")
    spec["execution"].update(mode="daily_close_proxy", match_price_mode="open")
    spec["time_model"]["execution_clock"] = "market_close"
    _confirmed_proxy(spec, "daily_close_proxy")
    with pytest.raises(ContractValidationError):
        validate_strategy_spec(spec)


def test_daily_close_proxy_rejects_same_close_signal_and_fill():
    spec = load_example("strategy_spec.example.json")
    spec["execution"].update(mode="daily_close_proxy", match_price_mode="close")
    spec["time_model"].update(
        execution_clock="current_bar", signal_data_cutoff="T-close")
    _confirmed_proxy(spec, "daily_close_proxy")
    with pytest.raises(ContractValidationError):
        validate_strategy_spec(spec)


def test_proxy_requires_explicit_confirmed_approximation_record():
    spec = load_example("strategy_spec.example.json")
    spec["execution"].update(mode="daily_open_proxy", match_price_mode="open")
    spec["time_model"]["execution_clock"] = "market_open"
    with pytest.raises(ContractValidationError):
        validate_strategy_spec(spec)
