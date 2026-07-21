"""PR4 契约测试：分钟订单执行（黄金用例 1 + 清理项 15:00 bar 下单）。

验证目标：
1. 分钟即时撮合用 bar.close（end-labeled）
2. 日内多次下单（09:35 买入、14:55 卖出 ETF）
3. 15:00 bar 下单按收盘价成交（清理项）
4. 分钟强制即时，不走 pending queue
"""
import pytest
from tests.conftest import minute_row, daily_row, make_providers

DAY = "2026-01-05"


def _etf_bars():
    """159870 边界 bar，价格随时间变化"""
    return [
        minute_row("159870", DAY, 9, 31, 0.95),
        minute_row("159870", DAY, 9, 35, 1.0),
        minute_row("159870", DAY, 10, 0, 1.1),
        minute_row("159870", DAY, 11, 30, 1.2),
        minute_row("159870", DAY, 13, 1, 1.3),
        minute_row("159870", DAY, 14, 0, 1.4),
        minute_row("159870", DAY, 14, 55, 1.5),
        minute_row("159870", DAY, 15, 0, 1.6),
    ]


def _etf_daily():
    return [daily_row("159870", DAY, 1.0, high=1.6, low=0.95, preclose=0.95, pctchg=0.0526)]   # 5.26% 非涨停


# ========== 黄金 1：09:35 买、14:55 卖 ETF ==========

def test_minute_buy_sell_etf_at_scheduled_times(build_db, cal):
    """09:35 买入按 09:35 bar.close 成交；14:55 卖出按 14:55 bar.close 成交"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily())
    state = {'bought_price': None, 'sold_price': None}

    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        hhmm = context.current_dt.hour * 100 + context.current_dt.minute
        if hhmm == 935 and state['bought_price'] is None:
            o = order_target_value("159870.SZ", 10000)
            if o is not None and o.status == "filled":
                state['bought_price'] = o.price
        elif hhmm == 1455 and state['sold_price'] is None:
            o = order_target_value("159870.SZ", 0)
            if o is not None and o.status == "filled":
                state['sold_price'] = o.price

    strategy = {'initialize': lambda c: None, 'handle_data': handle_data}
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close", etf_t0=True,
        providers=make_providers(db, cal),
    )
    engine.run()
    assert state['bought_price'] is not None
    assert abs(state['bought_price'] - 1.0) < 0.01
    assert state['sold_price'] is not None
    assert abs(state['sold_price'] - 1.5) < 0.01


# ========== 15:00 bar 下单按收盘价（清理项）==========

def test_minute_order_at_1500_bar_uses_close_price(build_db, cal):
    """15:00 bar 下单按该 bar close（=收盘价）成交"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily())
    state = {'price': None}

    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        hhmm = context.current_dt.hour * 100 + context.current_dt.minute
        if hhmm == 1500 and state['price'] is None:
            o = order_target_value("159870.SZ", 5000)
            if o is not None and o.status == "filled":
                state['price'] = o.price

    strategy = {'initialize': lambda c: None, 'handle_data': handle_data}
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close", etf_t0=True,
        providers=make_providers(db, cal),
    )
    engine.run()
    assert state['price'] is not None
    assert abs(state['price'] - 1.6) < 0.01   # 15:00 bar close = 1.6


# ========== 分钟强制即时，不走 pending ==========

def test_minute_profile_does_not_use_pending_queue(build_db, cal):
    """分钟 Profile 下成交后 _pending_orders 始终为空（即时撮合）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily())

    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        if context.current_dt.hour == 9 and context.current_dt.minute == 35:
            order_target_value("159870.SZ", 5000)

    strategy = {'initialize': lambda c: None, 'handle_data': handle_data}
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close", etf_t0=True,
        providers=make_providers(db, cal),
    )
    engine.run()
    assert all(p.status != "pending" for p in engine._pending_orders)
