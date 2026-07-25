"""TushareAdapter — Tushare Pro 数据源（标准源）

tushare 是字段口径锚点（identity=true，无需映射）。
日线：daily()；分钟：stk_mins()（月度分页）；财务：fina_indicator()；指数：index_daily()
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .base import BaseSourceAdapter

logger = logging.getLogger(__name__)

# tushare API 方法 → table 映射
TUSHARE_API_MAP = {
    ("stock_daily", "daily"):   "daily",
    ("etf_daily", "daily"):     "fund_daily",   # ETF/基金日线用 fund_daily（daily 接口不含基金）
    ("etf_basic", "daily"):     "fund_basic",   # ETF reference snapshot; no date range
    ("stock_minutes", "1min"):  "stk_mins",
    ("stock_minutes", "5min"):  "stk_mins",
    ("stock_minutes", "15min"): "stk_mins",
    ("stock_minutes", "30min"): "stk_mins",
    ("stock_minutes", "60min"): "stk_mins",
    ("etf_minutes", "1min"):    "stk_mins",   # ETF 分钟线复用 stk_mins（tushare 无 fund_mins，stk_mins 支持 ETF 代码）
    ("fin_indicator", "daily"): "fina_indicator",
    ("index_daily", "daily"):   "index_daily",
    # 三大报表（按 ts_code 逐只查，全历史）
    ("balance_statement", "daily"):  "balancesheet",
    ("income_statement", "daily"):   "income",
    ("cashflow_statement", "daily"): "cashflow",   # tushare 接口名是 cashflow（单数）
    # 除权除息
    ("stock_dividend", "daily"):     "dividend",
}


class TushareAdapter(BaseSourceAdapter):
    """Tushare Pro 数据源适配器

    config 示例：
        {"name": "tushare", "token": "xxx",
         "rate_limit": {"calls_per_min": 200, "wait_on_429": True}}
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        self.token = config.get("token") or self.api_config.get("token")
        if not self.token:
            raise ValueError("TushareAdapter 缺少 token（TUSHARE_TOKEN）")
        self._init_client()

    def _init_client(self):
        try:
            import tushare as ts
        except ImportError as e:
            raise ImportError("未安装 tushare，请 pip install tushare") from e
        ts.set_token(self.token)
        # 显式设置请求超时（tushare 底层 requests 默认 30s，这里显式写出，
        # 让限流/网络异常尽快抛出而非慢滴挂起；配合 base._retry_with_backoff 的
        # 线程级硬超时，彻底杜绝"调用挂起导致整轮任务假死"）。
        self._client = ts.pro_api(self.token, timeout=30)
        logger.info(f"[TushareAdapter] 初始化成功")

    def supports_freq(self, freq: str) -> bool:
        return freq in ("daily", "1min", "5min", "15min", "30min", "60min")

    def supports_task(self, table: str, freq: str) -> tuple:
        """tushare 支持矩阵：日线 + 分钟线 + 财务 + 指数，不支持 tick"""
        supported = set(TUSHARE_API_MAP) | {
            ("stock_float_share", "daily"), ("stock_daily_valuation", "daily"),
            ("index_constituents", "daily"), ("sw_industry", "daily")}
        return ((True, "") if (table, freq) in supported else
                (False, f"tushare 未实现 {table}/{freq}"))

    def get_last_date(self, table: str, freq: str = "daily") -> Optional[str]:
        """tushare 本身无状态，由 DBWriter 维护水位，这里返回 None"""
        return None

    def get_all_stock_codes(self) -> List[str]:
        """获取全市场 A 股股票代码（tushare 格式 600000.SH）。
        tushare 的 stock_basic API 一次返回全部，比 baostock 快得多。"""
        try:
            df = self._retry_with_backoff(
                self._call_api, "stock_basic",
                exchange="", list_status="L",  # L=上市
                fields="ts_code,symbol,name,list_status")
            # 过滤 A 股（沪深主板/创业板/科创板/北交所）
            a_share = df[df["ts_code"].str.match(r"^\d{6}\.(SH|SZ|BJ)$")]["ts_code"].tolist()
            logger.info(f"[TushareAdapter] 全市场 A 股: {len(a_share)} 只")
            return a_share
        except Exception as e:
            logger.error(f"[TushareAdapter] get_all_stock_codes 失败: {e}")
            return []

    def get_etf_codes(self) -> List[str]:
        """获取全市场 ETF 基金代码（tushare 格式 510050.SH / 159919.SZ）。
        沪市 ETF: 51x/56x/58x 开头；深市 ETF: 15x 开头。"""
        try:
            df = self._retry_with_backoff(
                self._call_api, "fund_basic",
                market="E",   # E=场内
                fields="ts_code,name,status")
            # status='L' 过滤在上市状态的（fund_basic 用 status 字段，非 list_status）
            df = df[df["status"] == "L"]
            etf_codes = df["ts_code"].tolist()
            logger.info(f"[TushareAdapter] 全市场 ETF: {len(etf_codes)} 只")
            return etf_codes
        except Exception as e:
            logger.error(f"[TushareAdapter] get_etf_codes 失败: {e}")
            return []

    def get_index_codes(self) -> List[str]:
        """获取主要指数代码（tushare 格式 000300.SH / 399001.SZ）。
        包含沪深交易所核心指数，用于 index_daily 全市场拉取。"""
        # 常用指数代码（市场认可度高，策略常用）
        core_indices = [
            "000001.SH",  # 上证指数
            "000016.SH",  # 上证50
            "000300.SH",  # 沪深300
            "000905.SH",  # 中证500
            "000852.SH",  # 中证1000
            "399001.SZ",  # 深证成指
            "399005.SZ",  # 中小板指
            "399006.SZ",  # 创业板指
            "399101.SZ",  # 中小板综
            "399102.SZ",  # 创业板综
            "399300.SZ",  # 大盘价值
            "000688.SH",  # 科创50
            "000688.CSI",  # 科创50(中证)
            "899050.BJ",  # 北证50
        ]
        logger.info(f"[TushareAdapter] 指数代码: {len(core_indices)} 个")
        return core_indices

    def get_st_codes(self) -> set:
        """获取当前 ST 股票代码集合（裸码格式）。
        tushare 的 stock_basic 表 name 字段含 'ST' 标识。"""
        try:
            df = self._retry_with_backoff(
                self._call_api, "stock_basic",
                exchange="", list_status="L",
                fields="ts_code,symbol,name")
            # name 含 'ST' 或 '*ST' 的为 ST 股票
            st_df = df[df["name"].str.contains("ST", case=False, na=False)]
            # 转裸码（去掉 .SH/.SZ 后缀）
            st_codes = set()
            for ts_code in st_df["ts_code"]:
                bare = str(ts_code).split(".")[0]
                st_codes.add(bare)
            logger.info(f"[TushareAdapter] ST 股票: {len(st_codes)} 只")
            self._st_codes = st_codes  # 缓存
            return st_codes
        except Exception as e:
            logger.warning(f"[TushareAdapter] get_st_codes 失败: {e}")
            return getattr(self, "_st_codes", set())

    def fetch_table(self, table: str, start: str, end: str,
                    freq: str = "daily",
                    codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        # codes=None/ALL → 自动获取全市场
        if codes is None or codes == ["ALL"] or codes == "ALL":
            if table in ("stock_float_share", "index_constituents", "etf_basic"):
                pass  # 这两个表有自己的 codes 逻辑
            elif table == "etf_daily":
                codes = self.get_etf_codes()
            elif table == "etf_minutes":
                codes = self.get_etf_codes()
            elif table == "index_daily":
                codes = self.get_index_codes()
            else:
                codes = self.get_all_stock_codes()

        # 流通股本表：从 daily_basic 提取 circ_mv/free_share
        # ETF reference snapshot; standardized by FieldAligner.
        if table == "etf_basic":
            return self._fetch_etf_basic()

        if table == "stock_float_share":
            return self._fetch_float_share(start, end, codes)

        # 指数成分股表
        if table == "index_constituents":
            return self._fetch_index_constituents(start, end, codes)

        # ETF 日线表：按 ts_code 遍历全市场拉取
        if table == "etf_daily":
            return self._fetch_etf_daily(start, end, codes)

        # 三大报表（balancesheet/income/cashflows，按 ts_code 逐只查全历史）
        if table in ("balance_statement", "income_statement", "cashflow_statement"):
            return self._fetch_financial_report(table, start, end, codes)

        # 除权除息（dividend，按 ts_code 逐只查全历史）
        if table == "stock_dividend":
            return self._fetch_dividend(start, end, codes)

        # 申万行业分类（index_classify + stock_basic 合并）
        if table == "sw_industry":
            return self._fetch_sw_industry(start, end, codes)

        api_name = TUSHARE_API_MAP.get((table, freq))
        if not api_name:
            raise ValueError(f"tushare 不支持 {table}/{freq}")

        start_fmt = self._fmt_date(start)
        end_fmt = self._fmt_date(end)

        if table in ("stock_minutes", "etf_minutes"):
            df = self._fetch_minutes(codes, start_fmt, end_fmt, freq)
        elif codes:
            # 按代码循环拉
            dfs = []
            for code in codes:
                raw = self._retry_with_backoff(
                    self._call_api, api_name, ts_code=code,
                    start_date=start_fmt, end_date=end_fmt)
                dfs.append(raw)
            df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        else:
            df = self._retry_with_backoff(
                self._call_api, api_name, start_date=start_fmt, end_date=end_fmt)

        # [补丁1] stock_daily 需要 merge daily_basic 补 peTTM/pbMRQ/psTTM/turn
        # khQuant 36 字段里的扩展指标在 tushare 分布在 daily_basic 表，不在 daily 里
        is_daily_basic_needed = (table == "stock_daily")
        if is_daily_basic_needed and len(df) > 0:
            df = self._merge_daily_basic(df, codes, start_fmt, end_fmt)

        # 填充 isST（tushare 的 daily_basic 不含 isST，从 stock_basic.name 判断）
        if table == "stock_daily" and "ts_code" in df.columns:
            df = self.fill_isst(df, code_col="ts_code",
                                ) if hasattr(self, "fill_isst") else df
            # fill_isst 用 code_col 匹配，但 ts_code 是 600000.SH 格式
            # 需要用裸码匹配
            st_codes = self.get_st_codes()
            if st_codes:
                df["isST"] = df["ts_code"].apply(
                    lambda tc: 1 if str(tc).split(".")[0] in st_codes else 0)

        metadata = {
            "source": "tushare", "freq": freq, "table": table,
            "code_format": "tushare_to_raw", "date_format": "YYYYMMDD",
            "units": {"vol": "手", "amount": "千元", "pct_chg": "%"},
            "rows": len(df),
        }
        logger.info(f"[TushareAdapter] {table}/{freq} fetched {len(df)} rows ({start}~{end})")
        return df, metadata

    def _merge_daily_basic(self, daily_df: pd.DataFrame, codes: Optional[List[str]],
                           start_fmt: str, end_fmt: str) -> pd.DataFrame:
        """[补丁1] merge daily_basic 补 peTTM/pbMRQ/psTTM/turnover_rate。
        daily_basic 字段：ts_code/trade_date/turnover_rate/pe_ttm/pb/ps_ttm 等。
        pcfNcfTTM 无直接对应暂不补（aligner 阶段留 NULL）。
        isST 由 daemon/aligner 从 stock_basic.name LIKE %ST% 判断（此处不拉 stock_basic，避免额外请求）。"""
        try:
            if codes:
                dfs_basic = []
                for code in codes:
                    raw = self._retry_with_backoff(
                        self._call_api, "daily_basic", ts_code=code,
                        start_date=start_fmt, end_date=end_fmt)
                    dfs_basic.append(raw)
                basic_df = pd.concat(dfs_basic, ignore_index=True) if dfs_basic else pd.DataFrame()
            else:
                basic_df = self._retry_with_backoff(
                    self._call_api, "daily_basic", start_date=start_fmt, end_date=end_fmt)
            if len(basic_df) == 0:
                logger.warning("[TushareAdapter] daily_basic 返回空，扩展指标留 NULL")
                return daily_df
            # 仅保留需要的列，避免列名冲突
            keep = ["ts_code", "trade_date", "turnover_rate", "pe_ttm", "pb", "ps_ttm"]
            basic_df = basic_df[[c for c in keep if c in basic_df.columns]]
            merged = daily_df.merge(basic_df, on=["ts_code", "trade_date"], how="left")
            logger.info(f"[TushareAdapter] merged daily_basic: {len(merged)} rows "
                        f"(+turnover_rate/pe_ttm/pb/ps_ttm)")
            return merged
        except Exception as e:
            logger.warning(f"[TushareAdapter] daily_basic merge 失败（扩展指标留 NULL）: {e}")
            return daily_df

    def _call_api(self, api_name: str, **params) -> pd.DataFrame:
        api = getattr(self._client, api_name)
        return api(**params)

    def _fetch_minutes(self, codes: List[str], start: str, end: str, freq: str) -> pd.DataFrame:
        """分钟数据月度分页拉取（tushare stk_mins 限制）"""
        dfs = []
        target_codes = codes or ["000001.SZ"]  # 无 codes 时拉示例
        # 生成月份列表
        months = self._month_range(start, end)
        for code in target_codes:
            for ym in months:
                try:
                    raw = self._retry_with_backoff(
                        self._call_api, "stk_mins", ts_code=code, freq=freq,
                        start_date=f"{ym}01 09:00:00", end_date=f"{ym}31 15:00:00")
                    dfs.append(raw)
                except Exception as e:
                    logger.warning(f"[TushareAdapter] stk_mins {code}/{ym} failed: {e}")
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    @staticmethod
    def _fmt_date(s: str) -> str:
        return s.replace("-", "")[:8]

    @staticmethod
    def _month_range(start: str, end: str) -> List[str]:
        s = datetime.strptime(start[:6 if len(start) == 8 else 6].zfill(6) if len(start) >= 6 else start,
                              "%Y%m" if len(start) >= 6 else "%Y%m")
        e = datetime.strptime(end[:6], "%Y%m")
        months = []
        cur = s
        while cur <= e:
            months.append(cur.strftime("%Y%m"))
            cur = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        return months



    def _fetch_etf_basic(self) -> Tuple[pd.DataFrame, Dict]:
        """Fetch the Tushare exchange-fund reference snapshot for etf_basic.

        Only baseline-consumed fields are requested. Fields with unrelated numeric
        units such as issue_amount and p_value are deliberately excluded. The raw
        list/delist dates are YYYYMMDD and are converted by the canonical standardizer.
        """
        fields = (
            "ts_code,name,fund_type,list_date,delist_date,benchmark,"
            "status,invest_type,type"
        )
        df = self._retry_with_backoff(
            self._call_api, "fund_basic", market="E", fields=fields)
        if df is None:
            df = pd.DataFrame()
        metadata = {
            "source": "tushare",
            "source_endpoint": "fund_basic",
            "freq": "daily",
            "table": "etf_basic",
            "granularity": "snapshot",
            "code_format": "tushare",
            "date_format": "YYYYMMDD",
            "units": {
                "list_date": "calendar_date_YYYYMMDD",
                "delist_date": "calendar_date_YYYYMMDD",
            },
            "rows": len(df),
        }
        logger.info(f"[TushareAdapter] etf_basic snapshot fetched {len(df)} raw rows")
        return df, metadata

    def _fetch_float_share(self, start: str, end: str,
                           codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """拉取流通股本+流通市值（tushare daily_basic 的 circ_mv/free_share 字段）。
        按交易日遍历（每天一次全市场 daily_basic 调用）。"""
        from datetime import datetime, timedelta
        import tushare as ts
        pro = ts.pro_api(self.token)

        # 获取交易日历
        try:
            cal = pro.trade_cal(exchange="SSE", start_date=start.replace("-",""),
                                end_date=end.replace("-",""))
            trade_days = cal[cal["is_open"] == 1]["cal_date"].tolist()
        except Exception as e:
            logger.error(f"[TushareAdapter] 流通股本获取交易日历失败: {e}")
            return pd.DataFrame(), {}

        dfs = []
        for day in trade_days:
            try:
                self.rate_limiter.acquire()
                df = pro.daily_basic(trade_date=day,
                    fields="ts_code,trade_date,circ_mv,total_mv,free_share,total_share,turnover_rate")
                if len(df) > 0:
                    dfs.append(df)
            except Exception as e:
                logger.debug(f"[TushareAdapter] daily_basic {day} failed: {e}")

        if not dfs:
            return pd.DataFrame(), {}

        result = pd.concat(dfs, ignore_index=True)
        # 单位转换：circ_mv/total_mv 万元→元, free_share/total_share 万股→股
        result["circ_mv"] = result["circ_mv"] * 10000
        result["total_mv"] = result["total_mv"] * 10000
        result["free_share"] = result["free_share"] * 10000
        result["total_share"] = result["total_share"] * 10000

        metadata = {
            "source": "tushare", "freq": "daily", "table": "stock_float_share",
            "code_format": "tushare_to_raw", "date_format": "YYYYMMDD",
            "units": {"free_share": "股", "circ_mv": "元"},
            "rows": len(result),
        }
        logger.info(f"[TushareAdapter] stock_float_share fetched {len(result)} rows ({len(trade_days)} days)")
        return result, metadata

    def _fetch_index_constituents(self, start: str, end: str,
                                  codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """拉取指数成分股（tushare index_weight）。
        codes 是指数代码列表，如 ['399101']（中小板综）。"""
        import tushare as ts
        pro = ts.pro_api(self.token)

        # 默认拉中小板综
        index_codes = codes or ["399101"]
        # 去掉后缀（tushare index_weight 用 399101.SZ 格式）
        index_map = {"399101": "399101.SZ", "000300": "000300.SH",
                     "000016": "000016.SH", "000905": "000905.SH",
                     "399001": "399001.SZ", "399006": "399006.SZ"}

        dfs = []
        for idx in index_codes:
            bare = str(idx).split(".")[0]
            ts_code = index_map.get(bare, f"{bare}.SZ")
            try:
                self.rate_limiter.acquire()
                df = pro.index_weight(index_code=ts_code,
                                      start_date=start.replace("-",""),
                                      end_date=end.replace("-",""))
                if len(df) > 0:
                    # 成分股月度更新，取每月最后一个交易日的全部成分股
                    df["trade_date"] = df["trade_date"].astype(str)
                    df["ym"] = df["trade_date"].str[:6]
                    # 取每个月最后一日的全部成分股
                    last_day_per_month = df.groupby("ym")["trade_date"].max()
                    df = df[df["trade_date"].isin(list(last_day_per_month))]
                    df = df.drop(columns=["ym"])
                    dfs.append(df)
            except Exception as e:
                logger.warning(f"[TushareAdapter] index_weight {ts_code} failed: {e}")

        if not dfs:
            return pd.DataFrame(), {}

        result = pd.concat(dfs, ignore_index=True)
        # con_code → 成分股代码，加 index_code 列
        result["con_code"] = result["con_code"].astype(str)
        # 提取指数裸码
        result["index_code"] = result["index_code"].apply(
            lambda c: str(c).split(".")[0])
        # con_code 转裸码
        result["stock_code"] = result["con_code"].apply(
            lambda c: str(c).split(".")[0])

        metadata = {
            "source": "tushare", "freq": "daily", "table": "index_constituents",
            "code_format": "tushare_to_raw", "date_format": "YYYYMMDD",
            "units": {"weight": "%"},
            "rows": len(result),
        }
        logger.info(f"[TushareAdapter] index_constituents fetched {len(result)} rows")
        return result, metadata

    def _fetch_etf_daily(self, start: str, end: str,
                         codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """拉取 ETF 日线（tushare fund_daily 接口）。
        ⚠ 按代码逐只查询（fund_daily 的 trade_date 全市场模式有权限限制且历史数据缺失，
        ts_code 模式可查全历史）。2141 只 ETF 逐只拉取，耗时较长（~7分钟）。
        ETF 无 pe/pb，不 merge daily_basic；isST 恒为 0。"""
        import tushare as ts
        import time as _time
        pro = ts.pro_api(self.token)

        # codes=None/ALL → 自动获取全市场 ETF 列表
        if codes is None or codes == ["ALL"] or codes == "ALL":
            etf_codes = self.get_etf_codes()
        else:
            etf_codes = codes

        start_fmt = self._fmt_date(start)
        end_fmt = self._fmt_date(end)

        dfs = []
        total = len(etf_codes)
        t0 = _time.time()
        fail_count = 0
        # 批量模式（>20只）才打进度日志，逐只模式（per_stock 单只调用）静默避免刷屏
        show_progress = total > 20
        for i, code in enumerate(etf_codes):
            if show_progress and (i % 100 == 0 or i == total - 1):
                elapsed = _time.time() - t0
                speed = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (total - i - 1) / speed if speed > 0 else 0
                logger.info(f"[TushareAdapter] ETF 进度: {i+1}/{total} "
                            f"({(i+1)*100//total}%) 已用{elapsed:.0f}s 剩余~{eta:.0f}s "
                            f"({speed:.1f} 只/s)")
            try:
                self.rate_limiter.acquire()
                df = pro.fund_daily(ts_code=code, start_date=start_fmt, end_date=end_fmt)
                if df is not None and len(df) > 0:
                    dfs.append(df)
            except Exception as e:
                fail_count += 1
                logger.debug(f"[TushareAdapter] fund_daily {code} failed: {e}")

        if not dfs:
            # 逐只模式下（per_stock 单只调用）无数据多为新上市 ETF，降级 DEBUG 避免刷屏
            logger.debug(f"[TushareAdapter] etf_daily 无数据 (codes={total} 失败={fail_count})")
            return pd.DataFrame(), {}

        result = pd.concat(dfs, ignore_index=True)
        # ETF 不存在 ST，isST 恒为 0
        result["isST"] = 0

        metadata = {
            "source": "tushare", "freq": "daily", "table": "etf_daily",
            "code_format": "tushare_to_raw", "date_format": "YYYYMMDD",
            "units": {"vol": "手", "amount": "千元", "pct_chg": "%"},
            "rows": len(result),
        }
        # 批量模式打 INFO，逐只模式（per_stock）打 DEBUG 避免刷屏
        if show_progress:
            logger.info(f"[TushareAdapter] etf_daily fetched {len(result)} rows "
                        f"({total} codes, 失败 {fail_count})")
        else:
            logger.debug(f"[TushareAdapter] etf_daily fetched {len(result)} rows ({total} codes)")
        return result, metadata

    def _fetch_financial_report(self, table: str, start: str, end: str,
                                codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """拉取三大报表（balancesheet/income/cashflows），按 ts_code 逐只查全历史。
        fields 由 alignment_rules 的 column_map 反向映射决定（取 tushare 原始字段名）。"""
        import tushare as ts
        import time as _time
        pro = ts.pro_api(self.token)
        api_name = TUSHARE_API_MAP[(table, "daily")]
        if codes is None or codes == ["ALL"]:
            codes = self.get_all_stock_codes()

        # alignment_rules 的 column_map 给出了 tushare原始字段名→标准字段名 的映射
        # 我们需要原始字段名去拉取（反向取 keys）
        import json
        from pathlib import Path
        rules_p = Path("config/alignment_rules.json")
        with rules_p.open("r", encoding="utf-8") as f:
            rules = json.load(f)
        col_map = rules["source_mappings"]["tushare"][table]["column_map"]
        # tushare 字段名（不含 ts_code，ts_code 固定取）
        tushare_fields = ["ts_code", "ann_date", "end_date"] + \
                         [k for k in col_map.keys() if k not in ("ts_code", "code", "ann_date", "end_date", "update_time")]
        fields_str = ",".join(tushare_fields)

        start_fmt = self._fmt_date(start)
        end_fmt = self._fmt_date(end)
        dfs = []
        total = len(codes)
        fail_count = 0
        for i, code in enumerate(codes):
            if total > 50 and i % 200 == 0:
                logger.info(f"[TushareAdapter] {table} 进度: {i}/{total} ({i*100//total}%)")
            try:
                self.rate_limiter.acquire()
                df = getattr(pro, api_name)(ts_code=code, start_date=start_fmt, end_date=end_fmt,
                                             fields=fields_str)
                if df is not None and len(df) > 0:
                    dfs.append(df)
            except Exception as e:
                fail_count += 1
                logger.debug(f"[TushareAdapter] {api_name} {code} failed: {e}")
        if not dfs:
            return pd.DataFrame(), {}
        result = pd.concat(dfs, ignore_index=True)
        metadata = {"source": "tushare", "freq": "daily", "table": table,
                    "code_format": "tushare_to_raw", "date_format": "YYYYMMDD", "rows": len(result)}
        # 批量模式打 INFO，逐只模式（per_stock 单只）打 DEBUG 避免刷屏
        if total > 20:
            logger.info(f"[TushareAdapter] {table} fetched {len(result)} rows ({total} codes, 失败 {fail_count})")
        else:
            logger.debug(f"[TushareAdapter] {table} fetched {len(result)} rows ({total} codes)")
        return result, metadata

    def _fetch_dividend(self, start: str, end: str,
                        codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """拉除权除息（dividend），按 ts_code 逐只查全历史。"""
        import tushare as ts
        pro = ts.pro_api(self.token)
        if codes is None or codes == ["ALL"]:
            codes = self.get_all_stock_codes()
        dfs = []
        total = len(codes)
        fail_count = 0
        for i, code in enumerate(codes):
            if total > 50 and i % 200 == 0:
                logger.info(f"[TushareAdapter] dividend 进度: {i}/{total} ({i*100//total}%)")
            try:
                self.rate_limiter.acquire()
                df = pro.dividend(ts_code=code, fields="ts_code,ex_date,record_date,cash_div,stk_div,div_rat")
                if df is not None and len(df) > 0:
                    dfs.append(df)
            except Exception as e:
                fail_count += 1
                logger.debug(f"[TushareAdapter] dividend {code} failed: {e}")
        if not dfs:
            return pd.DataFrame(), {}
        result = pd.concat(dfs, ignore_index=True)
        # 过滤无除权日的记录（预案/公告阶段，非实际除权）
        if "ex_date" in result.columns:
            result = result.dropna(subset=["ex_date"])
            result = result[result["ex_date"].astype(str).str.strip() != "None"]
        metadata = {"source": "tushare", "freq": "daily", "table": "stock_dividend",
                    "code_format": "tushare_to_raw", "date_format": "YYYYMMDD", "rows": len(result)}
        logger.info(f"[TushareAdapter] stock_dividend fetched {len(result)} rows ({total} codes, 失败 {fail_count})")
        return result, metadata

    def _fetch_sw_industry(self, start: str, end: str,
                           codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """拉申万行业分类（index_classify L1 分类 + stock_basic 的 industry 字段合并）。"""
        import tushare as ts
        pro = ts.pro_api(self.token)
        # 1. 取申万 L1 行业分类
        try:
            ic = pro.index_classify(level="L1", src="SW2021")
        except Exception as e:
            logger.warning(f"[TushareAdapter] index_classify failed: {e}")
            ic = pd.DataFrame()
        industry_map = {}
        if len(ic) > 0:
            for _, r in ic.iterrows():
                industry_map[r["index_code"]] = r["industry_name"]
        # 2. 取全市场股票的行业（stock_basic 的 industry 字段）
        try:
            sb = pro.stock_basic(list_status="L", fields="ts_code,industry")
        except Exception as e:
            logger.warning(f"[TushareAdapter] stock_basic failed: {e}")
            return pd.DataFrame(), {}
        # 3. 合并：股票→行业名→行业代码（申万 L1）
        result = []
        for _, r in sb.iterrows():
            ind_name = r.get("industry", "")
            # 匹配申万行业代码
            ind_code = ""
            for k, v in industry_map.items():
                if v == ind_name:
                    ind_code = k
                    break
            if not ind_code and ind_name:
                ind_code = f"SW_{ind_name}"  # 无精确匹配时用名称生成代码
            result.append({
                "ts_code": r["ts_code"], "industry_code": ind_code or "UNKNOWN",
                "industry_name": ind_name or "未分类", "industry_level": "L1",
            })
        if not result:
            return pd.DataFrame(), {}
        df = pd.DataFrame(result)
        metadata = {"source": "tushare", "freq": "daily", "table": "sw_industry",
                    "code_format": "tushare_to_raw", "rows": len(df)}
        logger.info(f"[TushareAdapter] sw_industry fetched {len(df)} rows")
        return df, metadata


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import os
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        print("⚠ 跳过：未设置 TUSHARE_TOKEN 环境变量")
    else:
        adapter = TushareAdapter({"name": "tushare", "token": token})
        df, meta = adapter.fetch_table("stock_daily", "2026-07-07", "2026-07-10",
                                       codes=["600000.SH"])
        print(df.head())
        print("meta:", meta)
