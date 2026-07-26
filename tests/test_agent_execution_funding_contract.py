"""Regression tests for the execution funding matrix cross-checks.

Covers references/execution-funding-matrix.md enforcement in
agent_skill_common.execution_funding_errors (Skill 0.6.0).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_skill_common import execution_funding_errors


def funding_contract(**overrides) -> dict:
    contract = {
        "requires_same_cycle_sell_proceeds": True,
        "implementation_mode": "sell_then_buy_immediate",
        "cash_only_for_new_buys": False,
    }
    contract.update(overrides)
    return contract


def design(match_mode: str, lifecycle: str, contract: dict,
           rebalance_mode: str | None = None) -> dict:
    engine_profile = {"profile_id": "daily-bar-v1", "bar_frequency": "1d",
                      "match_price_mode": match_mode}
    if rebalance_mode is not None:
        engine_profile["rebalance_mode"] = rebalance_mode
        if rebalance_mode == "callback_basket":
            engine_profile["expected_engine_semantics_version"] = "0.4.0-next_open_basket"
    return {
        "engine_profile": engine_profile,
        "timing": {"decision_events": [{"name": "rebalance", "lifecycle": lifecycle}]},
        "rebalance_funding_contract": contract,
    }


def rule_ids(match_mode: str, lifecycle: str, contract: dict,
             rebalance_mode: str | None = None) -> set[str]:
    return {item["rule_id"] for item in execution_funding_errors(
        design(match_mode, lifecycle, contract, rebalance_mode))}


def test_close_run_daily_sell_then_buy_passes():
    assert execution_funding_errors(design("close", "run_daily", funding_contract())) == []


def test_open_run_daily_sell_then_buy_passes():
    assert execution_funding_errors(design("open", "run_daily", funding_contract())) == []


def test_next_open_run_daily_cannot_use_same_cycle_proceeds():
    rules = rule_ids("next_open", "run_daily", funding_contract())
    assert "EXECUTION-SELL-PROCEEDS-UNAVAILABLE" in rules


def test_next_open_before_trading_start_cannot_use_same_cycle_proceeds():
    rules = rule_ids("next_open", "before_trading_start", funding_contract())
    assert "EXECUTION-SELL-PROCEEDS-UNAVAILABLE" in rules


def test_next_open_without_proceeds_dependency_is_allowed():
    contract = funding_contract(requires_same_cycle_sell_proceeds=False)
    assert execution_funding_errors(design("next_open", "run_daily", contract)) == []


def test_next_open_handle_data_basket_atomic_passes():
    contract = funding_contract(implementation_mode="basket_atomic_sell_first")
    assert execution_funding_errors(
        design("next_open", "handle_data", contract, rebalance_mode="callback_basket")) == []


def test_basket_atomic_requires_callback_basket_rebalance_mode():
    # next_open + handle_data alone is NOT proof of basket activation: the real
    # engine also requires rebalance_mode='callback_basket'.
    contract = funding_contract(implementation_mode="basket_atomic_sell_first")
    assert "EXECUTION-BASKET-REQUIRED" in rule_ids("next_open", "handle_data", contract)
    assert "EXECUTION-BASKET-REQUIRED" in rule_ids(
        "next_open", "handle_data", contract, rebalance_mode="legacy")


def test_basket_atomic_requires_next_open_handle_data():
    contract = funding_contract(implementation_mode="basket_atomic_sell_first")
    assert "EXECUTION-BASKET-REQUIRED" in rule_ids(
        "next_open", "run_daily", contract, rebalance_mode="callback_basket")
    assert "EXECUTION-BASKET-REQUIRED" in rule_ids(
        "close", "handle_data", contract, rebalance_mode="callback_basket")


def test_next_open_staged_two_phase_passes():
    contract = funding_contract(implementation_mode="staged_two_phase")
    assert execution_funding_errors(design("next_open", "run_daily", contract)) == []


def test_unknown_match_mode_is_blocked():
    rules = rule_ids("vwap", "run_daily", funding_contract())
    assert "EXECUTION-FUNDING-INCOMPATIBLE" in rules


def test_unknown_implementation_mode_is_blocked():
    rules = rule_ids("close", "run_daily", funding_contract(implementation_mode="yolo"))
    assert "EXECUTION-FUNDING-INCOMPATIBLE" in rules


def test_cash_only_contradicts_proceeds_dependency():
    contract = funding_contract(cash_only_for_new_buys=True)
    rules = rule_ids("close", "run_daily", contract)
    assert "EXECUTION-FUNDING-INCOMPATIBLE" in rules


def test_close_run_daily_is_not_routed_to_pending_semantics():
    # close + run_daily executes immediately: depending on same-cycle proceeds
    # is valid and must NOT be flagged as pending.
    rules = rule_ids("close", "run_daily", funding_contract())
    assert "EXECUTION-SELL-PROCEEDS-UNAVAILABLE" not in rules


def test_basket_atomic_requires_expected_engine_semantics_version():
    # next_open + handle_data + callback_basket WITHOUT the semantics version
    # must not pass: R5 would otherwise have nothing to prove activation with.
    contract = funding_contract(implementation_mode="basket_atomic_sell_first")
    missing = design("next_open", "handle_data", contract)
    missing["engine_profile"]["rebalance_mode"] = "callback_basket"
    rules = {item["rule_id"] for item in execution_funding_errors(missing)}
    assert "EXECUTION-BASKET-REQUIRED" in rules


def test_basket_atomic_rejects_wrong_engine_semantics_version():
    contract = funding_contract(implementation_mode="basket_atomic_sell_first")
    wrong = design("next_open", "handle_data", contract, rebalance_mode="callback_basket")
    wrong["engine_profile"]["expected_engine_semantics_version"] = "0.2.0-next_open_pending"
    rules = {item["rule_id"] for item in execution_funding_errors(wrong)}
    assert "EXECUTION-BASKET-REQUIRED" in rules
