"""Regression tests for the get_history(is_dict=True) return-shape contract.

Covers Skill 0.6.0 / PTrade profile 1.8.0:
- unguarded pandas-only access on history items is BLOCKED;
- np.asarray normalization and hasattr-guarded helpers PASS;
- the agent-first runtime-shape fixture executes the real helper against
  DataFrame/Series/structured-array/recarray/empty/missing-field inputs;
- the retired contrarian_loser_reversal candidate is a negative fixture.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_agent_strategy import validate_strategy
from validate_runtime_shapes import validate_runtime_shapes


def design() -> dict:
    return {
        "design_version": "2.0",
        "strategy_id": "history_shape_test",
        "strategy_name": "History Shape Test",
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


HISTORY_CALL = (
    "    hist = get_history(60, frequency='1d', field=['close'],\n"
    "                       security_list=['600000.SS'], fq='pre', include=False, is_dict=True)\n"
)


def source_with_body(body: str, prefix: str = "") -> str:
    parts = ["import numpy as np", ""]
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
        HISTORY_CALL.rstrip("\n"),
        body.rstrip("\n"),
        "",
    ])
    return "\n".join(parts)


def block_rules(source: str) -> set[str]:
    report = validate_strategy(design(), source, target_profile="ptrade")
    return {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}


def test_history_item_dot_values_is_blocked():
    body = (
        "    for code, df in hist.items():\n"
        "        closes = np.asarray(df['close'].values, dtype=float)"
    )
    assert "PTRADE-HISTORY-SHAPE-UNSAFE" in block_rules(source_with_body(body))


def test_history_item_iloc_loc_empty_columns_to_numpy_are_blocked():
    body = (
        "    for code, df in hist.items():\n"
        "        last = df['close'].iloc[-1]\n"
        "        first = df.iloc[-1]['close']\n"
        "        if df.empty:\n"
        "            continue\n"
        "        cols = df.columns\n"
        "        arr = df.to_numpy()"
    )
    rules = block_rules(source_with_body(body))
    assert "PTRADE-HISTORY-PANDAS-ONLY" in rules


def test_direct_numeric_use_without_normalization_is_blocked():
    body = (
        "    for code, df in hist.items():\n"
        "        avg = np.mean(df['close'])"
    )
    assert "PTRADE-HISTORY-NORMALIZATION-MISSING" in block_rules(source_with_body(body))


def test_np_asarray_normalization_passes():
    body = (
        "    for code, df in hist.items():\n"
        "        closes = np.asarray(df['close'], dtype=float)\n"
        "        if len(closes) > 1 and closes[-1] > 0:\n"
        "            log.info(str(closes[-1]))"
    )
    report = validate_strategy(design(), source_with_body(body), target_profile="ptrade")
    assert report["status"] == "BLOCKED", report
    blocked_rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "PTRADE-IS-DICT-BAN" in blocked_rule_ids


def test_hasattr_guarded_normalization_passes():
    body = (
        "    for code, df in hist.items():\n"
        "        values = df['close']\n"
        "        if hasattr(values, 'values'):\n"
        "            values = values.values\n"
        "        closes = np.asarray(values, dtype=float)\n"
        "        if len(closes) > 0:\n"
        "            log.info(str(closes[-1]))"
    )
    report = validate_strategy(design(), source_with_body(body), target_profile="ptrade")
    assert report["status"] == "BLOCKED", report
    blocked_rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "PTRADE-IS-DICT-BAN" in blocked_rule_ids


def test_extract_history_field_helper_passes():
    helper = (
        "def _extract_history_field(history_item, field, dtype=float):\n"
        "    if history_item is None:\n"
        "        return np.asarray([], dtype=dtype)\n"
        "    try:\n"
        "        values = history_item[field]\n"
        "    except (KeyError, IndexError, TypeError, ValueError):\n"
        "        return np.asarray([], dtype=dtype)\n"
        "    if hasattr(values, 'values'):\n"
        "        values = values.values\n"
        "    return np.asarray(values, dtype=dtype).reshape(-1)\n\n"
    )
    body = (
        "    for code, df in hist.items():\n"
        "        closes = _extract_history_field(df, 'close', float)\n"
        "        if len(closes) > 0:\n"
        "            log.info(str(closes[-1]))"
    )
    report = validate_strategy(
        design(), source_with_body(body, prefix=helper), target_profile="ptrade")
    assert report["status"] == "BLOCKED", report
    blocked_rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "PTRADE-IS-DICT-BAN" in blocked_rule_ids


def test_subscripted_history_item_is_tracked():
    body = (
        "    df = hist['600000.SS']\n"
        "    closes = df['close'].values"
    )
    assert "PTRADE-HISTORY-SHAPE-UNSAFE" in block_rules(source_with_body(body))


def test_retired_contrarian_candidate_is_blocked():
    fixture_dir = ROOT / "tests" / "fixtures" / "retired_contrarian"
    candidate = fixture_dir / "contrarian_loser_reversal__candidate_quantstudio.py"
    old_design_path = fixture_dir / "agent_strategy_design.json"
    old_design = json.loads(old_design_path.read_text(encoding="utf-8-sig"))
    report = validate_strategy(
        old_design, candidate.read_text(encoding="utf-8-sig"),
        str(candidate), target_profile="ptrade")
    rules = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert report["status"] == "BLOCKED"
    assert "PTRADE-HISTORY-SHAPE-UNSAFE" in rules


# --- agent-first runtime-shape fixture (no backtest) ---

HELPER_SOURCE = '''
def _extract_history_field(history_item, field, dtype=float):
    if history_item is None:
        return np.asarray([], dtype=dtype)
    try:
        values = history_item[field]
    except (KeyError, IndexError, TypeError, ValueError):
        return np.asarray([], dtype=dtype)
    if hasattr(values, "values"):
        values = values.values
    return np.asarray(values, dtype=dtype).reshape(-1)
'''


def test_runtime_shape_fixture_dataframe_recarray_equal_and_fail_soft(tmp_path):
    strategy = tmp_path / "strategy.py"
    strategy.write_text(HELPER_SOURCE, encoding="utf-8")
    report = validate_runtime_shapes(strategy)
    assert report["status"] == "PASS", report
    checks = {check["fixture"]: check for check in report["checks"]}
    assert checks["dataframe"]["result_len"] == checks["recarray"]["result_len"] == 5
    for name in ("none", "empty_structured", "missing_close_field"):
        assert checks[name]["result_len"] == 0


def test_runtime_shape_fixture_flags_raising_helper(tmp_path):
    strategy = tmp_path / "bad_strategy.py"
    strategy.write_text(
        "def _extract_history_field(history_item, field, dtype=float):\n"
        "    return history_item[field].values\n",
        encoding="utf-8")
    report = validate_runtime_shapes(strategy)
    assert report["status"] == "FAIL"
    assert report["failures"]



# --- design 2.2: standard helper contract (Skill 0.6.0 option A) ---

def _design_22() -> dict:
    from tests.test_agent_portfolio_contract import _design_22 as base
    return base()


def source_22(body: str, prefix: str = "") -> str:
    parts = ["import numpy as np", ""]
    if prefix:
        parts.append(prefix)
    parts.extend([
        "def _ensure_runtime_state():",
        "    if not hasattr(g, 'ready'):",
        "        g.ready = True",
        "",
        "def _portfolio_total_value(context):",
        "    return context.portfolio.total_value",
        "",
        "def initialize(context):",
        "    _ensure_runtime_state()",
        "    run_daily(context, rebalance, time='09:31')",
        "",
        "def handle_data(context, data):",
        "    _ensure_runtime_state()",
        "",
        "def after_trading_end(context):",
        "    _ensure_runtime_state()",
        "    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%s' % ('20260720_1', '2026-07-20', 20))",
        "",
        "def rebalance(context):",
        "    _ensure_runtime_state()",
        HISTORY_CALL.rstrip("\n"),
        body.rstrip("\n"),
        "    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%s buy_submitted=%s' % ('20260720_1', '2026-07-20', 20, 20))",
        "",
    ])
    return "\n".join(parts)


def block_rules_22(source: str) -> set[str]:
    report = validate_strategy(_design_22(), source, target_profile="ptrade")
    return {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}


def test_design_22_requires_standard_history_helper():
    body = (
        "    for code, df in hist.items():\n"
        "        closes = np.asarray(df['close'], dtype=float)"
    )
    rules = block_rules_22(source_22(body))
    assert "PTRADE-HISTORY-HELPER-REQUIRED" in rules


def test_design_22_with_standard_helper_passes():
    helper = (
        "def _extract_history_field(history_item, field, dtype=float):\n"
        "    if history_item is None:\n"
        "        return np.asarray([], dtype=dtype)\n"
        "    try:\n"
        "        values = history_item[field]\n"
        "    except (KeyError, IndexError, TypeError, ValueError):\n"
        "        return np.asarray([], dtype=dtype)\n"
        "    if hasattr(values, 'values'):\n"
        "        values = values.values\n"
        "    return np.asarray(values, dtype=dtype).reshape(-1)\n\n"
    )
    body = (
        "    for code, df in hist.items():\n"
        "        closes = _extract_history_field(df, 'close', float)\n"
        "        if len(closes) > 0:\n"
        "            log.info(str(closes[-1]))"
    )
    report = validate_strategy(
        _design_22(), source_22(body, prefix=helper), target_profile="ptrade")
    assert report["status"] == "BLOCKED", report
    blocked_rule_ids = {item["rule_id"] for item in report["issues"] if item["severity"] == "BLOCK"}
    assert "PTRADE-IS-DICT-BAN" in blocked_rule_ids


def test_audit_markers_in_comments_do_not_fool_the_gate():
    # The audit strings exist only in a comment; no log.* call emits them, so
    # the R5 audit gate must still BLOCK.
    helper = (
        "def _extract_history_field(history_item, field, dtype=float):\n"
        "    return np.asarray([], dtype=dtype)\n\n"
    )
    body = (
        "    for code, df in hist.items():\n"
        "        closes = _extract_history_field(df, 'close', float)\n"
        "    # QS_REBALANCE_AUDIT date= selected= buy_submitted="
    )
    source = source_22(body, prefix=helper)
    source = source.replace(
        "    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%s' % ('20260720_1', '2026-07-20', 20))",
        "    return None  # QS_PORTFOLIO_AUDIT date= positions=")
    source = source.replace(
        "    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%s buy_submitted=%s' % ('20260720_1', '2026-07-20', 20, 20))",
        "    return None")
    rules = block_rules_22(source)
    assert rules & {"R5-AUDIT-LOG-MISSING", "R5-REBALANCE-AUDIT-INCOMPLETE"}
