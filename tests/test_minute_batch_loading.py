"""PR4 真实数据修复回归测试（2026-07-22）：_load_minute_snapshots 批量化。

背景：原逐 code 循环（5525 只 × 4 次 DB 调用/日）在真实全 universe 上导致 duckdb
C 扩展 GIL 累积崩溃（Fatal PyEval_SaveThread）。改为批量查询（query_minute_bars_by_range_batch，
一次 SQL per 表）。本测试锁定三条契约：

1. 查询计数：全 universe 加载的 DB 往返 ≤ 2 次（与 universe 大小无关）
2. 结果等价：批量路径与逐 code 路径返回相同的 (code, time) 集合
3. 生命周期不退化：合成全 universe 分钟 smoke，生命周期调用次数正确（initialize 1 /
   before_trading_start 1 / handle_data = bar 数 / after_trading_end 1）
"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from tests.conftest import minute_row, daily_row, make_providers


DAY = "2026-01-05"


def _make_universe(n_codes=5, bars_per_code=None):
    """构造 n 只 code 的分钟 + 日线数据。

    bars_per_code: 每只 code 的 bar 列表；None 则用默认 7 bar。
    返回 (stock_minutes_rows, stock_daily_rows, codes)。
    """
    if bars_per_code is None:
        bars = [(9, 31), (9, 32), (10, 0), (11, 30), (13, 1), (14, 0), (15, 0)]
    else:
        bars = bars_per_code
    codes = [f"60000{i}" for i in range(n_codes)]
    sm_rows = []
    sd_rows = []
    for code in codes:
        for h, m in bars:
            sm_rows.append(minute_row(code, DAY, h, m, 10.0))
        sd_rows.append(daily_row(code, DAY, 10.0, pctchg=0.0))
    return sm_rows, sd_rows, codes


# ========== 断言 1：查询计数 ≤ 2（与 universe 大小无关）==========

@pytest.mark.parametrize("n_codes", [1, 3, 5])
def test_batch_query_count_independent_of_universe_size(build_db, cal, n_codes):
    """批量加载的 DB 往返 ≤ 2 次，与 universe 大小无关。

    对比：原逐 code 路径 5 只 code = 20 次调用，5525 只 = 2.2 万次。
    参数化：每个 n_codes 独立 tmp db（避免累积主键冲突）。
    """
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess

    sm_rows, sd_rows, codes = _make_universe(n_codes=n_codes)
    db = build_db(stock_minutes=sm_rows, stock_daily=sd_rows)
    try:
        da = DuckDBDataAccess(str(db))
        # spy：包装 _get_conn 返回的连接，计数 execute 调用
        real_conn = da._get_conn()
        call_count = [0]

        class _CountingConn:
            """透传连接，计数 execute（duckdb conn execute 是只读属性，不能直接 patch）。"""
            def execute(self, *args, **kwargs):
                call_count[0] += 1
                return real_conn.execute(*args, **kwargs)
            def __getattr__(self, name):
                return getattr(real_conn, name)

        da._ro_conn = _CountingConn()
        df = da.query_minute_bars_by_range_batch(
            codes, DAY, DAY, '1min', None, cal)
    finally:
        da.close()

    # 批量路径：每表 1 次 freq 检查 + 1 次主查询 = 2 次（仅 stock_minutes 一张表）
    assert call_count[0] <= 2, (
        f"universe={n_codes} 只时 DB 调用 {call_count[0]} 次，应 ≤ 2（与 universe 大小无关）。"
    )
    assert len(df) == n_codes * 7, f"结果行数应 = {n_codes}*7"


# ========== 断言 2：批量 vs 逐 code 结果等价 ==========

def test_batch_equals_iterative_result(build_db, cal):
    """批量路径与逐 code 路径返回相同的 (code, time) 集合。"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess

    sm_rows, sd_rows, codes = _make_universe(n_codes=4)
    db = build_db(stock_minutes=sm_rows, stock_daily=sd_rows)
    da = DuckDBDataAccess(str(db))

    # 批量
    batch_df = da.query_minute_bars_by_range_batch(codes, DAY, DAY, '1min', None, cal)
    batch_keys = set(zip(batch_df['code'], batch_df['time']))

    # 逐 code
    iter_keys = set()
    for code in codes:
        df = da.query_minute_bars_by_range(code, DAY, DAY, '1min', None, cal)
        for _, row in df.iterrows():
            iter_keys.add((row['code'], row['time']))

    assert batch_keys == iter_keys, (
        f"批量与逐 code 结果不一致:\n  批量独有: {batch_keys - iter_keys}\n  "
        f"逐 code 独有: {iter_keys - batch_keys}"
    )


