from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from quantstudio.backtest.ptrade_api import _api
from quantstudio.backtest.strategy_runner import load_strategy


STRATEGY = "output/generated_strategies/first_cover_event_daily/agent_workspace/strategy.py"


@pytest.fixture
def module():
    _api.reset_session()
    _functions, mod = load_strategy(STRATEGY)
    mod._ensure_runtime_state()
    return mod


def context(day="2025-01-03", cash=100000.0, positions=None):
    return SimpleNamespace(
        current_dt=pd.Timestamp(day),
        portfolio=SimpleNamespace(cash=cash, positions=positions or {}),
        blotter=SimpleNamespace(current_dt=pd.Timestamp(day)),
    )


def test_embedded_snapshot_contract(module):
    assert module.EVENT_SNAPSHOT_SHA256 == "b858f35247cfaea26af114c2a90af0ed3e517fafef4d88e9e3ea870c92d89257"
    assert sum(len(events) for events in module.EVENTS_BY_DATE.values()) == 2513
    assert min(module.EVENTS_BY_DATE) == "2025-01-02"
    assert max(module.EVENTS_BY_DATE) == "2026-06-23"


def test_runtime_guard_is_idempotent(module):
    holdings = {"600000.SS": {"age": 2}}
    module.g.holdings = holdings
    module._ensure_runtime_state()
    module._ensure_runtime_state()
    assert module.g.holdings is holdings
    assert module.g.holdings["600000.SS"]["age"] == 2


def test_effective_day_duplicate_normalization(module):
    events = [
        ("600001.SS", "old", "A", "buy", "2025-01-01", 1),
        ("600001.SS", "new", "A", "buy", "2025-01-02", 2),
        ("600002.SS", "second", "B", "buy", "2025-01-02", 5),
    ]
    normalized = module._normalize_effective_events(events)
    assert [item[0] for item in normalized] == ["600001.SS", "600002.SS"]
    assert normalized[0][1] == "new"


def test_two_round_sizing_respects_cash_and_lots(module):
    shares, cost = module._size_order(33333.33, 12.34)
    assert shares >= 100
    assert shares % 100 == 0
    assert cost <= 33333.33


def test_holding_age_counts_only_subsequent_trading_callbacks(module):
    holding = {"entry_date": "2025-01-03", "last_age_date": "2025-01-03", "age": 0}
    module._advance_holding_age(holding, "2025-01-03")
    assert holding["age"] == 0
    for day in ("2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09"):
        module._advance_holding_age(holding, day)
    assert holding["age"] == 4


def test_entry_day_position_cannot_exit(module, monkeypatch):
    code = "600000.SS"
    module.g.holdings = {code: {"entry_date": "2025-01-03", "last_age_date": "2025-01-03", "age": 0}}
    module.g.last_close_date = None
    monkeypatch.setattr(module, "_reconcile_positions", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_position_amount", lambda _code: 100)
    monkeypatch.setattr(module, "get_position", lambda _code: SimpleNamespace(amount=100, enable_amount=0, avg_cost=10.0))
    monkeypatch.setattr(module, "get_history", lambda *args, **kwargs: {})
    submitted = []
    monkeypatch.setattr(module, "_submit_exit", lambda *args, **kwargs: submitted.append(args))
    module.manage_exits_at_close(context("2025-01-03"))
    assert submitted == []


def test_stop_blacklist_only_after_position_is_gone(module, monkeypatch):
    code = "600000.SS"
    module.g.holdings = {code: {"entry_date": "2025-01-02", "age": 1}}
    module.g.pending_exits = {code: {"reason": "\u6b62\u635f", "last_submit_date": "2025-01-03"}}
    monkeypatch.setattr(module, "get_position", lambda _code: SimpleNamespace(amount=0, enable_amount=0))
    monkeypatch.setattr(module, "get_open_orders", lambda **kwargs: [])
    module._reconcile_positions(context("2025-01-03"), "2025-01-03", final=True)
    assert code in module.g.blacklist
    assert code not in module.g.holdings


def test_active_batch_discards_today_events(module, monkeypatch):
    module.g.active_batch = True
    module.g.holdings = {"600000.SS": {"entry_date": "2025-01-02"}}
    monkeypatch.setattr(module, "_reconcile_positions", lambda *args, **kwargs: None)
    module.before_trading_start(context("2025-01-03"), None)
    assert module.g.prepared_events == []


def test_initialize_registers_confirmed_clocks(module, monkeypatch):
    schedules = []
    monkeypatch.setattr(module, "set_benchmark", lambda value: None)
    monkeypatch.setattr(module, "set_commission", lambda **kwargs: None)
    monkeypatch.setattr(module, "set_slippage", lambda **kwargs: None)
    monkeypatch.setattr(module, "run_daily", lambda ctx, callback, time: schedules.append((callback.__name__, time)))
    module.initialize(context())
    assert schedules == [("enter_batch_at_open", "09:31"), ("manage_exits_at_close", "15:00")]
