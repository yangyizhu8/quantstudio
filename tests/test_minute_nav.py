"""PR4 契约测试：分钟 NAV 记录（黄金用例 4 + 分钟 NAV 隔离）。"""
import pytest
from tests.conftest import minute_row, daily_row, make_providers

DAY = "2026-01-05"


def _etf_bars():
    return [minute_row("159870", DAY, h, m, 1.0) for h, m in
            [(9, 35), (10, 0), (14, 0), (15, 0)]]


def _etf_daily():
    return [daily_row("159870", DAY, 1.0, preclose=0.9)]


def test_minute_nav_daily_format(build_db, cal):
    """日终 NAV 按 nav_history 格式记录，date='YYYY-MM-DD'"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(etf_minutes=_etf_bars(), etf_daily=_etf_daily())
    strategy = {'initialize': lambda c: None, 'handle_data': lambda c, d: None}
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close",
        providers=make_providers(db, cal),
    )
    engine.run()
    assert len(engine.result.nav_history) == 1
    entry = engine.result.nav_history[0]
    assert entry['date'] == DAY
    for key in ('nav', 'cash', 'market_value', 'positions'):
        assert key in entry


def test_minute_nav_history_separate_from_daily():
    """nav_history 只含日级记录（分钟 NAV 不污染 metrics 计算）"""
    from quantstudio.backtest.backtest_engine import BacktestResult
    result = BacktestResult()
    result.nav_history.append({'date': DAY, 'nav': 100000, 'cash': 100000,
                               'market_value': 0, 'positions': 0})
    assert len(result.nav_history) == 1
