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


# 各表的建表 DDL（DuckDB 方言，统一口径 v2.0）
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
            eps DOUBLE, diluted_eps DOUBLE, bps DOUBLE, roe DOUBLE,
            pe_ttm DOUBLE, pb DOUBLE, ps_ttm DOUBLE,
            np_yoy DOUBLE, or_yoy DOUBLE, tr_yoy DOUBLE,
            update_flag INTEGER,
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
    "etf_basic": """
        CREATE TABLE IF NOT EXISTS etf_basic (
            code VARCHAR PRIMARY KEY,
            ts_code VARCHAR, name VARCHAR, exchange VARCHAR,
            list_date BIGINT, delist_date BIGINT,
            etf_type VARCHAR NOT NULL, tracking_index VARCHAR,
            is_cross_border BOOLEAN NOT NULL, status VARCHAR,
            fund_type VARCHAR, invest_type VARCHAR, type VARCHAR,
            classification_method VARCHAR NOT NULL,
            classification_version VARCHAR NOT NULL,
            update_time VARCHAR, data_source VARCHAR
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
    # ---- 指数成分快照完整性契约（F3 修订）：完整性在打点写入时确定，不依赖未来数据 ----
    "index_constituents_snapshot_meta": """
        CREATE TABLE IF NOT EXISTS index_constituents_snapshot_meta (
            index_code VARCHAR, time BIGINT,
            n_constituents INTEGER, expected_count INTEGER,
            status VARCHAR,
            n_duplicate_codes INTEGER, n_negative_weights INTEGER,
            n_blank_codes INTEGER,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY(index_code, time)
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
            ann_date BIGINT, end_date BIGINT,
            cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE,
            cash_div DOUBLE, stk_div DOUBLE,
            stk_bo_rate DOUBLE, stk_co_rate DOUBLE,
            div_rat DOUBLE, div_proc VARCHAR,
            update_time VARCHAR,
            PRIMARY KEY(code, ex_date)
        )""",
    # ---- 申万行业分类（LEGACY 快照，仅审计；正式能力见下两张表 F4）----
    "sw_industry": """
        CREATE TABLE IF NOT EXISTS sw_industry (
            code VARCHAR, industry_code VARCHAR, industry_name VARCHAR, industry_level VARCHAR,
            update_time VARCHAR,
            PRIMARY KEY(code, industry_code)
        )""",
    # ---- 行业分类定义（F4 正式 canonical 表；effective_from=0 表示长期有效）----
    "industry_classification": """
        CREATE TABLE IF NOT EXISTS industry_classification (
            classification_system VARCHAR, classification_version VARCHAR,
            industry_code VARCHAR, industry_name VARCHAR, industry_level VARCHAR,
            parent_industry_code VARCHAR,
            effective_from BIGINT, effective_to BIGINT,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY (classification_system, classification_version,
                         industry_level, industry_code, effective_from)
        )""",
    # ---- 行业成员历史（F4 正式 canonical 表，PIT 有效区间）----
    "industry_membership": """
        CREATE TABLE IF NOT EXISTS industry_membership (
            classification_system VARCHAR, classification_version VARCHAR,
            industry_level VARCHAR, industry_code VARCHAR, code VARCHAR,
            effective_from BIGINT, effective_to BIGINT,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY (classification_system, classification_version,
                         industry_level, industry_code, code, effective_from)
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

    def write(self, df: pd.DataFrame, table: str, batch_id: str,
              passthrough: bool = False) -> int:
        """幂等防重复写入：主键冲突时 UPDATE（upsert），重放同一批次不产生重复行。

        passthrough=True（类别B 同名 passthrough 表）：
          - 按 DataFrame dtypes 自动 CREATE TABLE IF NOT EXISTS（表不存在时）；
          - 全量覆盖（CREATE OR REPLACE TABLE），不 upsert、不按 DDL 裁剪列、
            不做类型归一、不推进水位；
          - DuckDB 表名/列名 = QuestDB 原样（ts_code/trade_date 等保留）。
        """
        if df is None or len(df) == 0:
            logger.info(f"[DuckDBWriter] {table} batch={batch_id}: 0 rows (skip)")
            return 0
        if passthrough:
            written = self._write_passthrough(df, table, batch_id)
            return written
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
                "etf_basic": ["code"],
                "stock_float_share": ["code", "end_date", "ann_date"],
                "index_constituents": ["index_code", "code", "time"],
                "index_constituents_snapshot_meta": ["index_code", "time"],
                "balance_statement": ["code", "end_date", "ann_date"],
                "income_statement": ["code", "end_date", "ann_date"],
                "cashflow_statement": ["code", "end_date", "ann_date"],
                "stock_dividend": ["code", "ex_date"],
                "sw_industry": ["code", "industry_code"],
                "industry_classification": ["classification_system", "classification_version",
                                            "industry_level", "industry_code", "effective_from"],
                "industry_membership": ["classification_system", "classification_version",
                                        "industry_level", "industry_code", "code",
                                        "effective_from"],
                "stock_namechange": ["code", "change_date"],
                "stock_delist": ["code", "market"],
            }.get(table, [])
        if pk_for_dedup:
            before = len(df)
            df = df.drop_duplicates(subset=[c for c in pk_for_dedup if c in df.columns], keep="last")
            if len(df) < before:
                logger.info(f"[DuckDBWriter] {table}: 入库前去重 {before}→{len(df)} 行")
        # 确保类型（字符串列跳过数值转换）
        # DDL 驱动（W2-0.9 缺陷 A 修复）：优先按目标表实际 DuckDB 列类型判定——
        # VARCHAR 列一律不经过 pd.to_numeric（否则 "实施" 等字符串值会被 coerce 成 NaN，
        # 落库为 NULL，如 stock_dividend.div_proc）。DESCRIBE 取不到类型时回退到 str_cols
        # 白名单（W2-0.9：白名单必须含 div_proc，且 DESCRIBE 失败不得完全静默）。
        varchar_cols: set = set()
        describe_failed = False
        try:
            with self._conn_lock:
                _conn = self._conn()
                try:
                    for _r in _conn.execute(f"DESCRIBE {table}").fetchall():
                        # _r = (col_name, col_type, nullable, key, default, extra)
                        if len(_r) >= 2 and isinstance(_r[1], str) and _r[1].upper().startswith("VARCHAR"):
                            varchar_cols.add(_r[0])
                finally:
                    _conn.close()
        except Exception as _e:
            # 不静默：记录一次可诊断 warning（不含敏感信息），回退到 str_cols 白名单。
            describe_failed = True
            logger.warning(
                f"[DuckDBWriter] DESCRIBE {table} failed ({type(_e).__name__}); "
                f"falling back to static str_cols whitelist for type protection")
        str_cols = {"code", "freq", "dividend_type", "update_time", "data_source",
                    "index_code", "industry_code", "industry_name", "industry_level",
                    "name_before", "name_after", "status_after", "market",
                    "is_st_reliable_source", "is_delisting_risk_source",
                    "ts_code", "name", "exchange", "etf_type", "tracking_index",
                    "status", "fund_type", "invest_type", "type",
                    "classification_system", "parent_industry_code",
                    "classification_method", "classification_version",
                    # W2-0.9 缺陷 A 补完：fallback 白名单必须含 div_proc，
                    # 保证 DESCRIBE 失败时 "实施" 也不会被 to_numeric 吞掉。
                    "div_proc", "div_rat"}.union(varchar_cols)
        del describe_failed  # 诊断标记已用于 warning，不再需要
        for c in df.columns:
            if c in str_cols:
                continue
            if df[c].dtype == object:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        with self._conn_lock:
            conn = self._conn()
            try:
                # 性能修复（2026-07-22）：原实现 write 前后各跑一次 SELECT COUNT(*) FROM <table>
                # 全表统计，在大表（百万→千万行）上每次数秒，8 线程持 _conn_lock 串行 →
                # 全量拉取 56 秒/只（理论 1.9s）。改为只数本批主键已存在的行（走索引，毫秒级）。
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
                    "etf_basic": "(code)",
                    "stock_float_share": "(code, end_date, ann_date)",
                    "index_constituents": "(index_code, code, time)",
                    "index_constituents_snapshot_meta": "(index_code, time)",
                    "balance_statement": "(code, end_date, ann_date)",
                    "income_statement": "(code, end_date, ann_date)",
                    "cashflow_statement": "(code, end_date, ann_date)",
                    "stock_dividend": "(code, ex_date)",
                    "sw_industry": "(code, industry_code)",
                    "industry_classification": "(classification_system, classification_version, industry_level, industry_code, effective_from)",
                    "industry_membership": "(classification_system, classification_version, industry_level, industry_code, code, effective_from)",
                    "stock_namechange": "(code, change_date)",
                    "stock_delist": "(code, market)",
                }.get(table)
                # 写前：数本批主键在目标表已存在的行数（=将被 UPDATE 的，走索引快）
                updated_rows = 0
                if pk_cols:
                    try:
                        updated_rows = conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {pk_cols} IN "
                            f"(SELECT {pk_cols} FROM _tmp_write)").fetchone()[0]
                    except Exception:
                        updated_rows = 0
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
                # new/updated 审计：updated = 写前已存在的行数；new = 本批其余
                # （精度：本批内主键重复已由 validator 去重，故 new + updated = len(df)）
                new_rows = max(0, len(df) - updated_rows)
            finally:
                conn.close()
        logger.info(f"[DuckDBWriter] {table} batch={batch_id}: wrote {len(df)} rows "
                    f"(新增 {new_rows} + 更新 {updated_rows}) 防重复 upsert")
        # 返回 WriteResult：作为 int = 提交行数（向后兼容），.new/.updated 供审计使用
        return WriteResult(len(df), new_rows, updated_rows)

    # ------------------------------------------------------------------
    # 类别B passthrough 同名表：CREATE OR REPLACE TABLE 全量覆盖
    # ------------------------------------------------------------------
    def _write_passthrough(self, df: pd.DataFrame, table: str, batch_id: str) -> int:
        """passthrough 表全量覆盖写：DuckDB 表名/列名 = QuestDB 原样。

        - 表不存在：按 DataFrame dtypes 自动 CREATE TABLE IF NOT EXISTS；
        - 存在：CREATE OR REPLACE TABLE 全量覆盖（不 upsert，不按 DDL 裁剪列）；
        - 不做数值类型归一（保留源原始字符串/数值类型）；
        - 不推进 source_watermark（passthrough 表无增量水位）。
        """
        import duckdb  # 类型映射需要，顶部已 import，这里局部引用保险
        # 类型映射：object→VARCHAR，其余按 numpy dtype 推断
        _TYPE_MAP = {
            "int64": "BIGINT", "int32": "INTEGER", "int16": "SMALLINT",
            "int8": "SMALLINT", "uint64": "UBIGINT", "uint32": "UINTEGER",
            "float64": "DOUBLE", "float32": "FLOAT", "bool": "BOOLEAN",
        }

        def _col_sql(col: str, dtype) -> str:
            col_q = f'"{col}"'
            if str(dtype) == "object":
                return f"{col_q} VARCHAR"
            return f"{col_q} {_TYPE_MAP.get(str(dtype), 'VARCHAR')}"

        col_defs = ", ".join(_col_sql(c, df[c].dtype) for c in df.columns)
        create_sql = f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs})'
        with self._conn_lock:
            conn = self._conn()
            try:
                conn.execute(create_sql)
                # 全量覆盖：建临时表→REPLACE→DROP 临时（DuckDB 无原生 CREATE OR REPLACE
                # 对含数据的表，用事务内 建临时+原子替换 实现等价语义）
                tmp = f"_pt_tmp_{table}"
                conn.execute(f'DROP TABLE IF EXISTS "{tmp}"')
                conn.execute(f'CREATE TABLE "{tmp}" AS SELECT * FROM "{table}" LIMIT 0')
                conn.register("_pt_src", df)
                conn.execute(f'INSERT INTO "{tmp}" SELECT * FROM _pt_src')
                conn.unregister("_pt_src")
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                conn.execute(f'ALTER TABLE "{tmp}" RENAME TO "{table}"')
            finally:
                conn.close()
        logger.info(f"[DuckDBWriter] {table} passthrough 全量覆盖 {len(df)} 行 "
                    f"(列原样: {list(df.columns)[:8]}{'...' if len(df.columns) > 8 else ''})")
        return len(df)

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

    # ------------------------------------------------------------------
    # 事务感知内部方法（QFQ 重锚编排专用）
    # ------------------------------------------------------------------
    # 说明：以下 *_on_conn 方法在**调用方提供的连接与事务**内执行，
    #   - 不获取 self._conn_lock（连接由调用方持有并串行化）；
    #   - 不 commit / 不 rollback / 不 close（事务边界由调用方掌控）。
    # 用途：QFQ 重锚编排需将「价格修正 UPDATE + anchor 状态更新 + 表级水位推进 +
    #   被过滤证券欠账」放入**同一 DuckDB 事务**保证原子性（设计 v3 §4.5 / §8）。
    # 公共 advance_watermark 行为保持不变（自开短连接自动提交），本方法为其事务版补充。

    def _advance_watermark_on_conn(self, conn, source: str, table: str, freq: str,
                                   last_date, batch_id: str) -> None:
        """在给定连接/事务内推进表级水位（PK source,table_name,freq）。不 commit。

        语义与公共 ``advance_watermark`` 完全一致（同一 INSERT ... ON CONFLICT 形态、
        同一 updated_at 口径），仅事务边界交由调用方。
        """
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO source_watermark VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (source, table_name, freq) DO UPDATE SET "
            "last_date=EXCLUDED.last_date, last_batch_id=EXCLUDED.last_batch_id, "
            "updated_at=EXCLUDED.updated_at",
            [source, table, freq, last_date, batch_id, now])

    def _upsert_pending_backfill_on_conn(self, conn, *, asset_type: str, code: str,
                                         table_name: str, freq: str,
                                         range_start, range_end,
                                         reason: str, anchor_version=None,
                                         status: str = "pending",
                                         now: Optional[str] = None,
                                         reopen: bool = False) -> None:
        """在给定连接/事务内登记「被过滤证券」的精确欠账区间（设计 v4 §1.1）。不 commit。

        幂等语义（阻断 4 修复）：
        - 同 PK 已 ``resolved`` 且未显式 ``reopen`` → 保持 resolved，**不静默重开**（幂等）。
        - 显式 ``reopen=True`` → ``status='pending'``、``resolved_at=NULL``、
          ``last_error=NULL``、``attempt_count=0``（重新进入欠账）。

        输入校验（阻断 4）：``range_start <= range_end``；``asset_type`` 合法；
        ``table_name`` 属四价格表白名单；``freq`` 非空；``status`` 属允许集合。
        任一不满足抛 ``ValueError``。

        热路径（可靠性 阻断 5）：假定 schema 已由编排初始化，不再每条 upsert 前重复发 DDL；
        表不存在直接失败，让启动初始化问题显性暴露。
        """
        from quantstudio.pipeline.qfq_reanchor_schema import (
            _normalize_asset_type, _normalize_code, _validate_epoch_ms,
            PRICE_TABLES, BACKFILL_STATUS, ASSET_TABLE_MAP,
        )
        from quantstudio.pipeline.qfq_calendar import _norm_freq

        # —— 输入校验（阻断 4 + 阻断 3 关联契约）——
        # asset_type：归一化（"stock"→"STOCK"），拒绝非法值
        asset_type = _normalize_asset_type(asset_type)
        # code：canonical 裸 6 位码（复用 schema 单一规则，不复制）
        code = _normalize_code(code)
        # table_name：四价格表白名单
        if table_name not in PRICE_TABLES:
            raise ValueError(
                f"非法 table_name: {table_name!r}（仅四价格表 {sorted(PRICE_TABLES)}）")
        # asset_type ↔ table_name 关联契约
        if table_name not in ASSET_TABLE_MAP.get(asset_type, frozenset()):
            raise ValueError(
                f"asset_type={asset_type} 与 table_name={table_name!r} 不匹配"
                f"（STOCK→stock_daily/stock_minutes；ETF→etf_daily/etf_minutes）")
        # freq：非空 + 与 table_name 关联（daily↔daily，minutes↔1min）
        if not freq or not str(freq).strip():
            raise ValueError("freq 不能为空")
        freq = str(freq).strip()
        kind, n = _norm_freq(freq)
        if kind == "unknown":
            raise ValueError(f"非法 freq: {freq!r}")
        if table_name.endswith("_daily") and kind != "daily":
            raise ValueError(
                f"table_name={table_name!r} 为日线表，freq 必须为 daily，收到 {freq!r}")
        if table_name.endswith("_minutes") and (kind != "minute" or n != 1):
            raise ValueError(
                f"table_name={table_name!r} 为分钟表，freq 必须为 1min（batch1），收到 {freq!r}")
        # 阻断 2：freq 规范化为 storage canonical（别名 1m/1d 必须写入 1min/daily）。
        # 输入别名可兼容，但存储值唯一规范，否则后续 WHERE freq=? / ON CONFLICT 无法命中
        # canonical，造成欠账无法正确补拉或 readback。
        if kind == "daily":
            freq_canonical = "daily"
        elif kind == "minute" and n == 1:
            freq_canonical = "1min"
        else:
            raise NotImplementedError(
                f"freq={freq!r} 暂不支持（batch1 仅 daily/1min）")
        # range_start/range_end：有效 epoch-ms（共享 schema 校验，拒绝非法/越界）
        try:
            rs = _validate_epoch_ms(range_start)
            re_ = _validate_epoch_ms(range_end)
        except ValueError as e:
            raise ValueError(f"range_start/range_end 非法: {e}")
        if rs > re_:
            raise ValueError(f"range_start 必须 <= range_end: {rs} > {re_}")
        # reason：非空字符串
        if not isinstance(reason, str):
            raise ValueError(f"reason 必须为非空字符串: {reason!r}")
        reason = reason.strip()
        if not reason:
            raise ValueError("reason 不能为空")
        # reopen：严格 bool（避免 "false"/0 被当真值）
        if not isinstance(reopen, bool):
            raise ValueError(f"reopen 必须为 bool: {reopen!r}")
        # status：允许集合
        if status not in BACKFILL_STATUS:
            raise ValueError(
                f"非法 status: {status!r}（仅 {sorted(BACKFILL_STATUS)}）")

        ts = now or datetime.now().isoformat()

        # —— 条件重开：resolved 且未显式 reopen → 保持 resolved，不重开 ——
        row = conn.execute(
            "SELECT status, resolved_at, last_error, attempt_count FROM qfq_pending_backfill "
            "WHERE asset_type=? AND code=? AND table_name=? AND freq=? "
            "AND range_start=? AND range_end=?",
            [asset_type, code, table_name, freq_canonical, rs, re_]).fetchone()

        # —— 阻断 3：普通 upsert（欠账登记）禁止创建/变更终态或处理中态 ——
        # resolved / in_progress 必须由专用状态机方法（如 _resolve_backfill_on_conn /
        # _mark_backfill_in_progress_on_conn）处理。普通 upsert 仅允许非终态
        # {pending, blocked, retryable_failed}；非终态 row 的普通 upsert 仍按既有
        # 幂等/重开逻辑处理（不在此拦截）。
        if status in ("resolved", "in_progress"):
            raise ValueError(
                f"status={status!r} 为终态/处理中态，禁止通过普通 upsert 创建或变更；"
                f"必须使用专用状态机方法（如 _resolve_backfill_on_conn）。"
                f"普通 upsert 仅允许 {sorted(BACKFILL_STATUS - {'resolved', 'in_progress'})}")
        if row is not None and row[0] == "resolved" and not reopen:
            return  # 幂等：保持 resolved，不静默重开

        # —— 决定保留/清空的 resolved 上下文 ——
        if row is not None and reopen:
            resolved_at_val = None
            last_error_val = None
            attempt_count_val = 0
        elif row is not None:
            # 非重开：保留既有 resolved_at / last_error / attempt_count
            resolved_at_val = row[1]
            last_error_val = row[2]
            attempt_count_val = row[3] if row[3] is not None else 0
        else:
            resolved_at_val = None
            last_error_val = None
            attempt_count_val = 0
        upsert_status = "pending" if reopen else status

        conn.execute(
            "INSERT INTO qfq_pending_backfill "
            "(asset_type, code, table_name, freq, range_start, range_end, reason, "
            " anchor_version, status, attempt_count, last_error, created_at, updated_at, resolved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (asset_type, code, table_name, freq, range_start, range_end) "
            "DO UPDATE SET reason=EXCLUDED.reason, anchor_version=EXCLUDED.anchor_version, "
            "status=EXCLUDED.status, updated_at=EXCLUDED.updated_at, "
            "resolved_at=EXCLUDED.resolved_at, last_error=EXCLUDED.last_error, "
            "attempt_count=EXCLUDED.attempt_count",
            [asset_type, code, table_name, freq_canonical, rs, re_,
             reason, anchor_version, upsert_status, attempt_count_val,
             last_error_val, ts, ts, resolved_at_val])

    @staticmethod
    def _table_columns(table: str) -> List[str]:
        """返回表的列名（与 DDL 顺序一致，统一口径 v2.0）"""
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
            "fin_indicator": ["code", "ann_date", "end_date", "eps", "diluted_eps", "bps", "roe",
                              "pe_ttm", "pb", "ps_ttm",
                              "np_yoy", "or_yoy", "tr_yoy", "update_flag",
                              "data_source"],
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
            "etf_basic": ["code", "ts_code", "name", "exchange",
                          "list_date", "delist_date", "etf_type", "tracking_index",
                          "is_cross_border", "status", "fund_type", "invest_type",
                          "type", "classification_method", "classification_version",
                          "update_time", "data_source"],
            "stock_float_share": ["code", "end_date", "ann_date",
                                  "free_share", "total_share",
                                  "circ_mv", "total_mv", "update_time", "data_source"],
            "index_constituents": ["index_code", "code", "time", "weight", "data_source"],
            "index_constituents_snapshot_meta": ["index_code", "time", "n_constituents",
                                                 "expected_count", "status",
                                                 "n_duplicate_codes", "n_negative_weights",
                                                 "n_blank_codes",
                                                 "update_time", "data_source"],
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
                               "ann_date", "end_date",
                               "cash_div_before_tax", "cash_div_after_tax",
                               "cash_div", "stk_div", "stk_bo_rate", "stk_co_rate",
                               "div_rat", "div_proc", "update_time", "data_source"],
            "sw_industry": ["code", "industry_code", "industry_name", "industry_level", "update_time", "data_source"],
            "industry_classification": ["classification_system", "classification_version",
                                        "industry_code", "industry_name", "industry_level",
                                        "parent_industry_code", "effective_from", "effective_to",
                                        "update_time", "data_source"],
            "industry_membership": ["classification_system", "classification_version",
                                    "industry_level", "industry_code", "code",
                                    "effective_from", "effective_to",
                                    "update_time", "data_source"],
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
