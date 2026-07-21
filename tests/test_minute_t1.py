"""PR4 契约测试：分钟 T+1 与 ETF T+0（黄金用例 3 + 决策 4 隔离）。

验证目标：
1. 黄金 3：T 日分钟买入股票，当日禁止卖出（T+1）
2. 决策 4 隔离：日线 Profile 下 ETF 仍 T+1（守护黄金基线）
3. ETF T+0（etf_t0=True）：当日买入当日可卖
"""
import pytest
from tests.conftest import minute_row, daily_row, make_providers

DAY = "2026-01-05"


def _stock_bars():
    return [minute_row("600000", DAY, h, m, 10.0) for h, m in
            [(9, 35), (10, 0), (14, 0), (14, 55), (15, 0)]]


def _stock_daily(pctchg=0.0):
    return [daily_row("600000", DAY, 10.0, pctchg=pctchg)]


def _etf_bars():
    return [minute_row("159870", DAY, h, m, p) for h, m, p in
            [(9, 35, 1.0), (10, 0, 1.1), (14, 55, 1.5), (15, 0, 1.6)]]


def _etf_daily():
    return [daily_row("159870", DAY, 1.0, preclose=0.95, pctchg=0.0526)]   # 5.26% 非涨停


def test_stock_minute_buy_then_same_day_sell_blocked(build_db, cal):
    """T 日 09:35 分钟买入股票，14:55 卖出被 T+1 阻断（can_sell=0）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(stock_minutes=_stock_bars(), stock_daily=_stock_daily())
    state = {'sell_status': None}

    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        hhmm = context.current_dt.hour * 100 + context.current_dt.minute
        if hhmm == 935:
            order_target_value("600000.SH", 10000)
        elif hhmm == 1455 and state['sell_status'] is None:
            o = order_target_value("600000.SH", 0)
            state['sell_status'] = o.status if o else None

    strategy = {'initialize': lambda c: None, 'handle_data': handle_data}
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close",
        providers=make_providers(db, cal),
    )
    engine.run()
    assert state['sell_status'] in (None, "rejected")


def test_daily_profile_etf_remains_t1():
    """日线 Profile（默认）下 etf_t0 强制为 False（守护黄金基线）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start=DAY, end=DAY,
        engine_profile="daily-bar-v1", etf_t0=True,
    )
    assert engine.etf_t0 is False


def test_minute_profile_etf_t0_default_false():
    """分钟 Profile 默认 etf_t0=False"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close",
    )
    assert engine.etf_t0 is False


def test_etf_t0_allows_same_day_buy_sell(build_db, cal):
    """etf_t0=True：ETF 09:35 买入，14:55 可卖出（T+0）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily())
    state = {'sold': False}

    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        hhmm = context.current_dt.hour * 100 + context.current_dt.minute
        if hhmm == 935:
            order_target_value("159870.SZ", 10000)
        elif hhmm == 1455:
            o = order_target_value("159870.SZ", 0)
            if o is not None and o.status == "filled":
                state['sold'] = True

    strategy = {'initialize': lambda c: None, 'handle_data': handle_data}
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close", etf_t0=True,
        providers=make_providers(db, cal),
    )
    engine.run()
    assert state['sold'] is True
