from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "output" / "generated_strategies" / "smallcap_overnight_scalp_7" / "agent_workspace_v2" / "strategy.py"


def load_module():
    spec = importlib.util.spec_from_file_location("smallcap_agent_v2_test", STRATEGY)
    module = importlib.util.module_from_spec(spec)
    module.g = SimpleNamespace()
    module.log = SimpleNamespace(info=lambda *args, **kwargs: None)
    spec.loader.exec_module(module)
    return module


def initialize_without_runtime(module):
    module.set_benchmark = lambda *args, **kwargs: None
    module.set_commission = lambda *args, **kwargs: None
    module.set_slippage = lambda *args, **kwargs: None
    module.run_daily = lambda *args, **kwargs: None
    module.initialize(SimpleNamespace())


def clear_status(module):
    module.get_stock_status = lambda stocks, **kwargs: {code: False for code in stocks}


def minute_history(code, open_price=9.8, close_price=9.9, volume=1000.0):
    return {code: pd.DataFrame({"open": [open_price], "close": [close_price], "volume": [volume]})}


def test_runtime_state_is_idempotent_and_survives_initialize_failure():
    module = load_module()
    module.g.batches = {"existing": ["600000"]}
    module.set_benchmark = lambda *args, **kwargs: None
    module.set_commission = lambda *args, **kwargs: None
    module.set_slippage = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("broker failure"))
    module.run_daily = lambda *args, **kwargs: None
    try:
        module.initialize(SimpleNamespace())
    except RuntimeError:
        pass
    assert module.g.batches == {"existing": ["600000"]}
    assert isinstance(module.g.pending_exits, set)
    module._ensure_runtime_state()
    assert module.g.batches == {"existing": ["600000"]}


def test_daily_feature_uses_portable_ema_and_four_day_amplitude():
    module = load_module()
    initialize_without_runtime(module)
    close = np.linspace(8.0, 10.0, 60)
    high = close * 1.01
    low = close * 0.99
    volume = np.full(60, 10000.0)
    frame = pd.DataFrame({"close": close, "high": high, "low": low, "volume": volume})
    module.get_history = lambda *args, **kwargs: {"600000.SS": frame}
    feature = module._completed_daily_feature("600000.SS")
    assert feature is not None
    expected_amp = (high[-4:].max() - low[-4:].min()) / low[-4:].min()
    expected_ema = pd.Series(close).ewm(span=5, adjust=False).mean().iloc[-1]
    assert abs(feature["amplitude4"] - expected_amp) < 1e-12
    assert abs(feature["ema5"] - expected_ema) < 1e-12



def test_listing_age_uses_one_batch_get_stock_info_call():
    module = load_module()
    initialize_without_runtime(module)
    calls = []

    def stock_info(stocks, field=None):
        calls.append((list(stocks), field))
        return {code: {"listed_date": "2020-01-01"} for code in stocks}

    module.get_stock_info = stock_info
    stocks = ["600000.SS", "000001.SZ"]
    result = module._filter_by_listing_age(stocks, pd.Timestamp("2026-07-02"), 365)
    assert result == stocks
    assert calls == [(stocks, ["listed_date"])]

def test_buy_batch_targets_seven_percent_and_uses_order_reconciliation():
    module = load_module()
    initialize_without_runtime(module)
    module.g.preopen_candidates = [
        {"code": f"600{i:03d}.SS", "float_value": float(i), "previous_low": 10.0}
        for i in range(7)
    ]
    clear_status(module)
    positions = {}
    module.get_history = lambda *args, **kwargs: minute_history(kwargs["security_list"])
    module.get_position = lambda code: positions.get(
        code, SimpleNamespace(amount=0, enable_amount=0, market_value=0.0))
    module.get_open_orders = lambda **kwargs: []
    module.get_order = lambda order_id: None
    orders = []

    def submit(code, value):
        orders.append((code, value))
        positions[code] = SimpleNamespace(amount=700, enable_amount=0, market_value=value)
        return "order-" + code

    module.order_target_value = submit
    context = SimpleNamespace(
        current_dt=pd.Timestamp("2026-07-02 09:31:00"),
        portfolio=SimpleNamespace(cash=100000.0, total_value=100000.0, positions=positions),
    )
    module.buy_today_batch(context)
    assert len(orders) == 7
    assert all(abs(value - 7000.0) < 1e-9 for _, value in orders)
    assert len(module.g.batches["2026-07-02"]) == 7


def test_due_exit_sells_only_t1_enabled_quantity():
    module = load_module()
    initialize_without_runtime(module)
    module.g.batches = {"2026-07-01": ["600000"]}
    position = SimpleNamespace(amount=1400, enable_amount=700, market_value=14000.0)
    module.get_position = lambda code: position
    module.get_open_orders = lambda **kwargs: []
    module.get_order = lambda order_id: None
    clear_status(module)
    submitted = []

    def submit(code, amount):
        submitted.append((code, amount))
        position.amount -= abs(amount)
        position.enable_amount = 0
        return "exit-1"

    module.order = submit
    context = SimpleNamespace(current_dt=pd.Timestamp("2026-07-02 10:30:00"))
    module.sell_due_batches(context)
    assert submitted == [("600000.SS", -700)]
    assert "2026-07-01" not in module.g.batches
    assert "600000" not in module.g.pending_exits


def test_failed_exit_remains_pending_without_false_fill():
    module = load_module()
    initialize_without_runtime(module)
    module.g.batches = {"2026-07-01": ["600000"]}
    position = SimpleNamespace(amount=700, enable_amount=700, market_value=7000.0)
    module.get_position = lambda code: position
    module.get_open_orders = lambda **kwargs: []
    clear_status(module)
    module.order = lambda *args, **kwargs: None
    context = SimpleNamespace(current_dt=pd.Timestamp("2026-07-02 10:30:00"))
    module.sell_due_batches(context)
    assert "600000" in module.g.pending_exits
    assert module.g.batches["2026-07-01"] == ["600000"]


def test_initialize_registers_confirmed_schedules_and_exact_slippage_keyword():
    module = load_module()
    calls = []
    module.set_benchmark = lambda *args, **kwargs: None
    module.set_commission = lambda *args, **kwargs: None
    module.set_slippage = lambda *args, **kwargs: calls.append(("slippage", kwargs))
    schedules = []
    module.run_daily = lambda context, callback, time: schedules.append((callback.__name__, time))
    module.initialize(SimpleNamespace())
    assert calls == [("slippage", {"slippage": 0.0})]
    assert schedules == [("buy_today_batch", "09:31"), ("sell_due_batches", "10:30")]


def test_overlap_adds_seven_percent_to_existing_position():
    module = load_module()
    initialize_without_runtime(module)
    module.g.preopen_candidates = [
        {"code": "600000.SS", "float_value": 1.0, "previous_low": 10.0}
    ]
    clear_status(module)
    module.get_history = lambda *args, **kwargs: minute_history("600000.SS")
    position = SimpleNamespace(amount=700, enable_amount=700, market_value=7000.0)
    module.get_position = lambda code: position
    module.get_open_orders = lambda **kwargs: []
    module.get_order = lambda order_id: None
    orders = []

    def submit(code, value):
        orders.append((code, value))
        position.amount = 1400
        position.market_value = value
        return "order-overlap"

    module.order_target_value = submit
    context = SimpleNamespace(
        current_dt=pd.Timestamp("2026-07-02 09:31:00"),
        portfolio=SimpleNamespace(cash=93000.0, total_value=100000.0, positions={"600000.SS": position}),
    )
    module.buy_today_batch(context)
    assert orders == [("600000.SS", 14000.0)]
