"""F3 审核返工：snapshot_meta 完整性语义测试

- n_constituents = COUNT(DISTINCT code)（不是 COUNT(*)）；
- 重大质量违规（重复代码/负权重/空代码）→ status='invalid'，永远不得 complete；
- expectations 配置缺失 → fail-closed（抛错，不打点）；
- 未登记指数 → status='unknown'（fail-closed，Provider 不服务）；
- 可变成分指数必须显式登记 variable_indices 才允许 complete。
"""
from __future__ import annotations

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from quantstudio.pipeline.index_constituents_meta import (
    load_expectations, refresh_snapshot_meta, compute_snapshot_status,
    ExpectationsConfigError)
from quantstudio.pipeline.writers import DuckDBWriter
from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider


def _ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


JAN = _ms("2020-01-31")

CONFIG = {
    "expectations": {"000300": 300},
    "variable_indices": ["399101"],
}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "m.duckdb"
    writer = DuckDBWriter({"type": "duckdb", "path": str(path)})
    yield path, writer
    writer.close()


def _seed(db_path, rows):
    """按旧库/草稿库形态（无 PK 约束）重建成分表，允许重复行注入。"""
    con = duckdb.connect(str(db_path))
    con.execute("DROP TABLE IF EXISTS index_constituents")
    con.execute("""
        CREATE TABLE index_constituents (
            index_code VARCHAR, code VARCHAR, time BIGINT,
            weight DOUBLE, data_source VARCHAR)""")
    con.executemany(
        "INSERT INTO index_constituents VALUES (?, ?, ?, ?, ?)", rows)
    con.close()


def _meta(db_path, index_code):
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(
        "SELECT n_constituents, expected_count, status, n_duplicate_codes, "
        "n_negative_weights, n_blank_codes "
        "FROM index_constituents_snapshot_meta WHERE index_code=?",
        [index_code]).fetchall()
    con.close()
    return rows


def _refresh(db_path, config=CONFIG):
    con = duckdb.connect(str(db_path))
    try:
        return refresh_snapshot_meta(con, expectations=config)
    finally:
        con.close()


def test_distinct_count_299_plus_1_dup_not_complete(db):
    """审核复现：299 唯一 + 1 重复，expected=300 → 不得 complete。"""
    path, _ = db
    rows = [("000300", f"60{i:04d}", JAN, 1.0, "tushare") for i in range(299)]
    rows.append(("000300", "600000", JAN, 1.0, "tushare"))  # 重复代码
    _seed(path, rows)
    _refresh(path)
    n, expected, status, dup, neg, blank = _meta(path, "000300")[0]
    assert n == 299            # COUNT(DISTINCT code)
    assert status != "complete"
    assert dup == 1            # 重复违规被记录


def test_300_unique_plus_1_dup_is_invalid(db):
    """300 唯一 + 1 重复：数量达标但重复违规 → invalid，不得 complete。"""
    path, _ = db
    rows = [("000300", f"60{i:04d}", JAN, 1.0, "tushare") for i in range(300)]
    rows.append(("000300", "600000", JAN, 1.0, "tushare"))
    _seed(path, rows)
    _refresh(path)
    n, expected, status, dup, neg, blank = _meta(path, "000300")[0]
    assert n == 300 and dup == 1
    assert status == "invalid"


def test_negative_weight_is_invalid(db):
    path, _ = db
    _seed(path, [("000300", "600000", JAN, -1.0, "tushare")])
    _refresh(path)
    assert _meta(path, "000300")[0][2] == "invalid"


def test_blank_code_is_invalid(db):
    path, _ = db
    _seed(path, [("000300", "", JAN, 1.0, "tushare")])
    _refresh(path)
    assert _meta(path, "000300")[0][2] == "invalid"


def test_missing_config_fail_closed(db):
    """配置缺失 → fail-closed 抛错，不打点、不默认放行。"""
    path, _ = db
    _seed(path, [("000300", "600000", JAN, 1.0, "tushare")])
    con = duckdb.connect(str(path))
    try:
        with pytest.raises(ExpectationsConfigError):
            refresh_snapshot_meta(
                con, config_path=path.parent / "nonexistent.json")
    finally:
        con.close()
    assert _meta(path, "000300") == []


def test_unregistered_index_unknown_fail_closed(db):
    """未登记指数 → status='unknown'，Provider 不服务。"""
    path, _ = db
    _seed(path, [("000999", "600000", JAN, 1.0, "tushare")])
    _refresh(path)
    assert _meta(path, "000999")[0][2] == "unknown"
    provider = DuckDBReferenceDataProvider(path)
    assert provider.get_index_constituents("000999", "2020-02-15") == []


def test_variable_index_must_be_registered(db):
    """可变成分指数显式登记 variable_indices 后才允许 complete。"""
    path, _ = db
    _seed(path, [("399101", "600000", JAN, 1.0, "tushare"),
                 ("399101", "000001", JAN, 1.0, "tushare")])
    _refresh(path)
    assert _meta(path, "399101")[0][2] == "complete"
    # 未登记为 variable 时 → unknown
    _refresh(path, config={"expectations": {}, "variable_indices": []})
    assert _meta(path, "399101")[0][2] == "unknown"


def test_provider_never_serves_invalid_snapshot(db):
    path, _ = db
    rows = [("000300", f"60{i:04d}", JAN, 1.0, "tushare") for i in range(300)]
    rows.append(("000300", "600000", JAN, 1.0, "tushare"))
    _seed(path, rows)
    _refresh(path)
    provider = DuckDBReferenceDataProvider(path)
    assert provider.get_index_constituents("000300", "2020-02-15") == []


def test_load_expectations_reads_variable_list(tmp_path):
    cfg = tmp_path / "exp.json"
    cfg.write_text('{"expectations": {"000300": 300}, "variable_indices": ["399101"]}',
                   encoding="utf-8")
    exp = load_expectations(cfg)
    assert exp["expectations"]["000300"] == 300
    assert exp["variable_indices"] == ["399101"]
