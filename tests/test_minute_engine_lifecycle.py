"""PR4 契约测试：分钟引擎生命周期时序（主计划 7.24）。

验证目标：
1. before_trading_start 每日 1 次（在第一根 bar 前）
2. handle_data 每 bar 1 次
3. after_trading_end 每日 1 次（在最后一根 bar 后）
4. current_dt 精确到分钟
5. ctx.current_dt 与 ctx.blotter.current_dt 双更新
6. 含 15:00 收盘 bar
"""
import pytest
import pandas as pd

from tests.conftest import minute_row, daily_row, make_providers


def _make_counting_strategy():
    """构造一个记录所有生命周期调用的策略"""
    log = {'initialize': 0, 'before_trading_start': 0, 'handle_data': 0,
           'after_trading_end': 0, 'handle_data_dts': [], 'current_dts': [], 'blotter_dts': []}

    def initialize(context):
        log['initialize'] += 1

    def before_trading_start(context, data):
        log['before_trading_start'] += 1
        log['current_dts'].append(context.current_dt)
        log['blotter_dts'].append(context.blotter.current_dt)

    def handle_data(context, data):
        log['handle_data'] += 1
        log['handle_data_dts'].append(str(context.current_dt))
        log['current_dts'].append(context.current_dt)
        log['blotter_dts'].append(context.blotter.current_dt)

    def after_trading_end(context, data):
        log['after_trading_end'] += 1

    return {'initialize': initialize, 'before_trading_start': before_trading_start,
            'handle_data': handle_data, 'after_trading_end': after_trading_end}, log


DAY = "2026-01-05"
BARS = [(9, 31), (9, 32), (10, 0), (11, 30), (13, 1), (14, 0), (15, 0)]


def _bars():
    return [minute_row("600000", DAY, h, m, 10.0) for h, m in BARS]


def _daily():
    return [daily_row("600000", DAY, 10.0, pctchg=0.0)]


# ========== 生命周期调用次数 ==========

def test_minute_engine_calls_lifecycle_correct_counts(build_db, cal):
    """分钟引擎：initialize 1 次、before_trading_start 每日 1 次、handle_data 每 bar 1 次、after_trading_end 每日 1 次"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(stock_minutes=_bars(), stock_daily=_daily())
    strategy, log = _make_counting_strategy()
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close", providers=make_providers(db, cal),
    )
    engine.run()
    assert log['initialize'] == 1
    assert log['before_trading_start'] == 1
    assert log['after_trading_end'] == 1
    assert log['handle_data'] == 7   # 7 根 bar


def test_minute_engine_current_dt_is_minute_precision(build_db, cal):
    """current_dt 精确到分钟（含 HH:MM:SS）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(stock_minutes=_bars(), stock_daily=_daily())
    strategy, log = _make_counting_strategy()
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close", providers=make_providers(db, cal),
    )
    engine.run()
    handle_dts = log['handle_data_dts']
    assert any('09:31' in dt or '10:00' in dt or '15:00' in dt for dt in handle_dts), \
        f"current_dt 应精确到分钟，got {handle_dts}"


def test_minute_engine_blotter_current_dt_double_updated(build_db, cal):
    """ctx.current_dt 与 ctx.blotter.current_dt 双更新（每 bar 同步）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    db = build_db(stock_minutes=_bars(), stock_daily=_daily())
    strategy, log = _make_counting_strategy()
    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close", providers=make_providers(db, cal),
    )
    engine.run()
    for ctx_dt, blotter_dt in zip(log['current_dts'], log['blotter_dts']):
        assert ctx_dt == blotter_dt, f"ctx.current_dt 与 blotter.current_dt 不同步: {ctx_dt} vs {blotter_dt}"


def test_minute_engine_rejects_next_open_match_price():
    """分钟 Profile 不支持 next_open（分钟即时撮合模型）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    with pytest.raises(ValueError, match="next_open"):
        BacktestEngine(
            db_path="/tmp/test.db", strategy={}, start=DAY, end=DAY,
            engine_profile="minute-bar-v1", match_price_mode="next_open",
        )
