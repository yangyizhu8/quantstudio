"""PR2 契约测试：pending order 末日处理 + cancel_order。

验证目标（主计划 7.13 "末日订单标记 expired 或保留 pending" + 7.14 测试清单）：
1. 主循环结束仍 pending 的订单 → status=expired，预扣原路归还
2. cancel_order：status=cancelled（独立三态），精确移除目标单，精确归还对应预扣
3. scheduled_dt=None（无下一交易日，即末日下单）→ 创建即 rejected/expired，无预扣发生
4. 多日重复挂单允许累积，cancel 只移除目标单

主计划生命周期：created → pending → filled/rejected/expired/cancelled。
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


# ========== 末日 expired：主循环结束仍 pending ==========

def test_expire_remaining_pending_refunds_locked_cash():
    """主循环结束仍 pending 的买单 → expired，locked_cash 原路退回"""
    engine = _make_next_open_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine._create_pending_order("600000.SH", instruction="target_value",
                                  target_value=10_000)
    po = engine._pending_orders[0]
    cash_after_create = engine.account.cash
    locked_after_create = engine.account.locked_cash

    engine._expire_remaining_pending()

    assert po.status == "expired"
    # locked_cash 原路退回 cash
    assert engine.account.locked_cash == 0
    assert engine.account.cash == pytest.approx(cash_after_create + locked_after_create)
    # 无穿越 trade_record
    assert len(engine.result.trade_records) == 0


def test_expire_remaining_pending_refunds_pending_sell_shares():
    """主循环结束仍 pending 的卖单 → expired，pending_sell_shares 归还"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_next_open_engine(cash=10_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine._create_pending_order("600000.SH", instruction="sell_all")
    po = engine._pending_orders[0]

    engine._expire_remaining_pending()

    assert po.status == "expired"
    pos = engine.account.positions["600000.SH"]
    assert pos.pending_sell_shares == 0
    assert pos.volume == 1000  # volume 不变
    assert len(engine.result.trade_records) == 0


# ========== scheduled_dt=None：末日下单创建即拒 ==========

def test_create_pending_on_last_day_returns_rejected_no_lock():
    """末日下单（无下一交易日）：返回 rejected reason=no_next_trade_day，无预扣"""
    engine = _make_next_open_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    # 模拟末日：_next_trade_day_str 返回 None
    engine._next_trade_day_str = lambda d: None

    order = engine._create_pending_order("600000.SH", instruction="target_value",
                                          target_value=10_000)

    assert order.status == "rejected"
    assert order.reason == "no_next_trade_day"
    # 无预扣发生
    assert engine.account.locked_cash == 0
    assert engine.account.cash == 100_000
    assert len(engine._pending_orders) == 0


# ========== cancel_order：独立 cancelled 状态 + 精确归还 ==========

def test_cancel_order_sets_cancelled_status_and_refunds():
    """cancel_order：status=cancelled（独立三态），locked_cash 归还，从队列移除"""
    engine = _make_next_open_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    order = engine._create_pending_order("600000.SH", instruction="target_value",
                                          target_value=10_000)
    po = engine._pending_orders[0]
    order_id = po.order_id
    locked_before = engine.account.locked_cash

    engine._cancel_pending_order(order_id)

    assert po.status == "cancelled"  # 独立三态，非 rejected
    assert engine.account.locked_cash == 0
    # 从队列移除
    assert all(p.order_id != order_id for p in engine._pending_orders)


def test_cancel_order_sell_refunds_pending_sell_shares():
    """cancel 卖单：status=cancelled，pending_sell_shares 归还"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_next_open_engine(cash=10_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine._create_pending_order("600000.SH", instruction="sell_all")
    po = engine._pending_orders[0]

    engine._cancel_pending_order(po.order_id)

    assert po.status == "cancelled"
    pos = engine.account.positions["600000.SH"]
    assert pos.pending_sell_shares == 0


def test_cancel_only_targets_specified_order():
    """多单累积时，cancel 只移除目标单，其他单预扣不受影响"""
    engine = _make_next_open_engine(cash=200_000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 20.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    # 两笔买单
    engine._create_pending_order("600000.SH", instruction="target_value",
                                  target_value=10_000)
    engine._create_pending_order("600001.SH", instruction="target_value",
                                  target_value=10_000)
    target_po = engine._pending_orders[0]
    other_po = engine._pending_orders[1]
    locked_before = engine.account.locked_cash

    engine._cancel_pending_order(target_po.order_id)

    # 目标单 cancelled，另一单仍 pending
    assert target_po.status == "cancelled"
    assert other_po.status == "pending"
    # 队列只剩 1 个
    remaining = [p for p in engine._pending_orders if p.status == "pending"]
    assert len(remaining) == 1
    assert remaining[0].order_id == other_po.order_id
