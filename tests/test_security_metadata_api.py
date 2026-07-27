"""F2: 统一股票/ETF 证券元数据 API 测试（任务书 §4.7）

覆盖：
- query_security_metadata 统一内部字段与 stock_basic/etf_basic 路由
- 股票/ETF 上市日、ETF 退市日、名称与类型
- 上市日优先级：stock_basic.list_date → stock_daily.MIN(time) 兼容 fallback（标记来源）
- ETF list_date 缺失 → etf_daily 首个交易日补齐（标记来源）
- .SH/.SS/.SZ/裸代码标准化、批量查询不错位、field 过滤
- 未知证券兼容空值行为；旧库无 etf_basic 时股票不受影响
- get_stock_info 股票返回结构与修复前完全一致（键集合/类型）
- get_security_info 本地扩展兼容
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider
from quantstudio.backtest.ptrade_api import PtradeAPI


def _ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


STOCK_LIST = _ms("1991-04-03")
STOCK_FIRST_BAR = _ms("2018-01-02")
ETF_LIST = _ms("2012-05-28")
ETF_DELIST = _ms("2024-09-30")
ETF_NO_LIST_FIRST_BAR = _ms("2020-06-15")


@pytest.fixture
def meta_db(tmp_path):
    """含 stock_basic/etf_basic/stock_daily/etf_daily 的临时库。"""
    db = tmp_path / "meta.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE stock_basic (
            code VARCHAR, ts_code VARCHAR, name VARCHAR, exchange VARCHAR,
            list_status VARCHAR, list_date BIGINT, delist_date BIGINT,
            update_time VARCHAR, data_source VARCHAR)""")
    con.execute("""
        CREATE TABLE etf_basic (
            code VARCHAR, ts_code VARCHAR, name VARCHAR, exchange VARCHAR,
            list_date BIGINT, delist_date BIGINT, etf_type VARCHAR,
            status VARCHAR, update_time VARCHAR, data_source VARCHAR)""")
    con.execute("CREATE TABLE stock_daily (code VARCHAR, time BIGINT)")
    con.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
    # 股票：stock_basic 上市日 1991-04-03，但行情从 2018-01-02 开始（数据窗口≠上市日）
    con.execute(
        "INSERT INTO stock_basic VALUES ('000001', '000001.SZ', '平安银行', 'SZSE', 'L', ?, NULL, '2026-01-01', 'tushare')",
        [STOCK_LIST])
    con.execute("INSERT INTO stock_daily VALUES ('000001', ?), ('000001', ?)",
                [STOCK_FIRST_BAR, STOCK_FIRST_BAR + 86_400_000])
    # 股票：不在 stock_basic（旧库兼容场景），只有行情
    con.execute("INSERT INTO stock_daily VALUES ('600000', ?)", [STOCK_FIRST_BAR])
    # ETF：完整元数据
    con.execute(
        "INSERT INTO etf_basic VALUES ('510300', '510300.SH', '华泰柏瑞沪深300ETF', 'SS', ?, NULL, 'equity', 'L', '2026-01-01', 'tushare')",
        [ETF_LIST])
    con.execute("INSERT INTO etf_daily VALUES ('510300', ?)", [ETF_LIST])
    # ETF：已退市
    con.execute(
        "INSERT INTO etf_basic VALUES ('159901', '159901.SZ', '深100ETF(退市)', 'SZ', ?, ?, 'equity', 'D', '2026-01-01', 'tushare')",
        [ETF_LIST, ETF_DELIST])
    con.execute("INSERT INTO etf_daily VALUES ('159901', ?)", [ETF_LIST])
    # ETF：list_date 缺失 → 按 etf_basic 管线契约用首个 etf_daily 交易日补齐
    con.execute(
        "INSERT INTO etf_basic VALUES ('158000', '158000.SZ', '测试ETF-无上市日', 'SZ', NULL, NULL, 'equity', 'L', '2026-01-01', 'tushare')")
    con.execute("INSERT INTO etf_daily VALUES ('158000', ?), ('158000', ?)",
                [ETF_NO_LIST_FIRST_BAR, ETF_NO_LIST_FIRST_BAR + 86_400_000])
    con.close()
    return db


