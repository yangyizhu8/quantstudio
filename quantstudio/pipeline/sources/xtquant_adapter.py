"""XtquantAdapter — 迅投 miniQMT 数据源（本地客户端，需 QMT 运行）

代码格式 600000.SH（带.market后缀）；vol=股, amount=元；支持 tick/1m/5m/1d。
复权支持 none/front/back/front_ratio/back_ratio。

依赖：需安装 miniQMT 客户端 + xtquant 库（QMT 安装目录 user_data_path 里）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .base import BaseSourceAdapter

logger = logging.getLogger(__name__)

# xtquant period → 我们的 freq 映射
PERIOD_MAP = {
    "daily": "1d",
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "30min": "30m",
    "60min": "1h",
}
# 反向映射（xtquant period → 我们的 freq）
PERIOD_REVERSE = {v: k for k, v in PERIOD_MAP.items()}

# 我们的 table → xtquant period
TABLE_PERIOD = {
    ("stock_daily", "daily"): "1d",
    ("etf_daily", "daily"): "1d",   # ETF 日线复用个股 1d period
    ("stock_minutes", "1min"): "1m",
    ("stock_minutes", "5min"): "5m",
    ("stock_minutes", "15min"): "15m",
    ("stock_minutes", "30min"): "30m",
    ("stock_minutes", "60min"): "1h",
    ("etf_minutes", "1min"): "1m",       # ETF 分钟线复用个股 1m period
    ("etf_minutes", "5min"): "5m",
    ("etf_minutes", "15min"): "15m",
    ("etf_minutes", "30min"): "30m",
    ("etf_minutes", "60min"): "1h",
    ("tick", "tick"): "tick",
    ("index_daily", "daily"): "1d",
}

# ---- 财务类表（走 get_financial_data，不走行情 period）----
# DuckDB 表 → xtquant 报表类型 + 字段映射（DuckDB字段 ← xtquant字段）
# 未映射的 DuckDB 字段在 aligner 层留 NULL（xtquant 无对应或字段名待补）
FINANCIAL_TABLE_MAP = {
    "stock_float_share": {
        "xt_report": "Capital",
        "download_type": "Capital",
        "fields": {
            # DuckDB: (code, end_date, ann_date, free_share, total_share, circ_mv, total_mv)
            "total_capital": "total_share",            # xtquant 总股本 → DuckDB total_share
            "circulating_capital": "free_share",       # 流通股本 → free_share
            # circ_mv / total_mv xtquant 无，由 aligner 用 free_share×close 补算或留 NULL
        },
    },
    "balance_statement": {
        "xt_report": "Balance",
        "download_type": "Balance",
        "fields": {
            "total_assets": "total_assets",
            "total_liability": "total_liability",
            "total_equity": "total_equity",
            # 其余 DuckDB 字段（total_current_assets 等）xtquant 有但字段名待补，先留 NULL
        },
    },
    "income_statement": {
        "xt_report": "Income",
        "download_type": "Income",
        "fields": {
            "operating_revenue": "operating_revenue",
            "operating_cost": "operating_cost",
            "operating_profit": "operating_profit",
            "total_profit": "total_profit",
            "net_profit": "net_profit",
            "np_parent_company_owners": "np_parent_company_owners",
        },
    },
    "cashflow_statement": {
        "xt_report": "CashFlow",
        "download_type": "CashFlow",
        "fields": {
            "net_operate_cash_flow": "net_operate_cash_flow",
            "net_invest_cash_flow": "net_invest_cash_flow",
            "net_finance_cash_flow": "net_finance_cash_flow",
        },
    },
    "fin_indicator": {
        "xt_report": "PershareIndex",
        "download_type": "PfC",
        "fields": {
            "s_fa_eps_basic": "eps",
            "s_fa_bps": "bps",
            "du_return_on_equity": "roe",
        },
    },
}


class XtquantAdapter(BaseSourceAdapter):
    """xtquant 数据源适配器（需 miniQMT 客户端运行）

    config 示例：
        {"name": "xtquant", "qmt_path": "D:/国金QMT/userdata_mini"}
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        self.qmt_path = config.get("qmt_path", "")
        self._init_client()

    def _init_client(self):
        try:
            import xtquant.xtdata as xtdata
        except ImportError as e:
            raise ImportError(
                "未安装 xtquant。请确保 miniQMT 客户端已安装，"
                "且 xtquant 库路径在 PYTHONPATH 中。"
                f"\n错误: {e}") from e
        self._client = xtdata
        # 连接延迟到首次真正取数时（_ensure_connected）。
        # 这样 xtquant 作为回退源被 _resolve_source_chain 实例化/校验时（supports_task 不需要连接），
        # 不会在未启动 QMT 的环境下打出不相关的 WARNING，避免误导用户以为权威源用了 xtquant。
        self._connected = False

    def _ensure_connected(self):
        """首次真正访问 miniQMT 时才连接（懒加载）。"""
        if self._connected:
            return
        try:
            self._client.connect()
            self._connected = True
            logger.info("[XtquantAdapter] 连接 miniQMT 成功")
        except Exception as e:
            logger.warning(f"[XtquantAdapter] 连接 miniQMT 失败（可能未启动QMT）: {e}")
            raise

    def supports_freq(self, freq: str) -> bool:
        return freq in PERIOD_MAP

    def get_st_codes(self) -> set:
        """xtquant 获取 ST 股票列表（从板块成分股获取）"""
        try:
            self._ensure_connected()
            xt = self._client
            # xtquant 的 ST 股票可通过板块获取
            st_stocks = xt.get_stock_list_in_sector("ST板块")
            if st_stocks:
                # 转裸码
                st_codes = set(s.split(".")[0] for s in st_stocks if "." in s)
                logger.info(f"[XtquantAdapter] ST 股票: {len(st_codes)} 只")
                self._st_codes = st_codes
                return st_codes
            return set()
        except Exception as e:
            logger.warning(f"[XtquantAdapter] get_st_codes 失败: {e}")
            return getattr(self, "_st_codes", set())

    def supports_task(self, table: str, freq: str) -> tuple:
        """xtquant 支持：日线 + 分钟线 + tick + 指数 + 财务（Capital/三大报表/PershareIndex）"""
        # 财务类表通过 get_financial_data 拉取（不走行情 period）
        if table in FINANCIAL_TABLE_MAP and freq == "daily":
            return (True, "")
        if (table, freq) in TABLE_PERIOD:
            return (True, "")
        return (False, f"xtquant 未实现 {table}/{freq}")

    def get_last_date(self, table: str, freq: str = "daily") -> Optional[str]:
        return None

    @staticmethod
    def _merge_adjusted_ohlc(raw: pd.DataFrame, adjusted: pd.DataFrame,
                             suffix: str) -> pd.DataFrame:
        """按 bar 时间键合并复权 OHLC，避免缺 bar 时发生位置错配。"""
        if adjusted is None or len(adjusted) == 0:
            return raw
        price_cols = {"open", "high", "low", "close"}
        keys = [c for c in raw.columns if c in adjusted.columns and c not in price_cols]
        if not keys:
            return raw
        time_key = "time" if "time" in keys else ("index" if "index" in keys else keys[0])
        rename = {c: f"{c}_{suffix}" for c in price_cols if c in adjusted.columns}
        right = adjusted[[time_key] + list(rename)].rename(columns=rename)
        right = right.drop_duplicates(time_key, keep="last")
        return raw.merge(right, on=time_key, how="left")

    def get_all_stock_codes(self) -> List[str]:
        """获取全市场 A 股股票代码（xtquant 格式 600000.SH）"""
        try:
            self._ensure_connected()
            # xtquant 的 get_stock_list_in_sector 获取板块成分股
            stocks = self._client.get_stock_list_in_sector("沪深A股")
            if not stocks:
                stocks = self._client.get_stock_list_in_sector("沪深A股")
            logger.info(f"[XtquantAdapter] 全市场 A 股: {len(stocks)} 只")
            return stocks
        except Exception as e:
            logger.error(f"[XtquantAdapter] get_all_stock_codes 失败: {e}")
            return []

    def get_etf_codes(self) -> List[str]:
        """获取全市场 ETF 代码（xtquant 格式 510050.SH / 159919.SZ）。
        通过板块成分股接口获取（沪深ETF / ETF基金 板块）。"""
        try:
            self._ensure_connected()
            codes = self._client.get_stock_list_in_sector("沪深ETF")
            if not codes:
                codes = self._client.get_stock_list_in_sector("ETF基金")
            logger.info(f"[XtquantAdapter] 全市场 ETF: {len(codes)} 只")
            return codes
        except Exception as e:
            logger.error(f"[XtquantAdapter] get_etf_codes 失败: {e}")
            return []

    def fetch_table(self, table: str, start: str, end: str,
                    freq: str = "daily",
                    codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        # 财务类表走专门的 get_financial_data 路径（不走行情 period）
        if table in FINANCIAL_TABLE_MAP:
            return self._fetch_financial(table, start, end, codes)

        self._ensure_connected()
        xt = self._client

        # 确定 xtquant period
        period = TABLE_PERIOD.get((table, freq))
        if not period:
            raise ValueError(f"xtquant 不支持 {table}/{freq}")

        # codes=None/ALL → 全市场
        if codes is None or codes == ["ALL"] or codes == "ALL":
            codes = self.get_etf_codes() if table in ("etf_daily", "etf_minutes") else self.get_all_stock_codes()

        # 先下载数据（xtquant 需要先 download 再 get）
        for code in codes:
            try:
                xt.download_history_data(code, period, start, end)
            except Exception as e:
                logger.debug(f"[XtquantAdapter] download {code} {period} failed: {e}")

        # 获取数据
        # 复权策略：xtquant 原生支持 dividend_type=front/back，直接拉出前/后复权价
        # 作为 open_front/close_front 等列，交由 aligner 的 passthrough 路径填充
        # （与 baostock 统一），彻底摆脱对 tushare adj_factor 接口（2000 积分门槛）的依赖。
        dfs = []
        for code in codes:
            try:
                st = start.replace("-", "")
                et = end.replace("-", "")
                # 主列：原始价（不复权）
                data = xt.get_market_data_ex(
                    stock_list=[code], period=period,
                    start_time=st, end_time=et, dividend_type="none")
                if code not in data or len(data[code]) == 0:
                    continue
                df = data[code].copy().reset_index()
                df["stock_code"] = code
                # 前复权价 → open_front/high_front/low_front/close_front
                try:
                    fdata = xt.get_market_data_ex(
                        stock_list=[code], period=period,
                        start_time=st, end_time=et, dividend_type="front")
                    if code in fdata and len(fdata[code]) > 0:
                        fr = fdata[code].reset_index()
                        df = self._merge_adjusted_ohlc(df, fr, "front")
                except Exception as e:
                    logger.debug(f"[XtquantAdapter] front {code} failed: {e}")
                # 后复权价 → open_back/...（尽力，失败留 NULL；schema back 列 required=false）
                try:
                    bdata = xt.get_market_data_ex(
                        stock_list=[code], period=period,
                        start_time=st, end_time=et, dividend_type="back")
                    if code in bdata and len(bdata[code]) > 0:
                        bk = bdata[code].reset_index()
                        df = self._merge_adjusted_ohlc(df, bk, "back")
                except Exception as e:
                    logger.debug(f"[XtquantAdapter] back {code} failed: {e}")
                dfs.append(df)
            except Exception as e:
                logger.debug(f"[XtquantAdapter] get {code} failed: {e}")

        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

        metadata = {
            "source": "xtquant", "freq": freq, "table": table,
            "code_format": "tushare_to_raw",  # 600000.SH → 裸码
            "date_format": "YYYYMMDD",
            "units": {"vol": "股", "amount": "元", "pct_chg": "%"},
            "rows": len(df),
        }
        logger.info(f"[XtquantAdapter] {table}/{freq} fetched {len(df)} rows")
        return df, metadata

    def _fetch_financial(self, table: str, start: str, end: str,
                         codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        """拉取财务类表（Capital/三大报表/PershareIndex），走 get_financial_data。

        返回的 DataFrame 已映射到 DuckDB schema 字段：
        - code: 裸码（从 002719.SZ 提取）
        - end_date / ann_date: m_timetag / m_anntime（YYYYMMDD 字符串，aligner 转 ms）
        - 业务字段：按 FINANCIAL_TABLE_MAP[table].fields 映射，未映射的留空不入
        """
        cfg = FINANCIAL_TABLE_MAP[table]
        xt_report = cfg["xt_report"]
        download_type = cfg["download_type"]
        fields_map = cfg["fields"]  # {xtquant字段: DuckDB字段}

        self._ensure_connected()
        xt = self._client
        # codes=None/ALL → 全市场 A 股
        if codes is None or codes == ["ALL"] or codes == "ALL":
            codes = self.get_all_stock_codes()

        # start/end 转 YYYYMMDD（download_financial_data 用）
        start_fmt = str(start).replace("-", "")[:8]
        end_fmt = str(end).replace("-", "")[:8]

        all_rows = []
        total = len(codes)
        log_interval = max(50, total // 20)  # 约 5% 打一次，至少 50 只
        t_start = __import__("time").time()
        for idx, code in enumerate(codes, 1):
            # 进度日志（防误判卡死）：每 log_interval 只 + 首只 + 最后一只
            if idx == 1 or idx == total or idx % log_interval == 0:
                elapsed = __import__("time").time() - t_start
                rate = idx / elapsed if elapsed > 0 else 0
                eta = (total - idx) / rate if rate > 0 else 0
                logger.info(f"[XtquantAdapter] {table}({xt_report}) 进度: {idx}/{total} "
                            f"({idx*100//total}%)，累计 {len(all_rows)} 行，"
                            f"{rate:.1f} 只/秒，预计剩余 {eta:.0f} 秒")
            try:
                # get_financial_data（取该票全部历史，按 start/end 过滤由后续裁剪）
                # 不强制 download（本地已缓存则直接取；缺数据时由 daemon 增量补）
                fd = xt.get_financial_data([code])
                if code not in fd:
                    continue
                stock_data = fd[code]
                if xt_report not in stock_data:
                    continue
                df_one = stock_data[xt_report]
                if not isinstance(df_one, pd.DataFrame) or df_one.empty:
                    continue
                # 按 m_timetag 过滤 start~end（YYYYMMDD 字符串比较）
                if "m_timetag" in df_one.columns:
                    df_one = df_one[df_one["m_timetag"].astype(str).between(start_fmt, end_fmt)]
                if len(df_one) == 0:
                    continue
                df_one = df_one.copy()
                df_one["code"] = str(code).split(".")[0]  # 裸码
                all_rows.append(df_one)
            except Exception as e:
                logger.debug(f"[XtquantAdapter] financial {code} {xt_report}: {e}")

        if not all_rows:
            return pd.DataFrame(), {"source": "xtquant", "table": table, "rows": 0}

        raw = pd.concat(all_rows, ignore_index=True)

        # 字段映射：构建输出 DataFrame（code, end_date, ann_date, 业务字段...）
        out = pd.DataFrame()
        out["code"] = raw["code"]
        out["end_date"] = raw["m_timetag"].astype(str)   # YYYYMMDD，aligner 转 ms
        out["ann_date"] = raw["m_anntime"].astype(str)   # 公告日，PIT 关键
        for xt_field, db_field in fields_map.items():
            if xt_field in raw.columns:
                out[db_field] = raw[xt_field]

        metadata = {
            "source": "xtquant",
            "table": table,
            "freq": "quarterly",
            "code_format": "xtquant_to_raw",  # 002719.SZ → 002719
            "date_format": "YYYYMMDD",        # end_date/ann_date 是字符串，aligner time_to_ms 转
            "units": {},
            "rows": len(out),
            "xt_report": xt_report,
        }
        logger.info(f"[XtquantAdapter] {table} ({xt_report}) fetched {len(out)} rows "
                    f"({len(codes)} codes)")
        return out, metadata

    def close(self):
        try:
            self._client.disconnect()
            logger.info("[XtquantAdapter] 断开连接")
        except Exception:
            pass
        super().close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        adapter = XtquantAdapter({"name": "xtquant"})
        df, meta = adapter.fetch_table("stock_daily", "2026-07-07", "2026-07-10",
                                       codes=["600000.SH"])
        print(df.head() if len(df) else "（无数据 - 可能QMT未运行）")
        adapter.close()
    except ImportError as e:
        print(f"⚠ 跳过：{e}")
