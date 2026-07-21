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
            logger.debug(f"[Preload] 加载 {len(self._preload_daily)} 行行情")
            # 预加载每只股票的上市日期（get_security_info 用，避免逐只 MIN(time) 查询）
            # 上市日是历史固定值，无 PIT 问题，可全局加载
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

    def query_listing_dates(self) -> pd.DataFrame:
        """迁移自 PtradeAPI._preload_market_data() 中的上市日期查询 (ptrade_api.py:388-390)"""
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        return conn.execute(
            "SELECT code, MIN(time) as listing_time FROM stock_daily GROUP BY code"
        ).fetchdf()

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
            sub_all = self._preload_daily[self._preload_daily['code'] == bare]
            if prev_ms is not None:
                sub_all = sub_all[sub_all['time'] <= prev_ms]
            sub = sub_all.head(int(count))
            if len(sub) == 0:
                # 尝试 ETF/指数 fallback（与 DuckDB 路径一致的 INDEX_ETF_MAP）
                INDEX_ETF_MAP = {"000300": "510300", "000905": "510500",
                                 "000016": "510050", "000852": "510880"}
                if bare in INDEX_ETF_MAP:
                    sub = self._preload_daily[self._preload_daily['code'] == INDEX_ETF_MAP[bare]].head(int(count))
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
