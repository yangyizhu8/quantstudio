"""PR2 契约测试：next_open 净值时序（T 日净值不含 T+1 成交）。

验证目标（主计划 7.13 "T 日净值不包含未执行订单" + 7.15 "T 日现金和持仓不变"）：
1. T 日创建买单后，nav（总资产）与无单时一致
2. T 日 nav_history['cash'] 仅含自由现金（locked_cash 不重复计入 nav）
3. T 日 cash + locked_cash 守恒
4. T+1 drain 成交后，T+1 nav 才反映持仓变化

这是 ETF 漂移事故教训的直接防护：pending order 不得改变 T 日净值。
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


# ========== 总资产属性含 locked_cash ==========

def test_account_total_asset_includes_locked_cash():
    """Account.total_asset 和 total_asset_at_price 都含 locked_cash"""
    from quantstudio.backtest.backtest_engine import Account, Position
    acc = Account(cash=80_000, locked_cash=20_000)
    acc.positions["600000.SH"] = Position(
        code="600000.SH", volume=100, avg_cost=10.0)
    # total_asset = cash + locked_cash + market_value(用成本)
    assert acc.total_asset == 80_000 + 20_000 + 100 * 10.0
    # total_asset_at_price = cash + locked_cash + market_value(用价)
    assert acc.total_asset_at_price({"600000.SH": 12.0}) == 80_000 + 20_000 + 100 * 12.0


def test_account_total_asset_zero_locked_cash_matches_legacy():
    """locked_cash=0 时 total_asset 与 legacy 数值一致（close/open 模式回归保护）"""
    from quantstudio.backtest.backtest_engine import Account, Position
    acc = Account(cash=100_000)  # locked_cash 默认 0
    acc.positions["600000.SH"] = Position(
        code="600000.SH", volume=100, avg_cost=10.0)
    # legacy 公式：cash + market_value
    assert acc.total_asset == 100_000 + 100 * 10.0


# ========== T 日净值守恒 ==========

def test_t_day_nav_unchanged_by_pending_order():
    """T 日创建买单：total_asset_at_price 在创建前后不变（locked_cash 计入总额）"""
    engine = _make_next_open_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    prices = {"600000.SH": 10.0}

    nav_before = engine.account.total_asset_at_price(prices)
    engine._create_pending_order("600000.SH", instruction="target_value", target_value=10_000)
    nav_after = engine.account.total_asset_at_price(prices)

    assert nav_after == pytest.approx(nav_before)


def test_t_day_cash_plus_locked_cash_is_conserved():
    """T 日 cash + locked_cash 守恒（买单预扣总额等于 cash 减少量）"""
    engine = _make_next_open_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    cash_before = engine.account.cash
    engine._create_pending_order("600000.SH", instruction="target_value", target_value=20_000)

    # cash 减少量 == locked_cash（守恒）
    cash_decrease = cash_before - engine.account.cash
    assert cash_decrease == pytest.approx(engine.account.locked_cash)
    assert cash_decrease > 0


def test_t_day_volume_unchanged_by_pending_buy():
    """T 日买单：持仓 volume 不变（T 日不记账持仓）"""
    engine = _make_next_open_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine._create_pending_order("600000.SH", instruction="target_value", target_value=10_000)

    pos = engine.account.positions.get("600000.SH")
    # T 日不应建立持仓
    assert pos is None or pos.volume == 0


# ========== T+1 成交后净值才变化 ==========

def test_t1_nav_reflects_fill_after_drain():
    """T+1 drain 成交后，持仓市值才进入总资产"""
    engine = _make_next_open_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine._create_pending_order("600000.SH", instruction="target_value", target_value=10_000)

    # T+1 drain
    t1_data = pd.DataFrame({
        'code': ['600000'],
        'open': [10.0], 'close': [10.0], 'preClose': [10.0],
        'volume': [100000], 'suspendFlag': [0],
    })
    engine._drain_pending_orders(t1_data, "2026-01-06", {"600000.SH": 10.0})

    # 成交后：locked_cash 归零，持仓建立
    assert engine.account.locked_cash == 0
    pos = engine.account.positions["600000.SH"]
    assert pos is not None and pos.volume > 0
    # 总资产 = cash + 持仓市值（成交后 cash 剩余 + 持仓 ≈ 原本金，含成本损耗）
    total = engine.account.total_asset_at_price({"600000.SH": 10.0})
    assert total < 100_000  # 因佣金/过户费，总资产略降
