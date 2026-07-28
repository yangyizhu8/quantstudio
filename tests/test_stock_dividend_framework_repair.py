"""W2-0.8 缺陷 A 测试：writer DDL 驱动 VARCHAR 类型保护（div_proc 不再被 to_numeric）。

验证 stock_dividend 的 VARCHAR 列（div_proc/data_source 等）落库后保留原值，
而非被 pd.to_numeric(errors="coerce") 转成 NaN→NULL。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import duckdb
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_writer_db(tmp: Path) -> Path:
    """Create a DuckDB with stock_dividend schema (VARCHAR div_proc)."""
    db = tmp / "w.db"
    c = duckdb.connect(str(db))
    c.execute(
        "CREATE TABLE stock_dividend ("
        " code VARCHAR, ex_date INTEGER, record_date INTEGER, ann_date INTEGER,"
        " end_date INTEGER, cash_div DOUBLE, stk_div DOUBLE, div_rat DOUBLE,"
        " update_time VARCHAR, data_source VARCHAR, cash_div_before_tax DOUBLE,"
        " cash_div_after_tax DOUBLE, stk_bo_rate DOUBLE, stk_co_rate DOUBLE,"
        " div_proc VARCHAR, PRIMARY KEY(code, ex_date))"
    )
    c.execute(
        "CREATE TABLE fin_indicator ("
        " code VARCHAR, ann_date INTEGER, end_date INTEGER, eps DOUBLE,"
        " data_source VARCHAR, np_yoy DOUBLE, PRIMARY KEY(code, end_date, ann_date))"
    )
    c.close()
    return db


class TestDivProcPreserved:
    """div_proc='实施' 必须原值落库，不能变 NULL。"""

    def test_div_proc_implementation_preserved(self):
        from quantstudio.pipeline.writers import DuckDBWriter
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_writer_db(tmp_p)
            w = DuckDBWriter({"path": str(db)})
            df = pd.DataFrame([{
                "code": "000001.SZ", "ex_date": 20240601, "record_date": 20240531,
                "ann_date": 20240130, "end_date": 20231231, "cash_div": 0.8,
                "stk_div": 0.1, "div_rat": 0.0, "update_time": "2024-01-30",
                "data_source": "tushare", "cash_div_before_tax": 1.0,
                "cash_div_after_tax": 0.8, "stk_bo_rate": 0.05, "stk_co_rate": 0.05,
                "div_proc": "实施",
            }])
            w.write(df, "stock_dividend", "b1")
            w.close()
            c = duckdb.connect(str(db), read_only=True)
            val = c.execute(
                "SELECT div_proc FROM stock_dividend WHERE code='000001.SZ'").fetchone()[0]
            c.close()
            assert val == "实施", f"div_proc must be '实施', got {val!r}"

    def test_div_proc_none_preserved(self):
        from quantstudio.pipeline.writers import DuckDBWriter
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_writer_db(tmp_p)
            w = DuckDBWriter({"path": str(db)})
            df = pd.DataFrame([{
                "code": "000002.SZ", "ex_date": 20240602, "record_date": 20240530,
                "ann_date": 20240129, "end_date": 20231231, "cash_div": 0.5,
                "stk_div": 0.0, "div_rat": 0.0, "update_time": "2024-01-29",
                "data_source": "tushare", "cash_div_before_tax": 0.625,
                "cash_div_after_tax": 0.5, "stk_bo_rate": 0.0, "stk_co_rate": 0.0,
                "div_proc": None,
            }])
            w.write(df, "stock_dividend", "b2")
            w.close()
            c = duckdb.connect(str(db), read_only=True)
            val = c.execute(
                "SELECT div_proc FROM stock_dividend WHERE code='000002.SZ'").fetchone()
            c.close()
            assert val[0] is None, f"div_proc None must stay NULL, got {val!r}"

    def test_div_proc_cancel_preserved(self):
        """非 '实施' 的字符串值（如 '取消'）也必须保留。"""
        from quantstudio.pipeline.writers import DuckDBWriter
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_writer_db(tmp_p)
            w = DuckDBWriter({"path": str(db)})
            df = pd.DataFrame([{
                "code": "000004.SZ", "ex_date": 20240604, "record_date": 20240601,
                "ann_date": 20240201, "end_date": 20231231, "cash_div": 0.0,
                "stk_div": 0.0, "div_rat": 0.0, "update_time": "2024-02-01",
                "data_source": "tushare", "cash_div_before_tax": 0.0,
                "cash_div_after_tax": 0.0, "stk_bo_rate": 0.0, "stk_co_rate": 0.0,
                "div_proc": "取消",
            }])
            w.write(df, "stock_dividend", "b4")
            w.close()
            c = duckdb.connect(str(db), read_only=True)
            val = c.execute(
                "SELECT div_proc FROM stock_dividend WHERE code='000004.SZ'").fetchone()[0]
            c.close()
            assert val == "取消", f"div_proc '取消' must be preserved, got {val!r}"


class TestNumericColumnsStillCoerced:
    """数值列（object dtype 的数字字符串）仍应被正确转 DOUBLE。"""

    def test_cash_div_numeric_string_coerced(self):
        from quantstudio.pipeline.writers import DuckDBWriter
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_writer_db(tmp_p)
            w = DuckDBWriter({"path": str(db)})
            # cash_div_before_tax as object (string "1.0") → should coerce to 1.0 DOUBLE
            df = pd.DataFrame([{
                "code": "600000.SH", "ex_date": 20240605, "record_date": 20240602,
                "ann_date": 20240202, "end_date": 20231231, "cash_div": "0.8",
                "stk_div": "0.1", "div_rat": "0.0", "update_time": "2024-02-02",
                "data_source": "tushare", "cash_div_before_tax": "1.0",
                "cash_div_after_tax": "0.8", "stk_bo_rate": "0.05",
                "stk_co_rate": "0.05", "div_proc": "实施",
            }])
            w.write(df, "stock_dividend", "b5")
            w.close()
            c = duckdb.connect(str(db), read_only=True)
            pre = c.execute(
                "SELECT cash_div_before_tax FROM stock_dividend WHERE code='600000.SH'"
            ).fetchone()[0]
            c.close()
            assert pre == 1.0, f"numeric string '1.0' must coerce to 1.0 DOUBLE, got {pre!r}"


class TestFinIndicatorDataPreserved:
    """fin_indicator 的 data_source VARCHAR 不被破坏。"""

    def test_fin_indicator_data_source_preserved(self):
        from quantstudio.pipeline.writers import DuckDBWriter
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_writer_db(tmp_p)
            w = DuckDBWriter({"path": str(db)})
            df = pd.DataFrame([{
                "code": "000001.SZ", "ann_date": 20240130, "end_date": 20231231,
                "eps": 1.5, "data_source": "tushare", "np_yoy": 10.0,
            }])
            w.write(df, "fin_indicator", "bf1")
            w.close()
            c = duckdb.connect(str(db), read_only=True)
            ds = c.execute(
                "SELECT data_source FROM fin_indicator WHERE code='000001.SZ'").fetchone()[0]
            c.close()
            assert ds == "tushare"


class TestDescribeFailureFallback:
    """W2-0.9 缺陷 A 补完：DESCRIBE 失败时 fallback 白名单必须含 div_proc。"""

    def test_div_proc_preserved_when_describe_fails(self, monkeypatch):
        """模拟 DESCRIBE 抛异常 → fallback str_cols（含 div_proc）→ '实施' 保留。

        直接在 writer 实例上劫持 DESCRIBE 查询：用一个真实连接代理所有调用，
        但 execute() 收到 DESCRIBE 时抛异常。其余（register/execute 写入/close）
        全部透传真实连接。
        """
        from quantstudio.pipeline.writers import DuckDBWriter
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = _make_writer_db(tmp_p)
            w = DuckDBWriter({"path": str(db)})

            # Each _conn() call opens a fresh real connection; DESCRIBE raises,
            # all other calls (register/execute/close) proxy to that fresh conn.
            # The writer's DESCRIBE block opens+close its own conn, and the write
            # block opens+close a separate conn, so no "already closed" conflict.

            def _broken_conn():
                real_conn = w._duckdb.connect(str(w.db_path))

                class _Proxy:
                    def execute(self, sql, *a, **kw):
                        if isinstance(sql, str) and sql.strip().upper().startswith("DESCRIBE"):
                            raise RuntimeError("simulated DESCRIBE failure")
                        return real_conn.execute(sql, *a, **kw)

                    def register(self, name, obj):
                        return real_conn.register(name, obj)

                    def unregister(self, name):
                        return real_conn.unregister(name)

                    def close(self):
                        return real_conn.close()

                return _Proxy()

            monkeypatch.setattr(w, "_conn", _broken_conn)
            df = pd.DataFrame([{
                "code": "000007.SZ", "ex_date": 20240607, "record_date": 20240604,
                "ann_date": 20240203, "end_date": 20231231, "cash_div": 0.4,
                "stk_div": 0.0, "div_rat": 0.0, "update_time": "2024-02-03",
                "data_source": "tushare", "cash_div_before_tax": 0.5,
                "cash_div_after_tax": 0.4, "stk_bo_rate": 0.0, "stk_co_rate": 0.0,
                "div_proc": "实施",
            }])
            w.write(df, "stock_dividend", "b7")
            w.close()
            # Re-open with a fresh clean connection to verify the row persisted.
            c = duckdb.connect(str(db), read_only=True)
            val = c.execute(
                "SELECT div_proc FROM stock_dividend WHERE code='000007.SZ'").fetchone()[0]
            c.close()
            assert val == "实施", (
                f"DESCRIBE-failure fallback must still preserve div_proc='实施', got {val!r}")

