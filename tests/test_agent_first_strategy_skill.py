from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from create_agent_workspace import create_workspace
from publish_agent_strategy import publish
from validate_agent_strategy import validate_strategy


def confirmed_design() -> dict:
    return {
        "design_version": "2.0",
        "strategy_id": "agent_generic_rotation",
        "strategy_name": "智能体通用轮动策略",
        "asset_class": "stock",
        "targets": ["quantstudio", "ptrade"],
        "engine_profile": {
            "profile_id": "minute-bar-v1",
            "bar_frequency": "1m",
            "match_price_mode": "current_bar",
        },
        "market_data_contract": {
            "signal_price_adjustment": "pre",
            "execution_price_basis": "pre_adjusted_price",
        },
        "strategy_semantics": {
            "universe": "All A-shares as of previous trading day, then confirmed status filters",
            "entry_rules": ["Calling agent computes a portable ranking from previous-day history"],
            "exit_rules": ["Exit securities outside the selected target set"],
            "portfolio_rules": ["Equal target value across selected securities"],
            "risk_rules": ["Check status and limit state before orders"],
        },
        "timing": {
            "signal_data_cutoff": "T-1-close plus current 09:31 bar",
            "holding_semantics": "Agent-defined; no hidden template semantics",
            "decision_events": [
                {"name": "prepare_candidates", "lifecycle": "before_trading_start", "purpose": "prepare PIT candidates"},
                {"name": "rebalance", "lifecycle": "run_daily", "time": "09:31", "purpose": "submit target orders"},
            ],
        },
        "components": {
            "lifecycle_hooks": ["initialize", "before_trading_start", "handle_data"],
            "api_groups": ["configuration", "universe_reference", "market_data", "portfolio_orders", "scheduling_diagnostics"],
            "required_apis": ["set_benchmark", "run_daily", "get_Ashares", "filter_stock_by_status", "get_history", "order_target_value"],
            "implementation_notes": ["Use per-security get_history for PTrade portability"],
        },
        "constraints": {
            "hard_filters": ["exclude ST, suspended and delisting securities", "check limit-up/limit-down before orders"],
            "no_lookahead": True,
            "portable_source_required": True,
            "runtime_state_guard_required": True,
        },
        "approximations": [{"id": "open_to_0931", "description": "Use first native minute bar", "confirmed": True}],
        "open_questions": [],
        "user_confirmations": {
            "strategy_semantics": True,
            "execution_approximations": True,
            "component_plan": True,
        },
        "output": {"overwrite": False},
    }


def implemented_source() -> str:
    return "\n".join([
        '"""One canonical agent-authored source."""',
        '',
        'def _ensure_runtime_state():',
        "    if not hasattr(g, 'candidates'):",
        '        g.candidates = []',
        '',
        'def initialize(context):',
        '    _ensure_runtime_state()',
        "    set_benchmark('000300.SS')",
        '    g.candidates = []',
        "    run_daily(context, rebalance, time='09:31')",
        '',
        'def before_trading_start(context, data):',
        '    _ensure_runtime_state()',
        '    stocks = get_Ashares(context.previous_date)',
        '    g.candidates = filter_stock_by_status(',
        "        stocks, filter_type=['ST', 'HALT', 'DELISTING'], query_date=None",
        '    )',
        '',
        'def handle_data(context, data):',
        '    _ensure_runtime_state()',
        '    return None',
        '',
        'def rebalance(context):',
        '    _ensure_runtime_state()',
        '    selected = []',
        '    for code in g.candidates[:10]:',
        "        history = get_history(5, '1d', field='close', security_list=code,",
        "                              fq='pre', include=False)",
        '        if history is None or len(history) < 5:',
        '            continue',
        '        selected.append(code)',
        '    target = context.portfolio.total_value / len(selected) if selected else 0',
        '    for code in selected:',
        '        order_target_value(code, target)',
        '',
    ])


def test_design_schema_requires_pre_adjusted_execution_basis():
    design = confirmed_design()
    design["market_data_contract"]["execution_price_basis"] = "pre_adjusted_price"
    assert validate_strategy(design, implemented_source(), target_profile="ptrade")["status"] == "PASS"

    design["market_data_contract"]["execution_price_basis"] = "raw_trade_price"
    report = validate_strategy(design, implemented_source(), target_profile="ptrade")
    assert report["status"] == "BLOCKED"
    assert any(item["rule_id"] == "DESIGN-SCHEMA" for item in report["issues"])


def test_unconfirmed_design_cannot_scaffold(tmp_path):
    design = confirmed_design()
    design["user_confirmations"]["component_plan"] = False
    design_path = tmp_path / "design.json"
    design_path.write_text(json.dumps(design), encoding="utf-8")
    with pytest.raises(ValueError, match="component_plan"):
        create_workspace(design_path, tmp_path / "workspace")


def test_scaffold_is_component_only_and_requires_agent_implementation(tmp_path):
    design = confirmed_design()
    design_path = tmp_path / "design.json"
    design_path.write_text(json.dumps(design), encoding="utf-8")
    workspace = create_workspace(design_path, tmp_path / "workspace")
    source = (workspace / "strategy.py").read_text(encoding="utf-8")
    assert "run_daily(context, rebalance, time='09:31')" in source
    assert "strategy_pattern" not in source
    assert "smallcap" not in source.lower()
    report = validate_strategy(design, source)
    assert report["status"] == "BLOCKED"
    assert any(item["rule_id"] == "AGENT-IMPLEMENTATION-MISSING" for item in report["issues"])


