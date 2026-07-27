"""F3 修订：get_index_stocks(date) 严格 PIT + 快照完整性 meta 契约（任务书 §5.7 + 审核返工）

PIT 契约：
- 只取不晚于查询日期的最近 status='complete' 快照（as-of），禁止历史并集、禁止未来快照；
- 完整性**只能**来自 index_constituents_snapshot_meta 的正式批次契约
  （n_constituents / expected_count / status），由写入方在打点时确定，
  **绝不依赖未来快照**；无 meta 的指数 fail-closed 返回空；
- 未来数据写入（新快照 + 新 meta）**不得改变**历史查询结果；
- 回测上下文 date=None 注入当前回测日期；非回测直接调用保留最新快照兼容。
"""
from __future__ import annotations

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider
from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.ptrade_api import PtradeAPI


def _ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


JAN, FEB, MAR = _ms("2020-01-31"), _ms("2020-02-28"), _ms("2020-03-31")


def _create_tables(con):
    con.execute("""
        CREATE TABLE index_constituents (
            index_code VARCHAR, code VARCHAR, time BIGINT,
            weight DOUBLE, data_source VARCHAR)""")
    con.execute("""
        CREATE TABLE index_constituents_snapshot_meta (
            index_code VARCHAR, time BIGINT,
            n_constituents INTEGER, expected_count INTEGER,
            status VARCHAR,
            n_duplicate_codes INTEGER, n_negative_weights INTEGER,
            n_blank_codes INTEGER,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY (index_code, time))""")


def _insert_snapshot(con, index_code, time_ms, codes, expected, weight=1.0):
    con.executemany(
        "INSERT INTO index_constituents VALUES (?, ?, ?, ?, ?)",
        [(index_code, c, time_ms, weight, "tushare") for c in codes])
    status = ("complete" if expected is None or len(codes) >= expected
              else "partial")
    con.execute(
        "INSERT INTO index_constituents_snapshot_meta VALUES (?,?,?,?,?,?,?,?,?,?)",
        [index_code, time_ms, len(codes), expected, status, 0, 0, 0,
         "2020-04-01", "tushare"])


@pytest.fixture
def pit_db(tmp_path):
    """三快照测试库（均 complete，expected=3）：
    2020-01-31: 600000,000001,000002 (A,B,C)
    2020-02-28: 000001,000002,600519 (B,C,D)
    2020-03-31: 000002,600519,601318 (C,D,E)
    000905: JAN partial（10/100），FEB 起 complete（100/100）
    """
    db = tmp_path / "pit.duckdb"
    con = duckdb.connect(str(db))
    _create_tables(con)
    _insert_snapshot(con, "000300", JAN, ["600000", "000001", "000002"], 3)
    _insert_snapshot(con, "000300", FEB, ["000001", "000002", "600519"], 3)
    # 同日重复行（多源冲突场景）：FEB 快照中 000001 重复；meta dup 计数置 1
    con.execute("UPDATE index_constituents_snapshot_meta SET n_duplicate_codes=1 "
                "WHERE index_code='000300' AND time=?", [FEB])
    con.execute("INSERT INTO index_constituents VALUES ('000300','000001',?,1.0,'tushare')", [FEB])
    _insert_snapshot(con, "000300", MAR, ["000002", "600519", "601318"], 3)
    _insert_snapshot(con, "000905", JAN, [f"60{i:04d}" for i in range(10)], 100)
    _insert_snapshot(con, "000905", FEB, [f"60{i:04d}" for i in range(100)], 100)
    con.close()
    return db


@pytest.fixture
def provider(pit_db):
    return DuckDBReferenceDataProvider(pit_db)


@pytest.fixture
def api(pit_db):
    return PtradeAPI(reference=DuckDBReferenceDataProvider(pit_db))


# ---------- Provider / 数据访问层 PIT 语义 ----------

def test_before_first_snapshot_returns_empty(provider):
    assert provider.get_index_constituents("000300", "2020-01-15") == []


def test_on_first_snapshot_date(provider):
    assert provider.get_index_constituents("000300", "2020-01-31") == [
        "000001", "000002", "600000"]


def test_between_snapshots_uses_earlier_snapshot(provider):
    assert provider.get_index_constituents("000300", "2020-02-15") == [
        "000001", "000002", "600000"]


def test_on_second_snapshot_date(provider):
    assert provider.get_index_constituents("000300", "2020-02-28") == [
        "000001", "000002", "600519"]


def test_future_constituents_do_not_leak(provider):
    for date in ("2020-01-31", "2020-02-15", "2020-02-28", "2020-03-15"):
        assert "601318" not in provider.get_index_constituents("000300", date)


def test_no_union_for_single_day(provider):
    result = provider.get_index_constituents("000300", "2020-04-01")
    assert result == ["000002", "600519", "601318"]
    assert "600000" not in result


def test_duplicates_removed(provider):
    result = provider.get_index_constituents("000300", "2020-02-28")
    assert len(result) == len(set(result)) == 3


