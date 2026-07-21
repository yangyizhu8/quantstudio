"""PR2 契约测试：next_open pending order 生命周期（T 日创建 / T+1 成交）。

验证目标（主计划 7.13 任务清单 + 7.15 验收标准）：
1. T 日信号下单 → 只创建 pending order，T 日不扣 volume、不产生 trade_record
2. T 日现金转入 locked_cash（对称预扣），总资产守恒
3. T+1 drain 成交：locked_cash 归零、trade_record 日期=T+1、filled_dt=T+1
4. 成交价 = T+1 open（±滑点），与 T+1 close 明确不同（锁住 drain 成交价口径）

这些测试覆盖 _create_pending_order / _drain_pending_orders 的核心 happy-path，
是 PR2 "消除跨日穿越" 的直接验证。
"""
import pytest
import pandas as pd


# ========== 辅助构造 ==========

def _make_next_open_engine(cash=100_000):
    """构造 next_open 模式引擎（不连真实库）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-01", end="2026-01-31",
        match_price_mode="next_open",
    )
    engine.account.cash = cash
    # _create_pending_order 读取 _current_date_str 作 created_dt（T 日）
    engine._current_date_str = "2026-01-05"
    return engine


def _make_t_day_data(codes, close, open_price=None, preclose=None):
    """构造 T 日全市场快照 DataFrame"""
    return pd.DataFrame({
        'code': codes,
        'open': [open_price if open_price is not None else close] * len(codes),
        'close': [close] * len(codes),
        'preClose': [preclose if preclose is not None else close] * len(codes),
    })


# ========== T 日创建 pending order ==========

def test_create_pending_buy_locks_cash_without_changing_volume():
    """T 日买单：cash 减、locked_cash 增等额、volume 不变、trade_records 空"""
    engine = _make_next_open_engine(cash=100_000)
    # 注入 T 日 close 价（_create_pending_order 用 T 日 close 估算预扣）
    engine._t_day_close_prices = {"600000.SH": 10.0}
    # mock _next_trade_day_str：T=2026-01-05 → T+1=2026-01-06
    engine._next_trade_day_str = lambda d: "2026-01-06"

    order = engine._create_pending_order(
        "600000.SH", instruction="target_value", target_value=10_000)

    # 返回 status=pending（__bool__ False，兼容老策略）
    assert order.status == "pending"
    assert order.created_dt == "2026-01-05"
    assert bool(order) is False

    # 对称预扣：cash 减、locked_cash 增（总额守恒）
    assert engine.account.cash < 100_000
    assert engine.account.locked_cash > 0
    assert engine.account.cash + engine.account.locked_cash == 100_000  # 守恒

    # T 日 volume 不变、无 trade_record
    pos = engine.account.positions.get("600000.SH")
    assert pos is None or pos.volume == 0
    assert len(engine.result.trade_records) == 0

    # pending order 入队
    assert len(engine._pending_orders) == 1
    po = engine._pending_orders[0]
    assert po.status == "pending"
    assert po.created_dt == "2026-01-05"
    assert po.scheduled_dt == "2026-01-06"
    assert po.direction == "buy"


def test_create_pending_sell_locks_can_sell_without_changing_volume():
    """T 日卖单：pending_sell_shares 预扣、volume 不变、cash 不变、trade_records 空"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_next_open_engine(cash=10_000)
    # 建底仓（昨日买入，今日可卖）
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    order = engine._create_pending_order(
        "600000.SH", instruction="sell_all")

    assert order.status == "pending"
    assert order.direction == "sell"

    pos = engine.account.positions["600000.SH"]
    # volume 不变，pending_sell_shares 预扣
    assert pos.volume == 1000
    assert pos.pending_sell_shares == 1000
    # cash 不变、无 trade_record
    assert engine.account.cash == 10_000
    assert engine.account.locked_cash == 0
    assert len(engine.result.trade_records) == 0


# ========== T+1 drain 成交 ==========

def test_drain_pending_buy_fills_at_t1_open_not_close():
    """T+1 drain：买单成交、locked_cash 归零、成交价=T+1 open（非 close）、日期=T+1"""
    engine = _make_next_open_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    # T 日创建
    engine._create_pending_order("600000.SH", instruction="target_value", target_value=10_000)
    po = engine._pending_orders[0]

    # T+1 数据：open=11.0（成交价），close=10.5（不触发涨停：10.5/10-1=5%）。
    # open 与 close 明显不同，锁住"成交价=open≠close"口径。
    t1_data = pd.DataFrame({
        'code': ['600000'],
        'open': [11.0], 'close': [10.5], 'preClose': [10.0],
        'volume': [100000], 'suspendFlag': [0],
    })
    t1_open_prices = {"600000.SH": 11.0}

    engine._drain_pending_orders(t1_data, "2026-01-06", t1_open_prices)

    assert po.status == "filled"
    assert po.filled_dt == "2026-01-06"
    assert po.filled_amount > 0
    # 成交价基于 T+1 open=11.0（±滑点），与 T+1 close=10.5 明确不同（锁住成交价口径）
    assert abs(po.price - 11.0) < 0.5  # 在 open±滑点附近
    assert po.price != 10.5  # 不是 close 价

    # locked_cash 归零（已转为持仓）
    assert engine.account.locked_cash == 0
    # 持仓已建立
    pos = engine.account.positions["600000.SH"]
    assert pos.volume == po.filled_amount
    # trade_record 日期 = T+1
    assert len(engine.result.trade_records) == 1
    assert engine.result.trade_records[0]['date'] == "2026-01-06"
    assert engine.result.trade_records[0]['action'] == "buy"


