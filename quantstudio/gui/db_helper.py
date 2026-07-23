"""GUI 专用只读数据库查询封装。
DuckDB 连接用 read_only=True（避免与采集进程写入冲突）；SQLite 短连接。

v3 评审 4：daemon 采集期持有 DuckDB RW 连接（collector_run.lock 内），
此时 GUI 的 read_only 查询可能触发 "另一进程正在使用此文件" IOException。
所有 DuckDB 查询经 _safe_query 包装，捕获 IOException 后返回空 DataFrame
+ 警告日志，GUI 不崩溃（优雅降级）。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _is_db_busy_error(exc: Exception) -> bool:
    """识别 DuckDB 文件锁冲突异常（跨中英文环境）。"""
    msg = str(exc).lower()
    return ("another process" in msg or "used by another process" in msg
            or "另一进程" in str(exc) or "正在使用" in str(exc)
            or "could not lock" in msg or "io error" in msg)


class DbHelper:
    """GUI 专用的只读数据库查询封装。"""

    def __init__(self, duckdb_path, quarantine_path, batch_audit_path):
        self.duckdb_path = Path(duckdb_path)
        self.quarantine_path = Path(quarantine_path)
        self.batch_audit_path = Path(batch_audit_path)

    def _safe_query(self, sql: str) -> pd.DataFrame:
        """v3 评审 4：DuckDB 查询统一包装，捕获 IO/lock 异常优雅降级。

        daemon 采集期（持有 RW 连接）时，GUI read_only 查询会触发 IOException。
        返回空 DataFrame + 警告日志，调用方据此显示"数据库采集中，请稍后刷新"。
        其它异常（SQL 语法错等）正常向上抛。
        """
        import duckdb
        try:
            with duckdb.connect(str(self.duckdb_path), read_only=True) as conn:
                return conn.execute(sql).fetchdf()
        except duckdb.IOException as e:
            if _is_db_busy_error(e):
                logger.warning(f"[DbHelper] DuckDB 忙（daemon 采集中？），返回空结果: {e}")
                return pd.DataFrame()
            raise
        except Exception as e:
            # 兼容某些 duckdb 版本将 IO 错误归为普通 Exception 的情况
            if _is_db_busy_error(e):
                logger.warning(f"[DbHelper] DuckDB 忙（daemon 采集中？），返回空结果: {e}")
                return pd.DataFrame()
            raise

    # ---------------- DuckDB（主库，只读）----------------
    def query_duckdb(self, sql: str) -> pd.DataFrame:
        """通用只读 SQL 查询（经 _safe_query 包装，DB 忙时返回空）。"""
        return self._safe_query(sql)

    def list_tables(self) -> list:
        """SHOW TABLES"""
        try:
            import duckdb
            with duckdb.connect(str(self.duckdb_path), read_only=True) as conn:
                return [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        except Exception:
            return []

    def table_rowcount(self, table: str) -> int:
        try:
            import duckdb
            with duckdb.connect(str(self.duckdb_path), read_only=True) as conn:
                return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            return 0

    def preview_table(self, table: str, limit: int = 100,
                      where: str = "", order_by: str = "") -> pd.DataFrame:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        sql += f" LIMIT {limit}"
        return self.query_duckdb(sql)

    def get_watermarks(self) -> pd.DataFrame:
        try:
            return self.query_duckdb("SELECT * FROM source_watermark")
        except Exception:
            return pd.DataFrame()

    def get_table_columns(self, table: str) -> list:
        try:
            import duckdb
            with duckdb.connect(str(self.duckdb_path), read_only=True) as conn:
                return [(r[0], r[1]) for r in conn.execute(f"DESCRIBE {table}").fetchall()]
        except Exception:
            return []

    def get_date_range(self, table: str, code: Optional[str] = None) -> dict:
        try:
            where = f"WHERE code='{code}'" if code else ""
            df = self.query_duckdb(
                f"SELECT MIN(time) as min_t, MAX(time) as max_t, COUNT(*) as cnt "
                f"FROM {table} {where}")
            if len(df) == 0:
                return {}
            row = df.iloc[0]
            import datetime
            min_d = datetime.datetime.fromtimestamp(row["min_t"]/1000).strftime("%Y-%m-%d") if row["min_t"] else ""
            max_d = datetime.datetime.fromtimestamp(row["max_t"]/1000).strftime("%Y-%m-%d") if row["max_t"] else ""
            return {"min_date": min_d, "max_date": max_d, "count": int(row["cnt"])}
        except Exception:
            return {}

    # ---------------- SQLite（隔离区 + 批次审计）----------------
    def query_quarantine(self, sql: str) -> pd.DataFrame:
        if not self.quarantine_path.exists():
            return pd.DataFrame()
        with sqlite3.connect(self.quarantine_path) as conn:
            return pd.read_sql_query(sql, conn)

    def quarantine_stats(self) -> dict:
        if not self.quarantine_path.exists():
            return {}
        try:
            with sqlite3.connect(self.quarantine_path) as conn:
                cur = conn.execute("SELECT status, COUNT(*) FROM quarantine GROUP BY status")
                return dict(cur.fetchall())
        except Exception:
            return {}

    def query_quarantine_all(self, status=None, table=None) -> pd.DataFrame:
        """查询隔离区全部状态的数据（不限 pending_repair）"""
        if not self.quarantine_path.exists():
            return pd.DataFrame()
        sql = "SELECT * FROM quarantine WHERE 1=1"
        params = []
        if status and status != "全部":
            sql += " AND status=?"
            params.append(status)
        if table:
            sql += " AND table_name=?"
            params.append(table)
        sql += " ORDER BY ingested_at DESC LIMIT 500"
        try:
            with sqlite3.connect(self.quarantine_path) as conn:
                return pd.read_sql_query(sql, conn, params=params)
        except Exception:
            return pd.DataFrame()

    def query_batch_audit(self, limit: int = 20) -> pd.DataFrame:
        if not self.batch_audit_path.exists():
            return pd.DataFrame()
        with sqlite3.connect(self.batch_audit_path) as conn:
            return pd.read_sql_query(
                f"SELECT * FROM batch_audit ORDER BY finished_at DESC LIMIT {limit}", conn)
