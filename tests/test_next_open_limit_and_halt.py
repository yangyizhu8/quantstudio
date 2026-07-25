"""PR2 契约测试：next_open T+1 拒单边界（涨跌停/停牌/资金不足）。

验证目标（主计划 7.15 验收标准）：
- T+1 涨停时买单拒绝（locked_cash 退回）
- T+1 停牌时不成交（suspendFlag=1 或 volume=0）
- T+1 跌停时卖单拒绝（pending_sell_shares 归还）
- T+1 资金不足（跳空放大买单需求）→ 整单拒单 insufficient_cash，不缩单

关键：拒单时预扣资源必须原路归还，不丢失。
"""
import pytest
import pandas as pd


def _make_next_open_engine(cash=100_000):
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-01", end="2026-01-31",
        match_price_mode="next_open",
    )
    engine.account.cash = cash
    engine._current_date_str = "2026-01-05"  # T 日（created_dt 来源）
    return engine


def _setup_pending_buy(engine, target_value=10_000, price=10.0):
    """构造一个已入队的 pending 买单"""
    engine._t_day_close_prices = {"600000.SH": price}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine._create_pending_order("600000.SH", instruction="target_value",
                                  target_value=target_value)
    return engine._pending_orders[0]


def _setup_pending_sell(engine, shares=1000, price=10.0):
    """构造一个已入队的 pending 卖单（含底仓）"""
    from quantstudio.backtest.backtest_engine import Position
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=shares, avg_cost=price, can_sell=shares)
    engine._t_day_close_prices = {"600000.SH": price}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine._create_pending_order("600000.SH", instruction="sell_all")
    # 模拟主循环 T+1 解锁
    pos = engine.account.positions["600000.SH"]
    pos.can_sell = pos.volume
    return engine._pending_orders[0]


# ========== T+1 涨停：买单拒单 ==========

def test_drain_rejects_buy_on_t1_limit_up():
    """T+1 涨停：买单拒单 reason=limit_up_blocked，locked_cash 退回"""
    engine = _make_next_open_engine(cash=100_000)
    po = _setup_pending_buy(engine, target_value=10_000, price=10.0)
    locked_before_drain = engine.account.locked_cash
    cash_before_drain = engine.account.cash

    # T+1 涨停：close/preClose = +10%
    t1_data = pd.DataFrame({
        'code': ['600000'],
        'open': [11.0], 'close': [11.0], 'preClose': [10.0],
        'volume': [100], 'suspendFlag': [0],
    })
    engine._drain_pending_orders(t1_data, "2026-01-06", {"600000.SH": 11.0})

    assert po.status == "rejected"
    assert po.reason == "limit_up_blocked"
    # locked_cash 原路退回 cash
    assert engine.account.locked_cash == 0
    assert engine.account.cash == pytest.approx(cash_before_drain + locked_before_drain)
    # 无成交记录
    assert len(engine.result.trade_records) == 0


# ========== T+1 跌停：卖单拒单 ==========



def test_drain_uses_open_gap_not_future_close_for_limit_check():
    """Open below limit but close at limit must fill; next-open cannot use future close."""
    engine = _make_next_open_engine(cash=100_000)
    po = _setup_pending_buy(engine, target_value=10_000, price=10.0)
    t1_data = pd.DataFrame({
        "code": ["600000"],
        "open": [10.5], "close": [11.0], "preClose": [10.0],
        "volume": [100000], "suspendFlag": [0],
    })
    engine._drain_pending_orders(t1_data, "2026-01-06", {"600000.SH": 10.5})
    assert po.status == "filled"
    assert po.reason == ""
    assert len(engine.result.trade_records) == 1

