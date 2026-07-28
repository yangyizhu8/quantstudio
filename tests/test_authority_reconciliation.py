"""W2-0.8 缺陷 B 测试：通用 authority_reconciliation 契约。

验证 _authority_reconcile 在正确条件下清除非权威 data_source 行 + watermark，
且在条件不满足（incremental / allow_fallback / 未声明）时不执行。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest


def _make_staging_db(tmp: Path) -> Path:
    """DuckDB with stock_dividend (mixed sources) + source_watermark."""
    db = tmp / "stg.db"
    c = duckdb.connect(str(db))
    c.execute(
        "CREATE TABLE stock_dividend ("
        " code VARCHAR, ex_date INTEGER, data_source VARCHAR, cash_div DOUBLE, "
        " PRIMARY KEY(code, ex_date))")
    # mixed: 3 tushare + 2 akshare + 1 NULL
    c.execute("INSERT INTO stock_dividend VALUES "
              "('000001.SZ',20240101,'tushare',0.8),"
              "('000002.SZ',20240102,'tushare',0.5),"
              "('000003.SZ',20240103,'tushare',1.0),"
              "('000004.SZ',20240104,'akshare',0.3),"
              "('000005.SZ',20240105,'akshare',0.2),"
              "('000006.SZ',20240106,NULL,0.1)")
    c.execute(
        "CREATE TABLE source_watermark ("
        " source VARCHAR, table_name VARCHAR, freq VARCHAR, last_date BIGINT, "
        " last_batch_id VARCHAR, updated_at TIMESTAMP, "
        " PRIMARY KEY(source, table_name, freq))")
    c.execute("INSERT INTO source_watermark VALUES "
              "('tushare','stock_dividend','daily',20240103,'b1','2026-07-28'),"
              "('akshare','stock_dividend','daily',20240105,'b2','2026-07-28')")
    c.close()
    return db


def _make_collector(db: Path, tmp: Path):
    """Build a minimal ResidentCollector-like object with writer + _conn_lock."""
    from quantstudio.pipeline.writers import DuckDBWriter
    import threading
    w = DuckDBWriter({"path": str(db)})
    coll = MagicMock()
    coll.writer = w
    # _authority_reconcile is a method on ResidentCollector; bind it
    from quantstudio.pipeline.daemon import ResidentCollector
    coll._authority_reconcile = ResidentCollector._authority_reconcile.__get__(coll)
    return coll, w


class TestAuthorityReconcileFullRange:
    """full_range + authority=tushare + fallback=false + enabled → 清除非权威行+watermark。"""

    def test_purges_non_authoritative_rows_and_watermarks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_staging_db(tmp_p)
            coll, w = _make_collector(db, tmp_p)
            task = {
                "name": "stock_dividend", "table": "stock_dividend",
                "authoritative_source": "tushare", "allow_fallback": False,
                "mode": "full_range",
                "authority_reconciliation": {
                    "enabled": True, "mode": "purge_non_authoritative",
                    "scope": "full_range_only", "cleanup_source_watermark": True},
            }
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert res["enabled"] and res["ran"], f"should run: {res}"
                assert res["ok"], f"should succeed: {res['reason']}"
                # 3 akshare+NULL rows purged (2 akshare + 1 NULL)
                assert res["rows_purged"] == 3, f"expected 3 purged, got {res['rows_purged']}"
                assert res["watermarks_purged"] == 1, f"expected 1 wm purged, got {res['watermarks_purged']}"
            finally:
                w.close()
            # verify only tushare remains (after writer closed, read_only is safe)
            c = duckdb.connect(str(db), read_only=True)
            sources = {r[0] for r in c.execute(
                "SELECT DISTINCT data_source FROM stock_dividend").fetchall()}
            c.close()
            assert sources == {"tushare"}, f"only tushare should remain, got {sources}"


class TestAuthorityReconcileConditions:
    """条件不满足时不执行。"""

    def test_incremental_does_not_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_staging_db(tmp_p)
            coll, w = _make_collector(db, tmp_p)
            task = {
                "name": "stock_dividend", "table": "stock_dividend",
                "authoritative_source": "tushare", "allow_fallback": False,
                "mode": "incremental",
                "authority_reconciliation": {"enabled": True, "mode": "purge_non_authoritative",
                                              "scope": "full_range_only", "cleanup_source_watermark": True},
            }
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert res["enabled"] and not res["ran"], f"incremental must not run: {res}"
                # data unchanged
                c = duckdb.connect(str(db), read_only=True)
                n = c.execute("SELECT COUNT(*) FROM stock_dividend").fetchone()[0]
                c.close()
                assert n == 6, "incremental must not purge"
            finally:
                w.close()

    def test_allow_fallback_does_not_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_staging_db(tmp_p)
            coll, w = _make_collector(db, tmp_p)
            task = {
                "name": "stock_dividend", "table": "stock_dividend",
                "authoritative_source": "tushare", "allow_fallback": True,  # not strict
                "mode": "full_range",
                "authority_reconciliation": {"enabled": True, "mode": "purge_non_authoritative",
                                              "scope": "full_range_only", "cleanup_source_watermark": True},
            }
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert res["enabled"] and not res["ran"]
            finally:
                w.close()

    def test_not_declared_does_not_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_staging_db(tmp_p)
            coll, w = _make_collector(db, tmp_p)
            task = {
                "name": "stock_dividend", "table": "stock_dividend",
                "authoritative_source": "tushare", "allow_fallback": False,
                "mode": "full_range",
                # NO authority_reconciliation key
            }
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert not res["enabled"], "undeclared must not enable"
                assert not res["ran"]
            finally:
                w.close()

    def test_other_tables_watermark_untouched(self):
        """清理只影响目标表，不影响其他表的 watermark。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_staging_db(tmp_p)
            # add an unrelated table watermark
            c = duckdb.connect(str(db))
            c.execute("INSERT INTO source_watermark VALUES "
                      "('xtquant','stock_daily','daily',20240107,'bx','2026-07-28')")
            c.close()
            coll, w = _make_collector(db, tmp_p)
            task = {
                "name": "stock_dividend", "table": "stock_dividend",
                "authoritative_source": "tushare", "allow_fallback": False,
                "mode": "full_range",
                "authority_reconciliation": {"enabled": True, "mode": "purge_non_authoritative",
                                              "scope": "full_range_only", "cleanup_source_watermark": True},
            }
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert res["ok"]
            finally:
                w.close()
            # verify other tables' watermark untouched (after writer closed)
            c = duckdb.connect(str(db), read_only=True)
            # stock_daily watermark must still be there
            n = c.execute("SELECT COUNT(*) FROM source_watermark WHERE table_name='stock_daily'").fetchone()[0]
            c.close()
            assert n == 1, "other tables' watermarks must be untouched"


