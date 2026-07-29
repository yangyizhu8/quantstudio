"""任务2.1：ETF/股票复权因子分库存储 + 历史错配迁移（纯单测，自包含 fixture）。

覆盖：
- fetch_adj_factor(is_etf=False) 写 adj_factor；is_etf=True 写 fund_adj；两张表彻底隔离，
  ETF 因子不出现在 adj_factor。
- migrate_split_etf_factors(dry_run=True) 不写入、返回 (moved_rows, sample)；
  dry_run=False 实跑后：adj_factor 不含 ETF 行、fund_adj 含原 ETF 行、行数守恒。
- get_stock_universe / get_etf_universe 在正式表缺失时返回空集且不抛异常（防崩）。

注：本文件只改测试，不触碰任何生产代码。所有 fixture 自带临时 DuckDB/SQLite。
"""

import sqlite3

import duckdb
import pandas as pd
import pytest

from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
from quantstudio.pipeline.qfq_maintenance import (
    QFQMaintenance,
    get_etf_universe,
    get_stock_universe,
    migrate_split_etf_factors,
)


class _NullRateLimiter:
    def acquire(self):
        pass


class _FakeTushareClient:
    """与 tushare adapter 接口对齐的 fake：adj_factor / fund_adj 返回 DataFrame。"""

    def adj_factor(self, ts_code, start_date, end_date):
        return pd.DataFrame(
            [{"ts_code": ts_code, "trade_date": "20260105", "adj_factor": 1.05}]
        )

    def fund_adj(self, ts_code, start_date, end_date):
        return pd.DataFrame(
            [{"ts_code": ts_code, "trade_date": "20260105", "adj_factor": 1.02}]
        )


class _FakeAdapter:
    def __init__(self):
        self._client = _FakeTushareClient()
        self.rate_limiter = _NullRateLimiter()


@pytest.fixture
def aux_db(tmp_path):
    p = tmp_path / "qfq_aux.db"
    conn = sqlite3.connect(str(p), timeout=30)
    init_sqlite_schema(conn)
    conn.commit()
    conn.close()
    return str(p)


def test_fetch_adj_factor_table_isolation(aux_db):
    """ETF 因子只进 fund_adj，股票因子只进 adj_factor；两张表彻底隔离。"""
    m = QFQMaintenance(db_path=aux_db)
    stock_n = m.fetch_adj_factor(_FakeAdapter(), ["600000.SH"], "20260101", "20260110", is_etf=False)
    etf_n = m.fetch_adj_factor(_FakeAdapter(), ["510300.SH"], "20260101", "20260110", is_etf=True)
    assert stock_n > 0
    assert etf_n > 0

    conn = sqlite3.connect(aux_db, timeout=30)
    adj_codes = {r[0] for r in conn.execute("SELECT code FROM adj_factor")}
    fund_codes = {r[0] for r in conn.execute("SELECT code FROM fund_adj")}
    conn.close()

    # 两张表完全不相交（彻底隔离）
    assert adj_codes.isdisjoint(fund_codes)
    # ETF 因子不出现在 adj_factor
    assert fund_codes and fund_codes.isdisjoint(adj_codes)
    # 各自至少写入一条
    assert len(adj_codes) >= 1
    assert len(fund_codes) >= 1


def test_fetch_adj_factor_writes_adj_factor_for_stock(aux_db):
    m = QFQMaintenance(db_path=aux_db)
    n = m.fetch_adj_factor(_FakeAdapter(), ["600000.SH", "600001.SH"], "20260101", "20260110", is_etf=False)
    assert n == 2
    conn = sqlite3.connect(aux_db, timeout=30)
    adj_codes = {r[0] for r in conn.execute("SELECT code FROM adj_factor")}
    fund_codes = {r[0] for r in conn.execute("SELECT code FROM fund_adj")}
    conn.close()
    assert "600000.SH" not in adj_codes  # 经 normalize_code 转裸码
    assert any(c.startswith("60000") or c == "600000" for c in adj_codes)
    # ETF 表在股票拉取时不被写入
    assert fund_codes == set()


def test_fetch_adj_factor_writes_fund_adj_for_etf(aux_db):
    m = QFQMaintenance(db_path=aux_db)
    n = m.fetch_adj_factor(_FakeAdapter(), ["510300.SH", "510050.SH"], "20260101", "20260110", is_etf=True)
    assert n == 2
    conn = sqlite3.connect(aux_db, timeout=30)
    fund_codes = {r[0] for r in conn.execute("SELECT code FROM fund_adj")}
    conn.close()
    assert fund_codes  # 经 normalize_code 转裸码


