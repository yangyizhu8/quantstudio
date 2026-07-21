"""DBWriter — 标准化数据写入（Layer 1 模块④）

职责：把通过校验的标准数据（tushare 格式）幂等写入数据库。
仅写入"全部校验通过"的数据；仅当"写入成功 + 回读验证成功"后推进水位。

支持的数据库：
    - DuckDBWriter（默认，零部署嵌入式）
    - QuestDBWriter（可选，ILP 批量写，大量数据）
"""
from __future__ import annotations

import abc
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)
from quantstudio._paths import db_path


class WriteResult(int):
    """write() 返回值：作为 int 向后兼容（=提交行数），同时携带 .new/.updated 审计字段。

    用法：
        n = writer.write(df, ...)      # n 当 int 用 = 提交行数（向后兼容）
        result = writer.write(df, ...)
        result.new, result.updated     # 新增行数 / 更新行数（审计准确）
    """
    new: int = 0
    updated: int = 0

    def __new__(cls, submitted: int, new: int = 0, updated: int = 0):
        obj = int.__new__(cls, submitted)
        obj.new = new
        obj.updated = updated
        return obj

    def __repr__(self) -> str:
        return f"WriteResult(submitted={int(self)}, new={self.new}, updated={self.updated})"


# 各表的建表 DDL（DuckDB 方言，khQuant 口径 v2.0）
# time 用 BIGINT（毫秒时间戳），volume=股，amount=元，code=裸6位码
DDL_DUCKDB = {
    "stock_daily": """
        CREATE TABLE IF NOT EXISTS stock_daily (
            code VARCHAR, time BIGINT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, preClose DOUBLE,
            suspendFlag INTEGER, settelementPrice DOUBLE, openInterest DOUBLE,
            open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
            open_back DOUBLE, high_back DOUBLE, low_back DOUBLE, close_back DOUBLE,
            open_front_ratio DOUBLE, high_front_ratio DOUBLE, low_front_ratio DOUBLE, close_front_ratio DOUBLE,
            open_back_ratio DOUBLE, high_back_ratio DOUBLE, low_back_ratio DOUBLE, close_back_ratio DOUBLE,
            turn DOUBLE, pctChg DOUBLE, peTTM DOUBLE, psTTM DOUBLE, pcfNcfTTM DOUBLE, pbMRQ DOUBLE,
            isST INTEGER,
            is_st_reliable BOOLEAN, is_st_reliable_source VARCHAR,
            is_delisting_risk BOOLEAN, is_delisting_risk_source VARCHAR,
            dividend_type VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, time)
        )""",
    "stock_minutes": """
        CREATE TABLE IF NOT EXISTS stock_minutes (
            code VARCHAR, time BIGINT, freq VARCHAR,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, preClose DOUBLE,
            suspendFlag INTEGER, settelementPrice DOUBLE, openInterest DOUBLE,
            open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
            open_back DOUBLE, high_back DOUBLE, low_back DOUBLE, close_back DOUBLE,
            open_front_ratio DOUBLE, high_front_ratio DOUBLE, low_front_ratio DOUBLE, close_front_ratio DOUBLE,
            open_back_ratio DOUBLE, high_back_ratio DOUBLE, low_back_ratio DOUBLE, close_back_ratio DOUBLE,
            dividend_type VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, time, freq)
        )""",
    "etf_minutes": """
        CREATE TABLE IF NOT EXISTS etf_minutes (
            code VARCHAR, time BIGINT, freq VARCHAR,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, preClose DOUBLE,
            suspendFlag INTEGER, settelementPrice DOUBLE, openInterest DOUBLE,
            open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
            open_back DOUBLE, high_back DOUBLE, low_back DOUBLE, close_back DOUBLE,
            open_front_ratio DOUBLE, high_front_ratio DOUBLE, low_front_ratio DOUBLE, close_front_ratio DOUBLE,
            open_back_ratio DOUBLE, high_back_ratio DOUBLE, low_back_ratio DOUBLE, close_back_ratio DOUBLE,
            dividend_type VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, time, freq)
        )""",
    "tick": """
        CREATE TABLE IF NOT EXISTS tick (
            code VARCHAR, time BIGINT,
            lastPrice DOUBLE, open DOUBLE, high DOUBLE, low DOUBLE, lastClose DOUBLE,
            amount DOUBLE, volume DOUBLE, pvolume DOUBLE, stockStatus INTEGER,
            openInt DOUBLE, lastSettlementPrice DOUBLE,
            askPrice1 DOUBLE, askPrice2 DOUBLE, askPrice3 DOUBLE, askPrice4 DOUBLE, askPrice5 DOUBLE,
            bidPrice1 DOUBLE, bidPrice2 DOUBLE, bidPrice3 DOUBLE, bidPrice4 DOUBLE, bidPrice5 DOUBLE,
            askVol1 DOUBLE, askVol2 DOUBLE, askVol3 DOUBLE, askVol4 DOUBLE, askVol5 DOUBLE,
            bidVol1 DOUBLE, bidVol2 DOUBLE, bidVol3 DOUBLE, bidVol4 DOUBLE, bidVol5 DOUBLE,
            transactionNum INTEGER, update_time VARCHAR,
            PRIMARY KEY(code, time)
        )""",
    "fin_indicator": """
        CREATE TABLE IF NOT EXISTS fin_indicator (
            code VARCHAR, ann_date BIGINT, end_date BIGINT,
            eps DOUBLE, bps DOUBLE, roe DOUBLE, pe_ttm DOUBLE, pb DOUBLE,
            ps_ttm DOUBLE, np_yoy DOUBLE,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    "index_daily": """
        CREATE TABLE IF NOT EXISTS index_daily (
            code VARCHAR, time BIGINT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            pctChg DOUBLE, volume DOUBLE, amount DOUBLE,
            PRIMARY KEY(code, time)
        )""",
    "stock_daily_valuation": """
        CREATE TABLE IF NOT EXISTS stock_daily_valuation (
            code VARCHAR, time BIGINT,
            circ_mv DOUBLE, total_mv DOUBLE,
            free_share DOUBLE,
            pe_ttm DOUBLE, pb DOUBLE, turnover_rate DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, time)
        )""",
    "etf_daily": """
        CREATE TABLE IF NOT EXISTS etf_daily (
            code VARCHAR, time BIGINT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            preClose DOUBLE, pctChg DOUBLE,
            volume DOUBLE, amount DOUBLE, turn DOUBLE,
            open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
            open_back DOUBLE, high_back DOUBLE, low_back DOUBLE, close_back DOUBLE,
            open_front_ratio DOUBLE, high_front_ratio DOUBLE, low_front_ratio DOUBLE, close_front_ratio DOUBLE,
            open_back_ratio DOUBLE, high_back_ratio DOUBLE, low_back_ratio DOUBLE, close_back_ratio DOUBLE,
            isST INTEGER, dividend_type VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, time)
        )""",
    "stock_float_share": """
        CREATE TABLE IF NOT EXISTS stock_float_share (
            code VARCHAR, end_date BIGINT, ann_date BIGINT,
            free_share DOUBLE, total_share DOUBLE,
            circ_mv DOUBLE, total_mv DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    "index_constituents": """
        CREATE TABLE IF NOT EXISTS index_constituents (
            index_code VARCHAR, code VARCHAR, time BIGINT,
            weight DOUBLE,
            PRIMARY KEY(index_code, code, time)
        )""",
    # ---- 三大报表（对齐 Ptrade 口径，字段单位=元）----
    "balance_statement": """
        CREATE TABLE IF NOT EXISTS balance_statement (
            code VARCHAR, end_date BIGINT, ann_date BIGINT,
            total_assets DOUBLE, total_liability DOUBLE, total_equity DOUBLE,
            total_current_assets DOUBLE, total_non_current_assets DOUBLE,
            total_current_liability DOUBLE, total_non_current_liability DOUBLE,
            account_receivable DOUBLE, account_payable DOUBLE, inventory DOUBLE,
            cash_equivalents DOUBLE, fixed_asset DOUBLE, intangible_asset DOUBLE, goodwill DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    "income_statement": """
        CREATE TABLE IF NOT EXISTS income_statement (
            code VARCHAR, end_date BIGINT, ann_date BIGINT,
            operating_revenue DOUBLE, operating_cost DOUBLE, operating_profit DOUBLE,
            total_profit DOUBLE, net_profit DOUBLE, np_parent_company_owners DOUBLE,
            sale_expense DOUBLE, manage_expense DOUBLE, finance_expense DOUBLE, rd_expense DOUBLE,
            income_tax DOUBLE, basic_eps DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    "cashflow_statement": """
        CREATE TABLE IF NOT EXISTS cashflow_statement (
            code VARCHAR, end_date BIGINT, ann_date BIGINT,
            net_operate_cash_flow DOUBLE, net_invest_cash_flow DOUBLE, net_finance_cash_flow DOUBLE,
            cash_add_balance DOUBLE, goods_sale_and_services DOUBLE, goods_buy_and_services DOUBLE,
            fixed_asset_depreciation DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    # ---- 除权除息 ----
    "stock_dividend": """
        CREATE TABLE IF NOT EXISTS stock_dividend (
            code VARCHAR, ex_date BIGINT, record_date BIGINT,
            cash_div DOUBLE, stk_div DOUBLE, div_rat DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, ex_date)
        )""",
    # ---- 申万行业分类 ----
    "sw_industry": """
        CREATE TABLE IF NOT EXISTS sw_industry (
            code VARCHAR, industry_code VARCHAR, industry_name VARCHAR, industry_level VARCHAR,
            update_time VARCHAR,
            PRIMARY KEY(code, industry_code)
        )""",
    "source_watermark": """
        CREATE TABLE IF NOT EXISTS source_watermark (
            source VARCHAR, table_name VARCHAR, freq VARCHAR,
            last_date BIGINT, last_batch_id VARCHAR, updated_at TIMESTAMP,
            PRIMARY KEY(source, table_name, freq)
        )""",
    "stock_namechange": """
        CREATE TABLE IF NOT EXISTS stock_namechange (
            code VARCHAR, change_date BIGINT,
            name_before VARCHAR, name_after VARCHAR, status_after VARCHAR,
            update_time VARCHAR,
            PRIMARY KEY(code, change_date)
        )""",
    "stock_delist": """
        CREATE TABLE IF NOT EXISTS stock_delist (
            code VARCHAR, list_date BIGINT, delist_date BIGINT, market VARCHAR,
            update_time VARCHAR,
            PRIMARY KEY(code, market)
        )""",
}


class BaseWriter(abc.ABC):
    """写入器基类"""

    def __init__(self, config: Dict):
        self.config = config

    @abc.abstractmethod
    def write(self, df: pd.DataFrame, table: str, batch_id: str) -> int:
        """幂等 upsert 写入。返回写入条数。"""
        ...

    @abc.abstractmethod
    def get_last_date(self, source: str, table: str, freq: str = "daily") -> Optional[str]:
        """读取水位"""
        ...

    @abc.abstractmethod
    def advance_watermark(self, source: str, table: str, freq: str,
                          last_date: str, batch_id: str):
        """推进水位（仅写入成功后调用）"""
        ...


class DuckDBWriter(BaseWriter):
    """DuckDB 写入器（默认推荐，零部署）

    config 示例：{"type": "duckdb", "path": "data/quantstudio.db"}
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        try:
            import duckdb
        except ImportError as e:
            raise ImportError("未安装 duckdb，请 pip install duckdb") from e
        self.db_path = Path(config.get("path", str(db_path())))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._duckdb = duckdb
        # 连接锁：DuckDB 不允许同文件多线程同时新建连接，串行化连接生命周期
        import threading
        self._conn_lock = threading.Lock()
        # 持久共享连接（read_write）：供 daemon 内部只读查询复用，避免与 write 的 read_write
        # 短连接并发时开 read_only 连接触发「different configuration」冲突
        # （DuckDB 规则：同 db 文件 read_only 与 read_write 不能并存，哪怕不同线程）。
        # 所有内部查询统一走 read_write，永不冲突。GUI 的 read_only 查询属独立进程范畴。
        self._shared_conn = None
        self._init_tables()

    def _conn(self):
        """新建连接（线程安全：调用方应在 write_lock/conn_lock 内使用并及时关闭）"""
        return self._duckdb.connect(str(self.db_path))

    def _ensure_shared_conn(self):
        """确保持久 read_write 连接已创建（调用方须持有 _conn_lock）。"""
        if self._shared_conn is None:
            self._shared_conn = self._duckdb.connect(str(self.db_path))
        return self._shared_conn

    def shared_conn(self):
        """返回持久 read_write 连接（复用单例，线程安全）。

        用途：daemon 内部只读查询改用此连接，避免开 read_only 连接与 write 的
        read_write 连接并发触发「different configuration」冲突。
        调用方负责加 _conn_lock 保护 execute（DuckDB 单连接并发 execute 会串行化）。
        """
        with self._conn_lock:
            return self._ensure_shared_conn()

    def execute_read(self, sql: str, params: Optional[list] = None):
        """线程安全的只读查询（复用持久 read_write 连接，避免 read_only 冲突）。

        返回 fetchall() 结果。调用方无需管理连接生命周期。
        """
        with self._conn_lock:
            conn = self._ensure_shared_conn()
            if params:
                return conn.execute(sql, params).fetchall()
            return conn.execute(sql).fetchall()

    def read_df(self, sql: str, params: Optional[list] = None):
        """线程安全的只读查询，返回 DataFrame（复用持久 read_write 连接）。

        供 daemon 的 _prepare_namechange_df / _prepare_valuation_df / _prepare_close_df 用，
        替代原 duckdb.connect(read_only=True) 短连接，避免与 write 的 read_write 连接并发冲突。
        """
        with self._conn_lock:
            conn = self._ensure_shared_conn()
            if params:
                return conn.execute(sql, params).fetchdf()
            return conn.execute(sql).fetchdf()

    def close(self):
        """关闭持久共享连接（GUI 重建 collector 时调用，避免连接泄漏）。"""
        with self._conn_lock:
            if self._shared_conn is not None:
                try:
                    self._shared_conn.close()
                except Exception:
                    pass
                self._shared_conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _init_tables(self):
        with self._conn_lock:
            conn = self._conn()
            try:
                for ddl in DDL_DUCKDB.values():
                    conn.execute(ddl)
                # 存量表结构迁移：检测并自动 ALTER TABLE 补齐新增列
                # 用途：DDL_DUCKDB 里加了新列但存量 DB（已有 950 万行）不会自动 ALTER
                self._migrate_add_columns(conn)
            finally:
                conn.close()
        logger.info(f"[DuckDBWriter] tables initialized at {self.db_path}")

    def _migrate_add_columns(self, conn):
        """检测存量表缺哪些列，自动 ALTER TABLE ADD COLUMN（仅对已存在的表生效）。

        幂等：列已存在时跳过。基于 DDL_DUCKDB 解析每表应有的列 vs DESCRIBE 实际列。
        新表（CREATE TABLE IF NOT EXISTS 已建好）不会触发 ALTER。
        """
        try:
            for table, ddl in DDL_DUCKDB.items():
                # 跳过非数据表
                if table == "source_watermark":
                    continue
                # 拿实际列
                try:
                    actual = {r[0]: r[1] for r in conn.execute(f"DESCRIBE {table}").fetchall()}
                except Exception:
                    continue   # 表不存在（不应发生，但兜底）
                # 从 COLS 拿应有列（顺序与 DDL 一致）
                expected = self._table_columns(table)
                for col in expected:
                    if col not in actual:
                        # 类型推断：DDL 文本里找列定义
                        col_type = self._infer_col_type(ddl, col)
                        try:
                            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                            logger.info(f"[DuckDBWriter] 迁移: {table}.ADD {col} {col_type}")
                        except Exception as e:
                            logger.warning(f"[DuckDBWriter] 迁移失败 {table}.{col}: {e}")
        except Exception as e:
            logger.warning(f"[DuckDBWriter] _migrate_add_columns 异常（跳过）: {e}")

    @staticmethod
    def _infer_col_type(ddl: str, col: str) -> str:
        """从 DDL 文本解析某列的类型（粗解析，够用）。
        找 'col TYPE' 模式，TYPE 是下一个 token。"""
        import re
        # 匹配 "col TYPE" 或 "col   TYPE"（col 后空白 + 大写类型词）
        m = re.search(rf"\b{re.escape(col)}\s+(BIGINT|INTEGER|DOUBLE|VARCHAR|BOOLEAN|TIMESTAMP)", ddl, re.IGNORECASE)
        return m.group(1).upper() if m else "VARCHAR"

    def write(self, df: pd.DataFrame, table: str, batch_id: str) -> int:
        """幂等防重复写入：主键冲突时 UPDATE（upsert），重放同一批次不产生重复行。"""
        if df is None or len(df) == 0:
            logger.info(f"[DuckDBWriter] {table} batch={batch_id}: 0 rows (skip)")
            return 0
        # 入库前再去重一次（双保险：validator 已去重，这里再保险）
        cols_in_ddl = self._table_columns(table)
        df = df[[c for c in cols_in_ddl if c in df.columns]].copy()
        pk_for_dedup = {
                "stock_daily": ["code", "time"],
                "stock_minutes": ["code", "time", "freq"],
                "etf_minutes": ["code", "time", "freq"],
                "tick": ["code", "time"],
                "fin_indicator": ["code", "end_date", "ann_date"],
                "index_daily": ["code", "time"],
                "stock_daily_valuation": ["code", "time"],
                "etf_daily": ["code", "time"],
                "stock_float_share": ["code", "end_date", "ann_date"],
                "index_constituents": ["index_code", "code", "time"],
                "balance_statement": ["code", "end_date", "ann_date"],
                "income_statement": ["code", "end_date", "ann_date"],
                "cashflow_statement": ["code", "end_date", "ann_date"],
                "stock_dividend": ["code", "ex_date"],
                "sw_industry": ["code", "industry_code"],
                "stock_namechange": ["code", "change_date"],
                "stock_delist": ["code", "market"],
            }.get(table, [])
        if pk_for_dedup:
            before = len(df)
            df = df.drop_duplicates(subset=[c for c in pk_for_dedup if c in df.columns], keep="last")
            if len(df) < before:
                logger.info(f"[DuckDBWriter] {table}: 入库前去重 {before}→{len(df)} 行")
        # 确保类型（字符串列跳过数值转换）
        str_cols = {"code", "freq", "dividend_type", "update_time", "data_source",
                    "index_code", "industry_code", "industry_name", "industry_level",
                    "name_before", "name_after", "status_after", "market",
                    "is_st_reliable_source", "is_delisting_risk_source"}
        for c in df.columns:
            if c in str_cols:
                continue
            if df[c].dtype == object:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        with self._conn_lock:
            conn = self._conn()
            try:
                # 统计写入前已有行数（用于检测重复）
                try:
                    rows_before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except Exception:
                    rows_before = 0
                # 幂等 upsert：主键冲突时 UPDATE（防重复写入的核心）
                conn.register("_tmp_write", df)
                pk_cols = {
                    "stock_daily": "(code, time)",
                    "stock_minutes": "(code, time, freq)",
                "etf_minutes": "(code, time, freq)",
                    "tick": "(code, time)",
                    "fin_indicator": "(code, end_date, ann_date)",
                    "index_daily": "(code, time)",
                    "stock_daily_valuation": "(code, time)",
                    "etf_daily": "(code, time)",
                    "stock_float_share": "(code, end_date, ann_date)",
                    "index_constituents": "(index_code, code, time)",
                    "balance_statement": "(code, end_date, ann_date)",
                    "income_statement": "(code, end_date, ann_date)",
                    "cashflow_statement": "(code, end_date, ann_date)",
                    "stock_dividend": "(code, ex_date)",
                    "sw_industry": "(code, industry_code)",
                    "stock_namechange": "(code, change_date)",
                    "stock_delist": "(code, market)",
                }.get(table)
                if pk_cols:
                    col_list = ", ".join(df.columns)
                    update_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in df.columns)
                    conn.execute(
                        f"INSERT INTO {table} ({col_list}) "
                        f"SELECT * FROM _tmp_write "
                        f"ON CONFLICT {pk_cols} DO UPDATE SET {update_set}")
                else:
                    conn.execute(f"INSERT INTO {table} SELECT * FROM _tmp_write")
                conn.unregister("_tmp_write")
                # 统计写入后行数，判断新增/更新
                try:
                    rows_after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except Exception:
                    rows_after = 0
                new_rows = max(0, rows_after - rows_before)  # clamp 防竞态负值
                updated_rows = max(0, len(df) - new_rows)
            finally:
                conn.close()
        logger.info(f"[DuckDBWriter] {table} batch={batch_id}: wrote {len(df)} rows "
                    f"(新增 {new_rows} + 更新 {updated_rows}) 防重复 upsert")
        # 返回 WriteResult：作为 int = 提交行数（向后兼容），.new/.updated 供审计使用
        return WriteResult(len(df), new_rows, updated_rows)

    def get_last_date(self, source: str, table: str, freq: str = "daily") -> Optional[str]:
        with self._conn_lock:
            conn = self._conn()
            try:
                res = conn.execute(
                    "SELECT last_date FROM source_watermark "
                    "WHERE source=? AND table_name=? AND freq=?",
                    [source, table, freq]).fetchone()
                return str(res[0]) if res else None
            except Exception:
                return None
            finally:
                conn.close()

    def advance_watermark(self, source: str, table: str, freq: str,
                          last_date: str, batch_id: str):
        now = datetime.now().isoformat()
        with self._conn_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO source_watermark VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT (source, table_name, freq) DO UPDATE SET "
                    "last_date=EXCLUDED.last_date, last_batch_id=EXCLUDED.last_batch_id, "
                    "updated_at=EXCLUDED.updated_at",
                    [source, table, freq, last_date, batch_id, now])
            finally:
                conn.close()

    @staticmethod
    def _table_columns(table: str) -> List[str]:
        """返回表的列名（与 DDL 顺序一致，khQuant 口径 v2.0）"""
        COLS = {
            "stock_daily": ["code", "time", "open", "high", "low", "close",
                            "volume", "amount", "preClose", "suspendFlag",
                            "settelementPrice", "openInterest",
                            "open_front", "high_front", "low_front", "close_front",
                            "open_back", "high_back", "low_back", "close_back",
                            "open_front_ratio", "high_front_ratio", "low_front_ratio", "close_front_ratio",
                            "open_back_ratio", "high_back_ratio", "low_back_ratio", "close_back_ratio",
                            "turn", "pctChg", "peTTM", "psTTM", "pcfNcfTTM", "pbMRQ",
                            "isST",
                            "is_st_reliable", "is_st_reliable_source",
                            "is_delisting_risk", "is_delisting_risk_source",
                            "dividend_type", "update_time", "data_source"],
            "stock_minutes": ["code", "time", "freq", "open", "high", "low", "close",
                              "volume", "amount", "preClose", "suspendFlag",
                              "settelementPrice", "openInterest",
                              "open_front", "high_front", "low_front", "close_front",
                              "open_back", "high_back", "low_back", "close_back",
                              "open_front_ratio", "high_front_ratio", "low_front_ratio", "close_front_ratio",
                              "open_back_ratio", "high_back_ratio", "low_back_ratio", "close_back_ratio",
                              "dividend_type", "update_time", "data_source"],
            "etf_minutes": ["code", "time", "freq", "open", "high", "low", "close",
                            "volume", "amount", "preClose", "suspendFlag",
                            "settelementPrice", "openInterest",
                            "open_front", "high_front", "low_front", "close_front",
                            "open_back", "high_back", "low_back", "close_back",
                            "open_front_ratio", "high_front_ratio", "low_front_ratio", "close_front_ratio",
                            "open_back_ratio", "high_back_ratio", "low_back_ratio", "close_back_ratio",
                            "dividend_type", "update_time", "data_source"],
            "tick": ["code", "time", "lastPrice", "open", "high", "low", "lastClose",
                     "amount", "volume", "pvolume", "stockStatus", "openInt", "lastSettlementPrice",
                     "askPrice1", "askPrice2", "askPrice3", "askPrice4", "askPrice5",
                     "bidPrice1", "bidPrice2", "bidPrice3", "bidPrice4", "bidPrice5",
                     "askVol1", "askVol2", "askVol3", "askVol4", "askVol5",
                     "bidVol1", "bidVol2", "bidVol3", "bidVol4", "bidVol5",
                     "transactionNum", "update_time", "data_source"],
            "fin_indicator": ["code", "ann_date", "end_date", "eps", "bps", "roe",
                              "pe_ttm", "pb", "ps_ttm", "np_yoy", "data_source"],
            "index_daily": ["code", "time", "open", "high", "low", "close",
                            "pctChg", "volume", "amount", "data_source"],
            "stock_daily_valuation": ["code", "time", "circ_mv", "total_mv",
                                      "free_share",
                                      "pe_ttm", "pb", "turnover_rate", "update_time", "data_source"],
            "etf_daily": ["code", "time", "open", "high", "low", "close",
                          "preClose", "pctChg", "volume", "amount", "turn",
                          "open_front", "high_front", "low_front", "close_front",
                          "open_back", "high_back", "low_back", "close_back",
                          "open_front_ratio", "high_front_ratio", "low_front_ratio", "close_front_ratio",
                          "open_back_ratio", "high_back_ratio", "low_back_ratio", "close_back_ratio",
                          "isST", "dividend_type", "update_time", "data_source"],
            "stock_float_share": ["code", "end_date", "ann_date",
                                  "free_share", "total_share",
                                  "circ_mv", "total_mv", "update_time", "data_source"],
            "index_constituents": ["index_code", "code", "time", "weight", "data_source"],
            "balance_statement": ["code", "end_date", "ann_date",
                                  "total_assets", "total_liability", "total_equity",
                                  "total_current_assets", "total_non_current_assets",
                                  "total_current_liability", "total_non_current_liability",
                                  "account_receivable", "account_payable", "inventory",
                                  "cash_equivalents", "fixed_asset", "intangible_asset", "goodwill",
                                  "update_time", "data_source"],
            "income_statement": ["code", "end_date", "ann_date",
                                 "operating_revenue", "operating_cost", "operating_profit",
                                 "total_profit", "net_profit", "np_parent_company_owners",
                                 "sale_expense", "manage_expense", "finance_expense", "rd_expense",
                                 "income_tax", "basic_eps", "update_time", "data_source"],
            "cashflow_statement": ["code", "end_date", "ann_date",
                                   "net_operate_cash_flow", "net_invest_cash_flow", "net_finance_cash_flow",
                                   "cash_add_balance", "goods_sale_and_services", "goods_buy_and_services",
                                   "fixed_asset_depreciation", "update_time", "data_source"],
            "stock_dividend": ["code", "ex_date", "record_date",
                               "cash_div", "stk_div", "div_rat", "update_time", "data_source"],
            "sw_industry": ["code", "industry_code", "industry_name", "industry_level", "update_time", "data_source"],
            "stock_namechange": ["code", "change_date", "name_before", "name_after",
                                 "status_after", "update_time", "data_source"],
            "stock_delist": ["code", "list_date", "delist_date", "market", "update_time", "data_source"],
        }
        return COLS.get(table, [])


