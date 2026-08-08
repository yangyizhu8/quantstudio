"""QFQ fresh 采集（fresh_capture）—— 证据固化层（单源锁定 xtquant）

职责边界（硬性约束）：
- **只采集 + 计算证据 hash + 落 ``qfq_fresh_capture`` 元数据表**；本模块**绝不写价格表**
  （stock_daily / stock_minutes / etf_daily / etf_minutes）。
- 价格源**只允许 xtquant**（单源锁定）。真实 fetcher 惰性 import xtquant，模块加载
  不触发任何网络连接。测试通过可注入的 ``FakeFreshFetcher`` 完全脱离 xtquant 运行。
- 落库的 ``metadata_sha256`` 必须来自真实数据内容（daily+minute hash + 元数据），
  引擎会校验其为合法 64-hex，禁止随机生成。
- ``capture_id`` 确定性（同券同轮稳定），供去重 / 重放。

对外依赖（均为本地模块，导入不触发网络）：
- ``qfq_orchestrator_types``: ``capture_id_of`` / ``FreshCaptureRecord`` / ``QFQOrchestratorConfig``
- ``qfq_reanchor_schema``: ``qfq_fresh_capture`` 表列定义 + ``init_duckdb_schema``
- ``sources.xtquant_adapter``: ``TABLE_PERIOD``（daily='1d' / minute='1m' 映射）
- ``aligner``: ``to_ms_timestamp``（epoch-ms BIGINT，Asia/Shanghai）

输出的 ``fresh_daily`` / ``fresh_minute`` DataFrame 列口径与
``qfq_reanchor_engine.apply_reanchor_for_security`` 严格一致：
``code, time, open, high, low, close, open_front, high_front, low_front, close_front``
（time 为 epoch-ms BIGINT），分钟额外含 ``freq`` 列（值 '1min'）。raw 来自
dividend_type='none'，front 来自 dividend_type='front'。
"""
from __future__ import annotations

import abc
import hashlib
import json
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

import pandas as pd

from quantstudio.pipeline.qfq_orchestrator_types import (
    FreshCaptureRecord,
    QFQOrchestratorConfig,
    capture_id_of,
)
from quantstudio.pipeline.qfq_reanchor_schema import (
    DUCKDB_COLS,
    FRESH_CAPTURE_STATUS,
)
from quantstudio.pipeline.sources.xtquant_adapter import TABLE_PERIOD
from quantstudio.pipeline.aligner import to_ms_timestamp

logger = logging.getLogger(__name__)

# qfq_fresh_capture 列顺序（与 schema 单一真相源 DUCKDB_COLS 逐字对齐）
FRESH_CAPTURE_COLS = DUCKDB_COLS["qfq_fresh_capture"]

# apply_reanchor_for_security 期望的前复权四价列
_FRONT_PRICE_COLS = ["open_front", "high_front", "low_front", "close_front"]
_RAW_PRICE_COLS = ["open", "high", "low", "close"]


# ---------------------------------------------------------------------------
# 代码格式：裸码 (600000 / 510050) → xtquant 格式 (600000.SH / 510050.SH)
# ---------------------------------------------------------------------------
# 注意：``aligner.normalize_code`` 仅提供 identity / tushare_to_raw / baostock_to_raw，
# 无 raw_to_xtquant 方向；且 ``market_of_code`` 不能正确处理 ETF 前缀（5xxxx 误判 BJ）。
# 本模块**不修改其它文件**，故自带转换（与 xtquant 约定对齐：6/5→SH，0/3/1/2→SZ，
# 其余→BJ）。测试用 600000.SH / 510050.SH 均覆盖到。
def raw_to_xtquant(code: str) -> str:
    """裸 6 位码 → xtquant 格式（带市场后缀）。

    - 6xxxxx / 5xxxxx → SH（股票沪市 / 沪市 ETF 如 510050）
    - 0xxxxx / 3xxxxx / 1xxxxx / 2xxxxx → SZ（深市股票 / 深市 ETF 如 159919）
    - 其余（8xxxxx / 4xxxxx 北交所）→ BJ
    """
    c = str(code).strip()
    if c.startswith(("6", "5")):
        mkt = "SH"
    elif c.startswith(("0", "3", "1", "2")):
        mkt = "SZ"
    else:
        mkt = "BJ"
    return f"{c}.{mkt}"


# ---------------------------------------------------------------------------
# epoch-ms ↔ YYYYMMDD（Asia/Shanghai）工具
# ---------------------------------------------------------------------------
def _ms_to_yyyymmdd(ms: int) -> str:
    """epoch-ms BIGINT → 'YYYYMMDD' 字符串（Asia/Shanghai 自然日）。"""
    dt = pd.Timestamp(int(ms), unit="ms")
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    dt = dt.tz_convert("Asia/Shanghai")
    return dt.strftime("%Y%m%d")


