"""AStockDataAdapter — 基于 mootdx 的免费数据源（无需 miniQMT/注册）

代码格式 6位裸码 688017；vol=股, amount=元；K线是不复权原始价。
mootdx 通过 TCP 7709 连接通达信服务器，不会被封禁（需中国IP）。

依赖：pip install mootdx
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .base import BaseSourceAdapter

logger = logging.getLogger(__name__)

# mootdx frequency 参数
FREQ_MAP = {
    "daily": 9,    # 日线
    "1min": 8,     # 1分钟
    "5min": 0,     # 5分钟
    "15min": 1,    # 15分钟
    "30min": 2,    # 30分钟
    "60min": 3,    # 60分钟
}


class AStockDataAdapter(BaseSourceAdapter):
    """a-stock-data（mootdx）数据源适配器（免费，无需注册）

    config 示例：
        {"name": "a_stock_data"}
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        self._init_client()

    def _init_client(self):
        try:
            from mootdx.quotes import Quotes
        except ImportError as e:
            raise ImportError(
                "未安装 mootdx。请 pip install mootdx\n"
                f"错误: {e}") from e
        # 使用标准行情服务器（bestip 自动探测）
        try:
            self._client = Quotes.factory(market="std")
            logger.info("[AStockData] mootdx 连接成功")
        except Exception as e:
            # fallback: 尝试 bestip
            try:
                from mootdx.quotes import Quotes as Q2
                self._client = Q2.factory(market="std")
                logger.info("[AStockData] mootdx 连接成功（fallback）")
            except Exception as e2:
                raise RuntimeError(f"mootdx 连接失败: {e} / {e2}")

    def supports_freq(self, freq: str) -> bool:
        return freq in FREQ_MAP

    def get_st_codes(self) -> set:
        """a-stock-data 获取 ST 股票列表（mootdx stocks() 返回名称，匹配含 ST 的）"""
        try:
            st_codes = set()
            for market in [0, 1]:  # 0=深圳, 1=上海
                df = self._client.stocks(market=market)
                if df is not None and len(df) > 0 and "name" in df.columns:
                    st_df = df[df["name"].str.contains("ST", case=False, na=False)]
                    if "code" in st_df.columns:
                        st_codes.update(st_df["code"].astype(str).tolist())
            logger.info(f"[AStockData] ST 股票: {len(st_codes)} 只")
            self._st_codes = st_codes
            return st_codes
        except Exception as e:
            logger.warning(f"[AStockData] get_st_codes 失败: {e}")
            return getattr(self, "_st_codes", set())

    def supports_task(self, table: str, freq: str) -> tuple:
        """Only index_daily is admitted to the canonical multi-source path.

        The adapter can technically fetch other market bars, but its corporate-
        action adjustment convention differs from the canonical convention, so
        those tables are intentionally rejected rather than silently mixed.
        """
        if (table, freq) == ("index_daily", "daily"):
            return (True, "")
        return (False, f"a_stock_data 未纳入统一口径的 {table}/{freq} 实现")

    def supports_qfq(self) -> bool:
        """是否支持复权价格"""
        return False

    def get_last_date(self, table: str, freq: str = "daily") -> Optional[str]:
        return None

    def get_all_stock_codes(self) -> List[str]:
        """获取全市场 A 股股票代码（6位裸码）"""
        try:
            # mootdx 获取股票列表
            sh_stocks = self._client.stocks(market=1)  # 1=上海
            sz_stocks = self._client.stocks(market=0)  # 0=深圳
            codes = []
            for df in [sh_stocks, sz_stocks]:
                if df is not None and len(df) > 0 and "code" in df.columns:
                    for code in df["code"]:
                        if re.match(r"^\d{6}$", str(code)):
                            codes.append(str(code))
            logger.info(f"[AStockData] 全市场 A 股: {len(codes)} 只")
            return codes
        except Exception as e:
            logger.error(f"[AStockData] get_all_stock_codes 失败: {e}")
            return []

    def get_etf_codes(self) -> List[str]:
        """获取全市场 ETF 代码（6位裸码）。
        mootdx stocks() 返回全部证券（含 ETF/LOF），按 ETF 代码段 + 名称双重过滤。
        沪市 ETF: 51x/56x/58x；深市 ETF: 15x/18x。"""
        try:
            codes = []
            for mkt in (1, 0):  # 1=上海 0=深圳
                df = self._client.stocks(market=mkt)
                if df is None or len(df) == 0 or "code" not in df.columns:
                    continue
                for _, row in df.iterrows():
                    code = str(row["code"])
                    name = str(row.get("name", ""))
                    # ETF 代码段 + 名称含 ETF（双重过滤，排除指数/LOF 误判）
                    is_etf_code = re.match(r"^(51|56|58|15|18)\d{4}$", code)
                    if is_etf_code and "ETF" in name.upper():
                        codes.append(code)
            logger.info(f"[AStockData] 全市场 ETF: {len(codes)} 只")
            return codes
        except Exception as e:
            logger.error(f"[AStockData] get_etf_codes 失败: {e}")
            return []

    def fetch_table(self, table: str, start: str, end: str,
                    freq: str = "daily",
                    codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        if table not in ("stock_daily", "stock_minutes", "index_daily", "etf_daily"):
            raise ValueError(f"a_stock_data 不支持 {table}")
        if freq not in FREQ_MAP:
            raise ValueError(f"a_stock_data 不支持频率 {freq}")

        # codes=None/ALL → 全市场（etf_daily 用 ETF 列表，否则用 A 股列表）
        if codes is None or codes == ["ALL"] or codes == "ALL":
            target_codes = self.get_etf_codes() if table == "etf_daily" else self.get_all_stock_codes()
        else:
            target_codes = codes
        mootdx_freq = FREQ_MAP[freq]

        # 计算需要拉取的 bar 数量（offset）
        # mootdx bars 是从最新往前数 offset 个
        import datetime as dt
        d_start = dt.datetime.strptime(start[:10], "%Y-%m-%d")
        d_end = dt.datetime.strptime(end[:10], "%Y-%m-%d")
        days = (d_end - d_start).days
        offset = max(days + 10, 10)  # 多取一些

        dfs = []
        for code in target_codes:
            try:
                market = self._code_to_market(code)
                df = self._retry_with_backoff(
                    self._client.bars, symbol=code, frequency=mootdx_freq, offset=offset)
                if df is not None and len(df) > 0:
                    # mootdx 返回列：open, close, high, low, vol/volume, amount, datetime
                    # （部分 mootdx 版本同时返回 vol 和 volume 同义列，保留 volume 删 vol 避免重复）
                    df = df.copy()
                    if "vol" in df.columns and "volume" in df.columns:
                        df = df.drop(columns=["vol"])
                    elif "vol" in df.columns and "volume" not in df.columns:
                        df = df.rename(columns={"vol": "volume"})
                    df["code"] = code
                    # 按日期范围过滤
                    if "datetime" in df.columns:
                        df["datetime"] = pd.to_datetime(df["datetime"])
                        df = df[(df["datetime"] >= start[:10]) & (df["datetime"] <= end[:10])]
                    # 计算复权价（mootdx 原始价 → front/back 复权价）
                    if table in ("stock_daily",) and freq == "daily":
                        df = self._apply_adjustment(df, code)
                    dfs.append(df)
            except Exception as e:
                logger.debug(f"[AStockData] {code} failed: {e}")

        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

        # 填充 isST（mootdx stocks() 名称含 ST）
        if table in ("stock_daily",) and "code" in df.columns:
            df = self.fill_isst(df, code_col="code")

        metadata = {
            "source": "a_stock_data", "freq": freq, "table": table,
            "code_format": "identity",  # 6位裸码，已是 khQuant 格式
            "date_format": "YYYY-MM-DD HH:MM",
            "units": {"vol": "股", "amount": "元"},
            "rows": len(df),
            "qfq_source": "none",
        }
        logger.info(f"[AStockData] {table}/{freq} fetched {len(df)} rows")
        return df, metadata

    def _apply_adjustment(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        """用 mootdx xdxr（除权除息）数据计算前/后复权价。
        mootdx 返回不复权原始价，这里计算 front/back 填入 open_front/close_front 等 8 列。

        复权因子公式（每次除权日调整）：
          送转股：factor *= (1 + songzhuangu/10)
          分红：  factor /= (1 - fenhong/(10*close))
          配股：  factor *= (1 + peigu/10) / (1 + peigu*peigujia/(10*close))
        """
        ohlc = ["open", "high", "low", "close"]
        try:
            xdxr = self._client.xdxr(symbol=code)
            if xdxr is None or len(xdxr) == 0:
                return df
            # 只取除权除息（category=1）
            dividends = xdxr[xdxr["category"] == 1].copy()
            if len(dividends) == 0:
                # 无除权记录 → front/back = 原始价
                for c in ohlc:
                    df[f"{c}_front"] = df[c]
                    df[f"{c}_back"] = df[c]
                return df

            # 构建除权日期
            dividends["ex_date"] = pd.to_datetime(
                dividends[["year", "month", "day"]].astype(str).agg("-".join, axis=1))
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.sort_values("datetime").reset_index(drop=True)

            # 只保留 K 线日期范围内的除权记录（跳过未来除权日）
            df_start = df["datetime"].min()
            df_end = df["datetime"].max()
            dividends = dividends[(dividends["ex_date"] >= df_start) & (dividends["ex_date"] <= df_end)]
            if len(dividends) == 0:
                # 无除权 → front/back = 原始价
                for c in ohlc:
                    df[f"{c}_front"] = df[c]
                    df[f"{c}_back"] = df[c]
                return df

            dividends = dividends.sort_values("ex_date")

            # 计算前复权因子（基准=最新日=1.0，往前逐个除权日累计缩小）
            front_factor = pd.Series(1.0, index=df.index)
            for _, div in dividends.iterrows():
                ex_date = div["ex_date"]
                before = df["datetime"] < ex_date
                if not before.any():
                    continue
                # 取除权日前一天收盘价（除权参考价）
                before_rows = df[before]
                close_on_ex = float(before_rows["close"].iloc[-1]) if len(before_rows) > 0 else 10.0
                if close_on_ex <= 0:
                    continue
                songzhuangu = float(div.get("songzhuangu", 0) or 0)
                fenhong = float(div.get("fenhong", 0) or 0)
                peigu = float(div.get("peigu", 0) or 0)
                peigujia = float(div.get("peigujia", 0) or 0)
                # 前复权比例 = (1 + 送转/10) / (1 - 分红/(10*close))
                ratio = 1.0 + songzhuangu / 10.0
                div_amt = fenhong / 10.0  # 每股分红（元）
                denom = 1.0 - div_amt / close_on_ex
                if abs(denom) > 1e-8:
                    ratio /= denom
                else:
                    ratio = 1.0 + songzhuangu / 10.0  # fallback
                if peigu > 0 and peigujia > 0:
                    peigudenom = 1.0 + peigu * peigujia / (10.0 * close_on_ex)
                    if abs(peigudenom) > 1e-8:
                        ratio = ratio * (1 + peigu / 10.0) / peigudenom
                # 防止 ratio 为 0 或负（异常数据）
                if ratio > 0.01:
                    front_factor[before] /= ratio

            for c in ohlc:
                df[f"{c}_front"] = df[c] * front_factor

            # 后复权（基准=最早日=1.0，往后累计放大）
            back_factor = pd.Series(1.0, index=df.index)
            for _, div in dividends.iterrows():
                ex_date = div["ex_date"]
                after = df["datetime"] >= ex_date
                if not after.any():
                    continue
                after_rows = df[after]
                close_on_ex = float(after_rows["close"].iloc[0]) if len(after_rows) > 0 else 10.0
                if close_on_ex <= 0:
                    continue
                songzhuangu = float(div.get("songzhuangu", 0) or 0)
                fenhong = float(div.get("fenhong", 0) or 0)
                ratio = 1.0 + songzhuangu / 10.0
                div_amt = fenhong / 10.0
                denom = 1.0 - div_amt / close_on_ex
                if abs(denom) > 1e-8:
                    ratio /= denom
                if ratio > 0.01:
                    back_factor[after] *= ratio

            for c in ohlc:
                df[f"{c}_back"] = df[c] * back_factor

            logger.debug(f"[AStockData] {code} 复权计算完成（{len(dividends)}次除权）")
            return df
        except Exception as e:
            logger.debug(f"[AStockData] {code} 复权计算失败: {e}")
            return df

    @staticmethod
    def _code_to_market(code: str) -> int:
        """裸码 → mootdx market（0=深圳, 1=上海）"""
        code = str(code).strip()
        if code.startswith(("5", "6", "9")):
            return 1  # 上海
        return 0  # 深圳

    def close(self):
        logger.info("[AStockData] 关闭")
        super().close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        adapter = AStockDataAdapter({"name": "a_stock_data"})
        df, meta = adapter.fetch_table("stock_daily", "2026-07-01", "2026-07-10",
                                       codes=["600000"])
        print(df.head() if len(df) else "（无数据）")
        print("meta:", meta)
        adapter.close()
    except ImportError as e:
        print(f"⚠ 跳过：{e}")
    except Exception as e:
        print(f"⚠ 失败：{e}")
