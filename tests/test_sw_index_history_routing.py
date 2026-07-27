"""F5: 申万行业指数 get_history 路由测试（任务书 §7.4/§7.7）

- 申万行业指数（801xxx）→ index_daily；普通股票 → stock_daily；
  ETF → etf_daily；普通指数 → index_daily（既有行为不变）；
- fq='pre' 对指数回退原始 OHLC（无复权列，指数行情不被 ETF 前复权逻辑修改）；
- 行业指数不读取 stock_daily；无数据时明确为空而非静默转换。
"""
from __future__ import annotations

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess


def _ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


DAY1, DAY2 = _ms("2026-07-23"), _ms("2026-07-24")


@pytest.fixture
def route_db(tmp_path):
    db = tmp_path / "route.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE stock_daily (
        code VARCHAR, time BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        volume DOUBLE, amount DOUBLE, pctChg DOUBLE, preClose DOUBLE, turn DOUBLE,
        peTTM DOUBLE, pbMRQ DOUBLE, open_front DOUBLE, high_front DOUBLE,
        low_front DOUBLE, close_front DOUBLE)""")
    con.execute("""CREATE TABLE etf_daily (
        code VARCHAR, time BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        volume DOUBLE, amount DOUBLE, pctChg DOUBLE, preClose DOUBLE, turn DOUBLE,
        open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE)""")
    con.execute("""CREATE TABLE index_daily (
        code VARCHAR, time BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        pctChg DOUBLE, volume DOUBLE, amount DOUBLE)""")
    # 股票（含前复权列）
    con.execute("INSERT INTO stock_daily VALUES ('600000', ?, 10, 11, 9, 10.5, 1e6, 1e7, 1.0, 10, 1, 20, 2, 9.5, 10.5, 8.5, 10)", [DAY2])
    # ETF（含前复权列）
    con.execute("INSERT INTO etf_daily VALUES ('510300', ?, 4, 4.1, 3.9, 4.05, 1e6, 4e6, 1.0, 4, 1, 3.95, 4.05, 3.85, 4)", [DAY2])
    # 普通指数（无复权列）
    con.execute("INSERT INTO index_daily VALUES ('000300', ?, 4700, 4750, 4640, 4649.19, -1.67, 2e10, 6e11)", [DAY2])
    # 申万行业指数（无复权列）
    con.execute("INSERT INTO index_daily VALUES ('801010', ?, 2368.43, 2377.55, 2312.71, 2312.71, -2.8, 1.77e9, 1.32e10)", [DAY2])
    con.execute("INSERT INTO index_daily VALUES ('801010', ?, 2339.77, 2384.79, 2328.25, 2379.45, 1.04, 1.73e9, 1.40e10)", [DAY1])
    con.close()
    return db


def test_sw_index_routes_to_index_daily(route_db):
    df = DuckDBDataAccess(route_db).query_bars_by_count_multi_table(
        "801010", 10, DAY2, use_qfq=False)
    assert len(df) == 2
    assert set(df["code"]) == {"801010"}
    assert df.sort_values("time").iloc[-1]["close"] == 2312.71


def test_sw_index_fq_pre_returns_raw_ohlc(route_db):
    """fq='pre' 对指数回退原始 OHLC（指数无复权列，不被 ETF 复权逻辑修改）。"""
    df = DuckDBDataAccess(route_db).query_bars_by_count_multi_table(
        "801010", 10, DAY2, use_qfq=True)
    assert len(df) == 2
    assert df.sort_values("time").iloc[-1]["open"] == 2368.43
    assert df["open_front"].isna().all()


def test_sw_index_not_read_from_stock_daily(route_db):
    """stock_daily 中不存在 801010 时绝不伪造股票行情；且行业指数行确实来自 index_daily。"""
    con = duckdb.connect(str(route_db), read_only=True)
    assert con.execute("SELECT COUNT(*) FROM stock_daily WHERE code='801010'").fetchone()[0] == 0
    con.close()
    df = DuckDBDataAccess(route_db).query_bars_by_count_multi_table(
        "801010", 10, DAY2, use_qfq=False)
    assert not df.empty
    assert df.iloc[0]["preClose"] is None or pd.isna(df.iloc[0]["preClose"])  # index 列形态


def test_normal_index_unaffected(route_db):
    df = DuckDBDataAccess(route_db).query_bars_by_count_multi_table(
        "000300", 10, DAY2, use_qfq=True)
    assert len(df) == 1
    assert df.iloc[0]["close"] == 4649.19


def test_stock_and_etf_unaffected(route_db):
    data = DuckDBDataAccess(route_db)
    stock = data.query_bars_by_count_multi_table("600000", 10, DAY2, use_qfq=True)
    assert stock.iloc[0]["open"] == 9.5  # 股票前复权列生效（既有行为）
    etf = data.query_bars_by_count_multi_table("510300", 10, DAY2, use_qfq=True)
    assert etf.iloc[0]["open"] == 3.95   # ETF 前复权列生效（既有行为）


def test_sw_index_no_data_returns_empty(route_db):
    df = DuckDBDataAccess(route_db).query_bars_by_count_multi_table(
        "801999", 10, DAY2, use_qfq=False)
    assert df.empty