def test_drain_rejects_sell_on_t1_limit_down():
    """T+1 跌停：卖单拒单 reason=limit_down_blocked，pending_sell_shares 归还"""
    engine = _make_next_open_engine(cash=10_000)
    po = _setup_pending_sell(engine, shares=1000, price=10.0)

    # T+1 跌停：close/preClose = -10%
    t1_data = pd.DataFrame({
        'code': ['600000'],
        'open': [9.0], 'close': [9.0], 'preClose': [10.0],
        'volume': [100], 'suspendFlag': [0],
    })
    engine._drain_pending_orders(t1_data, "2026-01-06", {"600000.SH": 9.0})

    assert po.status == "rejected"
    assert po.reason == "limit_down_blocked"
    pos = engine.account.positions["600000.SH"]
    # pending_sell_shares 归还，volume 不变
    assert pos.pending_sell_shares == 0
    assert pos.volume == 1000
    assert len(engine.result.trade_records) == 0


# ========== T+1 停牌：suspendFlag=1 ==========

def test_drain_rejects_on_t1_halt_via_suspend_flag():
    """T+1 停牌（suspendFlag=1）：拒单 reason=halted，预扣归还"""
    engine = _make_next_open_engine(cash=100_000)
    po = _setup_pending_buy(engine, target_value=10_000, price=10.0)
    locked_before = engine.account.locked_cash

    t1_data = pd.DataFrame({
        'code': ['600000'],
        'open': [10.0], 'close': [10.0], 'preClose': [10.0],
        'volume': [0], 'suspendFlag': [1],
    })
    engine._drain_pending_orders(t1_data, "2026-01-06", {"600000.SH": 10.0})

    assert po.status == "rejected"
    assert po.reason == "halted"
    assert engine.account.locked_cash == 0
    assert engine.account.cash == pytest.approx(100_000)


def test_drain_rejects_on_t1_halt_via_zero_volume():
    """T+1 停牌（volume=0，suspendFlag 未设）：拒单 reason=halted"""
    engine = _make_next_open_engine(cash=100_000)
    po = _setup_pending_buy(engine, target_value=10_000, price=10.0)

    t1_data = pd.DataFrame({
        'code': ['600000'],
        'open': [10.0], 'close': [10.0], 'preClose': [10.0],
        'volume': [0], 'suspendFlag': [0],
    })
    engine._drain_pending_orders(t1_data, "2026-01-06", {"600000.SH": 10.0})

    assert po.status == "rejected"
    assert po.reason == "halted"
    assert engine.account.locked_cash == 0


# ========== T+1 资金不足（跳空放大）：整单拒单不缩单 ==========

def test_drain_rejects_buy_when_t1_gap_exceeds_cash_no_partial():
    """T+1 跳空导致固定股数买单成本超过可用资金 → 整单拒单 insufficient_cash_or_rounding，不缩单。

    场景：T 日 close=10，buy_shares=1000，预扣 ~10035（1000×10+佣金+过户费），现金 10500 够预扣。
    T+1 open=10.6（preClose=10，close=10.6，涨幅 6% 不触发涨停），drain 释放预扣后 cash≈10500，
    但 1000 股 × 10.6 + 成本 ≈ 10635 > 10500 → _execute_buy 返回 vol=0 → 整单拒单，不缩单。
    """
    engine = _make_next_open_engine(cash=10_500)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    # 用固定股数买单（buy_shares），T+1 价格跳高后整手成本超过释放的现金
    engine._create_pending_order("600000.SH", instruction="buy_shares", shares=1000)
    po = engine._pending_orders[0]

    t1_data = pd.DataFrame({
        'code': ['600000'],
        'open': [10.6], 'close': [10.6], 'preClose': [10.0],
        'volume': [100000], 'suspendFlag': [0],
    })
    engine._drain_pending_orders(t1_data, "2026-01-06", {"600000.SH": 10.6})

    # vol=0 → rejected，无部分成交
    assert po.status == "rejected"
    assert "insufficient" in po.reason
    # locked_cash 原路退回
    assert engine.account.locked_cash == 0
    # 无成交持仓
    pos = engine.account.positions.get("600000.SH")
    assert pos is None or pos.volume == 0
    assert len(engine.result.trade_records) == 0
