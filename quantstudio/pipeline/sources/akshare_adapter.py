"""AkshareAdapter — Akshare 数据源（免费，东方财富后端）

中文字段名；代码 600000（裸码，已是 khQuant code 格式）；
成交量单位为手（需 ×100 转股 [khQuant 口径]）；成交额单位为元（与 khQuant 一致）。

日线：stock_zh_a_hist(period='daily', adjust='qfq'/'hfq'/None)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .base import BaseSourceAdapter

logger = logging.getLogger(__name__)


class AkshareAdapter(BaseSourceAdapter):
    """Akshare 数据源适配器（免费，无需 token）

    config 示例：
        {"name": "akshare", "rate_limit": {"calls_per_min": 30, "wait_on_429": True}}

    注意：akshare 成交量单位是"手"，khQuant volume 是"股"，FieldAligner 会 ×100 转换。
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        self._init_client()

    def _init_client(self):
        try:
            import akshare as ak
        except ImportError as e:
            raise ImportError("未安装 akshare，请 pip install akshare") from e
        self._client = ak
        logger.info("[AkshareAdapter] 初始化成功")

    def supports_freq(self, freq: str) -> bool:
        return freq in ("daily",)

    def get_st_codes(self) -> set:
        """akshare 获取 ST 股票列表（stock_zh_a_st_em 接口）"""
        try:
            ak = self._client
            st_df = ak.stock_zh_a_st_em()
            # code 列是 6 位裸码
            if st_df is not None and "代码" in st_df.columns:
                st_codes = set(st_df["代码"].astype(str).tolist())
                logger.info(f"[AkshareAdapter] ST 股票: {len(st_codes)} 只")
                self._st_codes = st_codes
                return st_codes
            return set()
        except Exception as e:
            logger.warning(f"[AkshareAdapter] get_st_codes 失败: {e}")
            return getattr(self, "_st_codes", set())

    def supports_task(self, table: str, freq: str) -> tuple:
        """akshare 支持日线 + 三大报表/除权/行业/指数（不支持 tick/分钟）"""
        daily_tables = {"stock_daily", "etf_daily", "index_daily", "balance_statement",
                        "income_statement", "cashflow_statement", "stock_dividend",
                        "sw_industry", "index_constituents", "stock_namechange",
                        "stock_delist", "stock_daily_valuation"}
        minute_freqs = {"1min", "5min", "15min", "30min", "60min"}
        ok = (freq == "daily" and table in daily_tables) or (
            table == "etf_minutes" and freq in minute_freqs)
        return ((True, "") if ok else (False, f"akshare 未实现 {table}/{freq}"))

    def get_last_date(self, table: str, freq: str = "daily") -> Optional[str]:
        return None

    @staticmethod
    def _merge_adjusted_ohlc(raw: pd.DataFrame, adjusted: pd.DataFrame,
                             suffix: str, date_col: str = "日期") -> pd.DataFrame:
        """按交易日期合并复权 OHLC，禁止按数组位置贴值。"""
        if adjusted is None or len(adjusted) == 0 or date_col not in adjusted.columns:
            return raw
        rename = {src: f"{dst}_{suffix}" for src, dst in (
            ("开盘", "open"), ("最高", "high"), ("最低", "low"), ("收盘", "close"))}
        cols = [date_col] + [c for c in rename if c in adjusted.columns]
        right = adjusted[cols].rename(columns=rename).drop_duplicates(date_col, keep="last")
        return raw.merge(right, on=date_col, how="left")

    def get_all_stock_codes(self) -> List[str]:
        """获取全市场 A 股代码（6位裸码），用 stock_zh_a_spot_em 实时行情。"""
        try:
            ak = self._client
            df = ak.stock_zh_a_spot_em()
            if df is not None and len(df) > 0 and "代码" in df.columns:
                codes = df["代码"].astype(str).tolist()
                logger.info(f"[AkshareAdapter] 全市场 A 股: {len(codes)} 只")
                return codes
        except Exception as e:
            logger.warning(f"[AkshareAdapter] get_all_stock_codes 失败: {e}")
        return []

    def get_etf_codes(self) -> List[str]:
        """获取全市场 ETF 代码（6位裸码）。
        用 fund_etf_spot_em 取实时行情里的代码列；接口失败时返回空（由 collector 传入 codes 兜底）。"""
        try:
            ak = self._client
            spot = ak.fund_etf_spot_em()
            if spot is not None and len(spot) > 0 and "代码" in spot.columns:
                codes = spot["代码"].astype(str).tolist()
                logger.info(f"[AkshareAdapter] 全市场 ETF: {len(codes)} 只")
                return codes
        except Exception as e:
            logger.warning(f"[AkshareAdapter] get_etf_codes 失败（fund_etf_spot_em）: {e}")
        return []

    def fetch_table(self, table: str, start: str, end: str,
                    freq: str = "daily",
                    codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        if table == "etf_daily":
            return self._fetch_etf_daily(start, end, codes)
        if table == "etf_minutes":
            return self._fetch_etf_minutes(start, end, codes, freq)
        if table == "index_daily":
            return self._fetch_ak_index(start, end, codes)
        if table in ("balance_statement", "income_statement", "cashflow_statement"):
            return self._fetch_ak_report(table, start, end, codes)
        if table == "stock_dividend":
            return self._fetch_ak_dividend(start, end, codes)
        if table == "sw_industry":
            return self._fetch_ak_industry(start, end, codes)
        if table == "index_constituents":
            return self._fetch_ak_index_constituents(start, end, codes)
        if table == "stock_namechange":
            return self._fetch_namechange()
        if table == "stock_delist":
            return self._fetch_delist()
        if table == "stock_daily_valuation":
            return self._fetch_valuation(start, end, codes)
        if table != "stock_daily":
            raise ValueError(f"akshare 不支持 {table}/{freq}")
        ak = self._client
        target_codes = codes or ["600000"]
        # 去掉后缀（akshare 只要 6 位数字）
        target_codes = [self._to_ak_code(c) for c in target_codes]

        dfs = []
        for code in target_codes:
            try:
                df = self._retry_with_backoff(
                    ak.stock_zh_a_hist, symbol=code, period="daily",
                    start_date=self._fmt(start), end_date=self._fmt(end),
                    adjust="")  # 原始价（不复权）
                if df is None or len(df) == 0:
                    continue
                df = df.copy()
                # 前复权价 → open_front 等列（passthrough，摆脱 tushare adj_factor 依赖）
                try:
                    df_q = self._retry_with_backoff(
                        ak.stock_zh_a_hist, symbol=code, period="daily",
                        start_date=self._fmt(start), end_date=self._fmt(end),
                        adjust="qfq")
                    if df_q is not None and len(df_q) > 0:
                        df = self._merge_adjusted_ohlc(df, df_q, "front")
                except Exception as e:
                    logger.debug(f"[AkshareAdapter] {code} qfq failed: {e}")
                # 后复权价 → open_back 等（尽力，失败留 NULL；schema back 列 required=false）
                try:
                    df_h = self._retry_with_backoff(
                        ak.stock_zh_a_hist, symbol=code, period="daily",
                        start_date=self._fmt(start), end_date=self._fmt(end),
                        adjust="hfq")
                    if df_h is not None and len(df_h) > 0:
                        df = self._merge_adjusted_ohlc(df, df_h, "back")
                except Exception as e:
                    logger.debug(f"[AkshareAdapter] {code} hfq failed: {e}")
                dfs.append(df)
            except Exception as e:
                logger.warning(f"[AkshareAdapter] {code} daily failed: {e}")

        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        # akshare 返回的列：日期/股票代码/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率
        metadata = {
            "source": "akshare", "freq": "daily", "table": "stock_daily",
            "code_format": "identity", "date_format": "YYYY-MM-DD",
            "units": {"成交量": "手", "成交额": "元", "涨跌幅": "%"},
            "rows": len(df),
        }
        logger.info(f"[AkshareAdapter] daily fetched {len(df)} rows")
        return df, metadata

    def _fetch_etf_daily(self, start: str, end: str,
                         codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """ETF 日线（akshare fund_etf_hist_em 接口）。
        返回中文字段名（日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率），
        无"股票代码"列 → 需补 code 列；isST 恒为 0。"""
        ak = self._client
        # codes=None/ALL → 全市场 ETF 列表
        if codes is None or codes == ["ALL"] or codes == "ALL":
            target_codes = self.get_etf_codes()
            if not target_codes:
                target_codes = ["510050", "510300", "510500", "159919", "159915"]  # 兜底常见 ETF
        else:
            target_codes = [self._to_ak_code(c) for c in codes]

        dfs = []
        for code in target_codes:
            try:
                df = self._retry_with_backoff(
                    ak.fund_etf_hist_em, symbol=code, period="daily",
                    start_date=self._fmt(start), end_date=self._fmt(end),
                    adjust="")  # 原始价（不复权）
                if df is None or len(df) == 0:
                    continue
                df = df.copy()
                df["股票代码"] = code  # fund_etf_hist_em 无 code 列，补上
                # 前复权价 → open_front 等列（passthrough，摆脱 tushare fund_adj 依赖）
                try:
                    df_q = self._retry_with_backoff(
                        ak.fund_etf_hist_em, symbol=code, period="daily",
                        start_date=self._fmt(start), end_date=self._fmt(end),
                        adjust="qfq")
                    if df_q is not None and len(df_q) > 0:
                        df = self._merge_adjusted_ohlc(df, df_q, "front")
                except Exception as e:
                    logger.debug(f"[AkshareAdapter] ETF {code} qfq failed: {e}")
                # 后复权价 → open_back 等（尽力）
                try:
                    df_h = self._retry_with_backoff(
                        ak.fund_etf_hist_em, symbol=code, period="daily",
                        start_date=self._fmt(start), end_date=self._fmt(end),
                        adjust="hfq")
                    if df_h is not None and len(df_h) > 0:
                        df = self._merge_adjusted_ohlc(df, df_h, "back")
                except Exception as e:
                    logger.debug(f"[AkshareAdapter] ETF {code} hfq failed: {e}")
                dfs.append(df)
            except Exception as e:
                logger.debug(f"[AkshareAdapter] ETF {code} failed: {e}")

        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        if len(df) > 0:
            df["isST"] = 0  # ETF 不存在 ST
        metadata = {
            "source": "akshare", "freq": "daily", "table": "etf_daily",
            "code_format": "identity", "date_format": "YYYY-MM-DD",
            "units": {"成交量": "手", "成交额": "元", "涨跌幅": "%"},
            "rows": len(df),
        }
        logger.info(f"[AkshareAdapter] etf_daily fetched {len(df)} rows ({len(target_codes)} codes)")
        return df, metadata

    def _fetch_etf_minutes(self, start: str, end: str,
                           codes: Optional[List[str]], freq: str) -> Tuple[pd.DataFrame, Dict]:
        """ETF 分钟线（akshare fund_etf_hist_min_em，逐 code 查）。
        freq 映射：1min→1, 5min→5, 15min→15, 30min→30, 60min→60。
        返回中文字段（时间/开盘/收盘/最高/最低/成交量/成交额），vol=手需×100。
        注意：period='1' 只返回近 5 天数据，不支持长历史。"""
        ak = self._client
        freq_map = {"1min": "1", "5min": "5", "15min": "15", "30min": "30", "60min": "60"}
        ak_freq = freq_map.get(freq, "1")
        if codes is None or codes == ["ALL"]:
            codes = self.get_etf_codes() or ["510050", "510300", "510500", "159919", "159915"]
        target_codes = [self._to_ak_code(c) for c in codes]
        dfs = []
        for code in target_codes:
            try:
                df = self._retry_with_backoff(
                    ak.fund_etf_hist_min_em, symbol=code, period=ak_freq,
                    start_date=self._fmt(start), end_date=self._fmt(end), adjust="")
                if df is not None and len(df) > 0:
                    df = df.copy()
                    df["股票代码"] = code
                    dfs.append(df)
            except Exception as e:
                logger.debug(f"[AkshareAdapter] ETF min {code} failed: {e}")
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        if len(df) > 0:
            df["isST"] = 0
        metadata = {"source": "akshare", "freq": freq, "table": "etf_minutes",
                    "code_format": "identity", "date_format": "YYYY-MM-DD HH:MM:SS",
                    "units": {"成交量": "手", "成交额": "元"}, "rows": len(df)}
        logger.info(f"[AkshareAdapter] etf_minutes fetched {len(df)} rows ({len(target_codes)} codes, {freq})")
        return df, metadata

    def _fetch_ak_report(self, table: str, start: str, end: str,
                         codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """三大报表（akshare stock_balance/profit/cashflow_sheet_by_report_em，逐 code 查全报告期）。"""
        ak = self._client
        api_map = {"balance_statement": "stock_balance_sheet_by_report_em",
                   "income_statement": "stock_profit_sheet_by_report_em",
                   "cashflow_statement": "stock_cash_flow_sheet_by_report_em"}
        api_name = api_map[table]
        if codes is None or codes == ["ALL"]:
            codes = self.get_all_stock_codes() or ["600000"]
        target_codes = [self._to_ak_code(c) for c in codes]
        dfs = []
        for i, code in enumerate(target_codes):
            if len(target_codes) > 50 and i % 200 == 0:
                logger.info(f"[AkshareAdapter] {table} 进度: {i}/{len(target_codes)}")
            try:
                df = self._retry_with_backoff(getattr(ak, api_name), symbol=code)
                if df is not None and len(df) > 0:
                    df = df.copy()
                    df["股票代码"] = code
                    dfs.append(df)
            except Exception as e:
                logger.debug(f"[AkshareAdapter] {api_name} {code} failed: {e}")
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        metadata = {"source": "akshare", "freq": "daily", "table": table,
                    "code_format": "identity", "date_format": "YYYYMMDD", "rows": len(df)}
        logger.info(f"[AkshareAdapter] {table} fetched {len(df)} rows ({len(target_codes)} codes)")
        return df, metadata

    def _fetch_ak_dividend(self, start: str, end: str,
                           codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """除权除息（akshare stock_history_dividend_detail，逐 code 查分红明细）。"""
        ak = self._client
        if codes is None or codes == ["ALL"]:
            codes = self.get_all_stock_codes() or ["600000"]
        target_codes = [self._to_ak_code(c) for c in codes]
        dfs = []
        for code in target_codes:
            try:
                df = self._retry_with_backoff(
                    ak.stock_history_dividend_detail, symbol=code, indicator="分红")
                if df is not None and len(df) > 0:
                    df = df.copy()
                    df["股票代码"] = code
                    dfs.append(df)
            except Exception as e:
                logger.debug(f"[AkshareAdapter] dividend {code} failed: {e}")
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        metadata = {"source": "akshare", "freq": "daily", "table": "stock_dividend",
                    "code_format": "identity", "rows": len(df)}
        logger.info(f"[AkshareAdapter] stock_dividend fetched {len(df)} rows")
        return df, metadata

    def _fetch_ak_industry(self, start: str, end: str,
                           codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """申万行业（akshare stock_board_industry_name_em 取板块列表 +
        stock_board_industry_cons_em 取各板块成分股，合并为 code→行业名）。"""
        ak = self._client
        try:
            boards = ak.stock_board_industry_name_em()
        except Exception as e:
            logger.warning(f"[AkshareAdapter] stock_board_industry_name_em failed: {e}")
            return pd.DataFrame(), {}
        if boards is None or len(boards) == 0:
            return pd.DataFrame(), {}
        dfs = []
        for _, board in boards.iterrows():
            board_name = board.get("板块名称", "")
            try:
                cons = ak.stock_board_industry_cons_em(symbol=board_name)
                if cons is not None and len(cons) > 0:
                    cons = cons.copy()
                    cons["板块名称"] = board_name
                    dfs.append(cons[["代码", "板块名称"]] if "代码" in cons.columns else pd.DataFrame())
            except Exception as e:
                logger.debug(f"[AkshareAdapter] industry cons {board_name} failed: {e}")
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        metadata = {"source": "akshare", "freq": "daily", "table": "sw_industry",
                    "code_format": "identity", "rows": len(df)}
        logger.info(f"[AkshareAdapter] sw_industry fetched {len(df)} rows")
        return df, metadata

    def _fetch_ak_index(self, start: str, end: str,
                        codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """指数日线（akshare stock_zh_index_daily_em，逐指数查）。
        codes 为指数代码如 sh000300/sz399001，或裸码 000300。"""
        ak = self._client
        if codes is None or codes == ["ALL"]:
            codes = ["sh000300", "sh000905", "sz399001", "sz399006"]
        dfs = []
        for code in codes:
            ak_code = str(code)
            # 裸码补前缀
            if ak_code.isdigit() and len(ak_code) == 6:
                ak_code = ("sh" if ak_code.startswith("000") else "sz") + ak_code
            try:
                df = self._retry_with_backoff(ak.stock_zh_index_daily_em, symbol=ak_code)
                if df is not None and len(df) > 0:
                    df = df.copy()
                    # 按日期过滤
                    if "日期" in df.columns:
                        df = df[(df["日期"] >= self._fmt(start)) & (df["日期"] <= self._fmt(end))]
                    dfs.append(df)
            except Exception as e:
                logger.debug(f"[AkshareAdapter] index {ak_code} failed: {e}")
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        metadata = {"source": "akshare", "freq": "daily", "table": "index_daily",
                    "code_format": "identity", "date_format": "YYYY-MM-DD", "rows": len(df)}
        logger.info(f"[AkshareAdapter] index_daily fetched {len(df)} rows")
        return df, metadata

    def _fetch_ak_index_constituents(self, start: str, end: str,
                                     codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """指数成分股（akshare index_stock_cons_csindex，逐指数查最新成分）。
        codes 为指数代码如 000300/000905/399101。"""
        ak = self._client
        if codes is None or codes == ["ALL"]:
            codes = ["000300", "000905", "000852", "000016", "399101"]
        dfs = []
        for idx_code in codes:
            bare = str(idx_code).split(".")[0]
            try:
                df = self._retry_with_backoff(ak.index_stock_cons_csindex, symbol=bare)
                if df is not None and len(df) > 0:
                    df = df.copy()
                    df["index_code"] = bare
                    dfs.append(df)
            except Exception as e:
                logger.debug(f"[AkshareAdapter] index_constituents {bare} failed: {e}")
        if not dfs:
            return pd.DataFrame(), {}
        result = pd.concat(dfs, ignore_index=True)
        # 统一列名：成分券代码→code, 日期→time
        # 注意：concat 前已手动加了 index_code 列，akshare 原始也有"指数代码"列，需先删原始列
        if "指数代码" in result.columns:
            result = result.drop(columns=["指数代码"])
        col_map = {"成分券代码": "code", "日期": "time"}
        for orig, std in col_map.items():
            if orig in result.columns:
                result = result.rename(columns={orig: std})
        # weight 列 akshare 不提供，留空
        if "weight" not in result.columns:
            result["weight"] = None
        metadata = {"source": "akshare", "freq": "daily", "table": "index_constituents",
                    "code_format": "identity", "date_format": "YYYY-MM-DD", "rows": len(result)}
        logger.info(f"[AkshareAdapter] index_constituents fetched {len(result)} rows ({len(codes)} indices)")
        return result, metadata

    def _fetch_namechange(self) -> Tuple[pd.DataFrame, Dict]:
        """拉取深市简称变更历史（带日期，全市场）。

        akshare 接口：stock_info_sz_change_name(symbol="简称变更")
        返回字段：变更日期 / 证券代码 / 证券简称 / 变更前简称 / 变更后简称

        本方法标准化为 stock_namechange 表口径：
          code (6 位裸码) / change_date (ms 时间戳) / name_before / name_after
          status_after (从 name_after 推导: ST/*ST/DELISTING_SORTING/NORMAL/UNKNOWN)

        ⚠️ 仅深市有对称接口（沪市 akshare 无带日期的简称变更全表）。
        沪市 ST 历史靠退市兜底（close<1 + circ_mv<5亿）覆盖。

        start/end/codes 参数忽略（akshare 一次性返回全表，本地按 PIT 过滤）。
        """
        ak = self._client
        try:
            df = self._retry_with_backoff(ak.stock_info_sz_change_name, symbol="简称变更")
        except Exception as e:
            logger.warning(f"[AkshareAdapter] stock_namechange 拉取失败: {e}")
            return pd.DataFrame(), {"source": "akshare", "table": "stock_namechange", "rows": 0}
        if df is None or len(df) == 0:
            logger.warning("[AkshareAdapter] stock_namechange 无数据")
            return pd.DataFrame(), {"source": "akshare", "table": "stock_namechange", "rows": 0}

        import pandas as pd
        df = df.copy()
        # 标准化列名（中文 → 标准字段）
        col_map = {
            "变更日期": "change_date_str",
            "证券代码": "code",
            "变更前简称": "name_before",
            "变更后简称": "name_after",
        }
        for orig, std in col_map.items():
            if orig in df.columns:
                df = df.rename(columns={orig: std})
        # code 补齐 6 位
        df["code"] = df["code"].astype(str).str.zfill(6)
        # change_date → ms 时间戳（当日 00:00 UTC，与 stock_daily.time 对齐口径）
        df["change_date"] = df["change_date_str"].apply(
            lambda s: int(pd.Timestamp(str(s)).timestamp() * 1000) if pd.notna(s) else None
        )
        # 推导 status_after（从 name_after 关键词）
        def _derive_status(name):
            if not isinstance(name, str):
                return "UNKNOWN"
            if "退" in name:
                return "DELISTING_SORTING"
            if "*ST" in name or name.startswith("*ST"):
                return "*ST"
            if "ST" in name:
                return "ST"
            return "NORMAL"
        df["status_after"] = df["name_after"].apply(_derive_status)
        # 清洗 name 中的空白字符
        for col in ("name_before", "name_after"):
            df[col] = df[col].astype(str).str.strip().str.replace(r"\x00+", "", regex=True).str.strip()
        df = df[["code", "change_date", "name_before", "name_after", "status_after"]]
        df = df.dropna(subset=["change_date"])
        metadata = {
            "source": "akshare", "freq": "daily", "table": "stock_namechange",
            "code_format": "identity", "date_format": "ms_timestamp",
            "rows": len(df),
        }
        logger.info(f"[AkshareAdapter] stock_namechange fetched {len(df)} rows（深市简称变更历史）")
        return df, metadata

    def _fetch_delist(self) -> Tuple[pd.DataFrame, Dict]:
        """拉取沪深退市公司名单（带终止上市日期）。

        akshare 接口：
          - 深市：stock_info_sz_delist(symbol="终止上市公司") → 证券代码/证券简称/上市日期/终止上市日期
          - 沪市：stock_info_sh_delist(symbol="全部") → 公司代码/公司简称/上市日期/暂停上市日期

        标准化字段：code / list_date (ms) / delist_date (ms) / market ('SZ'/'SH')
        其中沪市接口字段名为「暂停上市日期」，近似作为 delist_date（沪市退市数据精度较低）。

        start/end/codes 参数忽略（一次性返回全表）。
        """
        import pandas as pd
        ak = self._client
        pieces = []

        # 深市
        try:
            df_sz = self._retry_with_backoff(ak.stock_info_sz_delist, symbol="终止上市公司")
            if df_sz is not None and len(df_sz) > 0:
                df_sz = df_sz.copy()
                # 列名标准化
                rename_sz = {"证券代码": "code", "上市日期": "list_date_str", "终止上市日期": "delist_date_str"}
                df_sz = df_sz.rename(columns={c: rename_sz[c] for c in df_sz.columns if c in rename_sz})
                df_sz["market"] = "SZ"
                pieces.append(df_sz)
        except Exception as e:
            logger.warning(f"[AkshareAdapter] stock_delist 深市拉取失败: {e}")

        # 沪市
        try:
            df_sh = self._retry_with_backoff(ak.stock_info_sh_delist, symbol="全部")
            if df_sh is not None and len(df_sh) > 0:
                df_sh = df_sh.copy()
                rename_sh = {
                    "公司代码": "code", "上市日期": "list_date_str",
                    "暂停上市日期": "delist_date_str",  # 沪市接口字段名差异
                }
                df_sh = df_sh.rename(columns={c: rename_sh[c] for c in df_sh.columns if c in rename_sh})
                df_sh["market"] = "SH"
                pieces.append(df_sh)
        except Exception as e:
            logger.warning(f"[AkshareAdapter] stock_delist 沪市拉取失败: {e}")

        if not pieces:
            logger.warning("[AkshareAdapter] stock_delist 无数据（沪深均失败）")
            return pd.DataFrame(), {"source": "akshare", "table": "stock_delist", "rows": 0}

        df = pd.concat(pieces, ignore_index=True)
        df["code"] = df["code"].astype(str).str.zfill(6)
        # 日期 → ms 时间戳
        for col in ("list_date_str", "delist_date_str"):
            ms_col = col.replace("_str", "")
            df[ms_col] = df[col].apply(
                lambda s: int(pd.Timestamp(str(s)).timestamp() * 1000) if pd.notna(s) and str(s).strip() else None
            )
        df = df[["code", "list_date", "delist_date", "market"]]
        df = df.dropna(subset=["delist_date"])
        metadata = {
            "source": "akshare", "freq": "daily", "table": "stock_delist",
            "code_format": "identity", "date_format": "ms_timestamp",
            "rows": len(df),
        }
        logger.info(f"[AkshareAdapter] stock_delist fetched {len(df)} rows（沪深退市名单）")
        return df, metadata

    def _fetch_valuation(self, start: str, end: str,
                         codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """每日估值表（akshare stock_value_em，逐 code 查全历史每日市值）。

        返回字段：code / time(YYYY-MM-DD) / circ_mv(元) / total_mv(元) / pe_ttm / pb
        单位：stock_value_em 的 流通市值/总市值 已是元（非亿），直接入库，
              is_delisting_risk 的 5e8(5亿) 阈值有效。
        turnover_rate akshare 不提供，留 NULL（由 stock_daily.turn 兜底）。

        start/end 为 YYYY-MM-DD 字符串，用于按日期过滤（接口本身返回全历史）。
        """
        ak = self._client
        if codes is None or codes == ["ALL"] or codes == "ALL":
            target_codes = self.get_all_stock_codes() or ["600000"]
        else:
            target_codes = [self._to_ak_code(c) for c in codes]

        dfs = []
        for code in target_codes:
            try:
                df = self._retry_with_backoff(ak.stock_value_em, symbol=code)
                if df is None or len(df) == 0 or "数据日期" not in df.columns:
                    continue
                df = df.copy()
                _dts = pd.to_datetime(df["数据日期"])
                _mask = (_dts >= pd.Timestamp(str(start))) & (_dts <= pd.Timestamp(str(end)))
                df = df[_mask]
                if len(df) == 0:
                    continue
                # time 输出 YYYY-MM-DD 字符串，交给 aligner 的 time_to_ms 统一转上海午夜毫秒
                # （与 stock_daily 同口径，保证 is_delisting_risk 的 PIT JOIN 对齐）
                _date_str = df["数据日期"].apply(
                    lambda d: pd.Timestamp(str(d)).strftime("%Y-%m-%d"))
                out = pd.DataFrame({
                    "code": code,
                    "time": _date_str,
                    "circ_mv": df["流通市值"].astype(float),
                    "total_mv": df["总市值"].astype(float),
                    "pe_ttm": df["PE(TTM)"].astype(float),
                    "pb": df["市净率"].astype(float),
                })
                dfs.append(out)
            except Exception as e:
                logger.debug(f"[AkshareAdapter] valuation {code} failed: {e}")
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        metadata = {
            "source": "akshare", "freq": "daily", "table": "stock_daily_valuation",
            "code_format": "identity", "date_format": "YYYY-MM-DD", "rows": len(df),
        }
        logger.info(f"[AkshareAdapter] stock_daily_valuation fetched {len(df)} rows ({len(target_codes)} codes)")
        return df, metadata

    @staticmethod
    def _to_ak_code(code: str) -> str:
        """提取 6 位裸码（兼容 600000 / 600000.SH / sh.600000）"""
        code = str(code).strip()
        if "." in code:
            # 600000.SH → 600000 / sh.600000 → 600000
            parts = code.split(".")
            for p in parts:
                if len(p) == 6 and p.isdigit():
                    return p
        return code

    @staticmethod
    def _fmt(s: str) -> str:
        """akshare 要求 YYYYMMDD 格式"""
        return str(s).replace("-", "")[:8] if s else ""

    def close(self):
        logger.info("[AkshareAdapter] 关闭")
        super().close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    adapter = AkshareAdapter({"name": "akshare"})
    try:
        df, meta = adapter.fetch_table("stock_daily", "2026-07-07", "2026-07-10",
                                       codes=["600000.SH"])
        print(df.head() if len(df) else "（无数据 - 可能网络限流）")
        print("columns:", list(df.columns) if len(df) else "N/A")
        print("meta:", meta)
    finally:
        adapter.close()
