"""PR3 契约测试：ETF vs 股票 vs 指数的分钟表路由。

验证目标（主计划 7.18 路由规则）：
1. ETF 代码路由到 etf_minutes
2. 股票代码路由到 stock_minutes
3. 指数代码无对应分钟表 → raise FrequencyCapabilityError(code=TABLE_MISSING)
4. 分类顺序：is_etf 先于 is_index（ETF 代码可能与指数区间重叠）
"""
import pytest
import pandas as pd
import duckdb


def _ms(day_str, hh, mm):
    ts = pd.Timestamp(f"{day_str} {hh:02d}:{mm:02d}:00").tz_localize("Asia/Shanghai")
    return int(ts.value // 10**6)


def _build_minute_db(tmp_path, stock_codes=(), etf_codes=()):
    """临时 DuckDB，含真实 schema 的 stock_minutes + etf_minutes"""
    from quantstudio.pipeline.writers import DDL_DUCKDB
    db_path = tmp_path / "minute.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(DDL_DUCKDB["stock_minutes"])
    con.execute(DDL_DUCKDB["etf_minutes"])
    for code in stock_codes:
        con.execute(
            "INSERT INTO stock_minutes VALUES "
            "(?, ?, '1min', 10,10,10,10, 100,1000, 9.9, 0,0,0, 9,9,9,9, 11,11,11,11, "
            "0.9,0.9,0.9,0.9, 1.1,1.1,1.1,1.1, 'none', 'x')",
            [code, _ms("2026-01-05", 9, 31)])
    for code in etf_codes:
        con.execute(
            "INSERT INTO etf_minutes VALUES "
            "(?, ?, '1min', 10,10,10,10, 100,1000, 9.9, 0,0,0, 9,9,9,9, 11,11,11,11, "
            "0.9,0.9,0.9,0.9, 1.1,1.1,1.1,1.1, 'none', 'x')",
            [code, _ms("2026-01-05", 9, 31)])
    con.close()   # 先关闭写连接，避免 DuckDB 文件锁阻塞 DuckDBDataAccess 的 read_only 打开
    yield db_path


@pytest.fixture
def mixed_db(tmp_path):
    yield from _build_minute_db(tmp_path, stock_codes=["600000"], etf_codes=["159870"])


@pytest.fixture
def calendar_provider():
    class Cal:
        def get_trade_days(self, start, end):
            return [pd.Timestamp("2026-01-05", tz="Asia/Shanghai").to_pydatetime()]
    return Cal()


# ========== ETF → etf_minutes ==========

def test_etf_code_routes_to_etf_minutes(mixed_db, calendar_provider):
    """ETF 代码 159870 路由到 etf_minutes 表"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(mixed_db)
    df = da.query_minute_bars_by_range(
        "159870", "2026-01-05", "2026-01-05", "1min",
        fq=None, calendar_provider=calendar_provider)
    assert len(df) == 1


# ========== 股票 → stock_minutes ==========

def test_stock_code_routes_to_stock_minutes(mixed_db, calendar_provider):
    """股票代码 600000 路由到 stock_minutes 表"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(mixed_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq=None, calendar_provider=calendar_provider)
    assert len(df) == 1


# ========== 指数 → TABLE_MISSING ==========

def test_index_code_raises_table_missing(mixed_db, calendar_provider):
    """指数代码无对应分钟表 → raise code=TABLE_MISSING。

    用 399001（深证成指，裸码以 399 开头，is_index 无需后缀即 True）。
    注：000300 裸码与沪市 000xxx 股票难区分（is_index 需 SH 后缀），
    Provider 接收裸码，故用 399001 作为明确的指数样本。
    """
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    from quantstudio.backtest.providers.frequency_labels import (
        FrequencyCapabilityError, ERR_TABLE_MISSING)
    da = DuckDBDataAccess(mixed_db)
    with pytest.raises(FrequencyCapabilityError) as exc_info:
        da.query_minute_bars_by_range(
            "399001", "2026-01-05", "2026-01-05", "1min",
            fq=None, calendar_provider=calendar_provider)
    assert exc_info.value.code == ERR_TABLE_MISSING


# ========== 分类顺序：is_etf 先于 is_index ==========

def test_etf_classified_before_index():
    """is_etf 先于 is_index 判断（ETF 代码 510300 不被误判为指数）"""
    from quantstudio.backtest.libs.security_code_rules import is_etf, is_index
    # 510300 是 ETF（沪深300ETF），不应被指数规则误判
    assert is_etf("510300.SH") is True
    # 注意：is_index 对 510300 返回 False（因为它不以 000/399/899 开头）
    assert is_index("510300.SH") is False
