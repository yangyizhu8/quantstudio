from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "output" / "generated_strategies" / "smallcap_overnight_scalp_7" / "agent_workspace" / "strategy.py"


def load_module():
    spec = importlib.util.spec_from_file_location("smallcap_agent_test", STRATEGY)
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


def test_daily_feature_uses_four_day_amplitude_and_ema5():
    module = load_module()
    initialize_without_runtime(module)
    close = np.linspace(8.0, 10.0, 60)
    high = close * 1.01
    low = close * 0.99
    volume = np.full(60, 10000.0)
    frame = pd.DataFrame({"close": close, "high": high, "low": low, "volume": volume})
    module.get_history = lambda *args, **kwargs: {"600000.SS": frame}
    module.EMA = lambda values, period: pd.Series(values).ewm(span=period, adjust=False).mean().to_numpy()

    feature = module._completed_daily_feature("600000.SS")
    assert feature is not None
    expected_amp = (high[-4:].max() - low[-4:].min()) / low[-4:].min()
    assert abs(feature["amplitude4"] - expected_amp) < 1e-12
    assert feature["previous_close"] > feature["ema5"]
    assert feature["previous_low"] == low[-1]


def test_buy_batch_targets_seven_percent_increment_per_name():
    module = load_module()
    initialize_without_runtime(module)
    module.g.preopen_candidates = [
        {"code": f"600{i:03d}.SS", "float_value": float(i), "previous_low": 10.0}
        for i in range(7)
    ]
    module.filter_stock_by_status = lambda stocks, **kwargs: stocks
    module.check_limit = lambda code: {code: 0}
    module.get_snapshot = lambda code, frequency="1m": {
        "open": 9.8, "last_price": 9.9, "volume": 1000,
    }
    module.get_position = lambda code: SimpleNamespace(amount=0, enable_amount=0, market_value=0.0)
    orders = []
    module.order_target_value = lambda code, value: orders.append((code, value)) or SimpleNamespace(status="filled")

    context = SimpleNamespace(
        current_dt=pd.Timestamp("2026-07-02 09:31:00"),
        portfolio=SimpleNamespace(cash=100000.0, total_value=100000.0, positions={}),
    )
    module.buy_today_batch(context)
    assert len(orders) == 7
    assert all(abs(value - 7000.0) < 1e-9 for _, value in orders)
    assert len(module.g.batches["2026-07-02"]) == 7


def test_due_exit_sells_only_t1_enabled_quantity_and_resolves_old_batch():
    module = load_module()
    initialize_without_runtime(module)
    module.g.batches = {"2026-07-01": ["600000"]}
    position = SimpleNamespace(amount=1400, enable_amount=700, market_value=14000.0)
    module.get_position = lambda code: position
    module.get_open_orders = lambda *args, **kwargs: []
    module.filter_stock_by_status = lambda stocks, **kwargs: stocks
    module.check_limit = lambda code: {code: 0}
    submitted = []

    def fake_order(code, amount):
        submitted.append((code, amount))
        position.amount -= abs(amount)
        position.enable_amount = 0
        return SimpleNamespace(status="filled")

    module.order = fake_order
    context = SimpleNamespace(current_dt=pd.Timestamp("2026-07-02 10:30:00"))
    module.sell_due_batches(context)
    assert submitted == [("600000.SS", -700)]
    assert "2026-07-01" not in module.g.batches
    assert "600000" not in module.g.pending_exits


def test_failed_limit_down_exit_remains_pending_for_retry():
    module = load_module()
    initialize_without_runtime(module)
    module.g.batches = {"2026-07-01": ["600000"]}
    module.get_position = lambda code: SimpleNamespace(amount=700, enable_amount=700, market_value=7000.0)
    module.get_open_orders = lambda *args, **kwargs: []
    module.filter_stock_by_status = lambda stocks, **kwargs: stocks
    module.check_limit = lambda code: {code: -1}
    module.order = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not order at limit-down"))
    context = SimpleNamespace(current_dt=pd.Timestamp("2026-07-02 10:30:00"))
    module.sell_due_batches(context)
    assert "600000" in module.g.pending_exits
    assert module.g.batches["2026-07-01"] == ["600000"]



def test_initialize_registers_confirmed_0931_and_1030_schedules():
    module = load_module()
    module.set_benchmark = lambda *args, **kwargs: None
    module.set_commission = lambda *args, **kwargs: None
    module.set_slippage = lambda *args, **kwargs: None
    schedules = []
    module.run_daily = lambda context, callback, time: schedules.append((callback.__name__, time))
    module.initialize(SimpleNamespace())
    assert schedules == [("buy_today_batch", "09:31"), ("sell_due_batches", "10:30")]


def test_overlap_buy_adds_new_seven_percent_batch_on_top_of_existing_position():
    module = load_module()
    initialize_without_runtime(module)
    module.g.preopen_candidates = [
        {"code": "600000.SS", "float_value": 1.0, "previous_low": 10.0},
    ]
    module.filter_stock_by_status = lambda stocks, **kwargs: stocks
    module.check_limit = lambda code: {code: 0}
    module.get_snapshot = lambda code, frequency="1m": {
        "open": 9.8, "last_price": 9.9, "volume": 1000,
    }
    old_position = SimpleNamespace(amount=700, enable_amount=700, market_value=7000.0)
    module.get_position = lambda code: old_position
    orders = []
    module.order_target_value = lambda code, value: orders.append((code, value)) or SimpleNamespace(status="filled")
    context = SimpleNamespace(
        current_dt=pd.Timestamp("2026-07-02 09:31:00"),
        portfolio=SimpleNamespace(
            cash=93000.0,
            total_value=100000.0,
            positions={"600000.SS": old_position},
        ),
    )
    module.buy_today_batch(context)
    assert orders == [("600000.SS", 14000.0)]