@pytest.fixture
def legacy_db(tmp_path):
    """旧库：无 etf_basic 表。"""
    db = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE stock_basic (
            code VARCHAR, ts_code VARCHAR, name VARCHAR, exchange VARCHAR,
            list_status VARCHAR, list_date BIGINT, delist_date BIGINT,
            update_time VARCHAR, data_source VARCHAR)""")
    con.execute("CREATE TABLE stock_daily (code VARCHAR, time BIGINT)")
    con.execute(
        "INSERT INTO stock_basic VALUES ('000001', '000001.SZ', '平安银行', 'SZSE', 'L', ?, NULL, '2026-01-01', 'tushare')",
        [STOCK_LIST])
    con.execute("INSERT INTO stock_daily VALUES ('000001', ?)", [STOCK_FIRST_BAR])
    con.close()
    return db


@pytest.fixture
def provider(meta_db):
    return DuckDBReferenceDataProvider(meta_db)


@pytest.fixture
def api(meta_db):
    return PtradeAPI(reference=DuckDBReferenceDataProvider(meta_db))


# ---------- query_security_metadata（数据访问层统一查询） ----------

def test_query_security_metadata_unified_schema(meta_db):
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    df = DuckDBDataAccess(meta_db).query_security_metadata()
    assert list(df.columns) == ["code", "name", "security_type", "exchange",
                                "list_date", "delist_date", "status", "data_source"]
    by_code = df.set_index("code")
    assert by_code.loc["000001", "security_type"] == "stock"
    assert by_code.loc["510300", "security_type"] == "etf"
    assert by_code.loc["000001", "data_source"] == "tushare"


def test_query_security_metadata_filter_codes(meta_db):
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    df = DuckDBDataAccess(meta_db).query_security_metadata(codes=["510300"])
    assert df["code"].tolist() == ["510300"]
    assert df.iloc[0]["security_type"] == "etf"


def test_query_security_metadata_unknown_code_absent(meta_db):
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    df = DuckDBDataAccess(meta_db).query_security_metadata(codes=["999999"])
    assert df.empty


def test_query_security_metadata_without_etf_basic(legacy_db):
    """旧库无 etf_basic：股票元数据仍可用，不报错（fail-open 于股票）。"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    df = DuckDBDataAccess(legacy_db).query_security_metadata()
    assert df["code"].tolist() == ["000001"]
    assert df.iloc[0]["security_type"] == "stock"


# ---------- query_listing_dates 上市日优先级与 fallback 标记 ----------

