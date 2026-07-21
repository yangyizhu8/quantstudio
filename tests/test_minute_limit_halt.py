"""PR4 契约测试：分钟涨跌停与停牌（黄金用例 5）。"""
import pytest
from tests.conftest import minute_row, daily_row, make_providers

DAY = "2026-01-05"


def _bars(suspend=0):
    return [minute_row("600000", DAY, h, m, 10.0, suspend=suspend)
            for h, m in [(9, 35), (10, 0), (14, 0)]]


def _daily(pctchg, suspend=0):
    close = 10.0
    preclose = close / (1 + pctchg) if pctchg != 0.0 else 9.9
    return [daily_row("600000", DAY, close, preclose=preclose, pctchg=pctchg, suspend=suspend)]


def test_minute_limit_up_buy_rejected(build_db, cal):
    """日级 pctChg=+10%（涨停）：分钟买单拒单 reason=limit_up_blocked"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(stock_minutes=_bars(), stock_daily=_daily(0.10))
    state = {'status': None, 'reason': None}

    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        if context.current_dt.hour == 9 and context.current_dt.minute == 35:
            o = order_target_value("600000.SH", 10000)
            if o is not None:   # Order.__bool__ 对 rejected 返回 False，用 is not None 判断
                state['status'] = o.status
                state['reason'] = o.reason

    strategy = {'initialize': lambda c: None, 'handle_data': handle_data}
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close",
        providers=make_providers(db, cal),
    )
    engine.run()
    assert state['status'] == "rejected"
    assert state['reason'] == "limit_up_blocked"


def test_minute_uses_daily_pctchg_not_bar_pctchg(build_db, cal):
    """日级 pctChg=5%（非涨停）→ 买单成交"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(stock_minutes=_bars(), stock_daily=_daily(0.05))
    state = {'status': None}

    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        if context.current_dt.hour == 9 and context.current_dt.minute == 35:
            o = order_target_value("600000.SH", 10000)
            if o is not None:
                state['status'] = o.status

    strategy = {'initialize': lambda c: None, 'handle_data': handle_data}
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close",
        providers=make_providers(db, cal),
    )
    engine.run()
    assert state['status'] == "filled"


def test_minute_halt_rejected(build_db, cal):
    """停牌（suspendFlag=1）：分钟买单拒单 reason=halted"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(stock_minutes=_bars(suspend=1), stock_daily=_daily(0.0, suspend=1))
    state = {'status': None, 'reason': None}

    def handle_data(context, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        if context.current_dt.hour == 9 and context.current_dt.minute == 35:
            o = order_target_value("600000.SH", 10000)
            if o is not None:   # Order.__bool__ 对 rejected 返回 False
                state['status'] = o.status
                state['reason'] = o.reason

    strategy = {'initialize': lambda c: None, 'handle_data': handle_data}
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close",
        providers=make_providers(db, cal),
    )
    engine.run()
    assert state['status'] == "rejected"
    assert state['reason'] == "halted"
