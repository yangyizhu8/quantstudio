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
        """tushare 支持矩阵：日线 + 分钟线 + 财务 + 指数，不支持 tick

        F4 治理：tushare 不再宣称 sw_industry（旧实现为 index_classify 名称
        匹配 + 伪 SW_行业名 代码，已删除）；正式能力为
        industry_classification / industry_membership（index_classify +
        index_member 正式成员接口）。"""
        supported = set(TUSHARE_API_MAP) | {
            ("stock_float_share", "daily"), ("stock_daily_valuation", "daily"),
            ("index_constituents", "daily"),
            ("industry_classification", "daily"), ("industry_membership", "daily")}
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
            # F4a：统一归一化 None/'ALL'/['ALL'] → None，防止 'ALL' 被当作
            # 具体代码传入下游 fetcher（如请求 ALL.SI）。
            codes = None
            if table in ("stock_float_share", "index_constituents", "etf_basic",
                         "industry_classification", "industry_membership",
                         "index_daily"):
                pass  # 这些表有自己的 codes 逻辑
            elif table == "etf_daily":
                codes = self.get_etf_codes()
            elif table == "etf_minutes":
                codes = self.get_etf_codes()
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

        # 指数日线（F5：普通指数 index_daily + 申万行业指数 sw_daily 统一路由）
        if table == "index_daily":
            return self._fetch_index_daily(start, end, codes)

        # 申万行业正式分类定义与成员历史（F4：index_classify + index_member）
        if table == "industry_classification":
            return self._fetch_industry_classification(start, end, codes)
        if table == "industry_membership":
            return self._fetch_industry_membership(start, end, codes)

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
            # fin_indicator 显式请求所需字段（包含 update_flag 用于 PIT 修订记录去重）
            fin_indicator_fields = ("ts_code,ann_date,end_date,update_flag,"
                                    "eps,dt_eps,bps,roe,netprofit_yoy,or_yoy,tr_yoy")
            api_kwargs = {"start_date": start_fmt, "end_date": end_fmt}
            for code in codes:
                kwargs = {"ts_code": code, **api_kwargs}
                if api_name == "fina_indicator":
                    kwargs["fields"] = fin_indicator_fields
                raw = self._retry_with_backoff(
                    self._call_api, api_name, **kwargs)
                dfs.append(raw)
            df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        else:
            df = self._retry_with_backoff(
                self._call_api, api_name, start_date=start_fmt, end_date=end_fmt)

        # [补丁1] stock_daily 需要 merge daily_basic 补 peTTM/pbMRQ/psTTM/turn
        # 统一 36 字段里的扩展指标在 tushare 分布在 daily_basic 表，不在 daily 里
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

    #: F5：sw_daily 正式接口必须返回的字段（capability probe）
    _SW_DAILY_REQUIRED = {"ts_code", "trade_date", "open", "high", "low",
                          "close", "pct_change", "vol", "amount"}

    def get_sw_index_codes(self) -> List[str]:
        """申万行业指数宇宙（tushare 格式 801010.SI）。

        范围 = SW2021 L1 分类（probe 自 index_classify，与 industry_classification
        表同一源端口径）；不写任何策略白名单。probe 失败返回空列表并记 error
        （调用方对含 SW 代码的请求 fail-closed）。成功结果按实例缓存
        （per-stock 模式下避免逐代码重复 probe）；失败不缓存。
        """
        cached = getattr(self, "_sw_index_codes_cache", None)
        if cached:
            return list(cached)
        import tushare as ts
        pro = ts.pro_api(self.token)
        try:
            self.rate_limiter.acquire()
            ic = pro.index_classify(level="L1", src="SW2021")
        except Exception as e:
            logger.error(f"[TushareAdapter] index_classify probe failed: {e}")
            return []
        if ic is None or len(ic) == 0 or "index_code" not in ic.columns:
            logger.error("[TushareAdapter] index_classify probe returned invalid payload")
            return []
        codes = [str(c) for c in ic["index_code"].tolist()]
        self._sw_index_codes_cache = list(codes)
        logger.info(f"[TushareAdapter] 申万行业指数宇宙: {len(codes)} 个")
        return codes

    #: SW2021 L1 官方行业数量（申万正式分类事实，非策略白名单）
    SW2021_L1_EXPECTED_COUNT = 31
    _SW_CODE_RE = __import__("re").compile(r"^\d{6}\.SI$")

    def _validate_sw_universe(self, codes: List[str]) -> None:
        """SW2021 L1 宇宙完整性门控（F5 审核返工）。

        异常/空/数量不等于 31/重复/格式非法 → RuntimeError（整个采集任务
        失败，水位不变），绝不静默退化为仅普通指数宇宙。
        """
        if not codes:
            raise RuntimeError(
                "SW2021 L1 universe probe failed/empty (fail-closed)")
        if len(codes) != self.SW2021_L1_EXPECTED_COUNT:
            raise RuntimeError(
                f"SW2021 L1 universe incomplete: {len(codes)} codes, "
                f"expected {self.SW2021_L1_EXPECTED_COUNT} (fail-closed)")
        if len(set(codes)) != len(codes):
            raise RuntimeError("SW2021 L1 universe contains duplicates (fail-closed)")
        bad = [c for c in codes if not self._SW_CODE_RE.match(str(c))]
        if bad:
            raise RuntimeError(
                f"SW2021 L1 universe bad format {bad[:5]} (fail-closed)")

    def get_index_daily_universe(self) -> List[str]:
        """正式动态指数宇宙（F5）：普通指数 + SW2021 L1 申万行业指数。

        daemon full/incremental/resident 三种模式的 index_daily 任务统一经此
        方法取全量宇宙。SW 宇宙先过完整性门控（31/格式/唯一），门控失败
        抛 RuntimeError → 整个任务失败且水位不变，绝不静默退化。
        """
        sw = self.get_sw_index_codes()
        self._validate_sw_universe(sw)
        return list(self.get_index_codes()) + list(sw)

    @staticmethod
    def _looks_like_sw_index(code: str) -> bool:
        text = str(code).strip().upper()
        if text.endswith(".SI"):
            return True
        bare = text.split(".")[0]
        return bare.startswith("801")

    def _fetch_index_daily(self, start: str, end: str,
                           codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """指数日线统一入口（F5）：普通指数 + 申万行业指数 → 同一 canonical schema。

        源端路由（§7.2）：普通指数 → index_daily 接口；申万行业指数
        （.SI 后缀或属 SW2021 L1 宇宙）→ sw_daily 正式接口。
        单位换算（§7.3）：sw_daily vol=万股/amount=万元 → 本方法换算为
        index_daily 接口单位（手/千元），下游 aligner 统一映射为 股/元，
        与现有 index_daily 完全一致。输出列与 index_daily 接口对齐：
        ts_code/trade_date/open/high/low/close/pct_chg/vol/amount。

        fail-closed：分类 probe 失败且请求含 SW 代码 → RuntimeError；
        sw_daily 字段漂移 → RuntimeError；单代码空数据 → 跳过（不伪造行）。
        """
        import tushare as ts
        pro = ts.pro_api(self.token)
        start_fmt = self._fmt_date(start)
        end_fmt = self._fmt_date(end)

        if codes:
            code_list = [str(c) for c in codes]
        else:
            code_list = list(self.get_index_codes()) + list(self.get_sw_index_codes())

        # SW 宇宙 probe（仅当请求可能含 SW 代码时才需要）
        sw_universe: set = set()
        if any(self._looks_like_sw_index(c) for c in code_list):
            sw_universe = {c.split(".")[0] for c in self.get_sw_index_codes()}
            if not sw_universe:
                raise RuntimeError(
                    "index_classify probe failed/empty: cannot safely route "
                    "SW industry index codes (fail-closed)")

        def _is_sw(code: str) -> bool:
            text = str(code).strip().upper()
            if text.endswith(".SI"):
                return True
            return text.split(".")[0] in sw_universe

        normal_frames, sw_frames = [], []
        for code in code_list:
            if not _is_sw(code):
                try:
                    self.rate_limiter.acquire()
                    raw = pro.index_daily(ts_code=code, start_date=start_fmt,
                                          end_date=end_fmt)
                except Exception as e:
                    logger.warning(f"[TushareAdapter] index_daily {code} failed: {e}")
                    continue
                if raw is not None and len(raw) > 0:
                    normal_frames.append(raw)
                continue
            bare = str(code).split(".")[0]
            ts_code = f"{bare}.SI"
            try:
                self.rate_limiter.acquire()
                raw = pro.sw_daily(ts_code=ts_code, start_date=start_fmt,
                                   end_date=end_fmt)
            except Exception as e:
                raise RuntimeError(f"sw_daily {ts_code} failed: {e}") from e
            if raw is None or len(raw) == 0:
                logger.warning(f"[TushareAdapter] sw_daily {ts_code} empty, skipped")
                continue
            missing = self._SW_DAILY_REQUIRED - set(raw.columns)
            if missing:
                raise RuntimeError(
                    f"sw_daily {ts_code} field drift {missing} (fail-closed)")
            raw = raw.rename(columns={"pct_change": "pct_chg"})
            # 单位换算：万股→手（×100），万元→千元（×10），与 index_daily 接口口径一致
            raw["vol"] = pd.to_numeric(raw["vol"], errors="coerce") * 100
            raw["amount"] = pd.to_numeric(raw["amount"], errors="coerce") * 10
            sw_frames.append(raw[["ts_code", "trade_date", "open", "high", "low",
                                  "close", "pct_chg", "vol", "amount"]])

        frames = normal_frames + sw_frames
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        metadata = {
            "source": "tushare", "freq": "daily", "table": "index_daily",
            "code_format": "tushare_to_raw", "date_format": "YYYYMMDD",
            "units": {"vol": "手", "amount": "千元", "pct_chg": "%"},
            "rows": len(df),
        }
        logger.info(f"[TushareAdapter] index_daily fetched {len(df)} rows "
                    f"({len(normal_frames)} normal + {len(sw_frames)} SW codes)")
        return df, metadata

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
        """拉取除权除息（dividend），按 ts_code 逐只查全历史。

        单股模式（len(codes)==1）：失败抛异常（供 per_stock process_one 捕获计数），
        无数据视为 successful-empty（不计数为失败）。
        批量模式（codes=None/ALL/多股）：逐只捕获异常，metadata 记录完整统计。
        metadata 始终包含: total_codes, successful_codes, empty_codes,
        failed_codes, failed_code_samples（最多 5 个）。
        """
        if codes is None or codes == ["ALL"] or codes == "ALL":
            codes = self.get_all_stock_codes()
            is_single = False
        else:
            is_single = (len(codes) == 1)

        dfs = []
        total = len(codes)
        success_count = 0
        empty_count = 0
        fail_count = 0
        failed_samples: list = []

        div_fields = ("ts_code,ex_date,record_date,ann_date,end_date,"
                      "cash_div_tax,cash_div,stk_div,stk_bo_rate,stk_co_rate,div_proc")
        for i, code in enumerate(codes):
            if total > 50 and i % 200 == 0:
                logger.info(f"[TushareAdapter] dividend 进度: {i}/{total} ({i*100//total}%)")
            try:
                df = self._retry_with_backoff(
                    self._call_api, "dividend", ts_code=code, fields=div_fields)
                if df is not None and len(df) > 0:
                    dfs.append(df)
                    success_count += 1
                else:
                    empty_count += 1
            except Exception as e:
                fail_count += 1
                if len(failed_samples) < 5:
                    failed_samples.append({"code": code, "error": str(e)[:200]})
                if is_single:
                    # 单股模式：构建 metadata 后重新抛出，供 per_stock process_one 捕获
                    logger.error(f"[TushareAdapter] dividend 单股 {code} 拉取失败: {e}")
                    raise

        metadata = {
            "source": "tushare", "freq": "daily", "table": "stock_dividend",
            "code_format": "tushare_to_raw", "date_format": "YYYYMMDD",
            "total_codes": total,
            "successful_codes": success_count,
            "empty_codes": empty_count,
            "failed_codes": fail_count,
            "failed_code_samples": failed_samples,
        }

        if not dfs:
            metadata["rows"] = 0
            level = logger.info if fail_count == 0 else logger.warning
            level("[TushareAdapter] stock_dividend fetched 0 rows "
                  f"(total={total}, success={success_count}, empty={empty_count}, fail={fail_count})")
            return pd.DataFrame(), metadata

        result = pd.concat(dfs, ignore_index=True)
        # 过滤：只保留已实施的分红记录
        if "div_proc" in result.columns:
            result = result[result["div_proc"] == "实施"]
        # 过滤无除权日的记录（预案/公告阶段，非实际除权）
        if "ex_date" in result.columns:
            result = result.dropna(subset=["ex_date"])
            result = result[result["ex_date"].astype(str).str.strip() != "None"]
            result = result.reset_index(drop=True)

        metadata["rows"] = len(result)
        logger.info(f"[TushareAdapter] dividend 过滤后保留 {len(result)} 行（已实施+有除权日）；"
                    f"codes: total={total}, success={success_count}, empty={empty_count}, fail={fail_count}")
        return result, metadata

    #: F4 数据源契约（§6.3）：正式申万接口必须返回的字段（adapter capability probe）。
    _SW_CLASSIFY_REQUIRED = {"index_code", "industry_name", "level", "parent_code", "src"}
    _SW_MEMBER_REQUIRED = {"index_code", "con_code", "in_date", "out_date"}

    @staticmethod
    def _yyyymmdd_to_ms(value) -> Optional[int]:
        """YYYYMMDD → Asia/Shanghai 当日 00:00 毫秒；空值 → None。"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        text = str(value).strip()
        if not text or text.lower() in ("none", "nat", "nan"):
            return None
        return int(pd.to_datetime(text, format="%Y%m%d").tz_localize(
            "Asia/Shanghai").timestamp() * 1000)

    def _fetch_industry_classification(self, start: str, end: str,
                                       codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """申万行业分类定义（index_classify L1/SW2021）→ industry_classification（F4）。

        行业列表只来自正式分类接口；capability probe 确认字段齐全，字段漂移或
        源端不可用时 fail-closed 返回空（不写入）。绝不使用 stock_basic.industry
        中文名猜测，绝不生成伪 SW_行业名 代码。
        effective_from=0 表示分类定义长期有效（index_classify 不提供生效日期）。
        """
        import tushare as ts
        pro = ts.pro_api(self.token)
        try:
            self.rate_limiter.acquire()
            ic = pro.index_classify(level="L1", src="SW2021")
        except Exception as e:
            logger.error(f"[TushareAdapter] index_classify failed (fail-closed): {e}")
            return pd.DataFrame(), {}
        if ic is None or len(ic) == 0:
            logger.error("[TushareAdapter] index_classify returned empty (fail-closed)")
            return pd.DataFrame(), {}
        missing = self._SW_CLASSIFY_REQUIRED - set(ic.columns)
        if missing:
            logger.error(f"[TushareAdapter] index_classify field drift {missing} (fail-closed)")
            return pd.DataFrame(), {}
        rows = []
        for _, r in ic.iterrows():
            parent = str(r.get("parent_code") or "").strip()
            rows.append({
                "classification_system": "SW",
                "classification_version": str(r["src"]),
                "industry_code": str(r["index_code"]).split(".")[0],
                "industry_name": str(r["industry_name"]),
                "industry_level": str(r["level"]),
                "parent_industry_code": parent if parent and parent != "0" else None,
                "effective_from": 0,
                "effective_to": None,
            })
        df = pd.DataFrame(rows)
        metadata = {"source": "tushare", "freq": "daily",
                    "table": "industry_classification",
                    "code_format": "identity", "rows": len(df)}
        logger.info(f"[TushareAdapter] industry_classification fetched {len(df)} rows")
        return df, metadata

    def _fetch_industry_membership(self, start: str, end: str,
                                   codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """申万行业成员历史（index_member）→ industry_membership（F4）。

        股票—行业关系只来自正式成员接口（con_code + in_date/out_date PIT 区间）；
        不得用当前快照回填历史。行业范围：codes 显式行业代码列表，或全部
        SW2021 L1（来自 index_classify）。all-or-nothing fail-closed：任一行业
        成员拉取失败或字段漂移 → 返回空，绝不写部分快照。
        """
        import tushare as ts
        pro = ts.pro_api(self.token)
        if codes:
            industry_codes = [str(c).split(".")[0] for c in codes]
        else:
            cls_df, _ = self._fetch_industry_classification(start, end, None)
            if cls_df.empty:
                logger.error("[TushareAdapter] no SW2021 L1 classification (fail-closed)")
                return pd.DataFrame(), {}
            industry_codes = cls_df["industry_code"].tolist()
        frames = []
        for ind in industry_codes:
            try:
                self.rate_limiter.acquire()
                m = pro.index_member(index_code=f"{ind}.SI")
            except Exception as e:
                logger.error(f"[TushareAdapter] index_member {ind} failed "
                             f"(fail-closed, all-or-nothing): {e}")
                return pd.DataFrame(), {}
            if m is None or len(m) == 0:
                logger.error(f"[TushareAdapter] index_member {ind} empty/None "
                             f"(fail-closed, all-or-nothing)")
                return pd.DataFrame(), {}
            missing = self._SW_MEMBER_REQUIRED - set(m.columns)
            if missing:
                logger.error(f"[TushareAdapter] index_member {ind} field drift "
                             f"{missing} (fail-closed)")
                return pd.DataFrame(), {}
            frames.append(m)
        if not frames:
            return pd.DataFrame(), {}
        raw = pd.concat(frames, ignore_index=True)
        df = pd.DataFrame({
            "classification_system": "SW",
            "classification_version": "SW2021",
            "industry_level": "L1",
            "industry_code": raw["index_code"].astype(str).str.split(".").str[0],
            "code": raw["con_code"].astype(str).str.split(".").str[0],
            "effective_from": raw["in_date"].map(self._yyyymmdd_to_ms),
            "effective_to": raw["out_date"].map(self._yyyymmdd_to_ms),
        })
        df = df.dropna(subset=["effective_from"])
        # F4b：区间重叠治理 transform（同一证券同一 system/version/level 每日唯一）
        from ..industry_membership_standardizer import resolve_membership_intervals
        df, repair_stats = resolve_membership_intervals(df)
        metadata = {"source": "tushare", "freq": "daily",
                    "table": "industry_membership",
                    "code_format": "identity", "rows": len(df),
                    "interval_repair": repair_stats}
        logger.info(f"[TushareAdapter] industry_membership fetched {len(df)} rows "
                    f"({len(industry_codes)} industries)")
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
