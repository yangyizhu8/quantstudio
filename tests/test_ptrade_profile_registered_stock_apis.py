from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from publish_agent_strategy import publish
from validate_agent_strategy import validate_strategy
from quantstudio.backtest.ptrade_api import _api as ptrade_api


def _design() -> dict:
    return {
        "design_version": "2.1",
        "strategy_id": "portable_stock_api_probe",
        "strategy_name": "可移植股票API探针策略",
        "asset_class": "stock",
        "targets": ["quantstudio", "ptrade"],
        "engine_profile": {
            "profile_id": "minute-bar-v1",
            "bar_frequency": "1m",
            "match_price_mode": "close",
        },
        "market_data_contract": {
            "signal_price_adjustment": "pre",
            # 存量修复（2026-08-22，与中文命名改动无关但同为本次测试更新收口）：
            # fixture 仍用已废弃的 pre_adjusted_price，违反 schema 常量
            # raw_trade_price（2026-08-14 平台审计契约，先于本次改动已提交）。
            "execution_price_basis": "raw_trade_price",
        },
        "strategy_semantics": {
            "universe": "Previous-day A-share universe",
            "entry_rules": ["Buy one verified security"],
            "exit_rules": ["No strategy-specific exit in the probe"],
            "portfolio_rules": ["No leverage"],
            "risk_rules": ["Reject non-tradable securities"],
        },
        "timing": {
            "signal_data_cutoff": "T-1 close",
            "holding_semantics": "Probe only",
            "decision_events": [
                {
                    "name": "rebalance",
                    "lifecycle": "run_daily",
                    "time": "09:31",
                    "purpose": "Exercise registered portable APIs",
                }
            ],
        },
        "components": {
            "lifecycle_hooks": [
                "initialize",
                "before_trading_start",
                "handle_data",
                "after_trading_end",
            ],
            "api_groups": [
                "configuration",
                "universe_reference",
                "portfolio_orders",
                "scheduling_diagnostics",
            ],
            "required_apis": [
                "set_benchmark",
                "run_daily",
                "get_Ashares",
                "get_stock_status",
                "get_position",
                "order_target_value",
                "log",
            ],
            "implementation_notes": ["Use only profiled PTrade public APIs"],
        },
        "constraints": {
            "hard_filters": [],
            "no_lookahead": True,
            "portable_source_required": True,
            "runtime_state_guard_required": True,
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
        "output": {"overwrite": True},
        "universe_contract": {
            "mode": "portable_public_api",
            "local_dynamic_api_allowed": False,
        },
        "validation_execution": {
            "mode": "agent_managed",
            "require_hash_bound_evidence": True,
            "formal_publish_requires_backtest_pass": True,
        },
        "backtest_window_contract": {
            "strategy_must_not_hardcode_backtest_dates": True,
            "agent_must_not_start_unconfirmed_backtest": True,
            "actual_window_selected_by": "customer_confirmed_agent_run",
        },
    }


def _source() -> str:
    return '''
def _ensure_runtime_state():
    if not hasattr(g, "candidates"):
        g.candidates = []


def initialize(context):
    _ensure_runtime_state()
    set_benchmark("000300.SS")
    run_daily(context, rebalance, time="09:31")


def before_trading_start(context, data):
    _ensure_runtime_state()
    g.candidates = list(get_Ashares() or [])


def handle_data(context, data):
    _ensure_runtime_state()


def after_trading_end(context, data):
    _ensure_runtime_state()


def rebalance(context):
    _ensure_runtime_state()
    if not g.candidates:
        return
    code = g.candidates[0]
    status = get_stock_status([code], query_type="DELISTING")
    if status.get(code, False):
        return
    position = get_position(code)
    if getattr(position, "amount", 0) == 0:
        order_target_value(code, 10000)
    log.warning("portable API probe complete")
'''.lstrip()


def test_profile_registers_core_stock_portability_apis():
    profile = json.loads((ROOT / "skills" / "quantstudio-strategy-compiler" /
                          "references" / "ptrade-api-signatures.json").read_text(encoding="utf-8"))
    assert profile["profile_version"] == "1.10.0"
    for name in (
        "set_benchmark", "run_daily", "get_Ashares", "get_index_stocks",
        "get_stock_status", "get_positions", "get_position", "get_industry",
        "get_stock_exrights",
    ):
        assert name in profile["signatures"]
    assert profile["signatures"]["get_stock_status"]["canonical_keyword_values"]["query_type"] == [
        "ST", "HALT", "DELISTING"
    ]
    # F3：get_index_stocks 必须声明 PIT/date 契约
    assert any("as-of" in n for n in profile["signatures"]["get_index_stocks"]["notes"])


def test_profile_project_and_install_consistent():
    """项目 profile 与安装 profile 深度一致"""
    proj = json.loads((ROOT / "skills" / "quantstudio-strategy-compiler" /
                        "references" / "ptrade-api-signatures.json").read_text(encoding="utf-8"))
    inst = json.loads(Path("C:/Users/Administrator/.agents/skills/quantstudio-strategy-compiler/"
                           "references/ptrade-api-signatures.json").read_text(encoding="utf-8"))
    assert proj["profile_version"] == inst["profile_version"] == "1.10.0"
    assert "get_stock_exrights" in proj["signatures"]
    assert "get_stock_exrights" in inst["signatures"]
    assert "get_industry" in proj["signatures"]
    assert "get_industry" in inst["signatures"]
    assert "get_stock_info" in proj["signatures"]
    assert "get_stock_info" in inst["signatures"]
    assert "get_index_stocks" in proj["signatures"]
    assert "get_index_stocks" in inst["signatures"]


def test_profile_records_get_history_is_dict_return_contract():
    profile = json.loads((ROOT / "skills" / "quantstudio-strategy-compiler" /
                          "references" / "ptrade-api-signatures.json").read_text(encoding="utf-8"))
    contract = profile["signatures"]["get_history"]["return_contract"]["is_dict_true"]
    assert contract["container"] == "mapping"
    assert set(contract["item_types"]) == {
        "pandas.DataFrame", "numpy.structured_array", "numpy.recarray"}
    assert set(contract["field_value_types"]) == {"pandas.Series", "numpy.ndarray"}
    assert any("np.asarray" in rule for rule in profile["portable_rules"])


def test_registered_stock_api_source_passes_ptrade_validation():
    report = validate_strategy(_design(), _source(), target_profile="ptrade")
    assert report["status"] == "PASS", report


def test_unprofiled_required_api_blocks_dual_design():
    design = _design()
    design["components"]["required_apis"].append("mystery_ptrade_api")
    report = validate_strategy(design, _source(), target_profile="ptrade")
    assert any(item["rule_id"] == "PTRADE-DESIGN-UNPROFILED-API"
               and item["severity"] == "BLOCK" for item in report["issues"])


def test_unprofiled_external_top_level_call_blocks_source():
    source = _source() + "\n\ndef probe_unknown():\n    return mystery_ptrade_api()\n"
    report = validate_strategy(_design(), source, target_profile="ptrade")
    assert any(item["rule_id"] == "PTRADE-API-UNPROFILED"
               and item["severity"] == "BLOCK" for item in report["issues"])


def test_registered_api_keyword_and_query_type_are_strict():
    source = _source().replace('get_Ashares()', 'get_Ashares(query_date="20260724")')
    source = source.replace('query_type="DELISTING"', 'query_type="DELISTING_SORTING"')
    report = validate_strategy(_design(), source, target_profile="ptrade")
    signature_blocks = [item for item in report["issues"]
                        if item["rule_id"] == "PTRADE-API-SIGNATURE"
                        and item["severity"] == "BLOCK"]
    assert len(signature_blocks) >= 2, report


def test_local_get_stock_status_supports_public_delisting_value(monkeypatch):
    rows = pd.DataFrame([{
        "code": "600000",
        "is_st_reliable": False,
        "is_delisting_risk": True,
        "suspendFlag": 0,
        "volume": 1000,
    }])
    monkeypatch.setattr(ptrade_api, "_resolve_status_source",
                        lambda stocks, query_date: (rows, None))
    assert ptrade_api.get_stock_status(
        ["600000.SS"], query_type="DELISTING", query_date="20260724"
    ) == {"600000.SS": True}
    assert ptrade_api.get_stock_status(
        ["600000.SS"], query_type="DELISTING_SORTING", query_date="20260724"
    ) == {"600000.SS": True}


def test_registered_stock_api_source_publishes_identical_dual_targets(tmp_path):
    design = _design()
    source = _source()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    strategy_path = workspace / "strategy.py"
    design_path = workspace / "agent_strategy_design.json"
    strategy_path.write_text(source, encoding="utf-8")
    design_path.write_text(json.dumps(design), encoding="utf-8")

    project = tmp_path / "project"
    project_db = project / "data" / "quantstudio.db"
    project_db.parent.mkdir(parents=True)
    project_db.touch()
    (workspace / "workspace_state.json").write_text(json.dumps({
        "stage": "BACKTEST_PASS",
        "backtest_status": "PASS",
        "backtest_data_source": "duckdb_provider",
        "backtest_db_path": str(project_db.resolve()),
        "backtest_db_resolution": "project_data_preferred",
    }), encoding="utf-8")

    result = publish(strategy_path, design_path, project)
    assert result["identical_source"] is True
    assert result["validated_after_target_generation"] is True
    local = project / "quantstudio" / "backtest" / "strategies" / "可移植股票API探针策略.py"
    ptrade = project / "ptrade" / "portable_stock_api_probe_ptrade.py"
    assert local.read_bytes() == ptrade.read_bytes() == strategy_path.read_bytes()
