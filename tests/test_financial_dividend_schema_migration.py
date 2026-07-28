"""
测试 DuckDBWriter 旧 schema 自动迁移（W1.6）。

验证：
- 旧表结构 → 自动补齐新列
- 新建表 → 包含全部列
- 二次初始化幂等
- Schema 回读验证
"""
import os
import tempfile
import pytest
import pandas as pd
from pathlib import Path


def _make_temp_db():
    """创建临时 DuckDB（通过 DuckDB 连接创建，确保文件是有效的 DB 文件）"""
    import duckdb
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # 删除空文件，让 DuckDB 创建合法文件
    conn = duckdb.connect(path)
    conn.close()
    return path


def _table_columns(conn, table):
    return {row[0] for row in conn.execute(f"DESCRIBE {table}").fetchall()}


# ---------------------------------------------------------------------------
# fin_indicator migration
# ---------------------------------------------------------------------------
def test_fin_indicator_old_to_new_migration():
    """旧 schema（无 or_yoy/update_flag/diluted_eps）→ 自动补齐"""
    import duckdb
    from quantstudio.pipeline.writers import DuckDBWriter

    db_path = _make_temp_db()
    try:
        # 建旧表（模拟迁移前状态）
        conn = duckdb.connect(db_path)
        conn.execute("""
            CREATE TABLE fin_indicator (
                code VARCHAR, ann_date BIGINT, end_date BIGINT,
                eps DOUBLE, bps DOUBLE, roe DOUBLE, pe_ttm DOUBLE, pb DOUBLE,
                ps_ttm DOUBLE, np_yoy DOUBLE,
                PRIMARY KEY(code, end_date, ann_date)
            )
        """)
        conn.close()

        # Writer 初始化 → 应自动 ALTER TABLE 补齐新列
        writer = DuckDBWriter({"path": db_path})
        # Writer 构造时自动调用 _init_tables() → ALTER TABLE 补齐
        # 验证新列存在
        conn = duckdb.connect(db_path, read_only=True)
        cols = _table_columns(conn, "fin_indicator")
        assert "or_yoy" in cols
        assert "tr_yoy" in cols
        assert "update_flag" in cols
        assert "diluted_eps" in cols
        conn.close()
    finally:
        os.unlink(db_path)


def test_fin_indicator_new_table_has_all_columns():
    """新建表 → CREATE TABLE 包含全部列"""
    import duckdb
    from quantstudio.pipeline.writers import DuckDBWriter

    db_path = _make_temp_db()
    try:
        writer = DuckDBWriter({"path": db_path})
        # Writer 构造时自动调用 _init_tables() → ALTER TABLE 补齐
        conn = duckdb.connect(db_path, read_only=True)
        cols = _table_columns(conn, "fin_indicator")
        expected = {"code", "ann_date", "end_date", "eps", "diluted_eps", "bps", "roe",
                    "pe_ttm", "pb", "ps_ttm", "np_yoy", "or_yoy", "tr_yoy", "update_flag"}
        assert expected <= cols, f"Missing: {expected - cols}"
        conn.close()
    finally:
        os.unlink(db_path)


def test_fin_indicator_migration_idempotent():
    """二次初始化幂等：不抛异常，列数不变"""
    import duckdb
    from quantstudio.pipeline.writers import DuckDBWriter

    db_path = _make_temp_db()
    try:
        writer = DuckDBWriter({"path": db_path})
        col_count_1 = len(_table_columns(duckdb.connect(db_path, read_only=True), "fin_indicator"))

        # 二次构造幂等
        writer2 = DuckDBWriter({"path": db_path})
        col_count_2 = len(_table_columns(duckdb.connect(db_path, read_only=True), "fin_indicator"))

        assert col_count_1 == col_count_2
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# stock_dividend migration
# ---------------------------------------------------------------------------
def test_stock_dividend_old_to_new_migration():
    """旧 schema（只有 cash_div/stk_div/div_rat）→ 自动补齐"""
    import duckdb
    from quantstudio.pipeline.writers import DuckDBWriter

    db_path = _make_temp_db()
    try:
        conn = duckdb.connect(db_path)
        conn.execute("""
            CREATE TABLE stock_dividend (
                code VARCHAR, ex_date BIGINT, record_date BIGINT,
                cash_div DOUBLE, stk_div DOUBLE, div_rat DOUBLE,
                update_time VARCHAR,
                PRIMARY KEY(code, ex_date)
            )
        """)
        conn.close()

        writer = DuckDBWriter({"path": db_path})
        # Writer 构造时自动调用 _init_tables() → ALTER TABLE 补齐
        conn = duckdb.connect(db_path, read_only=True)
        cols = _table_columns(conn, "stock_dividend")
        assert "cash_div_before_tax" in cols
        assert "cash_div_after_tax" in cols
        assert "stk_bo_rate" in cols
        assert "stk_co_rate" in cols
        assert "ann_date" in cols
        assert "end_date" in cols
        assert "div_proc" in cols
        conn.close()
    finally:
        os.unlink(db_path)


def test_stock_dividend_new_table_has_all_columns():
    """新建表包含全部列"""
    import duckdb
    from quantstudio.pipeline.writers import DuckDBWriter

    db_path = _make_temp_db()
    try:
        writer = DuckDBWriter({"path": db_path})
        # Writer 构造时自动调用 _init_tables() → ALTER TABLE 补齐
        conn = duckdb.connect(db_path, read_only=True)
        cols = _table_columns(conn, "stock_dividend")
        expected = {"code", "ex_date", "record_date", "ann_date", "end_date",
                    "cash_div_before_tax", "cash_div_after_tax",
                    "cash_div", "stk_div", "stk_bo_rate", "stk_co_rate",
                    "div_rat", "div_proc", "update_time"}
        assert expected <= cols, f"Missing: {expected - cols}"
        conn.close()
    finally:
        os.unlink(db_path)


def test_stock_dividend_migration_idempotent():
    """二次初始化幂等"""
    import duckdb
    from quantstudio.pipeline.writers import DuckDBWriter

    db_path = _make_temp_db()
    try:
        writer = DuckDBWriter({"path": db_path})
        col_count_1 = len(_table_columns(duckdb.connect(db_path, read_only=True), "stock_dividend"))
        writer2 = DuckDBWriter({"path": db_path})
        col_count_2 = len(_table_columns(duckdb.connect(db_path, read_only=True), "stock_dividend"))
        assert col_count_1 == col_count_2
    finally:
        os.unlink(db_path)
