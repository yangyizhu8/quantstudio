from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_agent_strategy import validate_strategy
from validate_dual_consistency import compare_sources
from publish_agent_strategy import publish


def design() -> dict:
    return {
        "design_version": "2.0",
        "strategy_id": "portable_validation_test",
        "strategy_name": "可移植校验测试策略",
        "asset_class": "stock",
        "targets": ["quantstudio", "ptrade"],
        "engine_profile": {"profile_id": "minute-bar-v1", "bar_frequency": "1m", "match_price_mode": "close"},
        "market_data_contract": {
            "signal_price_adjustment": "pre",
            "execution_price_basis": "raw_trade_price",
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


def valid_source(extra_initialize: str = "", extra_rebalance: str = "") -> str:
    lines = [
        'def _ensure_runtime_state():',
        '    if not hasattr(g, "ready"):',
        '        g.ready = True',
        '',
        'def initialize(context):',
        '    _ensure_runtime_state()',
        '    run_daily(context, rebalance, time="09:31")',
    ]
    if extra_initialize:
        lines.append(extra_initialize)
    lines.extend([
        '',
        'def handle_data(context, data):',
        '    _ensure_runtime_state()',
        '    return None',
        '',
        'def rebalance(context):',
        '    _ensure_runtime_state()',
        extra_rebalance or '    return None',
        '',
    ])
    return "\n".join(lines)


def block_rules(source: str) -> set[str]:
    report = validate_strategy(design(), source, target_profile="ptrade")
    return {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}



def test_legacy_design_schema_issue_does_not_hide_runtime_signature_errors():
    legacy = design()
    legacy["constraints"].pop("runtime_state_guard_required")
    report = validate_strategy(
        legacy, valid_source(extra_initialize="    set_slippage(slippage_ratio=0.0)"),
        target_profile="ptrade")
    rules = {item["rule_id"] for item in report["issues"]}
    assert "DESIGN-SCHEMA" in rules
    assert "PTRADE-API-SIGNATURE" in rules

def test_ptrade_validator_blocks_wrong_set_slippage_keyword():
    rules = block_rules(valid_source(extra_initialize="    set_slippage(slippage_ratio=0.0)"))
    assert "PTRADE-API-SIGNATURE" in rules


def test_ptrade_validator_blocks_quantstudio_backtest_switches():
    rules = block_rules(valid_source(extra_initialize=(
        "    if not is_trade():\n"
        "        set_backtest()")))
    assert "PTRADE-LOCAL-SYMBOL" in rules
    assert "TARGET-LOCAL-EXTENSION-BAN" in rules


def test_ptrade_validator_blocks_log_warn_and_accepts_log_warning():
    blocked = block_rules(valid_source(extra_rebalance="    log.warn('rejected')"))
    assert "PTRADE-LOG-METHOD" in blocked

    report = validate_strategy(
        design(), valid_source(extra_rebalance="    log.warning('rejected')"),
        target_profile="ptrade")
    assert report["status"] == "PASS", report


def test_ptrade_validator_blocks_unimported_numpy_and_pandas_aliases():
    rules = block_rules(valid_source(extra_rebalance=(
        "    np.mean([1.0, 2.0])\n"
        "    pd.Series([1.0, 2.0])")))
    assert "PTRADE-RUNTIME-IMPORT" in rules


def test_ptrade_validator_accepts_explicit_numpy_and_pandas_imports():
    source = "import numpy as np\nimport pandas as pd\n\n" + valid_source(
        extra_rebalance=(
            "    np.mean([1.0, 2.0])\n"
            "    pd.Series([1.0, 2.0])"))
    report = validate_strategy(design(), source, target_profile="ptrade")
    assert report["status"] == "PASS", report


def test_ptrade_validator_accepts_real_set_slippage_keyword():
    report = validate_strategy(
        design(), valid_source(extra_initialize="    set_slippage(slippage=0.0)"),
        target_profile="ptrade")
    assert report["status"] == "PASS", report


def test_ptrade_validator_blocks_trading_only_backtest_apis():
    rules = block_rules(valid_source(extra_rebalance="    get_snapshot('600000.SS')\n    check_limit('600000.SS')"))
    assert "PTRADE-CONTEXT-API" in rules


def test_ptrade_validator_accepts_get_open_orders_in_backtest():
    report = validate_strategy(
        design(), valid_source(extra_rebalance="    get_open_orders(security='600000.SS')"),
        target_profile="ptrade")
    assert report["status"] == "PASS", report



def test_filter_stock_by_status_is_blocked_in_scheduled_callback():
    source = valid_source(extra_rebalance=(
        "    filter_stock_by_status(['600000.SS'], filter_type=['ST'], query_date='20260723')"))
    assert "PTRADE-CALLBACK-CONTEXT" in block_rules(source)

def test_ptrade_validator_blocks_local_stock_info_and_mytt_symbol():
    rules = block_rules(valid_source(extra_rebalance="    get_security_info('600000.SS')\n    EMA([1, 2, 3], 2)"))
    assert "PTRADE-API-UNSUPPORTED" in rules
    assert "PTRADE-LOCAL-SYMBOL" in rules


def test_state_guard_is_required_first_in_every_callback():
    source = valid_source().replace(
        "def rebalance(context):\n    _ensure_runtime_state()",
        "def rebalance(context):\n    value = 1\n    _ensure_runtime_state()")
    assert "RUNTIME-STATE-GUARD" in block_rules(source)


def test_current_minute_include_true_allowed_only_in_confirmed_schedule():
    source = valid_source(extra_rebalance=(
        "    get_history(1, frequency='1m', field=['open', 'close'], "
        "security_list='600000.SS', fq='pre', include=True)"))
    report = validate_strategy(design(), source, target_profile="ptrade")
    assert report["status"] == "PASS", report



def test_ptrade_validator_checks_all_profiled_api_keywords():
    rules = block_rules(valid_source(extra_rebalance=(
        "    get_history(1, frequency='1m', fq='pre', bogus_keyword=True)")))
    assert "PTRADE-API-SIGNATURE" in rules


def test_ptrade_validator_blocks_dynamic_kwargs_for_profiled_api():
    source = valid_source(extra_initialize=(
        "    slippage_options = {'slippage': 0.0}\n"
        "    set_slippage(**slippage_options)"))
    assert "PTRADE-API-SIGNATURE" in block_rules(source)


def test_runtime_state_helper_must_not_reset_existing_fields():
    source = valid_source().replace(
        '    if not hasattr(g, "ready"):\n        g.ready = True',
        '    g.ready = True')
    assert "RUNTIME-STATE-IDEMPOTENCE" in block_rules(source)


def test_current_minute_include_true_allowed_through_scheduled_helper():
    source = valid_source(extra_rebalance="    current_minute()") + "\n" + "\n".join([
        "def current_minute():",
        "    return get_history(1, frequency='1m', field=['open'], security_list='600000.SS', fq='pre', include=True)",
    ])
    report = validate_strategy(design(), source, target_profile="ptrade")
    assert report["status"] == "PASS", report

def test_dual_consistency_detects_business_logic_drift():
    local = valid_source(extra_rebalance="    order_target_value('600000.SS', 1000)")
    ptrade = valid_source(extra_rebalance="    order_target_value('600000.SS', 2000)")
    report = compare_sources(local, ptrade, design())
    assert report["status"] == "BLOCKED"
    assert any(item["rule_id"] == "SEMANTIC-AST" for item in report["issues"])


def test_publisher_blocks_skipped_backtest_stage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    strategy = workspace / "strategy.py"
    contract = workspace / "agent_strategy_design.json"
    strategy.write_text(valid_source(), encoding="utf-8")
    contract.write_text(json.dumps(design()), encoding="utf-8")
    (workspace / "workspace_state.json").write_text(json.dumps({
        "stage": "STATIC_VALIDATION_PASS", "backtest_status": "NOT_RUN"
    }), encoding="utf-8")
    try:
        publish(strategy, contract, tmp_path / "project")
    except ValueError as exc:
        assert "not publishable" in str(exc) or "backtest" in str(exc)
    else:
        raise AssertionError("publisher must block a skipped R5 backtest stage")


# ---------------------------------------------------------------------------
# per-code ETF T+0 契约（docs/etf-t0-per-code-design.md §6.3 / references/etf-t0-rules.md §6）
# ---------------------------------------------------------------------------

def _t0_design(**mdc_overrides) -> dict:
    d = design()
    mdc = dict(d["market_data_contract"])
    mdc.update(mdc_overrides)
    d["market_data_contract"] = mdc
    return d


def test_etf_t0_engine_per_code_requires_stop_deferral_semantics():
    """engine_per_code 未声明 stop_deferral_semantics → BLOCK"""
    blocks = block_rules(valid_source())
    assert "STOP-DEFERRAL-SEMANTICS-MISSING" not in blocks  # 未声明 enforcement 不触发
    d = _t0_design(etf_t0_enforcement="engine_per_code")
    report = validate_strategy(d, valid_source(), target_profile="ptrade")
    rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "STOP-DEFERRAL-SEMANTICS-MISSING" in rule_ids


def test_etf_t0_engine_per_code_accepts_stop_deferral_declared():
    """engine_per_code + stop_deferral_semantics 声明 → 不再 BLOCK"""
    d = _t0_design(etf_t0_enforcement="engine_per_code",
                   stop_deferral_semantics="trigger_lock_defer_next_sellable_day")
    report = validate_strategy(d, valid_source(), target_profile="ptrade")
    rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "STOP-DEFERRAL-SEMANTICS-MISSING" not in rule_ids


def test_etf_t0_enforcement_enum_rejected():
    """etf_t0_enforcement 不在枚举 → BLOCK"""
    d = _t0_design(etf_t0_enforcement="all_t0")
    report = validate_strategy(d, valid_source(), target_profile="ptrade")
    rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "ETF-T0-ENFORCEMENT-ENUM" in rule_ids


def test_validator_blocks_order_return_field_read():
    """读取订单返回值的本地字段 .status/.reason → BLOCK（可移植策略）"""
    src = valid_source(extra_rebalance="    o = order('600000.SS', 100)\n"
                                       "    if o.status == 'filled':\n"
                                       "        log.info('ok')")
    report = validate_strategy(_t0_design(), src, target_profile="ptrade")
    rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "ORDER-RETURN-FIELD-READ" in rule_ids


def test_validator_blocks_set_iteration():
    """set()/frozenset() → BLOCK（哈希迭代顺序跨进程不稳定，T3）"""
    src = valid_source(extra_rebalance="    for c in set(g.pool):\n"
                                       "        log.info('c=%s' % c)")
    report = validate_strategy(_t0_design(), src, target_profile="ptrade")
    rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "NONDETERMINISTIC-ITERATION" in rule_ids


def test_validator_accepts_deterministic_sorted_iteration():
    """list + sorted 迭代不触发 T3 BLOCK"""
    src = valid_source(extra_rebalance="    pool = list(g.pool)\n"
                                       "    for c in sorted(pool):\n"
                                       "        log.info('c=%s' % c)")
    report = validate_strategy(_t0_design(), src, target_profile="ptrade")
    rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "NONDETERMINISTIC-ITERATION" not in rule_ids