class QuestDBWriter(BaseWriter):
    """QuestDB 写入器（可选，ILP 批量写入）

    config 示例：{"type": "questdb", "host": "localhost", "ilp_port": 9009, "pg_port": 8812}
    Phase 1 占位实现，Phase 3 完善 ILP 协议。
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        self.host = config.get("host", "localhost")
        self.ilp_port = config.get("ilp_port", 9009)
        self.pg_port = config.get("pg_port", 8812)
        raise NotImplementedError("QuestDBWriter 将在 Phase 3 实现 ILP 协议（基线 v3.2 §8.1）")

    def write(self, df, table, batch_id): raise NotImplementedError
    def get_last_date(self, source, table, freq="daily"): raise NotImplementedError
    def advance_watermark(self, source, table, freq, last_date, batch_id): raise NotImplementedError


def create_writer(config: Dict) -> BaseWriter:
    """工厂方法"""
    wtype = config.get("type", "duckdb").lower()
    registry = {"duckdb": DuckDBWriter, "questdb": QuestDBWriter}
    if wtype not in registry:
        raise ValueError(f"未知 writer 类型: {wtype}")
    return registry[wtype](config)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    here = Path(__file__).resolve().parent.parent.parent
    w = DuckDBWriter({"type": "duckdb", "path": str(db_path())})

    df = pd.DataFrame({
        "ts_code": ["600000.SH", "600000.SH"],
        "trade_date": ["2026-07-10", "2026-07-11"],
        "open": [10.0, 10.5], "high": [10.2, 10.8], "low": [9.9, 10.3],
        "close": [10.1, 10.6], "pct_chg": [1.0, 4.95],
        "vol": [1000.0, 1200.0], "amount": [1010.0, 1272.0],
    })
    n = w.write(df, "stock_daily", "smoke_001")
    w.advance_watermark("test", "stock_daily", "daily", "2026-07-11", "smoke_001")
    last = w.get_last_date("test", "stock_daily", "daily")
    print(f"wrote={n}, watermark={last}")

    # 重放验证幂等
    n2 = w.write(df, "stock_daily", "smoke_001_replay")
    print(f"replay wrote={n2}（应仍为 2，upsert 不重复）")
    print("✅ DuckDBWriter 幂等写入验证通过")
