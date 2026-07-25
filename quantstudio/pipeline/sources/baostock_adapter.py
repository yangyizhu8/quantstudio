"""BaostockAdapter — Baostock 数据源（无 QMT 客户首选，免费）

代码格式 sh.600001 / sz.000001；volume/amount 单位为股/元；
含扩展指标 peTTM/pbMRQ/psTTM/isST/turn/pctChg。
日线：query_history_k_data_plus()；5分钟：freq="5"
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .base import BaseSourceAdapter

logger = logging.getLogger(__name__)

# baostock 字段（日线）—— 注意 isST（非 isdST），ST 标志
BS_DAILY_FIELDS = ("date,code,open,high,low,close,preclose,volume,amount,"
                   "adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,isST")
# baostock 字段（5分钟）
BS_MIN5_FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"


class BaostockAdapter(BaseSourceAdapter):
    """Baostock 数据源适配器（免费，无需 token）

    config 示例：
        {"name": "baostock", "user": "anonymous",
         "rate_limit": {"calls_per_min": 60, "wait_on_429": True}}
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        self.user = config.get("user", "anonymous")
        self._logged_in = False

    def _ensure_login(self):
        if self._logged_in:
            return
        try:
            import baostock as bs
        except ImportError as e:
            raise ImportError("未安装 baostock，请 pip install baostock") from e
        lg = bs.login(user_id=self.user, password="123456")
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        logger.info(f"[BaostockAdapter] 登录成功 (user={self.user})")
        self._logged_in = True
        self._client = bs

    def supports_freq(self, freq: str) -> bool:
        return freq in ("daily", "5min")

    def supports_task(self, table: str, freq: str) -> tuple:
        """baostock 支持矩阵：
        - 日线 daily ✅ / 5分钟 5min ✅ / 1分钟 1min ❌
        - 三大报表/除权/行业 ✅（query_balance/profit/cash_flow/dividend/industry_data）
        - tick ❌"""
        supported = {("stock_daily", "daily"), ("etf_daily", "daily"),
                     ("stock_minutes", "5min"), ("index_daily", "daily"),
                     ("balance_statement", "daily"), ("income_statement", "daily"),
                     ("cashflow_statement", "daily"), ("stock_dividend", "daily"),
                     ("sw_industry", "daily")}
        return ((True, "") if (table, freq) in supported else
                (False, f"baostock 未实现 {table}/{freq}"))

    def get_last_date(self, table: str, freq: str = "daily") -> Optional[str]:
        return None

    def get_all_stock_codes(self, day: Optional[str] = None) -> List[str]:
        """获取全市场 A 股股票代码（baostock 格式 sh.600000）。
        过滤掉指数(sh.000/sz.399)、基金(sh.5/sz.15)、可转债(sh.11/sz.12)、REITs 等。
        返回 baostock 格式（sh./sz. 前缀），供 fetch_table 内部使用。"""
        self._ensure_login()
        bs = self._client
        if day is None:
            # query_all_stock 很慢（~9s/次），用轻量 K 线探测最近有效交易日
            # baostock 数据有延迟，最新交易日可能 query_all_stock 返回 0
            from datetime import datetime, timedelta
            now = datetime.now()
            day = None
            for d in range(0, 15):
                test_day = (now - timedelta(days=d)).strftime("%Y-%m-%d")
                # 用 K 线快速探测（比 query_all_stock 快 100 倍）
                try:
                    rs_k = bs.query_history_k_data_plus(
                        "sh.600000", "date", start_date=test_day, end_date=test_day,
                        frequency="d", adjustflag="3")
                    if rs_k.next():  # 有数据 = 有效交易日
                        day = test_day
                        break
                except Exception:
                    continue
            if day is None:
                day = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            logger.info(f"[BaostockAdapter] get_all_stock_codes day={day} (K线探测)")
        rs = bs.query_all_stock(day=day)
        all_codes = []
        while rs.next():
            all_codes.append(rs.get_row_data())
        # 过滤 A 股：sh.6开头(主板/科创板) + sz.00开头(主板/中小板) + sz.30开头(创业板)
        a_share = []
        for row in all_codes:
            code = row[0]  # sh.600000 格式
            digits = code.split(".")[-1] if "." in code else code
            if re.match(r"^[63]\d{5}$", digits) or digits.startswith("00"):
                if code.startswith(("sh.000", "sz.399")):
                    continue
                a_share.append(code)
        logger.info(f"[BaostockAdapter] 全市场 A 股: {len(a_share)} 只 (day={day})")
        return a_share

    def get_etf_codes(self, day: Optional[str] = None) -> List[str]:
        """获取全市场 ETF 代码（baostock 格式 sh.510050 / sz.159919）。
        从 query_all_stock 结果按 ETF 代码段过滤（沪市 sh.51x/56x/58x，深市 sz.15x/18x）。"""
        self._ensure_login()
        bs = self._client
        # 复用 get_all_stock_codes 的 day 探测逻辑（若未传 day）
        if day is None:
            from datetime import datetime, timedelta
            now = datetime.now()
            for d in range(0, 15):
                test_day = (now - timedelta(days=d)).strftime("%Y-%m-%d")
                try:
                    rs_k = bs.query_history_k_data_plus(
                        "sh.600000", "date", start_date=test_day, end_date=test_day,
                        frequency="d", adjustflag="3")
                    if rs_k.next():
                        day = test_day
                        break
                except Exception:
                    continue
            if day is None:
                day = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        rs = bs.query_all_stock(day=day)
        all_codes = []
        while rs.next():
            all_codes.append(rs.get_row_data())
        # ETF 代码段过滤：sh.51x/56x/58x + sz.15x/18x
        etf_codes = []
        for row in all_codes:
            code = row[0]  # sh.510050 格式
            digits = code.split(".")[-1] if "." in code else code
            if re.match(r"^(51|56|58|15|18)\d{4}$", digits):
                etf_codes.append(code)
        logger.info(f"[BaostockAdapter] 全市场 ETF: {len(etf_codes)} 只 (day={day})")
        return etf_codes

    def fetch_table(self, table: str, start: str, end: str,
                    freq: str = "daily",
                    codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        self._ensure_login()
        bs = self._client

        # codes=None 或 "ALL" → 自动获取全市场列表（etf_daily 用 ETF 列表）
        if codes is None or codes == ["ALL"] or codes == "ALL":
            codes = self.get_etf_codes() if table == "etf_daily" else self.get_all_stock_codes()

        if table == "stock_daily":
            return self._fetch_daily(codes, start, end)
        if table == "etf_daily":
            return self._fetch_etf_daily(codes, start, end)
        if table == "stock_minutes" and freq == "5min":
            return self._fetch_min5(codes, start, end)
        if table == "index_daily":
            return self._fetch_index(codes, start, end)
        # 三大报表（baostock query_balance/profit/cash_flow_data）
        if table in ("balance_statement", "income_statement", "cashflow_statement"):
            return self._fetch_bs_report(table, codes, start, end)
        if table == "stock_dividend":
            return self._fetch_bs_dividend(codes, start, end)
        if table == "sw_industry":
            return self._fetch_bs_industry(codes, start, end)
        raise ValueError(f"baostock 不支持 {table}/{freq}")

    def _fetch_etf_daily(self, codes: Optional[List[str]], start: str, end: str) -> Tuple[pd.DataFrame, Dict]:
        """ETF 日线：单次 adjustflag=3 原始价（不做 3 次复权调用）。
        不提供 front/back 列 → aligner 用 fund_adj 因子统一计算复权（与其他源口径一致）。
        baostock 无 fund_adj 接口，故 baostock 源的 ETF 复权列留 NULL（不影响原始价/涨跌幅）。"""
        bs = self._client
        target_codes = [self._to_bs_code(c) for c in (codes or ["sh.510050"])]
        # ETF 日线字段（含 preclose/pctChg/turn）
        fields = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"
        dfs = []
        for code in target_codes:
            try:
                df = self._query_kline(code, start, end, adjustflag="3")  # 原始价
                if df is not None and len(df) > 0:
                    dfs.append(df)
            except Exception as e:
                logger.debug(f"[BaostockAdapter] ETF {code} failed: {e}")
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        # ETF 无 ST
        if len(df) > 0:
            df["isST"] = 0
        metadata = {"source": "baostock", "freq": "daily", "table": "etf_daily",
                    "code_format": "baostock_to_raw", "date_format": "YYYY-MM-DD",
                    "units": {"volume": "股", "amount": "元"}, "rows": len(df)}
        logger.info(f"[BaostockAdapter] etf_daily fetched {len(df)} rows")
        return df, metadata

    def _fetch_daily(self, codes: Optional[List[str]], start: str, end: str) -> Tuple[pd.DataFrame, Dict]:
        """日线：3 次 API 调用获取 raw(adjustflag=3) + front(2) + back(1) 三套复权价。
        统一 schema 要求 open/close=原始价，open_front/close_front=前复权，open_back/close_back=后复权。
        扩展指标(peTTM/pbMRQ/psTTM/isST/turn)从 adjustflag=3 的调用获取。"""
        bs = self._client
        target_codes = [self._to_bs_code(c) for c in (codes or ["sh.600000"])]

        # OHLC 字段名（用于 merge 重命名）
        ohlc = ["open", "high", "low", "close"]

        dfs = []
        total = len(target_codes)
        import time as _time
        t0 = _time.time()
        fail_count = 0
        # 单只调用时（daemon 逐只模式）不打进度日志，避免刷屏
        show_progress = total > 1
        for i, code in enumerate(target_codes):
            if show_progress and (i % 50 == 0 or i == total - 1):
                elapsed = _time.time() - t0
                speed = (i+1) / elapsed if elapsed > 0 else 0
                eta_sec = (total - i - 1) / speed if speed > 0 else 0
                logger.info(f"[BaostockAdapter] 进度: {i+1}/{total} ({(i+1)*100//total}%) "
                            f"已用 {elapsed:.0f}s, 预计剩余 {eta_sec:.0f}s ({speed:.1f} 只/s)")
            try:
                # adjustflag=3 不复权（原始价）→ 存入 open/high/low/close + 扩展指标
                raw_df = self._query_kline(code, start, end, adjustflag="3")
                # adjustflag=2 前复权 → 存入 *_front
                front_df = self._query_kline(code, start, end, adjustflag="2")
                # adjustflag=1 后复权 → 存入 *_back
                back_df = self._query_kline(code, start, end, adjustflag="1")

                if len(raw_df) == 0:
                    continue

                # 合并：raw 保留全部列，front/back 只取 OHLC 重命名后 merge
                merged = raw_df
                if len(front_df) > 0:
                    front_rename = {c: f"{c}_front" for c in ohlc}
                    front_df_r = front_df[["date"] + ohlc].rename(columns=front_rename)
                    merged = merged.merge(front_df_r, on="date", how="left")
                if len(back_df) > 0:
                    back_rename = {c: f"{c}_back" for c in ohlc}
                    back_df_r = back_df[["date"] + ohlc].rename(columns=back_rename)
                    merged = merged.merge(back_df_r, on="date", how="left")
                # 补全 4 个原始字段（raw_df 已含，确保列名）
                merged["code"] = code
                dfs.append(merged)
            except Exception as e:
                fail_count += 1
                logger.warning(f"[BaostockAdapter] {code} daily failed ({fail_count}th fail): {e}")

        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        # 数值列转换
        num_cols = ["open", "high", "low", "close", "preclose", "volume", "amount",
                    "turn", "pctChg", "peTTM", "pbMRQ", "psTTM",
                    "open_front", "high_front", "low_front", "close_front",
                    "open_back", "high_back", "low_back", "close_back"]
        for c in num_cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        metadata = {
            "source": "baostock", "freq": "daily", "table": "stock_daily",
            "code_format": "baostock_to_raw", "date_format": "YYYY-MM-DD",
            "units": {"volume": "股", "amount": "元", "pctChg": "%"},
            "extended_fields": ["peTTM", "pbMRQ", "psTTM", "isST", "turn"],
            "qfq_source": "baostock_3flags",  # 标记复权价来源（adapter 已提供）
            "rows": len(df),
        }
        if len(df) > 0:
            logger.debug(f"[BaostockAdapter] daily fetched {len(df)} rows (3 adjustflags merged)")
        return df, metadata

    def _query_kline(self, code: str, start: str, end: str, adjustflag: str) -> pd.DataFrame:
        """查询单只股票 K 线（指定 adjustflag）。
        大范围按年分段拉取（baostock 单次大范围查询极慢，按年快 10 倍）。"""
        bs = self._client
        # 判断是否需要分段（范围 > 1 年 → 按年分段）
        from datetime import datetime
        try:
            d_start = datetime.strptime(start[:10] if "-" in start else start[:4]+"-"+start[4:6]+"-"+start[6:8], "%Y-%m-%d")
            d_end = datetime.strptime(end[:10] if "-" in end else end[:4]+"-"+end[4:6]+"-"+end[6:8], "%Y-%m-%d")
        except Exception:
            d_start = datetime.strptime("2018-01-01", "%Y-%m-%d")
            d_end = datetime.now()

        year_span = d_end.year - d_start.year + 1
        if year_span <= 1:
            # 1 年内，单次查询
            return self._query_kline_single(code, start, end, adjustflag)

        # >1 年，按年分段
        all_rows = []
        for y in range(d_start.year, d_end.year + 1):
            seg_start = f"{y}-01-01" if y > d_start.year else start[:10]
            seg_end = f"{y}-12-31" if y < d_end.year else end[:10]
            df_seg = self._query_kline_single(code, seg_start, seg_end, adjustflag)
            if len(df_seg) > 0:
                all_rows.append(df_seg)
        return pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()

    def _query_kline_single(self, code: str, start: str, end: str, adjustflag: str) -> pd.DataFrame:
        """单次 K 线查询（不分段）"""
        bs = self._client
        rs = self._retry_with_backoff(
            bs.query_history_k_data_plus, code, BS_DAILY_FIELDS,
            start_date=self._fmt(start), end_date=self._fmt(end),
            frequency="d", adjustflag=adjustflag)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=BS_DAILY_FIELDS.split(",")) if rows else pd.DataFrame()

    def _fetch_min5(self, codes: Optional[List[str]], start: str, end: str) -> Tuple[pd.DataFrame, Dict]:
        bs = self._client
        target_codes = [self._to_bs_code(c) for c in (codes or ["sh.600000"])]
        dfs = []
        for code in target_codes:
            try:
                rs = self._retry_with_backoff(
                    bs.query_history_k_data_plus, code, BS_MIN5_FIELDS,
                    start_date=self._fmt(start), end_date=self._fmt(end),
                    frequency="5", adjustflag="2")
                while rs.next():
                    dfs.append(rs.get_row_data())
            except Exception as e:
                logger.warning(f"[BaostockAdapter] {code} 5min failed: {e}")
        df = pd.DataFrame(dfs, columns=BS_MIN5_FIELDS.split(",")) if dfs else pd.DataFrame()
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        metadata = {
            "source": "baostock", "freq": "5min", "table": "stock_minutes",
            "code_format": "baostock_to_raw", "date_format": "YYYYMMDDHHMMSS",
            "units": {"volume": "股", "amount": "元"},
            "rows": len(df),
        }
        logger.info(f"[BaostockAdapter] 5min fetched {len(df)} rows")
        return df, metadata

    def _fetch_index(self, codes: Optional[List[str]], start: str, end: str) -> Tuple[pd.DataFrame, Dict]:
        bs = self._client
        target_codes = [self._to_bs_code(c) for c in (codes or ["sh.000300"])]
        dfs = []
        for code in target_codes:
            try:
                rs = self._retry_with_backoff(
                    bs.query_history_k_data_plus, code,
                    "date,code,open,high,low,close,preclose,volume,amount,pctChg",
                    start_date=self._fmt(start), end_date=self._fmt(end),
                    frequency="d", adjustflag="3")
                while rs.next():
                    dfs.append(rs.get_row_data())
            except Exception as e:
                logger.warning(f"[BaostockAdapter] index {code} failed: {e}")
        df = pd.DataFrame(dfs, columns=["date","code","open","high","low","close",
                                        "preclose","volume","amount","pctChg"]) if dfs else pd.DataFrame()
        for c in ["open","high","low","close","preclose","volume","amount","pctChg"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        metadata = {"source": "baostock", "freq": "daily", "table": "index_daily",
                    "code_format": "baostock_to_raw", "date_format": "YYYY-MM-DD",
                    "units": {"volume": "股", "amount": "元"}, "rows": len(df)}
        return df, metadata

    def _fetch_bs_report(self, table: str, codes: Optional[List[str]],
                         start: str, end: str) -> Tuple[pd.DataFrame, Dict]:
        """拉三大报表（baostock query_balance/profit/cash_flow_data，按 year+quarter 查）。
        table → baostock API 映射：
          balance_statement  → query_balance_data
          income_statement   → query_profit_data
          cashflow_statement → query_cash_flow_data"""
        bs = self._client
        api_map = {"balance_statement": "query_balance_data",
                   "income_statement": "query_profit_data",
                   "cashflow_statement": "query_cash_flow_data"}
        api_name = api_map[table]
        target_codes = [self._to_bs_code(c) for c in (codes or ["sh.600000"])]
        # 从 start/end 生成 year+quarter 列表
        from datetime import datetime
        sy = int(start[:4]); ey = int(end[:4])
        quarters = [(y, q) for y in range(sy, ey + 1) for q in range(1, 5)]
        dfs = []
        for code in target_codes:
            for year, quarter in quarters:
                try:
                    self.rate_limiter.acquire()
                    rs = getattr(bs, api_name)(code=code, year=year, quarter=quarter)
                    rows = []
                    fields = []
                    while rs.next():
                        if not fields:
                            fields = rs.get_fields()
                        rows.append(rs.get_row_data())
                    if rows:
                        dfs.append(pd.DataFrame(rows, columns=fields))
                except Exception as e:
                    logger.debug(f"[BaostockAdapter] {api_name} {code} {year}Q{quarter} failed: {e}")
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        metadata = {"source": "baostock", "freq": "daily", "table": table,
                    "code_format": "baostock_to_raw", "date_format": "YYYY-MM-DD", "rows": len(df)}
        logger.info(f"[BaostockAdapter] {table} fetched {len(df)} rows ({len(target_codes)} codes)")
        return df, metadata

    def _fetch_bs_dividend(self, codes: Optional[List[str]], start: str, end: str) -> Tuple[pd.DataFrame, Dict]:
        """除权除息（baostock query_dividend_data，按 year 查 report 年度）。"""
        bs = self._client
        target_codes = [self._to_bs_code(c) for c in (codes or ["sh.600000"])]
        from datetime import datetime
        sy = int(start[:4]); ey = int(end[:4])
        dfs = []
        for code in target_codes:
            for year in range(sy, ey + 1):
                try:
                    self.rate_limiter.acquire()
                    rs = bs.query_dividend_data(code=code, year=year, yearType="report")
                    rows, fields = [], []
                    while rs.next():
                        if not fields: fields = rs.get_fields()
                        rows.append(rs.get_row_data())
                    if rows:
                        dfs.append(pd.DataFrame(rows, columns=fields))
                except Exception as e:
                    logger.debug(f"[BaostockAdapter] dividend {code} {year} failed: {e}")
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        metadata = {"source": "baostock", "freq": "daily", "table": "stock_dividend",
                    "code_format": "baostock_to_raw", "date_format": "YYYY-MM-DD", "rows": len(df)}
        logger.info(f"[BaostockAdapter] stock_dividend fetched {len(df)} rows")
        return df, metadata

    def _fetch_bs_industry(self, codes: Optional[List[str]], start: str, end: str) -> Tuple[pd.DataFrame, Dict]:
        """申万行业（baostock query_stock_industry，全市场一次返回）。"""
        bs = self._client
        try:
            rs = bs.query_stock_industry()
            rows, fields = [], []
            while rs.next():
                if not fields: fields = rs.get_fields()
                rows.append(rs.get_row_data())
            df = pd.DataFrame(rows, columns=fields) if rows else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[BaostockAdapter] query_stock_industry failed: {e}")
            df = pd.DataFrame()
        metadata = {"source": "baostock", "freq": "daily", "table": "sw_industry",
                    "code_format": "baostock_to_raw", "rows": len(df)}
        logger.info(f"[BaostockAdapter] sw_industry fetched {len(df)} rows")
        return df, metadata

    @staticmethod
    def _to_bs_code(code: str) -> str:
        """裸码 600000 → sh.600000 / 000001 → sz.000001；
        ETF: 510050 → sh.510050 / 159919 → sz.159919；
        兼容旧格式 600000.SH → sh.600000；已是 sh.600000 则原样返回"""
        code = str(code).strip()
        # 已是 baostock 格式
        if re.match(r"^(sh|sz|bj)\.\d{6}$", code.lower()):
            return code.lower()
        # tushare 格式 600000.SH
        m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", code, re.IGNORECASE)
        if m:
            return f"{m.group(2).lower()}.{m.group(1)}"
        # 裸码 → 按首位/前缀判市场
        if re.match(r"^\d{6}$", code):
            if code.startswith("6"):
                return f"sh.{code}"
            if code.startswith(("0", "3")):
                return f"sz.{code}"
            # ETF/基金：沪市 51x/56x/58x → sh，深市 15x/18x → sz
            if re.match(r"^(51|56|58)", code):
                return f"sh.{code}"
            if re.match(r"^(15|18)", code):
                return f"sz.{code}"
            return f"bj.{code}"
        return code

    @staticmethod
    def _fmt(s: str) -> str:
        """baostock 要求 YYYY-MM-DD 格式"""
        if not s:
            return ""
        s = str(s).strip()
        if "-" in s:
            return s[:10]
        if len(s) == 8:  # 20260707 → 2026-07-07
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return s

    def close(self):
        if self._logged_in:
            try:
                self._client.logout()
                logger.info("[BaostockAdapter] 登出")
            except Exception:
                pass
        super().close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    adapter = BaostockAdapter({"name": "baostock"})
    try:
        df, meta = adapter.fetch_table("stock_daily", "2026-07-07", "2026-07-10",
                                       codes=["600000.SH"])
        print(df.head() if len(df) else "（无数据）")
        print("meta:", meta)
    finally:
        adapter.close()
