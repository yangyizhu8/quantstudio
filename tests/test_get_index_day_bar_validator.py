"""get_index_day_bar 校验器规则单测（docs/get-index-day-bar-design.md，2026-09-08）。

覆盖：
- PREOPEN-INDEX-BAR：before_trading_start 内调用 → BLOCK
- MINUTE-PROFILE-INDEX-BAR：minute-bar-v1 设计内调用 → BLOCK
- quantstudio 目标 + handle_data 内调用 → 无新规则 BLOCK（签名注册生效，无 MISSING_REUSABLE_API）
- PTrade 目标 → TARGET-LOCAL-EXTENSION-BAN（local_only_symbols 登记生效）
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_agent_strategy import validate_strategy


def design(profile="daily-bar-v1", targets=("quantstudio",)) -> dict:
    return {
        "design_version": "2.3",
        "strategy_id": "index_bar_validator_test",
        "strategy_name": "指数日线读取校验测试策略",
        "asset_class": "stock",
        "targets": list(targets),
        "engine_profile": {"profile_id": profile,
                           "bar_frequency": "1d" if profile != "minute-bar-v1" else "1m",
                           "match_price_mode": "close"},
        "market_data_contract": {
            "signal_price_adjustment": "pre",
            "execution_price_basis": "raw_trade_price",
        },
        "strategy_semantics": {"universe": "manual", "entry_rules": [], "exit_rules": [],
                               "portfolio_rules": [], "risk_rules": []},
        "timing": {
            "signal_data_cutoff": "T日跌幅 get_index_day_bar 当日已完成读数",
            "holding_semantics": "test",
            "decision_events": [{"name": "signal_check", "lifecycle": "handle_data"}],
        },
        "components": {"lifecycle_hooks": ["initialize", "handle_data"],
                       "api_groups": [], "required_apis": ["get_index_day_bar"]},
        "constraints": {"hard_filters": [], "no_lookahead": True,
                        "portable_source_required": False,
                        "runtime_state_guard_required": True},
        "approximations": [],
        "open_questions": [],
        "user_confirmations": {"strategy_semantics": True, "execution_approximations": True,
                               "component_plan": True},
        "output": {"overwrite": False},
    }


SOURCE = (
    "import numpy as np\n"
    "def _ensure_runtime_state():\n"
    "    if not hasattr(g, 'ready'):\n"
    "        g.ready = True\n"
    "def initialize(context):\n"
    "    _ensure_runtime_state()\n"
    "def handle_data(context, data):\n"
    "    _ensure_runtime_state()\n"
    "    df = get_index_day_bar('000001.SS', count=2, fields=['pctChg'])\n"
)


def block_rules(profile, targets, source=SOURCE):
    report = validate_strategy(design(profile, targets), source,
                               target_profile="quantstudio")
    return {i["rule_id"] for i in report["issues"] if i["severity"] == "BLOCK"}


def test_preopen_index_bar_blocks():
    src = SOURCE.replace(
        "def handle_data(context, data):\n    _ensure_runtime_state()\n"
        "    df = get_index_day_bar('000001.SS', count=2, fields=['pctChg'])\n",
        "def before_trading_start(context, data):\n    _ensure_runtime_state()\n"
        "    df = get_index_day_bar('000001.SS', count=2, fields=['pctChg'])\n"
        "def handle_data(context, data):\n    _ensure_runtime_state()\n")
    rules = block_rules("daily-bar-v1", ("quantstudio",), src)
    assert "PREOPEN-INDEX-BAR" in rules


def test_minute_profile_index_bar_blocks():
    rules = block_rules("minute-bar-v1", ("quantstudio",))
    assert "MINUTE-PROFILE-INDEX-BAR" in rules


def test_daily_handle_data_passes_with_registered_api():
    rules = block_rules("daily-bar-v1", ("quantstudio",))
    assert "PREOPEN-INDEX-BAR" not in rules
    assert "MINUTE-PROFILE-INDEX-BAR" not in rules
    assert "MISSING_REUSABLE_API" not in rules


def test_ptrade_target_blocked_via_local_only_symbols():
    report = validate_strategy(design("daily-bar-v1", ("quantstudio", "ptrade")),
                               SOURCE, target_profile="ptrade")
    rules = {i["rule_id"] for i in report["issues"] if i["severity"] == "BLOCK"}
    assert "TARGET-LOCAL-EXTENSION-BAN" in rules