def test_batch_skips_index_codes_silently(build_db, cal):
    """指数/可转债 code（_resolve_minute_table=None）自动跳过，不报错。"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess

    sm_rows, sd_rows, codes = _make_universe(n_codes=2)
    db = build_db(stock_minutes=sm_rows, stock_daily=sd_rows)
    da = DuckDBDataAccess(str(db))

    # 混入指数代码（000001 上证指数格式，is_index=True）
    mixed_codes = codes + ["999999"]  # 假指数码，_resolve_minute_table 返回 None
    df = da.query_minute_bars_by_range_batch(mixed_codes, DAY, DAY, '1min', None, cal)
    # 指数 code 不在结果，股票 code 在
    assert "999999" not in set(df['code']), "指数 code 应被跳过"
    assert set(df['code']) == set(codes), "结果应只含股票 code"


def test_batch_raises_when_all_empty(build_db, cal):
    """全 universe 无分钟数据时 raise FrequencyCapabilityError（契约不变）。"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    from quantstudio.backtest.providers.frequency_labels import FrequencyCapabilityError

    # 只有日线，无分钟数据
    sd_rows = [daily_row("600000", DAY, 10.0, pctchg=0.0)]
    db = build_db(stock_daily=sd_rows)
    da = DuckDBDataAccess(str(db))

    with pytest.raises(FrequencyCapabilityError):
        da.query_minute_bars_by_range_batch(["600000"], DAY, DAY, '1min', None, cal)


# ========== 断言 3：生命周期不退化 ==========

def test_minute_engine_lifecycle_with_batch_loading(build_db, cal):
    """合成全 universe 分钟 smoke：批量加载后生命周期调用次数正确。

    initialize 1 / before_trading_start 1 / handle_data = bar 数 / after_trading_end 1。
    锁定批量路径不破坏 PR4 的事件时序。
    """
    from quantstudio.backtest.backtest_engine import BacktestEngine

    sm_rows, sd_rows, codes = _make_universe(n_codes=3)
    db = build_db(stock_minutes=sm_rows, stock_daily=sd_rows)

    log = {'init': 0, 'bts': 0, 'hd': 0, 'ate': 0, 'first_dt': None, 'last_dt': None}

    def initialize(ctx):
        log['init'] += 1

    def before_trading_start(ctx, data):
        log['bts'] += 1

    def handle_data(ctx, data):
        log['hd'] += 1
        if log['first_dt'] is None:
            log['first_dt'] = str(ctx.current_dt)
        log['last_dt'] = str(ctx.current_dt)

    def after_trading_end(ctx, data):
        log['ate'] += 1

    strategy = {'initialize': initialize, 'before_trading_start': before_trading_start,
                'handle_data': handle_data, 'after_trading_end': after_trading_end}

    engine = BacktestEngine(
        db_path=str(db), strategy=strategy, start=DAY, end=DAY,
        engine_profile="minute-bar-v1", match_price_mode="close",
        providers=make_providers(db, cal),
    )
    engine.run()

    assert log['init'] == 1, "initialize 应 1 次"
    assert log['bts'] == 1, "before_trading_start 应 1 次"
    assert log['ate'] == 1, "after_trading_end 应 1 次"
    # 7 根 bar（合成数据），handle_data 应 7 次
    assert log['hd'] == 7, f"handle_data 应 7 次（7 bar），实际 {log['hd']}"
    # 首末 bar 时序正确（end-labeled: 09:31 首, 15:00 末）
    assert "09:31" in log['first_dt'], f"首 bar 应含 09:31，实际 {log['first_dt']}"
    assert "15:00" in log['last_dt'], f"末 bar 应含 15:00，实际 {log['last_dt']}"


# ========== 断言 4：缓存生效（iter_trading_days_in_range 不重复查 calendar）==========

def test_trading_days_cache_hits_on_repeated_calls(cal):
    """iter_trading_days_in_range 第二次调用同一区间应命中缓存（不调 calendar_provider）。"""
    from quantstudio.backtest.providers.intraday_windows import (
        iter_trading_days_in_range, _TRADING_DAYS_CACHE)

    # 清空缓存确保干净起点
    _TRADING_DAYS_CACHE.clear()

    call_count = [0]
    original = cal.get_trade_days

    def counting_get_trade_days(start, end):
        call_count[0] += 1
        return original(start, end)

    cal.get_trade_days = counting_get_trade_days

    # 同一区间调 3 次
    for _ in range(3):
        iter_trading_days_in_range(DAY, DAY, cal)

    # 缓存命中：calendar_provider 只应被调 1 次
    assert call_count[0] == 1, (
        f"缓存未生效：3 次调用同一区间，calendar_provider 被调 {call_count[0]} 次（应 1 次）"
    )
