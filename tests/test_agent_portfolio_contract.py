"""Regression tests for the machine-checkable portfolio/capital contract.

Covers design 2.2 portfolio_contract cross-validation and the code-level
sizing checks in validate_agent_strategy (Skill 0.6.0).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_skill_common import (
    confirmation_evidence_errors, portfolio_contract_errors, validate_design,
)
from validate_agent_strategy import validate_strategy


def portfolio_contract(**overrides) -> dict:
    contract = {
        "sizing_mode": "runtime_total_value",
        "required_initial_cash": None,
        "allocation_mode": "equal_weight",
        "allocation_denominator": "configured_target_count",
        "target_holdings": 20,
        "gross_exposure_target": 0.85,
        "cash_buffer_ratio": 0.15,
        "per_position_target_weight": 0.0425,
        "max_single_weight": 0.05,
        "allow_leverage": False,
    }
    contract.update(overrides)
    return contract


def design(contract: dict | None) -> dict:
    result = {
        "design_version": "2.0",
        "strategy_id": "portfolio_contract_test",
        "strategy_name": "组合契约测试策略",
        "asset_class": "stock",
        "targets": ["quantstudio", "ptrade"],
        "engine_profile": {"profile_id": "minute-bar-v1", "bar_frequency": "1m", "match_price_mode": "close"},
        "market_data_contract": {
            "signal_price_adjustment": "pre",
            "execution_price_basis": "pre_adjusted_price",
        },
        "strategy_semantics": {"universe": "manual", "entry_rules": [], "exit_rules": [], "portfolio_rules": [], "risk_rules": []},
        "timing": {
            "signal_data_cutoff": "current completed 09:31 minute bar",
            "holding_semantics": "test",
            "decision_events": [{"name": "rebalance", "lifecycle": "run_daily", "time": "09:31"}],
        },
        "components": {"lifecycle_hooks": ["initialize", "handle_data"], "api_groups": [], "required_apis": ["run_daily"]},
        "constraints": {"hard_filters": [], "no_lookahead": True, "portable_source_required": True, "runtime_state_guard_required": True},
        "approximations": [],
        "open_questions": [],
        "user_confirmations": {"strategy_semantics": True, "execution_approximations": True, "component_plan": True},
        "output": {"overwrite": False},
    }
    if contract is not None:
        result["portfolio_contract"] = contract
    return result


def rule_ids(design_dict: dict) -> set[str]:
    return {item["rule_id"] for item in portfolio_contract_errors(design_dict)}


def test_full_deployment_plus_cash_buffer_is_contradiction():
    # 20 x 5% = 100% invested while claiming a 15% cash buffer: the exact
    # failure mode from the incident design.
    rules = rule_ids(design(portfolio_contract(per_position_target_weight=0.05)))
    assert "PORTFOLIO-CASH-BUFFER-CONTRADICTION" in rules
    assert "PORTFOLIO-WEIGHT-INCONSISTENT" in rules


def test_consistent_equal_weight_contract_passes():
    assert portfolio_contract_errors(design(portfolio_contract())) == []


def test_exposure_plus_buffer_above_one_is_blocked():
    rules = rule_ids(design(portfolio_contract(gross_exposure_target=0.95, cash_buffer_ratio=0.15)))
    assert "PORTFOLIO-EXPOSURE-CONTRADICTION" in rules


def test_fixed_notional_requires_cash_and_target():
    rules = rule_ids(design(portfolio_contract(
        sizing_mode="fixed_notional", fixed_target_value=None)))
    assert "PORTFOLIO-FIXED-CAPITAL-MISSING" in rules


def test_runtime_mode_forbids_fixed_amounts():
    rules = rule_ids(design(portfolio_contract(fixed_target_value=50000)))
    assert "PORTFOLIO-SIZING-MODE-MISMATCH" in rules
    rules = rule_ids(design(portfolio_contract(required_initial_cash=1000000)))
    assert "PORTFOLIO-SIZING-MODE-MISMATCH" in rules


# --- code-level sizing checks ---

RUNTIME_HELPER = (
    "def _portfolio_total_value(context):\n"
    "    return context.portfolio.total_value\n\n"
)


def source_with_rebalance(body: str, prefix: str = "") -> str:
    parts = []
    if prefix:
        parts.append(prefix)
    parts.extend([
        "def _ensure_runtime_state():",
        "    if not hasattr(g, 'ready'):",
        "        g.ready = True",
        "",
        "def initialize(context):",
        "    _ensure_runtime_state()",
        "    run_daily(context, rebalance, time='09:31')",
        "",
        "def handle_data(context, data):",
        "    _ensure_runtime_state()",
        "",
        "def rebalance(context):",
        "    _ensure_runtime_state()",
        body.rstrip("\n"),
        "",
    ])
    return "\n".join(parts)


def block_rules(source: str, contract: dict) -> set[str]:
    report = validate_strategy(design(contract), source, target_profile="quantstudio")
    return {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}


def test_runtime_sizing_blocks_hardcoded_g_capital():
    source = source_with_rebalance(
        "    g.capital = 1_000_000\n"
        "    g.per_target = g.capital / 20\n"
        "    order_target_value('600000.SS', g.per_target)",
        prefix=RUNTIME_HELPER)
    assert "PORTFOLIO-HARDCODED-CAPITAL" in block_rules(source, portfolio_contract())


def test_runtime_sizing_blocks_fixed_positive_order_value():
    source = source_with_rebalance(
        "    order_target_value('600000.SS', 50_000)",
        prefix=RUNTIME_HELPER)
    assert "PORTFOLIO-HARDCODED-CAPITAL" in block_rules(source, portfolio_contract())


def test_runtime_sizing_allows_liquidation_to_zero():
    source = source_with_rebalance(
        "    total = _portfolio_total_value(context)\n"
        "    per_target = total * 0.85 / 20\n"
        "    order_target_value('600000.SS', per_target)\n"
        "    order_target_value('000001.SZ', 0)",
        prefix=RUNTIME_HELPER)
    report = validate_strategy(
        design(portfolio_contract()), source, target_profile="quantstudio")
    assert report["status"] == "PASS", report


def test_runtime_sizing_requires_runtime_value_source():
    source = source_with_rebalance(
        "    order_target_value('600000.SS', g.per_target)")
    assert "PORTFOLIO-RUNTIME-VALUE-MISSING" in block_rules(source, portfolio_contract())


def test_fixed_notional_matching_amount_passes():
    contract = portfolio_contract(
        sizing_mode="fixed_notional",
        required_initial_cash=1000000,
        fixed_target_value=50000)
    source = source_with_rebalance(
        "    order_target_value('600000.SS', 50000)\n"
        "    order_target_value('000001.SZ', 0)")
    report = validate_strategy(design(contract), source, target_profile="quantstudio")
    assert report["status"] == "PASS", report


def test_fixed_notional_mismatched_amount_is_blocked():
    contract = portfolio_contract(
        sizing_mode="fixed_notional",
        required_initial_cash=1000000,
        fixed_target_value=50000)
    source = source_with_rebalance("    order_target_value('600000.SS', 25000)")
    assert "PORTFOLIO-FIXED-NOTIONAL-MISMATCH" in block_rules(source, contract)


def test_fixed_notional_g_capital_must_match_required_cash():
    contract = portfolio_contract(
        sizing_mode="fixed_notional",
        required_initial_cash=1000000,
        fixed_target_value=50000)
    source = source_with_rebalance(
        "    g.capital = 100000\n"
        "    order_target_value('600000.SS', 50000)")
    assert "PORTFOLIO-FIXED-NOTIONAL-MISMATCH" in block_rules(source, contract)


# --- design 2.2 schema + verbatim confirmation evidence ---

def _design_22() -> dict:
    base = design(portfolio_contract())
    base["design_version"] = "2.2"
    base["universe_contract"] = {"mode": "portable_public_api", "local_dynamic_api_allowed": False}
    base["validation_execution"] = {
        "mode": "agent_managed",
        "require_hash_bound_evidence": True,
        "formal_publish_requires_backtest_pass": True,
    }
    base["backtest_window_contract"] = {
        "actual_window_selected_by": "customer_confirmed_agent_run",
        "strategy_must_not_hardcode_backtest_dates": True,
        "agent_must_not_start_unconfirmed_backtest": True,
    }
    base["rebalance_funding_contract"] = {
        "requires_same_cycle_sell_proceeds": True,
        "implementation_mode": "sell_then_buy_immediate",
        "cash_only_for_new_buys": False,
    }
    base["history_coverage_contract"] = {
        "lookback_bars": 60,
        "frequency": "1d",
        "history_required_before_first_decision": True,
        "backtest_start_does_not_truncate_provider_history": True,
        "minimum_candidates_with_full_history": 20,
    }
    base["r5_deployment_invariants"] = {
        "holding_count_mode": "strict_target_when_candidates_available",
        "target_holdings": 20,
        "minimum_fill_ratio": 0.9,
        "minimum_gross_exposure": 0.8,
        "maximum_cash_ratio_after_rebalance": 0.2,
        "maximum_insufficient_cash_rejections": 0,
        "require_at_least_one_rebalance": True,
    }
    base["confirmation_evidence"] = {
        key: {
            "confirmed": True,
            "customer_text": f"确认 {key}",
            "confirmed_at": "2026-07-26T20:30:00+08:00",
            "source": "customer_reply",
        }
        for key in ("generation_target", "strategy_semantics", "portfolio_contract",
                    "rebalance_funding_contract", "r5_deployment_invariants")
    }
    base["user_confirmations"]["generation_target"] = True
    base["user_confirmations"]["backtest_validation_mode"] = True
    return base


def test_design_22_requires_new_contract_blocks():
    incomplete = _design_22()
    incomplete.pop("portfolio_contract")
    assert any("portfolio_contract" in item for item in validate_design(incomplete))


def test_design_22_full_contract_passes_schema_and_evidence():
    complete = _design_22()
    assert validate_design(complete) == []
    assert confirmation_evidence_errors(complete) == []


def test_design_22_rejects_boolean_only_confirmation():
    broken = _design_22()
    broken["confirmation_evidence"]["strategy_semantics"] = {
        "confirmed": True, "customer_text": "", "confirmed_at": "", "source": "agent"}
    rules = {item["rule_id"] for item in confirmation_evidence_errors(broken)}
    assert "DESIGN-CONFIRMATION-EVIDENCE" in rules

    missing_tz = _design_22()
    missing_tz["confirmation_evidence"]["portfolio_contract"]["confirmed_at"] = "2026-07-26 20:30"
    assert confirmation_evidence_errors(missing_tz)
