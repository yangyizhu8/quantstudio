"""PR3 契约测试：分钟复权查询（fq='pre'/'post'/None）。

验证目标（主计划 7.19 分钟复权口径明确）：
1. fq='pre'：OHLC 被 *_front 列替换
2. fq=None：原始价（不替换）
3. fq='post'：OHLC 被 *_back 列替换
4. preClose 保持原始（补齐 3 已知简化，报告注明）

与日线复权口径一致：fq='pre'/'dypre' 用 front 列（见 query_bars_by_count_multi_table）。
"""
import pytest
import pandas as pd
import duckdb


def _ms(day_str, hh, mm):
    ts = pd.Timestamp(f"{day_str} {hh:02d}:{mm:02d}:00").tz_localize("Asia/Shanghai")
    return int(ts.value // 10**6)


@pytest.fixture
def minute_db(tmp_path):
    """临时 DuckDB + 合成 stock_minutes 1min 数据，OHLC 与 front/back 列明显不同"""
    from quantstudio.pipeline.writers import DDL_DUCKDB
    db_path = tmp_path / "qfq.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(DDL_DUCKDB["stock_minutes"])
    con.execute(
        "INSERT INTO stock_minutes VALUES "
        "('600000', ?, '1min', 10.0, 10.5, 9.8, 10.2, 10000, 100000, 9.9, 0, 0, 0, "
        "9.0, 9.5, 8.8, 9.2, "      # front: open/high/low/close
        "11.0, 11.5, 10.8, 11.2, "  # back: open/high/low/close
        "0.9, 0.9, 0.9, 0.9, 1.1, 1.1, 1.1, 1.1, 'none', 'x')",
        [_ms("2026-01-05", 9, 31)])
    con.close()   # 先关闭写连接，避免 DuckDB 文件锁阻塞 DuckDBDataAccess 的 read_only 打开
    yield db_path


@pytest.fixture
def calendar_provider():
    class Cal:
        def get_trade_days(self, start, end):
            return [pd.Timestamp("2026-01-05", tz="Asia/Shanghai").to_pydatetime()]
    return Cal()


# ========== fq='pre'：用 front 列 ==========

def test_qfq_pre_replaces_ohlc_with_front(minute_db, calendar_provider):
    """fq='pre'：OHLC 被 front 列替换"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq='pre', calendar_provider=calendar_provider)
    assert (df['close'] == 9.2).all()   # close_front
    assert (df['open'] == 9.0).all()    # open_front
    assert (df['high'] == 9.5).all()    # high_front
    assert (df['low'] == 8.8).all()     # low_front


def test_qfq_dypre_equivalent_to_pre(minute_db, calendar_provider):
    """fq='dypre' 等价于 'pre'（与日线 use_qfq 判断一致）"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq='dypre', calendar_provider=calendar_provider)
    assert (df['close'] == 9.2).all()


# ========== fq=None：原始价 ==========

def test_qfq_none_keeps_original_ohlc(minute_db, calendar_provider):
    """fq=None：保持原始 OHLC"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq=None, calendar_provider=calendar_provider)
    assert (df['close'] == 10.2).all()  # 原始 close
    assert (df['open'] == 10.0).all()   # 原始 open


# ========== fq='post'：用 back 列 ==========

def test_qfq_post_replaces_ohlc_with_back(minute_db, calendar_provider):
    """fq='post'：OHLC 被 back 列替换"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq='post', calendar_provider=calendar_provider)
    assert (df['close'] == 11.2).all()  # close_back
    assert (df['open'] == 11.0).all()   # open_back


# ========== preClose 保持原始（补齐 3 已知简化）==========

def test_qfq_preclose_keeps_original(minute_db, calendar_provider):
    """fq='pre' 下 preClose 保持原始（已知简化，报告注明）"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq='pre', calendar_provider=calendar_provider)
    # preClose 原始 9.9，未被复权列替换（分钟 preClose 主要用于涨跌幅参考）
    assert (df['preClose'] == 9.9).all()
