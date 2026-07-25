from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_skill_common import confirmation_errors, validate_design
from publish_agent_strategy import publish
from validate_agent_strategy import validate_strategy


def local_etf_design() -> dict:
    return {
        "design_version": "2.1",
        "strategy_id": "local_dynamic_etf",
        "strategy_name": "Local Dynamic ETF",
        "asset_class": "etf",
        "targets": ["quantstudio"],
        "engine_profile": {
            "profile_id": "daily-bar-v1",
            "bar_frequency": "1d",
            "match_price_mode": "close",
        },
        "market_data_contract": {
            "signal_price_adjustment": "pre",
            "execution_price_basis": "raw_trade_price",
        },
        "strategy_semantics": {
            "universe": "PIT domestic equity ETF universe from local metadata",
            "entry_rules": [], "exit_rules": [], "portfolio_rules": [], "risk_rules": [],
        },
        "timing": {
            "signal_data_cutoff": "previous trading day close",
            "holding_semantics": "daily",
            "decision_events": [{
                "name": "build_etf_pool", "lifecycle": "before_trading_start",
                "purpose": "Build local PIT ETF universe",
            }],
        },
        "components": {
            "lifecycle_hooks": ["initialize", "before_trading_start"],
            "api_groups": ["universe_reference"],
            "required_apis": ["get_etf_list_local"],
            "implementation_notes": [],
        },
        "constraints": {
            "hard_filters": [], "no_lookahead": True,
            "portable_source_required": False,
            "runtime_state_guard_required": True,
        },
        "universe_contract": {
            "mode": "dynamic_local",
            "local_dynamic_api_allowed": True,
            "local_dynamic_api": "get_etf_list_local",
            "etf_type": "equity",
            "active_only": True,
        },
        "validation_execution": {
            "mode": "agent_managed",
            "require_hash_bound_evidence": True,
            "formal_publish_requires_backtest_pass": True,
        },
        "backtest_window_contract": {
            "actual_window_selected_by": "customer_confirmed_agent_run",
            "strategy_must_not_hardcode_backtest_dates": True,
            "agent_must_not_start_unconfirmed_backtest": True,
        },
        "approximations": [],
        "open_questions": [],
        "user_confirmations": {
            "generation_target": True,
            "backtest_validation_mode": True,
            "strategy_semantics": True,
            "execution_approximations": True,
            "component_plan": True,
        },
        "output": {"overwrite": False},
    }


def local_source() -> str:
    return """
def _ensure_runtime_state():
    if not hasattr(g, 'etf_pool'):
        g.etf_pool = []

def initialize(context):
    _ensure_runtime_state()

def before_trading_start(context, data):
    _ensure_runtime_state()
    g.etf_pool = get_etf_list_local(
        query_date=context.previous_date,
        etf_type='equity',
        active_only=True,
    )
"""


def dual_etf_design() -> dict:
    design = local_etf_design()
    design["strategy_id"] = "dual_static_etf"
    design["targets"] = ["quantstudio", "ptrade"]
    design["constraints"]["portable_source_required"] = True
    design["universe_contract"] = {
        "mode": "static_whitelist",
        "local_dynamic_api_allowed": False,
        "static_etf_whitelist": ["510050.SS", "159915.SZ"],
    }
    design["components"]["required_apis"] = []
    design["user_confirmations"]["static_etf_whitelist"] = True
    return design


def dual_source() -> str:
    return """
RISK_ETF_POOL = ['510050.SS', '159915.SZ']

def _ensure_runtime_state():
    if not hasattr(g, 'etf_pool'):
        g.etf_pool = []

def initialize(context):
    _ensure_runtime_state()
    g.etf_pool = list(RISK_ETF_POOL)

def before_trading_start(context, data):
    _ensure_runtime_state()
"""


def test_local_target_schema_and_validation_allow_local_etf_api():
    design = local_etf_design()
    assert validate_design(design) == []
    assert confirmation_errors(design) == []
    assert validate_strategy(design, local_source(), target_profile="quantstudio")["status"] == "PASS"
    assert validate_strategy(design, local_source(), target_profile="ptrade")["status"] == "NOT_APPLICABLE"


def test_dual_target_blocks_local_extension_even_during_local_validation():
    design = dual_etf_design()
    report = validate_strategy(design, local_source(), target_profile="quantstudio")
    assert report["status"] == "BLOCKED"
    assert "TARGET-LOCAL-EXTENSION-BAN" in {item["rule_id"] for item in report["issues"]}


def test_get_etf_list_is_blocked_in_backtest_source():
    design = local_etf_design()
    source = local_source().replace("get_etf_list_local(\n        query_date=context.previous_date,\n        etf_type='equity',\n        active_only=True,\n    )", "get_etf_list()")
    report = validate_strategy(design, source, target_profile="quantstudio")
    rules = {item["rule_id"] for item in report["issues"]}
    assert "PTRADE-GET-ETF-LIST-BACKTEST-BAN" in rules


def test_dual_etf_requires_confirmed_whitelist():
    design = dual_etf_design()
    design["user_confirmations"]["static_etf_whitelist"] = False
    assert validate_design(design)
    assert confirmation_errors(design)


def test_dual_static_whitelist_passes_both_profiles():
    design = dual_etf_design()
    assert validate_design(design) == []
    assert validate_strategy(design, dual_source(), target_profile="quantstudio")["status"] == "PASS"
    assert validate_strategy(design, dual_source(), target_profile="ptrade")["status"] == "PASS"


def test_local_only_publish_generates_no_ptrade_placeholder(tmp_path):
    project = tmp_path / "project"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = project / "data" / "quantstudio.db"
    db.parent.mkdir(parents=True)
    db.touch()

    design = local_etf_design()
    design_path = workspace / "agent_strategy_design.json"
    design_path.write_text(json.dumps(design), encoding="utf-8")
    strategy_path = workspace / "strategy.py"
    strategy_path.write_text(local_source(), encoding="utf-8")
    (workspace / "workspace_state.json").write_text(json.dumps({
        "stage": "BACKTEST_PASS",
        "backtest_status": "PASS",
        "backtest_data_source": "duckdb_provider",
        "backtest_db_path": str(db.resolve()),
    }), encoding="utf-8")

    report = publish(strategy_path, design_path, project)
    local_file = project / "quantstudio" / "backtest" / "strategies" / "local_dynamic_etf_quantstudio.py"
    ptrade_file = project / "ptrade" / "local_dynamic_etf_ptrade.py"
    assert local_file.exists()
    assert not ptrade_file.exists()
    assert report["ptrade_validation"]["status"] == "NOT_APPLICABLE"
    assert report["dual_consistency"]["status"] == "NOT_APPLICABLE"
    assert report["ptrade_output_status"] == "NOT_GENERATED"
    assert [item["platform"] for item in report["targets"]] == ["quantstudio"]