class TestAuthorityReconcileContractStrictness:
    """W2-0.9 缺陷 B 补完：完整契约 + fail-fast + 全触发条件 + 事务。"""

    def _task(self, **overrides):
        base = {
            "name": "stock_dividend", "table": "stock_dividend",
            "authoritative_source": "tushare", "allow_fallback": False,
            "mode": "full_range",
            "authority_reconciliation": {
                "enabled": True, "mode": "purge_non_authoritative",
                "scope": "full_range_only", "cleanup_source_watermark": True},
        }
        base.update(overrides)
        return base

    def test_invalid_mode_fail_fast_no_delete(self):
        """enabled=true 但 mode 非法 → ok=False，不执行任何 DELETE。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_staging_db(tmp_p)
            coll, w = _make_collector(db, tmp_p)
            task = self._task(authority_reconciliation={
                "enabled": True, "mode": "BOGUS", "scope": "full_range_only",
                "cleanup_source_watermark": True})
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert res["enabled"] and not res["ok"], f"must fail-fast: {res}"
                assert "mode" in res["reason"]
            finally:
                w.close()
            # data untouched
            c = duckdb.connect(str(db), read_only=True)
            n = c.execute("SELECT COUNT(*) FROM stock_dividend").fetchone()[0]
            c.close()
            assert n == 6, "invalid mode must not delete anything"

    def test_invalid_scope_fail_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_staging_db(tmp_p)
            coll, w = _make_collector(db, tmp_p)
            task = self._task(authority_reconciliation={
                "enabled": True, "mode": "purge_non_authoritative", "scope": "BOGUS",
                "cleanup_source_watermark": True})
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert not res["ok"] and "scope" in res["reason"]
            finally:
                w.close()

    def test_actual_source_not_authoritative_does_not_run(self):
        """本轮 source != authoritative_source → 不运行。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_staging_db(tmp_p)
            coll, w = _make_collector(db, tmp_p)
            task = self._task()  # authoritative=tushare
            try:
                res = coll._authority_reconcile(task, "akshare", "stock_dividend", "b1")
                assert res["enabled"] and not res["ran"], f"actual source != auth must not run: {res}"
                assert "actual source" in res["reason"]
            finally:
                w.close()

    def test_cleanup_watermark_false_skips_watermark_delete(self):
        """cleanup_source_watermark=False → 删数据行但不删 watermark。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_staging_db(tmp_p)
            coll, w = _make_collector(db, tmp_p)
            task = self._task(authority_reconciliation={
                "enabled": True, "mode": "purge_non_authoritative",
                "scope": "full_range_only", "cleanup_source_watermark": False})
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert res["ok"], f"should succeed: {res['reason']}"
                assert res["rows_purged"] == 3, "non-auth rows purged"
                assert res["watermarks_purged"] == 0, "watermark must NOT be deleted"
            finally:
                w.close()
            # akshare watermark still present
            c = duckdb.connect(str(db), read_only=True)
            n = c.execute("SELECT COUNT(*) FROM source_watermark WHERE source='akshare'").fetchone()[0]
            c.close()
            assert n == 1, "akshare watermark must remain when cleanup_watermark=False"

    def test_empty_table_after_purge_does_not_silently_pass(self):
        """full_range 有可用写入结果但表空（purge 后无权威行）→ 不静默 PASS。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = tmp_p / "stg.db"
            c = duckdb.connect(str(db))
            c.execute("CREATE TABLE stock_dividend (code VARCHAR, ex_date INTEGER, "
                      "data_source VARCHAR, cash_div DOUBLE, PRIMARY KEY(code, ex_date))")
            # all non-authoritative → purge → empty table
            c.execute("INSERT INTO stock_dividend VALUES "
                      "('A',1,'akshare',0.3),('B',2,'akshare',0.2)")
            c.close()
            coll, w = _make_collector(db, tmp_p)
            task = self._task()
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert not res["ok"], "empty table after purge must NOT silently pass"
                assert "empty" in res["reason"].lower() or "source set" in res["reason"].lower()
            finally:
                w.close()

    def test_transaction_rollback_on_post_check_failure(self):
        """后置校验失败时事务整体 ROLLBACK，不留半删除状态。

        构造：表里只有 akshare 行（无 tushare），purge 后应为空 → 后置校验失败 → rollback。
        rollback 后 akshare 行应仍在（未被半删除）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = tmp_p / "stg.db"
            c = duckdb.connect(str(db))
            c.execute("CREATE TABLE stock_dividend (code VARCHAR, ex_date INTEGER, "
                      "data_source VARCHAR, cash_div DOUBLE, PRIMARY KEY(code, ex_date))")
            c.execute("INSERT INTO stock_dividend VALUES ('A',1,'akshare',0.3),('B',2,'akshare',0.2)")
            c.close()
            coll, w = _make_collector(db, tmp_p)
            task = self._task()
            try:
                res = coll._authority_reconcile(task, "tushare", "stock_dividend", "b1")
                assert not res["ok"]
            finally:
                w.close()
            # After rollback, akshare rows should still be there (no half-delete)
            c = duckdb.connect(str(db), read_only=True)
            n = c.execute("SELECT COUNT(*) FROM stock_dividend").fetchone()[0]
            c.close()
            assert n == 2, f"rollback must restore all rows, got {n}"