def test_date_none_latest_snapshot_compat(provider):
    """Provider 直接调用且 date=None：最新 complete 快照（文档化兼容）。"""
    assert provider.get_index_constituents("000300", None) == [
        "000002", "600519", "601318"]


def test_partial_snapshot_not_used_as_full_pit(provider):
    """partial（meta status）快照不得被当作完整 PIT → fail-closed 返回空。"""
    assert provider.get_index_constituents("000905", "2020-02-15") == []
    full = provider.get_index_constituents("000905", "2020-02-28")
    assert len(full) == 100


def test_unknown_index_returns_empty(provider):
    assert provider.get_index_constituents("999999", "2020-02-15") == []


def test_missing_meta_fail_closed(tmp_path):
    """无 snapshot_meta 的指数：无法证明完整性 → fail-closed 返回空。"""
    db = tmp_path / "nometa.duckdb"
    con = duckdb.connect(str(db))
    _create_tables(con)
    con.execute("INSERT INTO index_constituents VALUES ('000300','600000',?,1.0,'tushare')", [JAN])
    con.close()
    provider = DuckDBReferenceDataProvider(db)
    assert provider.get_index_constituents("000300", "2020-02-15") == []


def test_future_writes_do_not_change_historical_query(pit_db):
    """未来数据写入（含更大的完整快照）不得改变历史查询结果。"""
    provider = DuckDBReferenceDataProvider(pit_db)
    before = provider.get_index_constituents("000300", "2020-02-15")
    assert before == ["000001", "000002", "600000"]
    # 写入未来快照：成分更多（500 只），且补齐未来 meta
    # （先关闭 provider 的只读连接，再开写连接，避免单写者冲突）
    provider._data.close()
    con = duckdb.connect(str(pit_db))
    _insert_snapshot(con, "000300", _ms("2020-04-30"),
                     [f"60{i:04d}" for i in range(500)], 500)
    con.close()
    after = provider.get_index_constituents("000300", "2020-02-15")
    assert after == before
    # 未来快照本身可查
    assert len(provider.get_index_constituents("000300", "2020-04-30")) == 500


# ---------- 快照质量报告（meta 契约） ----------

def test_quality_report_from_meta(pit_db):
    df = DuckDBDataAccess(pit_db).query_index_constituents_quality()
    assert not df.empty
    by_idx = df.set_index(["index_code", "time"])
    jan = by_idx.loc[("000300", JAN)]
    assert jan["n_constituents"] == 3
    assert jan["expected_count"] == 3
    assert jan["status"] == "complete"
    feb = by_idx.loc[("000300", FEB)]
    assert feb["n_duplicate_codes"] == 1  # 同日 000001 重复被门控发现
    partial = by_idx.loc[("000905", JAN)]
    assert partial["status"] == "partial"
    assert partial["expected_count"] == 100


def test_quality_report_flags_negative_weight(tmp_path):
    db = tmp_path / "neg.duckdb"
    con = duckdb.connect(str(db))
    _create_tables(con)
    con.executemany("INSERT INTO index_constituents VALUES (?, ?, ?, ?, ?)",
                    [("000300", "600000", JAN, -1.5, "tushare"),
                     ("000300", "000001", JAN, 2.0, "tushare")])
    con.execute("INSERT INTO index_constituents_snapshot_meta VALUES (?,?,?,?,?,?,?,?,?,?)",
                ["000300", JAN, 2, 2, "complete", 0, 1, 0, "2020-04-01", "tushare"])
    con.close()
    df = DuckDBDataAccess(db).query_index_constituents_quality()
    assert df.iloc[0]["n_negative_weights"] == 1


# ---------- PTrade API 层 ----------

def test_api_explicit_date(api):
    result = api.get_index_stocks("000300", "2020-02-28")
    assert result == ["000001.SZ", "000002.SZ", "600519.SS"]


def test_api_date_param_reaches_provider(api, monkeypatch):
    seen = {}
    original = api._reference.get_index_constituents

    def spy(index_code, date=None):
        seen["date"] = date
        return original(index_code, date)

    monkeypatch.setattr(api._reference, "get_index_constituents", spy)
    api.get_index_stocks("000300", "2020-01-31")
    assert seen["date"] == "2020-01-31"


def test_api_no_date_uses_current_backtest_date(api):
    api._current_date = "2020-02-15"
    result = api.get_index_stocks("000300")
    assert result == ["000001.SZ", "000002.SZ", "600000.SS"]
    assert "601318.SS" not in result


def test_api_no_date_without_backtest_context(api):
    assert api._current_date == ""
    result = api.get_index_stocks("000300")
    assert result == ["000002.SZ", "600519.SS", "601318.SS"]


def test_api_index_code_normalization(api):
    expected = ["000001.SZ", "000002.SZ", "600000.SS"]
    for code in ("000300", "000300.SH", "000300.SS", "000300.XBHS"):
        assert api.get_index_stocks(code, "2020-01-31") == expected, code


def test_api_returns_standard_ptrade_codes(api):
    result = api.get_index_stocks("000300", "2020-01-31")
    assert all(code.endswith((".SS", ".SZ")) for code in result)
    assert len(result) == len(set(result))
