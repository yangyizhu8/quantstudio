"""
DuckDBDataAccess — 把 PtradeAPI / BacktestEngine 的全部 SQL 查询收敛到单一类。

Phase 1（纯重构，零行为变更）：
- 每个方法内部的 SQL 与 ptrade_api.py / backtest_engine.py 原代码逐字符一致。
- 不暴露任何 Ptrade 代码格式转换 / 策略语义，只返回「源形状」DataFrame
  （部分方法因历史实现在内部做了裸码→Ptrade 格式转换，已在文档中标注）。
- 连接管理（持久只读连接）从 PtradeAPI._get_ro_conn() 迁移到此处的 _get_conn()。

设计原则：本类是「数据访问实现」，不是「数据源抽象」。Phase 2 会在此基础上
定义 MarketDataProvider / FundamentalDataProvider / ReferenceDataProvider /
CalendarProvider 抽象接口，并把本类作为 DuckDB 实现委托。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Any

import pandas as pd
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)


class DuckDBDataAccess:
    """DuckDB（QuantStudio 数据管线产出）数据访问实现。

    独占：连接创建、SQL、预加载内存缓存。
    不负责：撮合、指标计算、策略逻辑。
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._ro_conn = None
        # 预加载缓存（与 PtradeAPI 原缓存变量一一对应）
        self._preload_daily: Optional[pd.DataFrame] = None
        # code -> original row positions. Avoid scanning the full preload for
        # every portable per-security get_history call.
        self._preload_daily_code_positions: Optional[dict] = None
        self._preload_prev_ms: Optional[int] = None
        self._preload_float: Optional[pd.DataFrame] = None
        self._preload_listing: Optional[pd.DataFrame] = None
        self._preload_fs: Optional[pd.DataFrame] = None
        self._preload_fs_month: Optional[str] = None

    # ===================== 连接管理 =====================

    def _get_conn(self):
        """懒创建持久只读 DuckDB 连接（回测期间复用，性能优化）。

        迁移自 PtradeAPI._get_ro_conn() (ptrade_api.py:341-353)
        """
        if self._ro_conn is not None:
            return self._ro_conn
        try:
            import duckdb as _ddb
            if self._db_path and self._db_path.exists():
                self._ro_conn = _ddb.connect(str(self._db_path), read_only=True)
                return self._ro_conn
        except Exception:
            pass
        return None

    def available(self) -> bool:
        """数据库文件是否可访问（替代原 self._cfg.db_path.exists() 直接判断）。"""
        return self._db_path is not None and self._db_path.exists()

    def close(self):
        """关闭连接"""
        if self._ro_conn is not None:
            try:
                self._ro_conn.close()
            except Exception:
                pass
            self._ro_conn = None

    # ===================== 预加载 =====================

    def preload_daily_bars(self, prev_date: str) -> None:
        """预加载全市场行情到内存（仅首次加载，回测期间复用，get_history_from_preload 按 prev_ms 过滤）。

        迁移自 PtradeAPI._preload_market_data() (ptrade_api.py:355-392)
        PIT 语义：不同回测日查到的历史数据通过 _preload_prev_ms 过滤实现。
        首次加载后缓存 _preload_daily / _preload_listing，后续调用只更新 _preload_prev_ms。
        """
        prev_ms = int(pd.Timestamp(prev_date, tz='Asia/Shanghai').timestamp() * 1000) + 86_399_999
        # 估值数据（float_value/pe_ratio）每日重算 PIT 快照
        self.preload_fundamentals_pit(prev_ms, force=False)

        if self._preload_daily is not None:
            # 行情已加载，只更新 prev_ms（_get_history_from_preload 按 prev_ms 过滤）
            self._preload_prev_ms = prev_ms
            return
        try:
            conn = self._get_conn()
            if conn is None:
                return
            # 预加载足够大的行情窗口（回测全期，内存 ~60MB）
            self._preload_daily = conn.execute("""
                SELECT code, time, open, high, low, close, volume, amount,
                       pctChg, preClose, turn, peTTM, pbMRQ, psTTM,
                       open_front, high_front, low_front, close_front
                FROM stock_daily
                ORDER BY time DESC
                LIMIT 4000000
            """).fetchdf()
            self._preload_prev_ms = prev_ms
            self._build_preload_daily_code_positions()
            logger.debug(f"[Preload] 加载 {len(self._preload_daily)} 行行情")
            # 预加载每只股票的上市日期（get_security_info 用，避免逐只 MIN(time) 查询）
            # 上市日是历史固定值，无 PIT 问题，可全局加载
            tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
            if "stock_basic" in tables:
                self._preload_listing = conn.execute(
                    "SELECT code, list_date AS listing_time FROM stock_basic "
                    "WHERE list_date IS NOT NULL"
                ).fetchdf()
            else:
                # Compatibility fallback only. Strategy Compiler capability gates
                # requiring formal listing age must reject this fallback.
                self._preload_listing = conn.execute(
                    "SELECT code, MIN(time) as listing_time FROM stock_daily GROUP BY code"
                ).fetchdf()
        except Exception:
            pass

    def preload_fundamentals_pit(self, prev_ms: int, force: bool = False) -> None:
        """按 PIT（prev_ms）刷新估值快照。

        迁移自 PtradeAPI._refresh_fundamentals_pit() (ptrade_api.py:394-503)
        - 每日流通市值（circ_mv/total_mv/pe_ttm/pb/turnover）：来自 stock_daily_valuation，按日 PIT 重算。
        - 报告期总股本（total_share）与流通股本（free_share）：来自 stock_float_share，按月缓存。
        产出 self._preload_float：每只股票 <= prev_ms 的最新一期 circ_mv/pe_ratio 等。
        """
        try:
            conn = self._get_conn()
            if conn is None:
                return

            # ---- 报告期总股本 + 流通股本（total_share/free_share）：按月缓存，跨月重查 ----
            prev_month = pd.Timestamp(prev_ms, unit='ms', tz='Asia/Shanghai').strftime('%Y-%m')
            need_fs = force or self._preload_fs_month != prev_month or self._preload_fs is None
            if need_fs:
                float_share_df = self.query_float_share_for_preload(prev_ms)
                self._preload_fs = float_share_df
                self._preload_fs_month = prev_month
            else:
                float_share_df = self._preload_fs

            # ---- 每日流通市值 + a_floats（优先 free_share，fallback circ_mv/close）----
            # 按日 PIT 重算，不缓存！
            daily_val = self.query_valuation_for_preload(prev_ms)

            if len(daily_val) > 0:
                # 主路径：每日估值 + a_floats（优先 free_share，fallback circ_mv/close），join 报告期 total_share
                self._preload_float = daily_val.merge(
                    float_share_df[['code', 'total_share']], on='code', how='left')
            elif len(float_share_df) > 0:
                # 兼容回退：stock_daily_valuation 无数据时，用 stock_float_share 的 circ_mv
                # 同时从 stock_daily 取 prev_close 推导 a_floats（与主路径口径一致）
                fs_full = conn.execute(f"""
                    SELECT code, circ_mv AS float_value, total_mv AS total_value, total_share
                    FROM stock_float_share
                    WHERE ann_date <= {prev_ms}
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY ann_date DESC) = 1
                """).fetchdf()
                daily_latest = conn.execute(f"""
                    WITH latest_day AS (SELECT MAX(time) AS t FROM stock_daily WHERE time <= {prev_ms})
                    SELECT d.code, d.close, d.peTTM AS pe_ratio, d.peTTM AS pe_ttm,
                           d.pbMRQ AS pb_ratio, d.psTTM AS ps_ratio, d.turn AS turnover_ratio
                    FROM stock_daily d, latest_day WHERE d.time = latest_day.t
                """).fetchdf()
                merged = fs_full.merge(daily_latest, on='code', how='left')
                if len(merged) > 0:
                    merged['a_floats'] = merged.apply(
                        lambda r: r['float_value'] / r['close'] if r.get('close') and r['close'] != 0 else None, axis=1)
                self._preload_float = merged
            else:
                self._preload_float = pd.DataFrame()

            logger.debug(f"[Preload] PIT 估值刷新（每日 circ_mv，month={prev_month}）: "
                         f"{len(self._preload_float)} 行 "
                         f"(daily_val={len(daily_val)}, float_share={len(float_share_df)})")
        except Exception:
            pass

    # ===================== 行情查询 =====================

    def query_daily_snapshot(self, date_ms: int) -> pd.DataFrame:
        """迁移自 BacktestEngine._get_daily_data() (backtest_engine.py:724-735)

        支持股票 + ETF：
        - stock_daily 提供完整股票快照
        - etf_daily 补充 ETF 行情，填充统一列结构，便于 Ptrade 策略在同一日同时访问股票/ETF/行业 ETF
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            SELECT code, time, open, high, low, close, volume, amount,
                   pctChg, preClose, turn, peTTM, pbMRQ, isST, suspendFlag,
                   is_st_reliable, is_st_reliable_source,
                   is_delisting_risk, is_delisting_risk_source
            FROM stock_daily
            WHERE time = {date_ms}
            UNION ALL
            SELECT code, time, open, high, low, close, volume, amount,
                   pctChg, preClose, turn,
                   NULL AS peTTM, NULL AS pbMRQ,
                   COALESCE(isST, 0) AS isST,
                   0 AS suspendFlag,
                   FALSE AS is_st_reliable,
                   'etf_daily' AS is_st_reliable_source,
                   FALSE AS is_delisting_risk,
                   'etf_daily' AS is_delisting_risk_source
            FROM etf_daily
            WHERE time = {date_ms}
        """).fetchdf()

    def query_bars_by_range(self, code, start_ms, end_ms) -> pd.DataFrame:
        """迁移自 PtradeAPI.get_price() 的条件分支 (ptrade_api.py:1373-1384)

        start_ms / end_ms 任一为 None 时对应的时间下界/上界条件不生效。
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        conditions = [f"code = '{code}'"]
        if start_ms is not None:
            conditions.append(f"time >= {start_ms}")
        if end_ms is not None:
            conditions.append(f"time <= {end_ms}")
        where = " AND ".join(conditions)
        return conn.execute(f"""
            SELECT code, time, open, high, low, close, volume, amount,
                   preClose, pctChg, turn, peTTM, pbMRQ
            FROM stock_daily WHERE {where} ORDER BY time
        """).fetchdf()

    def query_bars_by_count(self, code, count, before_ms) -> pd.DataFrame:
        """迁移自 PtradeAPI.get_price() 的 count 分支 (ptrade_api.py:1363-1371)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            SELECT * FROM (
                SELECT code, time, open, high, low, close, volume, amount,
                       preClose, pctChg, turn, peTTM, pbMRQ
                FROM stock_daily WHERE code = '{code}' AND time <= {before_ms}
                ORDER BY time DESC LIMIT {count}
            ) ORDER BY time
        """).fetchdf()

    def query_bars_by_count_multi_table(self, code, count, before_ms, use_qfq: bool = False) -> pd.DataFrame:
        """迁移自 PtradeAPI.get_history() 的多表 fallback 逻辑 (ptrade_api.py:1211-1243)

        依次尝试 stock_daily → etf_daily → index_daily + INDEX_ETF_MAP fallback
        （000300→510300 等）。返回单只代码的 DataFrame（已排序、已加 trade_date、已按 qfq 替换价）。
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        INDEX_ETF_MAP = {"000300": "510300", "000905": "510500",
                         "000016": "510050", "000852": "510880"}
        df = pd.DataFrame()
        for tbl, cols in [("stock_daily", "code, time, open, high, low, close, volume, amount, pctChg, preClose, turn, peTTM, pbMRQ, open_front, high_front, low_front, close_front"),
                          ("etf_daily", "code, time, open, high, low, close, volume, amount, pctChg, preClose, turn, NULL as peTTM, NULL as pbMRQ, open_front, high_front, low_front, close_front"),
                          ("index_daily", "code, time, open, high, low, close, volume, amount, pctChg, NULL as preClose, NULL as turn, NULL as peTTM, NULL as pbMRQ, NULL as open_front, NULL as high_front, NULL as low_front, NULL as close_front")]:
            df = conn.execute(f"""
                SELECT {cols}
                FROM {tbl}
                WHERE code = '{code}' AND time <= {before_ms}
                ORDER BY time DESC LIMIT {int(count)}
            """).fetchdf()
            if len(df) > 0:
                break
        # 指数历史不足 → 用跟踪 ETF 替代（如 000300→510300，动量信号一致）
        if len(df) == 0 and code in INDEX_ETF_MAP:
            etf_code = INDEX_ETF_MAP[code]
            df = conn.execute(f"""
                SELECT code, time, open, high, low, close, volume, amount, pctChg, preClose, turn, NULL as peTTM, NULL as pbMRQ, open_front, high_front, low_front, close_front
                FROM etf_daily WHERE code = '{etf_code}' AND time <= {before_ms}
                ORDER BY time DESC LIMIT {int(count)}
            """).fetchdf()
        if len(df) > 0:
            # fq='pre'/'dypre'：用前复权列替换原始价
            if use_qfq:
                for orig, qfq in [("open", "open_front"), ("high", "high_front"),
                                  ("low", "low_front"), ("close", "close_front")]:
                    if qfq in df.columns and df[qfq].notna().any():
                        df[orig] = df[qfq]
            df = df.sort_values('time')
            df['trade_date'] = pd.to_datetime(df['time'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d')
        return df

    # ===================== PR3: 分钟 bar 查询 =====================

    def _resolve_minute_table(self, code: str) -> Optional[str]:
        """PR3: 根据 code 类型解析对应的分钟表名。

        分类顺序：is_etf 先于 is_index（ETF 代码如 510300 不被指数规则误判）。
        返回 "stock_minutes" / "etf_minutes" / None（指数/可转债等无分钟表）。
        """
        from ..libs.security_code_rules import is_etf, is_index, is_convertible_bond
        # ETF 先判断（ETF 代码可能与指数区间重叠，如 510300）
        if is_etf(code):
            return "etf_minutes"
        # 指数/可转债无对应分钟表（index_minutes/cb_minutes 不存在）→ None → TABLE_MISSING
        if is_index(code) or is_convertible_bond(code):
            return None
        return "stock_minutes"

    def query_minute_bars_by_range(
        self, code: str, start_date: str, end_date: str, storage_freq: str,
        fq: Optional[str] = None, calendar_provider=None,
        bar_cutoff_ms: Optional[int] = None,
    ) -> pd.DataFrame:
        """PR3: 查 stock_minutes/etf_minutes 的指定原生 freq（区间查询）。

        - 不聚合、不回退日线（严禁回退）。
        - 时段过滤用 Python 侧生成的 epoch 毫秒窗口（修正 v1 的 time%N bug）。
        - 表不存在/空/无该 freq → raise FrequencyCapabilityError（code 三分级）。
        - 复权：fq='pre'/'dypre' 用 *_front 列替换 OHLC；preClose 保持原始（已知简化）。
        - PR4 缺口 1：bar_cutoff_ms 非 None 时（分钟 Profile），当日窗口截断到此值（含当前 bar），
          防止未来 bar 泄漏；None 时走 PR3 原逻辑（end_date 当天 23:59:59 截断）。
        """
        from .frequency_labels import (
            FrequencyCapabilityError, ERR_TABLE_MISSING, ERR_TABLE_EMPTY,
            ERR_FREQ_NOT_IN_TABLE, api_to_storage)
        from .intraday_windows import build_intraday_sql_conditions, iter_trading_days_in_range

        table = self._resolve_minute_table(code)
        if table is None:
            raise FrequencyCapabilityError(
                ERR_TABLE_MISSING, api_freq=None,
                table=f"index_minutes（指数无对应分钟表）",
                detail=f"code={code} 是指数，无分钟表")

        conn = self._get_conn()
        if conn is None:
            raise FrequencyCapabilityError(
                ERR_TABLE_EMPTY, api_freq=None, table=table,
                detail="DuckDB 连接不可用")

        # 确认该 code 在表中有数据（防 table_empty 静默返回空冒充）
        cnt_row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE code = ?", [code]).fetchone()
        cnt = cnt_row[0] if cnt_row else 0
        if cnt == 0:
            raise FrequencyCapabilityError(
                ERR_TABLE_EMPTY, api_freq=None, table=table,
                detail=f"code={code} 在 {table} 中无数据")

        # 确认该 freq 存在（列出可用 freq 集合，便于调用方定位缺口）
        avail_rows = conn.execute(
            f"SELECT DISTINCT freq FROM {table} WHERE code = ?", [code]).fetchall()
        avail = [r[0] for r in avail_rows]
        if storage_freq not in avail:
            raise FrequencyCapabilityError(
                ERR_FREQ_NOT_IN_TABLE, api_freq=None, storage_freq=storage_freq,
                table=table, available_freqs=avail,
                detail=f"{table} 有数据但缺 freq={storage_freq}")

        # 时段窗口（本轮修正：Python 侧生成 epoch 毫秒区间）
        day_strs = iter_trading_days_in_range(start_date, end_date, calendar_provider)
        # PR4 缺口 1：分钟 Profile 传 bar_cutoff_ms（当前 bar 的 epoch 毫秒），
        # 当日窗口截断到此值（含当前 bar，end-labeled 下已完成是可见历史）；
        # None 时走 PR3 原逻辑（end_date 当天 23:59:59 截断，日级全天窗口）。
        end_cutoff_ms = bar_cutoff_ms if bar_cutoff_ms is not None else self._end_ms(end_date)
        where_clause, win_params = build_intraday_sql_conditions(day_strs, end_cutoff_ms)

        df = conn.execute(f"""
            SELECT code, time, freq, open, high, low, close, volume, amount, preClose,
                   open_front, high_front, low_front, close_front,
                   open_back, high_back, low_back, close_back,
                   suspendFlag
            FROM {table}
            WHERE code = ? AND freq = ? AND ({where_clause})
            ORDER BY time
        """, [code, storage_freq] + win_params).fetchdf()

        # 复权替换（补齐 3：preClose 保持原始，已知简化）
        fq_norm = str(fq).lower() if fq else ""
        if fq_norm in ('pre', 'dypre'):
            for orig, qfq in [("open", "open_front"), ("high", "high_front"),
                              ("low", "low_front"), ("close", "close_front")]:
                if qfq in df.columns and df[qfq].notna().any():
                    df[orig] = df[qfq]
        elif fq_norm in ('post', 'dyback', 'dy_post'):
            for orig, qfq in [("open", "open_back"), ("high", "high_back"),
                              ("low", "low_back"), ("close", "close_back")]:
                if qfq in df.columns and df[qfq].notna().any():
                    df[orig] = df[qfq]
        return df

    def query_minute_bars_by_range_batch(
        self, codes, start_date: str, end_date: str, storage_freq: str,
        fq: Optional[str] = None, calendar_provider=None,
        bar_cutoff_ms: Optional[int] = None,
    ) -> pd.DataFrame:
        """PR4 真实数据修复（2026-07-22）：批量查多 code 的分钟 bar，一次 SQL per 表。

        替代 _load_minute_snapshots 的逐 code 循环（5525 只 × 4 次查询 = 2.2 万次 DB 调用，
        导致 duckdb C 扩展 GIL 累积崩溃）。本方法按表分组（stock_minutes/etf_minutes），
        每表一次 SQL（WHERE code IN (...) AND freq=? AND 时段窗口），单日 DB 往返 ≤ 2 次。

        契约对齐 query_minute_bars_by_range：
        - 复权替换（fq='pre'/'post'）逻辑一致
        - 时段窗口（iter_trading_days_in_range + bar_cutoff_ms）一致
        - FrequencyCapabilityError 语义：整表空（所有 code 都无数据）才 raise TABLE_EMPTY；
          个别 code 无数据自然不在结果集（与原"逐 code 跳过"一致）
        - 指数/可转债 code（_resolve_minute_table=None）自动跳过，不报错
        """
        from .frequency_labels import FrequencyCapabilityError, ERR_TABLE_EMPTY
        from .intraday_windows import build_intraday_sql_conditions, iter_trading_days_in_range
        import pandas as pd

        codes = [str(c) for c in (codes or []) if c is not None and str(c).strip()]
        if not codes:
            return pd.DataFrame()

        # 按表分组（stock_minutes / etf_minutes），指数/可转债（None）跳过
        table_codes = {}  # table -> [codes]
        for code in codes:
            table = self._resolve_minute_table(code)
            if table is None:
                continue  # 指数/可转债无分钟表，跳过（与原 except TABLE_MISSING 一致）
            table_codes.setdefault(table, []).append(code)

        if not table_codes:
            return pd.DataFrame()

        conn = self._get_conn()
        if conn is None:
            raise FrequencyCapabilityError(
                ERR_TABLE_EMPTY, api_freq=None, table="stock_minutes/etf_minutes",
                detail="DuckDB 连接不可用")

        day_strs = iter_trading_days_in_range(start_date, end_date, calendar_provider)
        end_cutoff_ms = bar_cutoff_ms if bar_cutoff_ms is not None else self._end_ms(end_date)
        where_clause, win_params = build_intraday_sql_conditions(day_strs, end_cutoff_ms)

        parts = []
        any_table_has_freq = False
        for table, tbl_codes in table_codes.items():
            # 整表 freq 存在性检查（一次，非每 code）
            freq_check = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE freq = ? AND code IN (SELECT unnest(?))",
                [storage_freq, tbl_codes]).fetchone()
            if freq_check[0] == 0:
                # 该表这批 code 无此 freq 数据，跳过（整表空在最后统一判断）
                continue
            any_table_has_freq = True
            # 一次 SQL 拉全 code（DuckDB unnest 参数化，避免 SQL 过长）
            df = conn.execute(f"""
                SELECT code, time, freq, open, high, low, close, volume, amount, preClose,
                       open_front, high_front, low_front, close_front,
                       open_back, high_back, low_back, close_back,
                       suspendFlag
                FROM {table}
                WHERE freq = ? AND code IN (SELECT unnest(?)) AND ({where_clause})
                ORDER BY time
            """, [storage_freq, tbl_codes] + win_params).fetchdf()
            if len(df) > 0:
                parts.append(df)

        if not any_table_has_freq:
            raise FrequencyCapabilityError(
                ERR_TABLE_EMPTY, api_freq=None, table="stock_minutes/etf_minutes",
                detail=f"全 universe 在 {start_date}~{end_date} 无 freq={storage_freq} 分钟数据")

        if not parts:
            return pd.DataFrame()
        result = pd.concat(parts, ignore_index=True)
        result = result.sort_values('time').reset_index(drop=True)

        # 复权替换（与单 code 版完全一致）
        fq_norm = str(fq).lower() if fq else ""
        if fq_norm in ('pre', 'dypre'):
            for orig, qfq in [("open", "open_front"), ("high", "high_front"),
                              ("low", "low_front"), ("close", "close_front")]:
                if qfq in result.columns and result[qfq].notna().any():
                    result[orig] = result[qfq]
        elif fq_norm in ('post', 'dyback', 'dy_post'):
            for orig, qfq in [("open", "open_back"), ("high", "high_back"),
                              ("low", "low_back"), ("close", "close_back")]:
                if qfq in result.columns and result[qfq].notna().any():
                    result[orig] = result[qfq]
        return result


    def query_minute_bars_by_count(
        self, code: str, count: int, end_date: str, storage_freq: str,
        fq: Optional[str] = None, calendar_provider=None,
        bar_cutoff_ms: Optional[int] = None,
    ) -> pd.DataFrame:
        """PR3: 查 stock_minutes/etf_minutes 的指定原生 freq（count 查询）。

        PR3 先实现"end_date 当日收盘前 N 根 bar"（不跨日回溯）；
        跨日 count 语义留待真实数据校准，文档注明。
        PR4 缺口 1：bar_cutoff_ms 非 None 时（分钟 Profile），count 从当前 bar 往前数（含当前 bar）。
        """
        df = self.query_minute_bars_by_range(
            code, end_date, end_date, storage_freq, fq, calendar_provider,
            bar_cutoff_ms=bar_cutoff_ms)
        if len(df) > count:
            df = df.tail(count).reset_index(drop=True)
        return df

    @staticmethod
    def _end_ms(date: str) -> int:
        """返回 date 当日 23:59:59.999 的 epoch 毫秒（end 当天截断用）。"""
        ts = pd.Timestamp(str(date)[:10]).tz_localize("Asia/Shanghai")
        return int((ts.value // 10**6) + 86_399_999)

    def query_benchmark(self, code, start_ms, end_ms) -> pd.DataFrame:
        """迁移自 BacktestEngine._get_benchmark() (backtest_engine.py:737-754)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            SELECT time, close FROM index_daily
            WHERE code = '{code}' AND time >= {start_ms} AND time <= {end_ms}
            ORDER BY time
        """).fetchdf()

    def query_strategy_events(self, event_type, effective_date=None, start_date=None,
                              end_date=None, codes=None) -> pd.DataFrame:
        """Query generic strategy events without exposing storage to strategies."""
        columns = [
            "event_type", "event_date", "effective_date", "code", "signal",
            "name", "category", "source", "source_row_id", "source_key", "payload", "imported_at",
        ]
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame(columns=columns)
        try:
            tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
            if "strategy_events" not in tables:
                return pd.DataFrame(columns=columns)
            where = ["event_type = ?"]
            params = [str(event_type)]
            if effective_date is not None:
                where.append("effective_date = ?::DATE")
                params.append(str(effective_date)[:10])
            if start_date is not None:
                where.append("effective_date >= ?::DATE")
                params.append(str(start_date)[:10])
            if end_date is not None:
                where.append("effective_date <= ?::DATE")
                params.append(str(end_date)[:10])
            normalized_codes = [str(code) for code in (codes or [])]
            if normalized_codes:
                placeholders = ",".join(["?"] * len(normalized_codes))
                where.append(f"code IN ({placeholders})")
                params.extend(normalized_codes)
            sql = (
                "SELECT event_type, event_date, effective_date, code, signal, "
                "name, category, source, source_row_id, source_key, payload, imported_at "
                "FROM strategy_events WHERE " + " AND ".join(where) +
                " ORDER BY effective_date, event_date, source_row_id NULLS LAST, source_key"
            )
            return conn.execute(sql, params).fetchdf()
        except Exception as exc:
            logger.warning("query_strategy_events failed: %s", exc)
            return pd.DataFrame(columns=columns)

    def query_corporate_actions(self, date_ms: int) -> pd.DataFrame:
        """Return ex-date cash/stock distributions for a trading date."""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame(columns=["code", "cash_div", "stk_div", "div_rat"])
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        if "stock_dividend" not in tables:
            return pd.DataFrame(columns=["code", "cash_div", "stk_div", "div_rat"])
        return conn.execute(
            "SELECT code, cash_div, stk_div, div_rat FROM stock_dividend WHERE ex_date = ?",
            [int(date_ms)],
        ).fetchdf()

    def query_listing_dates(self) -> pd.DataFrame:
        """迁移自 PtradeAPI._preload_market_data() 中的上市日期查询 (ptrade_api.py:388-390)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(
            "SELECT code, MIN(time) as listing_time FROM stock_daily GROUP BY code"
        ).fetchdf()

    def _build_preload_daily_code_positions(self):
        """Build a reusable code-to-row-position index for daily history."""
        if self._preload_daily is None:
            self._preload_daily_code_positions = None
            return
        if self._preload_daily_code_positions is None:
            self._preload_daily_code_positions = {
                str(code): positions
                for code, positions in self._preload_daily.groupby(
                    "code", sort=False, observed=True).indices.items()
            }

    def _preloaded_daily_for_code(self, bare):
        if self._preload_daily is None:
            return None
        self._build_preload_daily_code_positions()
        positions = (self._preload_daily_code_positions or {}).get(str(bare))
        if positions is None:
            return self._preload_daily.iloc[0:0]
        return self._preload_daily.iloc[positions]

    def get_history_from_preload(self, sec_list, count, fq, is_dict, fields, field_map) -> Optional[Any]:
        """从预加载内存查 get_history（避免 DuckDB 查询）。

        迁移自 PtradeAPI._get_history_from_preload() (ptrade_api.py:505-549)
        返回 None 表示预加载数据不足，需 fallback 到 DuckDB。
        """
        if self._preload_daily is None:
            return None
        use_qfq = str(fq).lower() in ("pre", "dypre")
        prev_ms = self._preload_prev_ms
        dfs = {}
        for sec in sec_list:
            bare = str(sec).split(".")[0]
            # PIT 过滤：只取 <= prev_date 的数据（预加载数据已按 time DESC 排序）
            sub_all = self._preloaded_daily_for_code(bare)
            if sub_all is None:
                return None
            if prev_ms is not None:
                sub_all = sub_all[sub_all['time'] <= prev_ms]
            sub = sub_all.head(int(count))
            if len(sub) == 0:
                # 尝试 ETF/指数 fallback（与 DuckDB 路径一致的 INDEX_ETF_MAP）
                INDEX_ETF_MAP = {"000300": "510300", "000905": "510500",
                                 "000016": "510050", "000852": "510880"}
                if bare in INDEX_ETF_MAP:
                    sub = self._preloaded_daily_for_code(INDEX_ETF_MAP[bare])
                    if prev_ms is not None:
                        sub = sub[sub['time'] <= prev_ms]
                    sub = sub.head(int(count))
            if len(sub) == 0:
                continue
            df = sub.sort_values('time').copy()
            if use_qfq:
                for orig, qfq in [("open", "open_front"), ("high", "high_front"),
                                  ("low", "low_front"), ("close", "close_front")]:
                    if qfq in df.columns and df[qfq].notna().any():
                        df[orig] = df[qfq]
            df['trade_date'] = pd.to_datetime(df['time'], unit='ms').dt.strftime('%Y-%m-%d')
            # 注：此处沿用原实现，按 Ptrade 格式输出键（与 DuckDB fallback 路径一致）
            dfs[self._to_ptrade_code(bare)] = df
        if not dfs:
            return None
        if is_dict:
            return CodeDict_clone(dfs)
        if len(dfs) == 1:
            df0 = list(dfs.values())[0]
        else:
            df0 = pd.concat(dfs.values(), ignore_index=False)
        if fields:
            mapped = [field_map.get(f, f) for f in fields]
            available = [m for m in mapped if m in df0.columns]
            if available:
                df0 = df0[available]
        return df0

    def get_fundamentals_from_preload(self, bare_codes, fields) -> Optional[pd.DataFrame]:
        """从预加载的估值 DataFrame 查 get_fundamentals(valuation)。

        迁移自 PtradeAPI._get_fundamentals_from_preload() (ptrade_api.py:551-568)
        返回 None 表示无预加载数据，需 fallback 到 DuckDB。
        """
        if self._preload_float is None:
            return None
        codes_in = bare_codes if isinstance(bare_codes, list) else [bare_codes]
        sub = self._preload_float[self._preload_float['code'].isin([str(c).split('.')[0] for c in codes_in])]
        if len(sub) == 0:
            return None
        df = sub.copy()
        # 注：此处沿用原实现，按 Ptrade 格式输出（code→index）
        df['code'] = df['code'].apply(self._to_ptrade_code)
        df = df.set_index('code')
        if fields:
            field_list = [fields] if isinstance(fields, str) else list(fields)
            available = [f for f in field_list if f in df.columns]
            if available:
                df = df[available]
        return df

    # ===================== 估值/基本面查询 =====================

    def query_float_share_for_preload(self, query_ms: int) -> pd.DataFrame:
        """迁移自 PtradeAPI._refresh_fundamentals_pit() 的 float_share 更新逻辑 (ptrade_api.py:430-437)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            SELECT code, total_share, free_share
            FROM stock_float_share
            WHERE ann_date <= {query_ms}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY ann_date DESC) = 1
        """).fetchdf()

    def query_valuation_for_preload(self, query_ms: int) -> pd.DataFrame:
        """迁移自 PtradeAPI._refresh_fundamentals_pit() 的每日估值查询 (ptrade_api.py:446-470)

        含 stock_daily_valuation + stock_daily(close) + stock_float_share(free_share) 三层 JOIN
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            SELECT v.code, v.circ_mv AS float_value, v.total_mv AS total_value,
                   v.pe_ttm AS pe_ratio, v.pe_ttm, v.pb AS pb_ratio, v.turnover_rate AS turnover_ratio,
                   COALESCE(fs.free_share,
                            CASE WHEN d.close IS NULL OR d.close = 0 THEN NULL
                                 ELSE v.circ_mv / d.close END) AS a_floats
            FROM (
                SELECT code, circ_mv, total_mv, pe_ttm, pb, turnover_rate
                FROM stock_daily_valuation
                WHERE time <= {query_ms}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) = 1
            ) v
            LEFT JOIN (
                SELECT code, close
                FROM stock_daily
                WHERE time <= {query_ms}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) = 1
            ) d ON d.code = v.code
            LEFT JOIN (
                SELECT code, free_share
                FROM stock_float_share
                WHERE end_date <= {query_ms}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY end_date DESC) = 1
            ) fs ON fs.code = v.code
        """).fetchdf()

    def query_valuation_daily_pit(self, bare_codes, query_ms) -> pd.DataFrame:
        """迁移自 PtradeAPI._fundamentals_valuation() 主路径 (ptrade_api.py:757-784)

        stock_daily_valuation PIT + stock_daily close 推导 a_floats + stock_float_share free_share
        返回「源形状」裸码 DataFrame（不含 total_share/pe_ratio_lyr，由调用方 finalize）。
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        codes_in = "','".join(bare_codes)
        return conn.execute(f"""
            SELECT s.code,
                   s.circ_mv          AS float_value,
                   s.circ_mv          AS circulating_market_cap,
                   s.total_mv         AS total_value,
                   s.total_mv         AS market_cap,
                   s.pe_ttm           AS pe_ratio,
                   s.pe_ttm           AS pe_ttm,
                   s.pb               AS pb_ratio,
                   s.turnover_rate    AS turnover_ratio,
                   COALESCE(fs.free_share,
                            CASE WHEN dc.close IS NULL OR dc.close = 0 THEN NULL
                                 ELSE s.circ_mv / dc.close END) AS a_floats
            FROM stock_daily_valuation s
            LEFT JOIN (
                SELECT code, close FROM stock_daily
                WHERE time <= {query_ms}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) = 1
            ) dc ON dc.code = s.code
            LEFT JOIN (
                SELECT code, free_share FROM stock_float_share
                WHERE end_date <= {query_ms}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY end_date DESC) = 1
            ) fs ON fs.code = s.code
            WHERE s.code IN ('{codes_in}') AND s.time <= {query_ms}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY s.code ORDER BY s.time DESC) = 1
        """).fetchdf()

    def query_valuation_monthly_fallback(self, bare_codes, query_ms) -> pd.DataFrame:
        """迁移自 PtradeAPI._fundamentals_valuation() 回退路径 (ptrade_api.py:786-808)

        stock_float_share + stock_daily fallback
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        codes_in = "','".join(bare_codes)
        return conn.execute(f"""
            SELECT s.code,
                   s.circ_mv        AS float_value,
                   s.circ_mv        AS circulating_market_cap,
                   s.total_mv       AS total_value,
                   s.total_mv       AS market_cap,
                   d.peTTM          AS pe_ratio,
                   d.peTTM          AS pe_ttm,
                   d.pbMRQ          AS pb_ratio,
                   d.psTTM          AS ps_ratio,
                   d.pcfNcfTTM      AS pcf_ratio,
                   d.turn           AS turnover_ratio,
                   CASE WHEN d.close IS NULL OR d.close = 0 THEN NULL
                        ELSE s.circ_mv / d.close END AS a_floats
            FROM stock_float_share s
            LEFT JOIN stock_daily d
              ON d.code = s.code
             AND d.time = (SELECT MAX(time) FROM stock_daily WHERE time <= {query_ms})
            WHERE s.code IN ('{codes_in}')
              AND s.time = (SELECT MAX(time) FROM stock_float_share WHERE time <= {query_ms})
        """).fetchdf()

    def query_total_share(self, bare_codes, query_ms) -> pd.DataFrame:
        """迁移自 PtradeAPI._fundamentals_valuation() 的 total_share 补查 (ptrade_api.py:812-818)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        codes_in = "','".join(bare_codes)
        return conn.execute(f"""
            SELECT code, total_share
            FROM stock_float_share
            WHERE code IN ('{codes_in}') AND ann_date <= {query_ms}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY code ORDER BY ann_date DESC, end_date DESC) = 1
        """).fetchdf()

    def query_valuation_orm(self, query_ms: int) -> pd.DataFrame:
        """valuation ORM：逐证券 PIT；market_cap 口径为亿元。

        float_value/total_mv 继续保持元，兼容既有小市值策略。
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            WITH valuation_pit AS (
                SELECT * FROM stock_daily_valuation
                WHERE time <= {query_ms}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) = 1
            ), share_pit AS (
                SELECT code, free_share, total_share
                FROM stock_float_share
                WHERE ann_date <= {query_ms}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY code ORDER BY ann_date DESC, end_date DESC) = 1
            ), daily_pit AS (
                SELECT code, close, psTTM
                FROM stock_daily
                WHERE time <= {query_ms}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) = 1
            )
            SELECT v.code,
                   v.total_mv / 1e8   AS market_cap,
                   v.circ_mv / 1e8    AS circulating_market_cap,
                   v.circ_mv          AS circ_mv,
                   v.circ_mv          AS float_value,
                   COALESCE(v.free_share, fs.free_share,
                            CASE WHEN d.close IS NULL OR d.close = 0 THEN NULL
                                 ELSE v.circ_mv / d.close END) AS a_floats,
                   v.total_mv          AS total_mv,
                   fs.total_share      AS total_share,
                   v.pe_ttm            AS pe_ratio,
                   v.pe_ttm            AS pe_ttm,
                   v.pb                AS pb_ratio,
                   d.psTTM             AS ps_ratio,
                   v.turnover_rate     AS turnover_ratio
            FROM valuation_pit v
            LEFT JOIN share_pit fs ON fs.code = v.code
            LEFT JOIN daily_pit d ON d.code = v.code
        """).fetchdf()
    def query_fin_indicator(self, codes, ann_date_ms, start_year, end_year, report_types) -> pd.DataFrame:
        """迁移自 PtradeAPI._fundamentals_fin_indicator() (ptrade_api.py:827-861)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        codes_in = "','".join(codes)
        where_date = f"f.ann_date <= {ann_date_ms}"
        where_year = ""
        if start_year and end_year:
            sy_ms = int(pd.Timestamp(f"{start_year}-01-01", tz='Asia/Shanghai').timestamp() * 1000)
            ey_ms = int(pd.Timestamp(f"{end_year}-12-31", tz='Asia/Shanghai').timestamp() * 1000)
            where_year = f"AND f.ann_date BETWEEN {sy_ms} AND {ey_ms}"

        rt_cond = ""
        if report_types:
            rt_map = {'1': (3, 3), '2': (6, 6), '3': (9, 9), '4': (12, 12)}
            if report_types in rt_map:
                m_end = rt_map[report_types][1]
                rt_cond = f"AND (CAST(strftime('%m', make_timestamp(f.end_date*1000)) AS INTEGER) = {m_end})"

        return conn.execute(f"""
            SELECT f.code,
                   f.end_date,
                   f.ann_date  AS publ_date,
                   f.eps, f.bps, f.roe,
                   f.pe_ttm, f.pb, f.ps_ttm, f.np_yoy
            FROM fin_indicator f
            WHERE f.code IN ('{codes_in}')
              AND {where_date} {where_year} {rt_cond}
            ORDER BY f.code, f.end_date
        """).fetchdf()

    # ===================== 参考数据查询 =====================

    def query_index_constituents(self, index_code) -> pd.DataFrame:
        """迁移自 PtradeAPI.get_index_stocks() (ptrade_api.py:607-610)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            SELECT DISTINCT code FROM index_constituents
            WHERE index_code = '{index_code}'
        """).fetchdf()

    def query_all_stocks(self, before_ms) -> pd.DataFrame:
        """迁移自 PtradeAPI.get_Ashares() (ptrade_api.py:1566-1570)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            SELECT DISTINCT code FROM stock_daily WHERE time = (
                SELECT MAX(time) FROM stock_daily WHERE time <= {before_ms}
            )
        """).fetchdf()

    def query_sw_industry(self, code):
        """迁移自 PtradeAPI.get_industry() (ptrade_api.py:2024-2026)

        返回 (industry_code, industry_name) 或 None
        """
        conn = self._get_conn()
        if conn is None:
            return None
        return conn.execute(
            f"SELECT industry_code, industry_name FROM sw_industry WHERE code='{code}' LIMIT 1"
        ).fetchone()

    def query_etf_list_active(self) -> pd.DataFrame:
        """迁移自 PtradeAPI.get_etf_list() (ptrade_api.py:1621-1628)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute("""
            SELECT DISTINCT code FROM stock_daily
            WHERE (code LIKE '510%' OR code LIKE '511%' OR code LIKE '512%'
                   OR code LIKE '513%' OR code LIKE '515%' OR code LIKE '516%'
                   OR code LIKE '518%' OR code LIKE '588%' OR code LIKE '159%')
              AND time = (SELECT MAX(time) FROM stock_daily)
              AND volume > 0
        """).fetchdf()

    def query_cb_list_active(self) -> pd.DataFrame:
        """迁移自 PtradeAPI.get_cb_list() (ptrade_api.py:1689-1695)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute("""
            SELECT DISTINCT code FROM stock_daily
            WHERE (code LIKE '110%' OR code LIKE '113%' OR code LIKE '118%'
                   OR code LIKE '123%' OR code LIKE '127%' OR code LIKE '128%')
              AND time = (SELECT MAX(time) FROM stock_daily)
              AND volume > 0
        """).fetchdf()

    def query_market_detail(self, table, condition) -> pd.DataFrame:
        """迁移自 PtradeAPI.get_market_detail() (ptrade_api.py:1748-1751)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            SELECT DISTINCT code FROM {table}
            WHERE ({condition}) AND time = (SELECT MAX(time) FROM {table})
        """).fetchdf()

    def query_daily_for_status(self, date_ms) -> pd.DataFrame:
        """简化版日线查询，专供 filter_stock_by_status / check_limit / get_stock_status 使用。

        迁移自 BacktestEngine._get_daily_data() 的简化列集。
        注：Phase 1 中这些 API 实际走内存（self._prev_day_data / self._current_day_data），
        本方法预留给 Phase 2 Provider 接口，当前不被直接调用。
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(f"""
            SELECT code, close, preClose, volume, suspendFlag,
                   is_st_reliable, is_st_reliable_source,
                   is_delisting_risk, is_delisting_risk_source
            FROM stock_daily WHERE time = {date_ms}
        """).fetchdf()

    def query_security_info_from_preload(self, code):
        """从 self._preload_listing 查上市日期（毫秒时间戳）或 None。

        迁移自 PtradeAPI.get_security_info() 的预加载分支 (ptrade_api.py:1989-1993)
        """
        if self._preload_listing is None:
            return None
        row = self._preload_listing[self._preload_listing['code'] == code]
        if len(row) > 0 and row.iloc[0]['listing_time']:
            return row.iloc[0]['listing_time']
        return None

    # ===================== 交易日历查询 =====================

    def query_trade_days_range(self, start_ms, end_ms) -> pd.DataFrame:
        """迁移自 BacktestEngine._get_trade_days() (backtest_engine.py:712-720)

        start_ms / end_ms 任一为 None 时对应条件不生效（等价于 1=1）。
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        conditions = []
        if start_ms is not None:
            conditions.append(f"time >= {start_ms}")
        if end_ms is not None:
            conditions.append(f"time <= {end_ms}")
        where = " AND ".join(conditions) if conditions else "1=1"
        return conn.execute(f"SELECT DISTINCT time FROM stock_daily WHERE {where} ORDER BY time").fetchdf()

    def query_trade_day_offset(self, curr_ms, offset) -> pd.DataFrame:
        """迁移自 PtradeAPI.get_trading_day() (ptrade_api.py:1473-1481)

        offset=0: SELECT MAX(time) FROM stock_daily WHERE time <= curr_ms
        offset>0: SELECT DISTINCT time ... WHERE time > curr_ms ORDER BY time LIMIT offset → tail(1)
        offset<0: SELECT DISTINCT time ... WHERE time < curr_ms ORDER BY time DESC LIMIT abs(offset) → tail(1)
        """
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        if offset == 0:
            df = conn.execute(f"SELECT MAX(time) as t FROM stock_daily WHERE time <= {curr_ms}").fetchdf()
        elif offset > 0:
            df = conn.execute(f"SELECT DISTINCT time FROM stock_daily WHERE time > {curr_ms} ORDER BY time LIMIT {offset}").fetchdf()
            df = df.tail(1)
        else:
            df = conn.execute(f"SELECT DISTINCT time FROM stock_daily WHERE time < {curr_ms} ORDER BY time DESC LIMIT {abs(offset)}").fetchdf()
            df = df.tail(1)
        return df

    def query_kline_count(self, before_ms) -> int:
        """迁移自 PtradeAPI.get_current_kline_count() (ptrade_api.py:1954-1956)"""
        conn = self._get_conn()
        if conn is None:
            return 0
        n = conn.execute(
            f"SELECT COUNT(DISTINCT time) FROM stock_daily WHERE time <= {before_ms}"
        ).fetchone()[0]
        return int(n)

    # ===================== 工具（与 PtradeAPI._to_ptrade_code 重复，保持行为一致）=====================

    @staticmethod
    def _to_ptrade_code(bare_code: str) -> str:
        """Normalize through the authoritative PTrade code rules."""
        from ..libs.security_code_rules import normalize_to_ptrade
        return normalize_to_ptrade(bare_code)


def CodeDict_clone(dfs):
    """复用 ptrade_api.CodeDict（跨模块导入在运行时完成，避免循环依赖）。"""
    from ..ptrade_api import CodeDict
    return CodeDict(dfs)