def test_agent_implemented_strategy_validates_and_publishes_identical_source(tmp_path):
    design = confirmed_design()
    source = implemented_source()
    report = validate_strategy(design, source)
    assert report["status"] == "PASS", report

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
    assert result["dual_consistency"]["comparison_phase"] == "post_generation_staging"
    assert result["dual_consistency"]["staged_targets_exist_before_comparison"] is True
    local = project / "quantstudio" / "backtest" / "strategies" / "智能体通用轮动策略.py"
    ptrade = project / "ptrade" / "agent_generic_rotation_ptrade.py"
    assert local.read_bytes() == ptrade.read_bytes() == strategy_path.read_bytes()




def test_validator_requires_literal_front_adjustment_for_signal_price_apis():
    design = confirmed_design()
    variants = {
        "missing": implemented_source().replace("                              fq='pre', include=False)",
                                                "                              include=False)"),
        "none": implemented_source().replace("fq='pre'", "fq=None"),
        "post": implemented_source().replace("fq='pre'", "fq='post'"),
        "dypre": implemented_source().replace("fq='pre'", "fq='dypre'"),
        "dynamic": implemented_source().replace("fq='pre'", "fq=g.adjustment"),
    }
    for label, source in variants.items():
        report = validate_strategy(design, source, target_profile="ptrade")
        assert report["status"] == "BLOCKED", (label, report)
        assert any(item["rule_id"] == "SIGNAL-PRICE-ADJUSTMENT"
                   and item["severity"] == "BLOCK" for item in report["issues"]), (label, report)


def test_validator_blocks_attribute_history_because_adjustment_is_unprovable():
    source = implemented_source() + "\n\ndef raw_history():\n    return attribute_history('600000.SS', 5)\n"
    report = validate_strategy(confirmed_design(), source, target_profile="ptrade")
    assert any(item["rule_id"] == "SIGNAL-PRICE-ADJUSTMENT"
               and item["severity"] == "BLOCK" for item in report["issues"])

def test_validator_blocks_storage_and_local_batch_api():
    design = confirmed_design()
    source = implemented_source() + "\nimport duckdb\n\ndef bad():\n    return get_history_batch([], 5, '1d')\n"
    report = validate_strategy(design, source)
    rules = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "STRATEGY-ISOLATION" in rules
    assert "PTRADE-LOCAL-SYMBOL" in rules


def test_validator_blocks_unavailable_0930_minute_schedule():
    design = confirmed_design()
    design["timing"]["decision_events"][1]["time"] = "09:30"
    source = implemented_source().replace("time='09:31'", "time='09:30'")
    report = validate_strategy(design, source)
    assert any(item["rule_id"] == "AUCTION-BAR-UNAVAILABLE" for item in report["issues"])


def test_no_strategy_specific_name_in_skill_core():
    skill_root = ROOT / "skills" / "quantstudio-strategy-compiler"
    checked = [
        skill_root / "SKILL.md",
        skill_root / "scripts" / "create_agent_workspace.py",
        skill_root / "scripts" / "validate_agent_strategy.py",
        skill_root / "scripts" / "publish_agent_strategy.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in checked)
    assert "smallcap_overnight_scalp" not in text
    assert 'strategy_pattern == ' not in text


def test_component_catalog_apis_exist_in_injected_runtime():
    from quantstudio.backtest import ptrade_import

    catalog = json.loads((ROOT / "skills" / "quantstudio-strategy-compiler" / "references" / "component-catalog.json").read_text(encoding="utf-8"))
    missing = [
        f"{group}:{name}"
        for group, names in catalog["api_groups"].items()
        for name in names
        if not hasattr(ptrade_import, name)
    ]
    assert not missing


def test_canonical_source_loads_through_project_strategy_runner(tmp_path):
    from quantstudio.backtest.strategy_runner import StrategyRunner

    strategy_path = tmp_path / "strategy.py"
    strategy_path.write_text(implemented_source(), encoding="utf-8")
    loaded = StrategyRunner.load(strategy_path)
    assert {"initialize", "before_trading_start", "handle_data"}.issubset(loaded.functions)




def test_skill_prompt_enforces_customer_stops_data_priority_and_post_generation_check():
    skill = (ROOT / "skills" / "quantstudio-strategy-compiler" / "SKILL.md").read_text(encoding="utf-8")
    assert "R0 and R2.5 are real conversational stop points" in skill
    assert "must not self-confirm" in skill
    assert "<current-project>/data/quantstudio.db" in skill
    # B.3（2026-08-11）local-only 改造后：PTrade 转换职责移交 PyQt tab / CLI，
    # skill 不再产出 PTrade 代码（旧断言 comparison_phase=post_generation_staging 已随 R6 改写删除）。
    assert "PTrade conversion is handled by a separate PyQt tab / CLI" in skill
    assert "patching only the currently failing strategy" in skill
    assert "literal keyword `fq='pre'`" in skill
    assert "signal_price_adjustment" in skill