def test_migrate_split_etf_factors_dry_run_noop(aux_db):
    """dry_run=True 只统计不写入（fail-safe 预览）。"""
    conn = sqlite3.connect(aux_db, timeout=30)
    conn.execute("INSERT INTO adj_factor (code, time, adj_factor) VALUES (?,?,?)", ["600000", 1700000000000, 1.0])
    conn.execute("INSERT INTO adj_factor (code, time, adj_factor) VALUES (?,?,?)", ["510300", 1700000000000, 1.1])
    conn.commit()

    moved, sample = migrate_split_etf_factors(aux_db, etf_universe={"510300"}, dry_run=True)
    assert moved == 1
    assert sample  # 预览样本非空

    # 数据库未改变
    adj_after = {r[0] for r in conn.execute("SELECT code FROM adj_factor")}
    fund_after = {r[0] for r in conn.execute("SELECT code FROM fund_adj")}
    conn.close()
    assert adj_after == {"600000", "510300"}
    assert fund_after == set()


def test_migrate_split_etf_factors_executes_and_conserves(aux_db):
    """dry_run=False 实跑：ETF 行从 adj_factor 迁移到 fund_adj，行数守恒。"""
    conn = sqlite3.connect(aux_db, timeout=30)
    conn.execute("INSERT INTO adj_factor (code, time, adj_factor) VALUES (?,?,?)", ["600000", 1700000000000, 1.0])
    conn.execute("INSERT INTO adj_factor (code, time, adj_factor) VALUES (?,?,?)", ["510300", 1700000000000, 1.1])
    conn.commit()

    moved, sample = migrate_split_etf_factors(aux_db, etf_universe={"510300"}, dry_run=False)
    assert moved == 1

    adj_after = {r[0] for r in conn.execute("SELECT code FROM adj_factor")}
    fund_after = {r[0] for r in conn.execute("SELECT code FROM fund_adj")}
    # ETF 已从 adj_factor 移除、进入 fund_adj
    assert "510300" not in adj_after
    assert adj_after == {"600000"}
    assert fund_after == {"510300"}
    # 行数守恒（迁移不丢数据）
    total = (
        len(conn.execute("SELECT code FROM adj_factor").fetchall())
        + len(conn.execute("SELECT code FROM fund_adj").fetchall())
    )
    conn.close()
    assert total == 2


def test_migrate_split_etf_factors_empty_universe_noop(aux_db):
    """etf_universe 为空 → 不移动任何行。"""
    conn = sqlite3.connect(aux_db, timeout=30)
    conn.execute("INSERT INTO adj_factor (code, time, adj_factor) VALUES (?,?,?)", ["510300", 1700000000000, 1.1])
    conn.commit()
    moved, _ = migrate_split_etf_factors(aux_db, etf_universe=set(), dry_run=False)
    assert moved == 0
    adj_after = {r[0] for r in conn.execute("SELECT code FROM adj_factor")}
    conn.close()
    assert adj_after == {"510300"}


def test_get_stock_universe_missing_table_returns_empty(tmp_path):
    """正式表缺失时不抛异常，返回空集（防崩）。"""
    p = tmp_path / "missing.db"
    res = get_stock_universe(str(p))
    assert res == set()
    res2 = get_etf_universe(str(p))
    assert res2 == set()


def test_get_stock_universe_reads_constituents(tmp_path):
    p = tmp_path / "u.db"
    conn = duckdb.connect(str(p), read_only=False)
    conn.execute("CREATE TABLE index_constituents (code VARCHAR)")
    conn.execute("CREATE TABLE stock_basic (code VARCHAR)")
    conn.execute("CREATE TABLE etf_basic (code VARCHAR)")
    # get_stock_universe 只读 index_constituents；get_etf_universe 只读 etf_basic
    conn.execute("INSERT INTO index_constituents VALUES ('600000'),('600001'),('600002')")
    conn.execute("INSERT INTO stock_basic VALUES ('600002')")
    conn.execute("INSERT INTO etf_basic VALUES ('510300'),('510050')")
    conn.close()
    assert get_stock_universe(str(p)) == {"600000", "600001", "600002"}
    assert get_etf_universe(str(p)) == {"510300", "510050"}