def test_drain_pending_sell_fills_at_t1_open_and_records_t1_date():
    """T+1 drain：卖单成交、pending_sell_shares 归零、trade_record 日期=T+1"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_next_open_engine(cash=10_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    # T 日创建卖单
    engine._create_pending_order("600000.SH", instruction="sell_all")

    # T+1 解锁（模拟主循环顺序：drain 之前已解锁）
    pos = engine.account.positions["600000.SH"]
    pos.can_sell = pos.volume

    t1_data = pd.DataFrame({
        'code': ['600000'],
        'open': [11.0], 'close': [12.0], 'preClose': [10.0],
        'volume': [100000], 'suspendFlag': [0],
    })
    t1_open_prices = {"600000.SH": 11.0}

    engine._drain_pending_orders(t1_data, "2026-01-06", t1_open_prices)

    po = engine._pending_orders[0] if engine._pending_orders else None
    # 成交后该 pending 已移出队列
    assert po is None or po.status == "filled"
    # 持仓已减、pending_sell_shares 归零
    pos = engine.account.positions["600000.SH"]
    assert pos.volume == 0
    assert pos.pending_sell_shares == 0
    # cash 增加（卖出所得）
    assert engine.account.cash > 10_000
    # trade_record 日期 = T+1
    assert len(engine.result.trade_records) == 1
    assert engine.result.trade_records[0]['date'] == "2026-01-06"
    assert engine.result.trade_records[0]['action'] == "sell"


# ========== 总资产守恒（T 日净值不受 T+1 成交影响）==========

def test_t_day_total_asset_unchanged_after_pending_buy():
    """T 日创建买单后，总资产 = cash + locked_cash + 市值，与无单时一致"""
    engine = _make_next_open_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    prices = {"600000.SH": 10.0}

    total_before = engine.account.total_asset_at_price(prices)
    engine._create_pending_order("600000.SH", instruction="target_value", target_value=10_000)
    total_after = engine.account.total_asset_at_price(prices)

    assert total_after == pytest.approx(total_before)


# ========== close/open 模式零触达（PR2 隔离契约）==========

def test_close_mode_account_has_no_locked_cash():
    """close 模式：account.locked_cash 恒为 0（PR2 隔离契约）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-01", end="2026-01-02",
        match_price_mode="close",
    )
    assert engine.account.locked_cash == 0


def test_open_mode_account_has_no_locked_cash():
    """open 模式：account.locked_cash 恒为 0（PR2 隔离契约）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-01", end="2026-01-02",
        match_price_mode="open",
    )
    assert engine.account.locked_cash == 0


def test_close_mode_immediate_execute_does_not_touch_locked_cash():
    """close 模式 _immediate_execute 成交后 locked_cash 仍为 0、pending_sell_shares 仍为 0。

    这锁定 close/open 即时执行路径完全不触碰 PR2 的预扣字段，
    是"51 项核心回归字节级不变"隔离契约的源码级防护。"""
    from quantstudio.backtest.backtest_engine import BacktestEngine, Position
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-01", end="2026-01-02",
        match_price_mode="close",
    )
    engine.account.cash = 100_000
    prices = {"600000.SH": 10.0}
    curr_data = pd.DataFrame({'code': ['600000'], 'close': [10.0], 'preClose': [10.0]})

    # close 模式买入
    engine._immediate_execute("600000.SH", target_value=10_000,
                              prices=prices, date="2026-01-05", curr_data=curr_data)
    assert engine.account.locked_cash == 0
    pos = engine.account.positions.get("600000.SH")
    assert pos is not None and pos.pending_sell_shares == 0

    # close 模式卖出（先解锁）
    pos.can_sell = pos.volume
    engine._immediate_execute("600000.SH", target_value=0,
                              prices=prices, date="2026-01-05", curr_data=curr_data)
    assert engine.account.locked_cash == 0


def test_engine_semantics_version_reflects_match_price_mode():
    """engine_semantics_version：close/open=legacy，next_open=0.2.0-next_open_pending"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    for mode, expected in [("close", "0.1.0-legacy"),
                            ("open", "0.1.0-legacy"),
                            ("next_open", "0.2.0-next_open_pending")]:
        engine = BacktestEngine(
            db_path="/tmp/test.db", strategy={},
            start="2026-01-01", end="2026-01-02",
            match_price_mode=mode,
        )
        assert engine.engine_semantics_version == expected