def _now_ts() -> str:
    """DuckDB TIMESTAMP 可解析的本地时间字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def _sha256_hex(s: str) -> str:
    """对字符串内容做确定性 sha256（64-hex）。"""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class FreshCaptureDownloadError(Exception):
    """任务5.1：download_history_data 任一窗口失败 → 进入 retryable failure。

    禁止静默用旧缓存：上层 trigger 应进入 retry / backoff，而非把过期数据解释成新证据。
    """
    pass


def _split_windows(start_yyyymmdd: str, end_yyyymmdd: str, cap_days: int):
    """任务5.1：把 [start,end] 切成单窗不超过 cap_days 的闭区间天列表（含端点）。"""
    s = pd.Timestamp(start_yyyymmdd)
    e = pd.Timestamp(end_yyyymmdd)
    if e < s:
        return []
    windows = []
    cur = s
    while cur <= e:
        nxt = min(cur + pd.Timedelta(days=cap_days - 1), e)
        windows.append((cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")))
        cur = nxt + pd.Timedelta(days=1)
    return windows


# ---------------------------------------------------------------------------
# 抽象 fetcher（可注入；测试用 FakeFreshFetcher 完全脱离 xtquant）
# ---------------------------------------------------------------------------
class FreshFetcher(abc.ABC):
    """价格源抽象：给定 (asset_type, xt_code, period, 区间) 返回 none/front 两份 df。

    返回的每份 DataFrame：index = DatetimeIndex（bar 时间），列含
    open/high/low/close（至少四价，可多不可少；多余列将被 _to_fresh_frame 忽略）。
    none_df 来自 dividend_type='none'；front_df 来自 dividend_type='front'。
    """

    @abc.abstractmethod
    def fetch_none_front(
        self,
        asset_type: str,
        xt_code: str,
        period: str,
        start_yyyymmdd: str,
        end_yyyymmdd: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """返回 (none_df, front_df)，每个 df index=datetime，列 open/high/low/close。"""
        raise NotImplementedError


class XtquantFreshFetcher(FreshFetcher):
    """真实 xtquant fetcher（惰性连接，模块加载不触发网络）。

    仅作为单源锁定下的真实实现；生产路径由 ``FreshCapture.capture`` 注入。

    任务5.1：fetch 前必须先 download_history_data（长区间分窗），再 get_market_data_ex；
    任一窗口下载失败 → 抛 FreshCaptureDownloadError（禁止静默用旧缓存）。
    """

    # 单窗上限（可配置）：daily 默认 365 天/窗，1min 默认 30 天/窗
    DAILY_WINDOW_DAYS = 365
    MINUTE_WINDOW_DAYS = 30

    def __init__(self, adapter=None):
        # adapter 预留（可传入已构造的 XtquantAdapter 复用连接）；默认惰性 import xtquant。
        self._adapter = adapter
        self._xt = None
        self._connected = False
        self.download_trace = None  # 任务5.2：跨 daily/minute 两次 fetch 累积的下载轨迹

    def _ensure(self):
        """惰性 import + 连接 xtquant；首次真正取数才发生（模块加载不触发网络）。"""
        if self._xt is not None:
            return self._xt
        try:
            import xtquant.xtdata as xt  # 惰性：仅当实际取数时 import
        except ImportError as e:
            raise ImportError(
                "未安装 xtquant，无法使用 XtquantFreshFetcher。请确认 miniQMT 客户端已安装"
                f"且 xtquant 在 PYTHONPATH。错误: {e}") from e
        self._xt = xt
        return xt

    def _ensure_connected(self, xt) -> None:
        if self._connected:
            return
        try:
            xt.connect()
            self._connected = True
        except Exception as e:  # pragma: no cover - 依赖运行环境
            logger.warning(f"[XtquantFreshFetcher] 连接 miniQMT 失败（可能未启动 QMT）: {e}")
            raise

    @staticmethod
    def _extract(df: Optional[pd.DataFrame]) -> pd.DataFrame:
        """从 get_market_data_ex 返回的 {code: DataFrame} 取出该 code 的 OHLC 子表。"""
        if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0:
            return pd.DataFrame(columns=_RAW_PRICE_COLS)
        sub = df.copy()
        keep = [c for c in _RAW_PRICE_COLS if c in sub.columns]
        if not keep:
            return pd.DataFrame(columns=_RAW_PRICE_COLS)
        out = sub[keep]
        # index 必须为 DatetimeIndex（get_market_data_ex 默认以时间为 index）
        if not isinstance(out.index, pd.DatetimeIndex):
            try:
                out.index = pd.to_datetime(out.index)
            except Exception:
                return pd.DataFrame(columns=_RAW_PRICE_COLS)
        return out

    def _get(self, xt, xt_code: str, period: str, start: str, end: str,
             dividend_type: str) -> pd.DataFrame:
        data = xt.get_market_data_ex(
            stock_list=[xt_code],
            period=period,
            start_time=start,
            end_time=end,
            dividend_type=dividend_type,
        )
        if not data or xt_code not in data or len(data[xt_code]) == 0:
            return pd.DataFrame(columns=_RAW_PRICE_COLS)
        return self._extract(data[xt_code])

    def _download(self, xt, xt_code, period, start_yyyymmdd, end_yyyymmdd):
        """任务5.1：先 download_history_data（长区间分窗），再返回窗口状态列表。

        任一窗口下载失败 → 抛 FreshCaptureDownloadError（禁止静默用旧缓存）。
        """
        cap = self.DAILY_WINDOW_DAYS if period == "1d" else self.MINUTE_WINDOW_DAYS
        windows = _split_windows(start_yyyymmdd, end_yyyymmdd, cap)
        trace_windows = []
        failed = False
        for (ws, we) in windows:
            try:
                xt.download_history_data(xt_code, period, ws, we)
                trace_windows.append({"start": ws, "end": we, "status": "ok"})
            except Exception as e:  # 网络/接口异常 → retryable failure
                trace_windows.append(
                    {"start": ws, "end": we, "status": "failed", "error": str(e)})
                failed = True
        if failed:
            raise FreshCaptureDownloadError(
                f"download_history_data 失败（{xt_code} {period} "
                f"{start_yyyymmdd}~{end_yyyymmdd}）：{trace_windows}")
        return trace_windows

    def fetch_none_front(
        self,
        asset_type: str,
        xt_code: str,
        period: str,
        start_yyyymmdd: str,
        end_yyyymmdd: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        xt = self._ensure()
        self._ensure_connected(xt)
        # 任务5.1：先 download_history_data（分窗），失败则进入 retryable failure
        self.download_trace = self.download_trace or {}
        if "download_start" not in self.download_trace:
            self.download_trace["download_start"] = datetime.now().isoformat(timespec="seconds")
        windows = self._download(xt, xt_code, period, start_yyyymmdd, end_yyyymmdd)
        none_df = self._get(xt, xt_code, period, start_yyyymmdd, end_yyyymmdd, "none")
        front_df = self._get(xt, xt_code, period, start_yyyymmdd, end_yyyymmdd, "front")
        # 任务5.2：累积下载轨迹（capture() 落库）
        self.download_trace[period] = {
            "requested_range": [start_yyyymmdd, end_yyyymmdd],
            "window_list": windows,
            "window_status": [w["status"] for w in windows],
        }
        self.download_trace["download_finish"] = datetime.now().isoformat(timespec="seconds")
        return none_df, front_df


class FakeFreshFetcher(FreshFetcher):
    """测试用确定性 fetcher：按 (asset_type, xt_code, period) 返回预定 none/front。

    ``data`` 键支持三种粒度（从高到低匹配）：
    1. ``(asset_type, xt_code, period)`` 三元组
    2. ``(xt_code, period)`` 二元组
    3. ``xt_code`` 裸字符串
    任一命中即用其 (none_df, front_df)；都未命中返回 (empty, empty)。
    """

    def __init__(self, data: Dict):
        self._data = data or {}

    def fetch_none_front(
        self,
        asset_type: str,
        xt_code: str,
        period: str,
        start_yyyymmdd: str,
        end_yyyymmdd: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        for key in ((asset_type, xt_code, period), (xt_code, period), xt_code):
            if key in self._data:
                none_df, front_df = self._data[key]
                return self._coerce(none_df), self._coerce(front_df)
        return pd.DataFrame(columns=_RAW_PRICE_COLS), pd.DataFrame(columns=_RAW_PRICE_COLS)

    @staticmethod
    def _coerce(df) -> pd.DataFrame:
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=_RAW_PRICE_COLS)
        out = df.copy()
        if not isinstance(out.index, pd.DatetimeIndex):
            out.index = pd.to_datetime(out.index)
        return out


# ---------------------------------------------------------------------------
# none/front → 引擎契约 DataFrame（time 为 epoch-ms BIGINT, Asia/Shanghai）
# ---------------------------------------------------------------------------
def _to_ms(idx_val) -> int:
    ms = to_ms_timestamp(idx_val)
    if ms is None:
        raise ValueError(f"无法将 bar 时间转为 epoch-ms: {idx_val!r}")
    return int(ms)


def _to_fresh_frame(
    none_df: pd.DataFrame,
    front_df: pd.DataFrame,
    asset_type: str,
    code: str,
    freq: Optional[str] = None,
) -> pd.DataFrame:
    """合并 none+front 为引擎契约 DataFrame。

    列：code(常量), time(epoch-ms BIGINT, Asia/Shanghai), open/high/low/close(来自 none),
    open_front/high_front/low_front/close_front(来自 front，按时间 index 对齐)。
    若 ``freq`` 给定（如 '1min'）额外加 ``freq`` 列。

    - 按 time 升序；
    - 只保留四价（open/high/low/close）列都非空的行（缺原始价的 bar 对重锚无意义）。
    """
    none = none_df if (none_df is not None and len(none_df) > 0) else \
        pd.DataFrame(columns=_RAW_PRICE_COLS)
    front = front_df if (front_df is not None and len(front_df) > 0) else \
        pd.DataFrame(columns=_RAW_PRICE_COLS)

    times = [_to_ms(ts) for ts in none.index]

    out = pd.DataFrame({"code": code, "time": times})
    for col in _RAW_PRICE_COLS:
        out[col] = none[col].values if col in none.columns else float("nan")

    # front 按 none 的 index 对齐（缺则 NaN）
    for col in _RAW_PRICE_COLS:
        fcol = f"{col}_front"
        if col in front.columns:
            out[fcol] = front[col].reindex(none.index).values
        else:
            out[fcol] = float("nan")

    if freq is not None:
        out["freq"] = freq

    out = out.sort_values("time", kind="stable").reset_index(drop=True)

    # 只保留四价都非空
    keep_mask = out[_RAW_PRICE_COLS].notna().all(axis=1)
    out = out[keep_mask].reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# FreshCapture —— 采集 + 证据 hash + 落元数据表
# ---------------------------------------------------------------------------
class FreshCapture:
    """QFQ fresh 采集器（价格源锁定 xtquant/mcp；只落 qfq_fresh_capture 元数据表）。

    P2-4：放开到 mcp（MCP 作传输通道，上游权威 xtquant，由 orchestrator price_source
    声明）。默认生产配置仍用 xtquant（不受影响）。
    """

    def __init__(self, cfg: QFQOrchestratorConfig):
        # 单源锁定：配置价格源必须为 xtquant 或 mcp（fail-fast，与 orchestrator 校验一致）。
        _ps = getattr(cfg, "price_source", "xtquant")
        if _ps not in ("xtquant", "mcp"):
            raise ValueError(
                f"FreshCapture 价格源必须锁定 xtquant/mcp，收到 {_ps!r}")
        self.cfg = cfg

    # —— 周期映射（复用 adapter 的 TABLE_PERIOD 单一真相源）——
    @staticmethod
    def _period_for(table_base: str, freq: str) -> str:
        """取 daily/minute 对应的 xtquant period 字符串（如 '1d' / '1m'）。"""
        return TABLE_PERIOD[(table_base, freq)]

    def capture(
        self,
        conn,
        *,
        asset_type: str,
        code: str,
        run_id: str,
        daily_range_ms: Tuple[int, int],
        minute_range_ms: Tuple[int, int],
        fetcher: FreshFetcher,
        source: str = "xtquant",
        write: bool = True,
    ) -> Tuple[FreshCaptureRecord, pd.DataFrame, pd.DataFrame]:
        """采集单证券 fresh 日线+分钟线，计算证据 hash 并落 ``qfq_fresh_capture`` 表。

        Args:
            conn: DuckDB 连接（调用方负责事务/commit）。
            asset_type: 'STOCK' | 'ETF'。
            code: canonical 裸码（无市场后缀）。
            run_id: 本轮运行 id（参与 capture_id 确定性）。
            daily_range_ms: (start_ms, end_ms) 日线区间（epoch-ms）。
            minute_range_ms: (start_ms, end_ms) 分钟区间（epoch-ms）。
            fetcher: 可注入的 FreshFetcher（生产用 XtquantFreshFetcher/McpFreshFetcher，测试用 Fake）。
            source: 价格源标识（锁定 'xtquant' / 'mcp'）。
        Returns:
            (FreshCaptureRecord, fresh_daily, fresh_minute)
        """
        if source not in ("xtquant", "mcp"):
            raise ValueError(f"FreshCapture 价格源必须锁定 xtquant/mcp，收到 {source!r}")

        # 裸码 → xtquant 格式
        xt_code = raw_to_xtquant(code)

        # range_ms → YYYYMMDD（自然日，Asia/Shanghai）
        ds = _ms_to_yyyymmdd(daily_range_ms[0])
        de = _ms_to_yyyymmdd(daily_range_ms[1])
        ms = _ms_to_yyyymmdd(minute_range_ms[0])
        me = _ms_to_yyyymmdd(minute_range_ms[1])

        daily_period = self._period_for(f"{asset_type.lower()}_daily", "daily")
        minute_period = self._period_for(f"{asset_type.lower()}_minutes", "1min")

        # 拉取 none/front
        none_d, front_d = fetcher.fetch_none_front(
            asset_type, xt_code, daily_period, ds, de)
        none_m, front_m = fetcher.fetch_none_front(
            asset_type, xt_code, minute_period, ms, me)

        fresh_daily = _to_fresh_frame(none_d, front_d, asset_type, code)
        fresh_minute = _to_fresh_frame(none_m, front_m, asset_type, code, freq="1min")

        # —— 证据 hash（必须来自真实数据内容）——
        daily_csv = fresh_daily[
            ["time", "open", "high", "low", "close"] + _FRONT_PRICE_COLS
        ].to_csv(index=False)
        daily_sha256 = _sha256_hex(daily_csv)

        minute_csv = fresh_minute[
            ["time", "open", "high", "low", "close", "freq"] + _FRONT_PRICE_COLS
        ].to_csv(index=False)
        minute_sha256 = _sha256_hex(minute_csv)

        metadata_sha256 = _sha256_hex(
            f"{daily_sha256}|{minute_sha256}|{source}|{asset_type}|{code}|"
            f"{daily_range_ms}|{minute_range_ms}"
        )

        capture_id = capture_id_of(asset_type, code, run_id)

        # 任务5.2：download 轨迹（仅 XtquantFreshFetcher 累积；FakeFreshFetcher 无）
        _dl = getattr(fetcher, "download_trace", None)
        download_trace = None
        if _dl is not None:
            download_trace = json.dumps({
                "download_start": _dl.get("download_start"),
                "download_finish": _dl.get("download_finish"),
                "requested_range": _dl.get("requested_range"),
                "window_list": _dl.get("window_list"),
                "window_status": _dl.get("window_status"),
                "daily_content_sha": daily_sha256,
                "minute_content_sha": minute_sha256,
                "metadata_sha": metadata_sha256,
            }, ensure_ascii=False)

        # min/max time
        daily_min_time = int(fresh_daily["time"].min()) if len(fresh_daily) else None
        daily_max_time = int(fresh_daily["time"].max()) if len(fresh_daily) else None
        minute_min_time = int(fresh_minute["time"].min()) if len(fresh_minute) else None
        minute_max_time = int(fresh_minute["time"].max()) if len(fresh_minute) else None

        now = _now_ts()
        record = FreshCaptureRecord(
            capture_id=capture_id,
            asset_type=asset_type,
            code=code,
            source=source,
            daily_range_start=int(daily_range_ms[0]),
            daily_range_end=int(daily_range_ms[1]),
            minute_range_start=int(minute_range_ms[0]),
            minute_range_end=int(minute_range_ms[1]),
            daily_row_count=len(fresh_daily),
            minute_row_count=len(fresh_minute),
            daily_min_time=daily_min_time,
            daily_max_time=daily_max_time,
            minute_min_time=minute_min_time,
            minute_max_time=minute_max_time,
            daily_sha256=daily_sha256,
            minute_sha256=minute_sha256,
            metadata_sha256=metadata_sha256,
            download_trace=download_trace,
            status="captured",
            created_at=now,
            updated_at=now,
        )

        # write=False：仅计算证据并落内存 record，捕获落库交由引擎的不可变契约
        # （resolve_fresh_capture → NEW 时 write_fresh_capture plain INSERT）完成，
        # 避免在编排器侧用 INSERT OR REPLACE 覆盖已提交捕获。
        if write:
            self._insert(conn, record)
        return record, fresh_daily, fresh_minute

    def _insert(self, conn, record: FreshCaptureRecord) -> None:
        # 不可变写入：复用引擎侧 write_fresh_capture（plain INSERT），遇到
        # 已存在同 capture_id 直接抛 Duplicate 而非覆盖（INSERT OR REPLACE 已移除）。
        write_fresh_capture(conn, record)

    def get_capture(
        self, conn, capture_id: str
    ) -> Optional[FreshCaptureRecord]:
        """按 capture_id 回读元数据记录。"""
        cols = ", ".join(FRESH_CAPTURE_COLS)
        row = conn.execute(
            f"SELECT {cols} FROM qfq_fresh_capture WHERE capture_id = ?",
            [capture_id],
        ).fetchone()
        if row is None:
            return None
        return FreshCaptureRecord(**dict(zip(FRESH_CAPTURE_COLS, row)))

    def mark_applied(self, conn, capture_id: str, *,
                     source_generation: Optional[str] = None,
                     cutover_id: Optional[str] = None) -> None:
        """Mark a capture applied, scoped to generation when supplied."""
        sql = "UPDATE qfq_fresh_capture SET status=?, updated_at=? WHERE capture_id=?"
        params = ["applied", _now_ts(), capture_id]
        if source_generation is not None:
            sql += " AND source_generation=?"
            params.append(source_generation)
        if cutover_id is not None:
            sql += " AND cutover_id=?"
            params.append(cutover_id)
        conn.execute(sql, params)


# ---------------------------------------------------------------------------
# capture 不可变契约（§3.5 / 阶段2 R1）
# ---------------------------------------------------------------------------

class CaptureContentConflict(Exception):
    """capture_id 已存在但登记内容与重新采集不一致（§3.5 冲突检测）。

    触发后**禁止 INSERT OR REPLACE 覆盖原 capture 元数据**；调用方应 BLOCK。
    """


# resolve_fresh_capture 返回值
CAPTURE_ACTION_NEW = "new"                              # 全新 capture，可 INSERT
CAPTURE_ACTION_RECOLLECT_OK = "recollect_ok"            # 已存在、event 未提交、内容一致，可重新采集
CAPTURE_ACTION_ALREADY_COMMITTED = "already_committed"  # 已存在、event 已提交，不重复写价
CAPTURE_ACTION_RECOVER_APPLIED_NO_EVENT = "recover_applied_no_event"  # 已 applied 但无 event，须显式恢复


def _event_committed_for_capture(conn, capture_id: str, *,
                                 source_generation: str, cutover_id: str) -> bool:
    """Check committed evidence, tolerating a pre-2.1 legacy table only for legacy mode."""
    event_cols = {str(r[0]).lower() for r in conn.execute(
        "DESCRIBE qfq_reanchor_event").fetchall()}
    if {"source_generation", "cutover_id"}.issubset(event_cols):
        n = conn.execute(
            "SELECT COUNT(*) FROM qfq_reanchor_event WHERE status='committed' "
            "AND source_generation=? AND cutover_id=? "
            "AND json_extract_string(minute_ratio_plan, '$.model_audit.fresh_capture_id')=?",
            [source_generation, cutover_id, capture_id]).fetchone()[0]
    elif source_generation == "xtquant-legacy":
        n = conn.execute(
            "SELECT COUNT(*) FROM qfq_reanchor_event WHERE status='committed' "
            "AND json_extract_string(minute_ratio_plan, '$.model_audit.fresh_capture_id')=?",
            [capture_id]).fetchone()[0]
    else:
        raise CaptureContentConflict(
            "dynamic capture recovery requires qfq_reanchor_event generation columns")
    return int(n) > 0


def resolve_fresh_capture(conn, *, capture_id: str, asset_type: str, code: str,
                           source: str, daily_range_start, daily_range_end,
                           minute_range_start, minute_range_end,
                           daily_sha256: str, minute_sha256: str,
                           metadata_sha256: str,
                           source_generation: str = "xtquant-legacy",
                           cutover_id: str = "legacy-xtquant-pre-cutover") -> str:
    """§3.5 capture 不可变契约：先查冲突，再决定动作。

    返回动作：
    - CAPTURE_ACTION_NEW：capture_id 不存在，调用方可 INSERT（**禁止 INSERT OR REPLACE**）。
    - CAPTURE_ACTION_ALREADY_COMMITTED：已存在且 event 已 committed → 修复
      capture.status='applied'（若不是），不重复写价。
    - CAPTURE_ACTION_RECOVER_APPLIED_NO_EVENT：capture.status=='applied' 但无 committed
      event → 异常恢复路径，**不得静默跳过**。
    - CAPTURE_ACTION_RECOLLECT_OK：已存在、event 未提交、内容一致 → 允许重新采集继续。

    冲突（已登记 source/code/asset_type/区间/SHA 与重新采集不一致）→ 抛
    CaptureContentConflict（禁止覆盖原元数据）。
    """
    present = {str(r[0]).lower() for r in conn.execute(
        "DESCRIBE qfq_fresh_capture").fetchall()}
    base_cols = ["capture_id", "asset_type", "code", "source", "daily_range_start",
                 "daily_range_end", "minute_range_start", "minute_range_end",
                 "daily_sha256", "minute_sha256", "metadata_sha256", "status"]
    has_identity = {"source_generation", "cutover_id"}.issubset(present)
    if not has_identity and source_generation != "xtquant-legacy":
        raise CaptureContentConflict(
            "dynamic capture requires qfq_fresh_capture generation columns")
    select_cols = base_cols + (["source_generation", "cutover_id"] if has_identity else [])
    row = conn.execute(
        f"SELECT {', '.join(select_cols)} FROM qfq_fresh_capture WHERE capture_id=?",
        [capture_id]).fetchone()
    if row is None:
        return CAPTURE_ACTION_NEW
    got = dict(zip(select_cols, row))
    if not has_identity:
        got["source_generation"] = "xtquant-legacy"
        got["cutover_id"] = "legacy-xtquant-pre-cutover"
    expected = dict(
        asset_type=asset_type, code=code, source=source,
        daily_range_start=daily_range_start, daily_range_end=daily_range_end,
        minute_range_start=minute_range_start, minute_range_end=minute_range_end,
        daily_sha256=daily_sha256, minute_sha256=minute_sha256,
        metadata_sha256=metadata_sha256, source_generation=source_generation,
        cutover_id=cutover_id)
    mismatch = [k for k in expected if str(got[k]) != str(expected[k])]
    if mismatch:
        raise CaptureContentConflict(
            f"capture_id={capture_id} 内容冲突（字段 {mismatch}）：已登记与重新采集不一致，"
            f"禁止 INSERT OR REPLACE 覆盖原 capture 元数据")
    if _event_committed_for_capture(
            conn, capture_id, source_generation=source_generation, cutover_id=cutover_id):
        if got["status"] != "applied":
            conn.execute(
                "UPDATE qfq_fresh_capture SET status='applied', updated_at=? "
                "WHERE capture_id=?", [_now_ts(), capture_id])
        return CAPTURE_ACTION_ALREADY_COMMITTED
    if got["status"] == "applied":
        return CAPTURE_ACTION_RECOVER_APPLIED_NO_EVENT
    return CAPTURE_ACTION_RECOLLECT_OK


def write_fresh_capture(conn, rec: "FreshCaptureRecord") -> None:
    """Insert one immutable fresh-capture record using the frozen column contract.

    ``FRESH_CAPTURE_COLS`` is the single source of truth, so download_trace,
    min/max timestamps, generation fields, and all other record fields are
    persisted together. Duplicate capture_id values fail through the PK.
    """
    cols = list(FRESH_CAPTURE_COLS)
    now = _now_ts()
    # Compatibility: older internal callers may pass record-like objects that
    # omit newly frozen optional columns. Use FreshCaptureRecord defaults for
    # optional fields while keeping the three required identity fields strict.
    required = ("capture_id", "asset_type", "code")
    for col in required:
        if not hasattr(rec, col):
            raise AttributeError(f"fresh capture record missing required field: {col}")
    defaults = FreshCaptureRecord(
        capture_id=rec.capture_id, asset_type=rec.asset_type, code=rec.code)
    vals = []
    for col in cols:
        value = getattr(rec, col, getattr(defaults, col))
        if col in ("created_at", "updated_at") and value is None:
            value = now
        vals.append(value)
    placeholders = ", ".join(["?"] * len(cols))
    conn.execute(
        f"INSERT INTO qfq_fresh_capture ({', '.join(cols)}) VALUES ({placeholders})",
        vals)


# ---------------------------------------------------------------------------
# P2-4 McpFreshFetcher：MCP 全数据源价格拉取（替代 XtquantFreshFetcher）
# ---------------------------------------------------------------------------
_PERIOD_TO_MCP_FREQ = {"1d": "daily", "1m": "1min", "5m": "5min",
                       "15m": "15min", "30m": "30min", "60m": "60min"}


class McpFreshFetcher(FreshFetcher):
    """MCP 源 fresh fetcher（P2-4 §7.2-C）。

    用 MCP adapter 拉价格（含 adj_factor），计算 none（未复权）与 front（前复权）：
        front = raw × (adj_i / adj_latest)
    替代 xtquant 三段式。MCP 是传输通道，上游权威 xtquant（由 price_source=mcp 声明）。

    惰性构造 MCP adapter（首次 fetch 才建连接），模块加载不触发网络。
    """

    def __init__(self, mcp_cfg: Optional[Dict] = None, adapter=None, main_db: Optional[str] = None):
        self._mcp_cfg = mcp_cfg or {}
        self._adapter = adapter
        self._connected = False
        self._main_db = main_db

    def _ensure(self):
        if not self._connected:
            if self._adapter is None:
                from .sources import create_adapter
                mcp_cfg = dict(self._mcp_cfg or {})
                if self._main_db and "main_db" not in mcp_cfg:
                    mcp_cfg["main_db"] = self._main_db
                # WP7-E3 阶段 2A：bootstrap 链路开启 export 缓存 + 网格化批次。
                # export_cache=True → _fetch_export 走缓存层 + _export_batches 网格化，
                # 全证券共享批次边界，export 次数从 2181×N 降至 ~N。
                # daemon 日常采集不经过 McpFreshFetcher → 默认 export_cache=false 行为不变。
                mcp_cfg.setdefault("export_cache", True)
                self._adapter = create_adapter("mcp", mcp_cfg)
            self._connected = True

    @staticmethod
    def _to_period_table(asset_type: str, freq: str) -> Tuple[str, str]:
        """(asset_type, freq) → (MCP table, xt period)。"""
        if asset_type == "ETF":
            table = "etf_minutes" if freq != "daily" else "etf_daily"
            period = "1m" if freq != "daily" else "1d"
        else:
            table = "stock_minutes" if freq != "daily" else "stock_daily"
            period = "1m" if freq != "daily" else "1d"
        return table, period

    def fetch_none_front(
        self,
        asset_type: str,
        xt_code: str,
        period: str,
        start_yyyymmdd: str,
        end_yyyymmdd: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """返回 (none_df, front_df)；index=DatetimeIndex(bar 时间)，列 open/high/low/close。

        MCP 返回 raw（含 adj_factor），none=raw OHLC，front=raw×(adj_i/adj_latest)。
        """
        self._ensure()
        mcp_freq = _PERIOD_TO_MCP_FREQ.get(period, "daily")
        table, _ = self._to_period_table(asset_type, mcp_freq)
        raw_df, _ = self._adapter.fetch_table(
            table, start_yyyymmdd, end_yyyymmdd, freq=mcp_freq, codes=[xt_code])
        if len(raw_df) == 0:
            empty = pd.DataFrame(columns=["open", "high", "low", "close"])
            return empty, empty.copy()
        # Deduplicate by trade_date before indexing (MCP export may return
        # multiple rows for the same date due to shards or source corrections).
        # Keep the last row (most recent correction) after sorting.
        if "trade_date" in raw_df.columns:
            raw_df = raw_df.sort_values("trade_date").drop_duplicates(
                subset=["trade_date"], keep="last").reset_index(drop=True)
        elif "time" in raw_df.columns:
            raw_df = raw_df.sort_values("time").drop_duplicates(
                subset=["time"], keep="last").reset_index(drop=True)
        elif "trade_time" in raw_df.columns:
            raw_df = raw_df.sort_values("trade_time").drop_duplicates(
                subset=["trade_time"], keep="last").reset_index(drop=True)
        # 时间 index：MCP raw 可能含 time(ms int) / trade_date(YYYYMMDD 或 YYYY-MM-DD)
        # / trade_time(ms int, 分钟线专用)
        if "time" in raw_df.columns:
            idx = pd.to_datetime(raw_df["time"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
        elif "trade_time" in raw_df.columns:
            # MCP minutes returns trade_time as datetime64[ns] (naive, CST local).
            # tz_localize to Asia/Shanghai (not unit='ms' which would misinterpret
            # datetime values as epoch-ms integers, adding 8h offset on round-trip).
            tt = pd.to_datetime(raw_df["trade_time"])
            if tt.dt.tz is None:
                idx = tt.dt.tz_localize("Asia/Shanghai")
            else:
                idx = tt.dt.tz_convert("Asia/Shanghai")
        elif "trade_date" in raw_df.columns:
            idx = pd.to_datetime(raw_df["trade_date"].astype(str), format="mixed", dayfirst=False)
        else:
            idx = pd.RangeIndex(len(raw_df))
        # none_df
        # CRITICAL: use .to_numpy() to strip the Series index before constructing
        # the DataFrame with a DatetimeIndex.  Using pd.to_numeric(Series) keeps
        # the raw RangeIndex, which misaligns with idx → silent all-NaN.
        #
        # Tick-precision contract: round OHLC to the same decimals as aligner
        # (_round_fields: STOCK=2, ETF=3).  fetch_none_front bypasses the
        # aligner, so without this round the MCP QFQ-restore math noise
        # (1e-4 magnitude) would cause rebase precheck to BLOCK on every row.
        price_decimals = 3 if asset_type == "ETF" else 2
        none_df = pd.DataFrame({
            "open": pd.to_numeric(raw_df["open"], errors="coerce").to_numpy().round(price_decimals),
            "high": pd.to_numeric(raw_df["high"], errors="coerce").to_numpy().round(price_decimals),
            "low": pd.to_numeric(raw_df["low"], errors="coerce").to_numpy().round(price_decimals),
            "close": pd.to_numeric(raw_df["close"], errors="coerce").to_numpy().round(price_decimals),
        }, index=idx)
        # front_df：前复权 = raw × (adj_i / adj_latest)
        # CRITICAL: ratio must share the SAME index as none_df (idx, not RangeIndex
        # from raw_df).  Using raw_df's RangeIndex for ratio causes silent all-NaN
        # alignment when none_df has a DatetimeIndex (reasonix Bug 4 root cause).
        if "adj_factor" in raw_df.columns:
            adj = pd.to_numeric(raw_df["adj_factor"], errors="coerce")
            # Take the last valid (non-NaN, > 0) adj_factor as the anchor,
            # BEFORE fillna — otherwise NaN (suspended days) becomes 1.0 and
            # is mistaken for a valid anchor (reasonix edge-case fix).
            valid_adj = adj[adj.notna() & (adj > 0)]
            adj_latest = valid_adj.iloc[-1] if len(valid_adj) > 0 else 1.0
            adj = adj.fillna(1.0)
            ratio = pd.Series((adj.to_numpy() / adj_latest), index=idx)
            front_df = none_df.mul(ratio, axis=0)
        else:
            front_df = none_df.copy()
        return none_df, front_df
