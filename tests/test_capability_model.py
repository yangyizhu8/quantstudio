"""PR0 multidimensional capability-state contract tests."""

import pytest

from quantstudio.strategy_compiler import (
    ContractValidationError,
    load_example,
    validate_capability_report,
)


def _capability(report, name):
    return next(item for item in report["capabilities"]
                if item["capability"] == name)


def test_tick_capability_is_expressible_but_not_ready():
    report = load_example("capability_report.example.json")
    tick = _capability(report, "stock_tick_backtest")
    assert tick["schema_status"] == "SCHEMA_ONLY"
    assert tick["execution_status"] == "PLANNED"
    validate_capability_report(report)


def test_tick_capability_cannot_claim_ready():
    report = load_example("capability_report.example.json")
    tick = _capability(report, "stock_tick_backtest")
    for field in ("schema_status", "data_status", "adapter_status",
                  "provider_status", "engine_status", "platform_status"):
        tick[field] = "READY"
    tick["execution_status"] = "READY"
    with pytest.raises(ContractValidationError):
        validate_capability_report(report)


def test_ready_capability_requires_every_dimension_ready_or_available():
    report = load_example("capability_report.example.json")
    daily = _capability(report, "stock_daily_backtest")
    daily["provider_status"] = "PROVIDER_MISSING"
    with pytest.raises(ContractValidationError, match="cannot be READY"):
        validate_capability_report(report)


def test_required_blocker_forces_overall_blocked():
    report = load_example("capability_report.example.json")
    daily = _capability(report, "stock_daily_backtest")
    daily["data_status"] = "DATA_MISSING"
    daily["execution_status"] = "BLOCKED"
    with pytest.raises(ContractValidationError, match="overall status"):
        validate_capability_report(report)
    report["overall_execution_status"] = "BLOCKED"
    report["blockers"] = ["stock_daily_backtest"]
    validate_capability_report(report)