def test_listing_dates_stock_uses_stock_daily_min(meta_db):
    """股票公共行为与修复前一致：stock_daily 首根K线（不用 stock_basic 覆盖）。"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    df = DuckDBDataAccess(meta_db).query_listing_dates()
    row = df[df["code"] == "000001"].iloc[0]
    assert int(row["listing_time"]) == STOCK_FIRST_BAR  # 不是 stock_basic 的 1991
    assert row["listing_source"] == "stock_daily_min"
    row2 = df[df["code"] == "600000"].iloc[0]
    assert int(row2["listing_time"]) == STOCK_FIRST_BAR
    assert row2["listing_source"] == "stock_daily_min"


def test_listing_dates_etf_from_etf_basic(meta_db):
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    df = DuckDBDataAccess(meta_db).query_listing_dates()
    row = df[df["code"] == "510300"].iloc[0]
    assert int(row["listing_time"]) == ETF_LIST
    assert row["listing_source"] == "etf_basic"


def test_listing_dates_etf_missing_list_date_filled_from_etf_daily(meta_db):
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    df = DuckDBDataAccess(meta_db).query_listing_dates()
    row = df[df["code"] == "158000"].iloc[0]
    assert int(row["listing_time"]) == ETF_NO_LIST_FIRST_BAR
    assert row["listing_source"] == "etf_daily_min_fallback"


# ---------- Provider.get_security_info ----------

def test_provider_security_info_stock(provider):
    """股票：display_name=裸码、start_date=stock_daily 首根K线（修复前行为）。"""
    info = provider.get_security_info("000001")
    assert info["code"] == "000001"
    assert info["security_type"] == "stock"
    assert info["display_name"] == "000001"
    assert isinstance(info["start_date"], datetime.datetime)
    assert pd.Timestamp(info["start_date"]).strftime("%Y-%m-%d") == "2018-01-02"
    assert info.get("end_date") is None


def test_provider_security_info_etf(provider):
    info = provider.get_security_info("159901")
    assert info["security_type"] == "etf"
    assert info["display_name"] == "深100ETF(退市)"
    assert pd.Timestamp(info["start_date"]).strftime("%Y-%m-%d") == "2012-05-28"
    assert info["end_date"] is not None
    assert pd.Timestamp(info["end_date"]).strftime("%Y-%m-%d") == "2024-09-30"
    assert info.get("exchange") in ("SZ", "SZSE")


def test_provider_security_info_etf_missing_list_date_filled(provider):
    info = provider.get_security_info("158000")
    assert info["security_type"] == "etf"
    assert pd.Timestamp(info["start_date"]).strftime("%Y-%m-%d") == "2020-06-15"
    assert info.get("data_source") == "etf_daily_min_fallback"


def test_provider_security_info_unknown_returns_none(provider):
    assert provider.get_security_info("999999") is None


def test_provider_security_info_stock_old_db(legacy_db):
    provider = DuckDBReferenceDataProvider(legacy_db)
    info = provider.get_security_info("000001")
    assert info["security_type"] == "stock"
    assert pd.Timestamp(info["start_date"]).strftime("%Y-%m-%d") == "2018-01-02"


# ---------- PtradeAPI.get_stock_info（PTrade 形状） ----------

def test_get_stock_info_stock_shape_unchanged(api):
    """股票返回容器/字段/日期格式与修复前完全一致。"""
    result = api.get_stock_info("000001.SZ")
    assert set(result.keys()) == {"000001.SZ"}
    record = result["000001.SZ"]
    assert set(record.keys()) == {"stock_name", "stock_type", "listed_date",
                                  "de_listed_date", "exchange_type", "code"}
    assert record["stock_type"] == "stock"
    assert record["listed_date"] == "2018-01-02"
    assert record["de_listed_date"] is None
    assert record["exchange_type"] == "SZ"
    assert record["code"] == "000001.SZ"
    assert record["stock_name"] == "000001"


def test_get_stock_info_etf(api):
    result = api.get_stock_info("510300.SS")
    record = result["510300.SS"]
    assert record["stock_type"] == "etf"
    assert record["stock_name"] == "华泰柏瑞沪深300ETF"
    assert record["listed_date"] == "2012-05-28"
    assert record["de_listed_date"] is None


def test_get_stock_info_etf_delisted(api):
    record = api.get_stock_info("159901.SZ")["159901.SZ"]
    assert record["stock_type"] == "etf"
    assert record["listed_date"] == "2012-05-28"
    assert record["de_listed_date"] == "2024-09-30"


def test_get_stock_info_suffix_normalization(api):
    for code in ("510300.SS", "510300.SH", "510300"):
        record = api.get_stock_info(code)[code]
        assert record["stock_type"] == "etf"
        assert record["listed_date"] == "2012-05-28", code


def test_get_stock_info_batch_no_misalignment(api):
    codes = ["000001.SZ", "510300.SS", "159901.SZ"]
    result = api.get_stock_info(codes)
    assert list(result.keys()) == codes
    assert result["000001.SZ"]["stock_type"] == "stock"
    assert result["510300.SS"]["stock_type"] == "etf"
    assert result["159901.SZ"]["de_listed_date"] == "2024-09-30"


def test_get_stock_info_field_string(api):
    record = api.get_stock_info("510300.SS", field="listed_date")["510300.SS"]
    assert record == {"listed_date": "2012-05-28"}


def test_get_stock_info_field_list(api):
    record = api.get_stock_info(
        "510300.SS", field=["listed_date", "stock_name"])["510300.SS"]
    assert record == {"listed_date": "2012-05-28",
                      "stock_name": "华泰柏瑞沪深300ETF"}


def test_get_stock_info_unknown_security_compat(api):
    """未知代码保持现有兼容空值行为。"""
    record = api.get_stock_info("999999.SH")["999999.SH"]
    assert record["stock_name"] == "999999.SH"
    assert record["stock_type"] == "stock"
    assert record["listed_date"] is None
    assert record["de_listed_date"] is None


def test_get_stock_info_old_db_etf_falls_back_to_compat(legacy_db):
    """旧库无 etf_basic：ETF 代码按未知证券兼容行为处理，股票不受影响。"""
    api = PtradeAPI(reference=DuckDBReferenceDataProvider(legacy_db))
    etf_record = api.get_stock_info("510300.SS")["510300.SS"]
    assert etf_record["listed_date"] is None
    stock_record = api.get_stock_info("000001.SZ")["000001.SZ"]
    assert stock_record["listed_date"] == "2018-01-02"


# ---------- get_security_info 本地扩展兼容 ----------

def test_get_security_info_local_extension_stock(api):
    info = api.get_security_info("000001.SZ")
    assert info.code == "000001.SZ"
    assert pd.Timestamp(info.start_date).strftime("%Y-%m-%d") == "2018-01-02"


def test_get_security_info_local_extension_etf(api):
    info = api.get_security_info("510300.SS")
    assert pd.Timestamp(info.start_date).strftime("%Y-%m-%d") == "2012-05-28"


# ---------- 真实库只读验证（159787 / 510300 真实数据） ----------

REAL_DB = Path("data/quantstudio.db")


@pytest.mark.skipif(not REAL_DB.exists(), reason="project DuckDB not available")
def test_real_db_etf_metadata():
    provider = DuckDBReferenceDataProvider(REAL_DB)
    api = PtradeAPI(reference=provider)
    rec = api.get_stock_info("159787.SZ")["159787.SZ"]
    assert rec["stock_type"] == "etf"
    assert rec["stock_name"] == "易方达中证全指建筑材料ETF"
    assert rec["listed_date"] == "2022-03-15"
    assert rec["de_listed_date"] is None
    rec2 = api.get_stock_info("510300.SS")["510300.SS"]
    assert rec2["stock_type"] == "etf"
    assert rec2["listed_date"] == "2012-05-28"


@pytest.mark.skipif(not REAL_DB.exists(), reason="project DuckDB not available")
def test_real_db_stock_metadata():
    """真实库股票：与修复前完全一致（名称=裸码、上市日=stock_daily 首根K线）。"""
    api = PtradeAPI(reference=DuckDBReferenceDataProvider(REAL_DB))
    rec = api.get_stock_info("000001.SZ")["000001.SZ"]
    assert rec["stock_type"] == "stock"
    assert rec["stock_name"] == "000001"
    assert rec["listed_date"] == "2018-01-02"


# ---------- 修复前后逐字段黄金兼容（股票，值/类型/空值/field/batch 完全一致） ----------

GOLDEN_PREFIX = (Path(__file__).resolve().parent
                 / "fixtures" / "golden_stock_info_prefix.json")


@pytest.mark.skipif(not (REAL_DB.exists() and GOLDEN_PREFIX.exists()),
                    reason="golden prefix snapshot not available")
def test_stock_api_identical_to_pre_fix_golden():
    """当前实现的股票输出必须与 HEAD（修复前）逐字段一致；ETF 键不受本断言约束。"""
    golden = json.loads(GOLDEN_PREFIX.read_text(encoding="utf-8"))
    api = PtradeAPI(reference=DuckDBReferenceDataProvider(REAL_DB))
    stocks = ["000001.SZ", "600000.SS", "600519.SS", "000002.SZ", "601318.SS",
              "000001", "600000", "300750.SZ"]
    current = {
        "single": api.get_stock_info("000001.SZ"),
        "batch": api.get_stock_info(stocks),
        "field_str": api.get_stock_info("600519.SS", field="listed_date"),
        "field_list": api.get_stock_info("600519.SS",
                                         field=["listed_date", "stock_name"]),
        "unknown": api.get_stock_info("999999.SH"),
        "batch_mixed": api.get_stock_info(["000001.SZ", "999999.SH", "600000"]),
    }
    assert current == golden
