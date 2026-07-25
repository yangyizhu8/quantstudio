"""性能回归：DuckDBMarketDataProvider.preload 不得创建 _preload_daily 全市场内存缓存。

纯性能优化（2026-07-25）：取消未使用的 _preload_daily 预加载。
- preload(start_date, end_date) 不再调用 DuckDBDataAccess.preload_daily_bars()
- _preload_daily 在回测全期保持 None（不再常驻约 759MiB（约 796MB 十进制）全市场 DataFrame）
- 活跃取数路径 get_history/get_history_batch → get_bars_by_count() 不受影响

本测试完全自包含：在最小 DuckDB 中插入确定性的 600519 行情数据，
不依赖真实项目数据库，也不使用 skip，稳定验证
「preload 不创建缓存，同时活跃取数路径仍返回正确数据」。
"""
import unittest.mock as mock
import pytest
import pandas as pd

from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider
from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess

# stock_daily 列为 query_bars_by_count_multi_table 实际 SELECT 的列集
_STOCK_DAILY_COLS = (
    "code VARCHAR, time BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,"
    "volume DOUBLE, amount DOUBLE, pctChg DOUBLE, preClose DOUBLE, turn DOUBLE,"
    "peTTM DOUBLE, pbMRQ DOUBLE,"
    "open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE"
)


def _make_minimal_db(path):
    import duckdb
    con = duckdb.connect(str(path))
    try:
        con.execute(f"CREATE TABLE stock_daily ({_STOCK_DAILY_COLS})")
    finally:
        con.close()


def _seed_600519(db_path, n=5, end_date="2026-04-29"):
    """向最小 DB 插入 n 条确定性的 600519 行情行（time <= end_date 当日截断）。"""
    import duckdb
    con = duckdb.connect(str(db_path))
    try:
        con.execute(f"CREATE TABLE IF NOT EXISTS stock_daily ({_STOCK_DAILY_COLS})")
        base = pd.Timestamp(end_date, tz="Asia/Shanghai")
        for i in range(n):
            d = base - pd.Timedelta(days=n - 1 - i)
            t = int(d.timestamp() * 1000)
            close = 1500.0 + i * 10.0
            con.execute(
                "INSERT INTO stock_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ["600519", t,
                 close - 5, close + 5, close - 10, close, 1e6, 1e9, 1.0,
                 close - 20, 1.0, 30.0, 10.0,
                 close - 5, close + 5, close - 10, close],
            )
    finally:
        con.close()


def test_market_preload_does_not_call_preload_daily_bars(tmp_path):
    """核心契约：preload 必须保持 no-op，绝不触发全市场预加载。"""
    db = tmp_path / "test.duckdb"
    _make_minimal_db(db)
    market = DuckDBMarketDataProvider(str(db))
    try:
        with mock.patch.object(DuckDBDataAccess, "preload_daily_bars") as m:
            market.preload("2024-01-01", "2024-12-31")
            m.assert_not_called()
        # 缓存必须保持未分配（纯性能优化：取消无效内存分配）
        assert market._data._preload_daily is None
    finally:
        market._data.close()


def test_market_preload_no_daily_cache_active_path_returns_data(tmp_path):
    """确定性最小 DB：preload 不建缓存，且活跃取数路径仍返回正确的 600519 数据。

    彻底移除真实数据库依赖与 skip——在临时 DuckDB 中插入确定性的 600519 行情，
    校验 get_bars_by_count（裸码入参）返回裸码键、行数=5、close 列存在且非空，
    且查询前后 _preload_daily 始终为 None。
    """
    db = tmp_path / "seeded.duckdb"
    _make_minimal_db(db)
    _seed_600519(str(db), n=5, end_date="2026-04-29")
    market = DuckDBMarketDataProvider(str(db))
    try:
        # 1) preload 契约：不调用 preload_daily_bars，缓存保持 None
        with mock.patch.object(DuckDBDataAccess, "preload_daily_bars") as m:
            market.preload("2024-01-01", "2024-12-31")
            m.assert_not_called()
        assert market._data._preload_daily is None

        # 2) 活跃取数路径：Provider 层按裸码 "600519" 入参（非 "600519.SH"）
        result = market.get_bars_by_count(
            ["600519"], count=5, end_date="2026-04-29", fields=["close"], fq="pre"
        )
        assert "600519" in result
        df = result["600519"]
        # 返回确定性的 5 行
        assert len(df) == 5
        # close 列存在且非空（fq='pre' 下由 close_front 填充）
        assert "close" in df.columns
        assert df["close"].notna().all()
        assert len(df["close"].dropna()) == 5

        # 3) 查询后缓存仍未被创建（纯性能优化未引入副作用）
        assert market._data._preload_daily is None
    finally:
        market._data.close()
