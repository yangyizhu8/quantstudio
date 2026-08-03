"""CalendarService —— QFQ 重锚子系统的交易日历 + 日内期望 bar 时刻服务（第一批基础设施）

职责（设计 v5.1 §2）：
1. **交易日历持久缓存**：trade_cal 持久化到 DuckDB ``trade_calendar`` 表（v5 §1.4），
   避免每次向 calendar_provider 重复查询；提供 is_trading_day / next_trading_day /
   prev_trading_day / get_trade_days。
2. **日内窗口**：**直接 import 并复用** ``intraday_windows.py`` 的
   ``MORNING_START / MORNING_END / AFTERNOON_START / AFTERNOON_END`` 与
   ``_day_windows_ms / iter_trading_days_in_range``，**不搬迁、不复制第二套常量**。

分层口径（用户 2026-07-26 已确认接受）：
- **存储层**：分钟 canonical 数据每个交易日 241 根 bar =
  09:30 集合竞价 bar + 连续竞价 [09:31, 11:30] ∪ [13:01, 15:00]（各 120 根）。
  本服务的 ``expected_minute_times`` / ``next_expected_time`` 按此**存储层 241 根**口径。
- **回测层**：``intraday_windows.py`` 的连续竞价 240 根窗口语义**保持不变**
  （其 MORNING_START = 09:31，不含 09:30）。本服务不修改 intraday_windows 的任何行为。

时间口径（实测确认）：
- 分钟 bar ``time`` = end-labeled epoch-ms（09:30 / 09:31 / … / 15:00）。
- 日线 bar ``time`` = 当日 00:00:00 Asia/Shanghai 的 epoch-ms
  （aligner.to_ms_timestamp(YYYYMMDD) 口径；实测 2026-07-24 → 1784822400000）。
- ``cal_date`` = 交易日 00:00:00 Asia/Shanghai 的 epoch-ms。

next_expected_time 规则（1min，含 auction bar；v5.1 §2.2）：
    prev = 09:30          → next = 同日 09:31
    prev ∈ [09:31, 11:29] → next = prev + 1min
    prev = 11:30          → next = 同日 13:01
    prev ∈ [13:01, 14:59] → next = prev + 1min
    prev = 15:00          → next = 下一交易日 09:30
    日线：next = 下一交易日的日线 time（00:00:00）。
"""
from __future__ import annotations

import datetime
import logging
import sqlite3  # noqa: F401  (保留：便于未来同库风格；本模块只用 DuckDB)
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from quantstudio._paths import db_path
# —— 直接复用 intraday_windows（禁止第二套常量）——
from quantstudio.backtest.providers.intraday_windows import (  # noqa: F401
    TZ,
    MORNING_START,
    MORNING_END,
    AFTERNOON_START,
    AFTERNOON_END,
    _day_windows_ms,
    iter_trading_days_in_range,
)
# —— 复用 trade_calendar DDL（唯一真相源，不建第二套）——
from quantstudio.pipeline.qfq_reanchor_schema import DDL_DUCKDB

logger = logging.getLogger(__name__)

# 存储层特有：09:30 集合竞价 bar（回测层 intraday_windows 不含，故在此单列）
AUCTION_BAR = (9, 30, 0)
# 日线 bar 标注时刻（实测：当日 00:00:00）
DAILY_BAR_TIME = (0, 0, 0)

# 自然日毫秒数（用于完整覆盖判定）
DAY_MS = 86_400_000

_TRADE_CALENDAR_DDL = DDL_DUCKDB["trade_calendar"]


# ---------------------------------------------------------------------------
# 自然日 / 覆盖判定工具
# ---------------------------------------------------------------------------

def _iter_natural_days(lo: int, hi: int):
    """yield 每个自然日 00:00（Asia/Shanghai epoch-ms），包含端点 [lo, hi]。"""
    d = lo
    while d <= hi:
        yield d
        d += DAY_MS


def _natural_day_count(lo: int, hi: int) -> int:
    """[lo, hi] 内的自然日个数（端点含）。hi < lo → 0（空区间视为已覆盖）。"""
    if hi < lo:
        return 0
    return (hi - lo) // DAY_MS + 1


def _range_fully_covered(conn, lo: int, hi: int) -> bool:
    """判定 [lo, hi] 的每一个自然日是否都已缓存进 trade_calendar。

    方法：区间内已缓存行数 == 自然日个数。不等即存在未缓存自然日（部分缓存），
    禁止当作完整日历返回（阻断 1 核心）。
    """
    if hi < lo:
        return True
    cnt = conn.execute(
        "SELECT COUNT(*) FROM trade_calendar WHERE cal_date BETWEEN ? AND ?",
        [lo, hi]).fetchone()[0]
    return int(cnt) == _natural_day_count(lo, hi)


# ---------------------------------------------------------------------------
# 纯时刻工具（epoch-ms ↔ 本地时刻），全部走 Asia/Shanghai
# ---------------------------------------------------------------------------

def _at(day_str: str, h: int, m: int, s: int = 0) -> int:
    """构造 day_str 当日 h:m:s（Asia/Shanghai）的 epoch-ms。"""
    ts = pd.Timestamp(f"{day_str} {h:02d}:{m:02d}:{s:02d}").tz_localize(TZ)
    return int(ts.value // 10**6)


def _parts(ms: int) -> Tuple[str, int, int, int]:
    """epoch-ms → (day_str, hour, minute, second)（Asia/Shanghai）。"""
    ts = pd.Timestamp(int(ms), unit="ms", tz=TZ)
    return ts.strftime("%Y-%m-%d"), ts.hour, ts.minute, ts.second


def _day_midnight_ms(ms: int) -> int:
    """epoch-ms → 当日 00:00:00（Asia/Shanghai）的 epoch-ms（日线 time / cal_date 口径）。"""
    day_str, _, _, _ = _parts(ms)
    return _at(day_str, 0, 0, 0)


def _norm_freq(freq: str) -> Tuple[str, int]:
    """归一化 freq → ("daily", 0) 或 ("minute", n)。

    存储层 canonical 分钟字面量为 "1min"；同时兼容回测 API 的 "1m"。
    日线兼容 "1d"/"1day"/"day"/"daily"/"d"。
    """
    f = str(freq).strip().lower()
    if f in ("1d", "1day", "day", "daily", "d"):
        return ("daily", 0)
    if f.endswith("min"):
        n = f[:-3]
        return ("minute", int(n) if n else 1)
    if f.endswith("m") and f[:-1].isdigit():
        return ("minute", int(f[:-1]))
    raise ValueError(f"未知 freq: {freq!r}")


# ---------------------------------------------------------------------------
# CalendarService
# ---------------------------------------------------------------------------

class CalendarService:
    """交易日历持久缓存 + 日内期望 bar 时刻服务。

    Args:
        main_db: DuckDB 主库路径（None → _paths.db_path()，即 quantstudio.db）。
        calendar_provider: 可选，提供 ``get_trade_days(start_str, end_str)``（返回
            datetime 列表）；用于 trade_calendar 冷启动 / 前向延伸填充。缺省仅读 DB。
    """

    def __init__(self, main_db: Optional[str | Path] = None, calendar_provider=None):
        self.main_db = Path(main_db) if main_db is not None else db_path()
        self.provider = calendar_provider
        self._provider_name = getattr(calendar_provider, "name", "provider") \
            if calendar_provider is not None else None

    # ---- DuckDB 连接（短连接；避免长期占用被 daemon 持有的主库）----
    def _connect(self):
        import duckdb
        conn = duckdb.connect(str(self.main_db))
        conn.execute(_TRADE_CALENDAR_DDL)  # 幂等确保表存在
        return conn

    # ---- trade_calendar 持久化（完整自然日：开市 + 闭市）----
    def persist_trade_days_on_conn(self, conn, days_ms: List[int],
                                   closed_ms: Optional[List[int]] = None,
                                   source: Optional[str] = None,
                                   updated_at: Optional[str] = None) -> int:
        """在给定 DuckDB 连接上 upsert 交易日历（**不 commit**，调用方决定事务边界）。

        days_ms：开市日 cal_date 列表（epoch-ms 当日 00:00）。closed_ms：可选闭市日。
        写入含 ``source`` / ``updated_at``（阻断 1 溯源）。
        返回写入行数。
        """
        conn.execute(_TRADE_CALENDAR_DDL)
        src = source or self._provider_name or "direct"
        ts = updated_at or datetime.datetime.now().isoformat(timespec="seconds")
        n = 0
        rows = [(int(d), True) for d in days_ms]
        if closed_ms:
            rows += [(int(d), False) for d in closed_ms]
        for cal_date, is_open in rows:
            conn.execute(
                "INSERT OR REPLACE INTO trade_calendar "
                "(cal_date, is_open, exchange, source, updated_at) "
                "VALUES (?, ?, 'SSE', ?, ?)",
                [cal_date, is_open, src, ts],
            )
            n += 1
        return n

    def _persist_range(self, conn, start_date: str, end_date: str) -> List[int]:
        """持久化 [start,end] 的**全部自然日**（开市 is_open=True，闭市 is_open=False）。

        返回开市日 cal_date（epoch-ms）升序列表。无 provider 时抛 LookupError。
        """
        lo = _at(start_date[:10], 0, 0, 0)
        hi = _at(end_date[:10], 0, 0, 0)
        natural = list(_iter_natural_days(lo, hi))
        if self.provider is None:
            raise LookupError("calendar provider 缺失，无法刷新区间")
        open_strs = set(iter_trading_days_in_range(start_date, end_date, self.provider))
        open_ms = {_at(d, 0, 0, 0) for d in open_strs}
        src = self._provider_name or "provider"
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        conn.execute(_TRADE_CALENDAR_DDL)
        for d in natural:
            conn.execute(
                "INSERT OR REPLACE INTO trade_calendar "
                "(cal_date, is_open, exchange, source, updated_at) "
                "VALUES (?, ?, 'SSE', ?, ?)",
                [d, d in open_ms, src, ts])
        return sorted(open_ms)

    def refresh_calendar(self, start_date: str, end_date: str) -> List[int]:
        """向 provider 查询 [start,end] 并持久化**完整自然日**（开市+闭市）到 trade_calendar
        （自开连接 + commit）。返回开市日 cal_date（epoch-ms）升序列表。无 provider 抛 LookupError。
        """
        if self.provider is None:
            raise LookupError("refresh_calendar 需要 calendar_provider")
        conn = self._connect()
        try:
            open_ms = self._persist_range(conn, start_date, end_date)
            conn.commit()
        finally:
            conn.close()
        logger.info(f"[calendar] refresh {start_date}~{end_date}: "
                    f"{len(open_ms)} 交易日已持久化（含闭市日）")
        return open_ms

    def _refresh_missing(self, lo: int, hi: int) -> None:
        """刷新 [lo, hi] 自然日区间（含开市+闭市），自开连接 + commit。"""
        start = pd.Timestamp(lo, unit="ms", tz=TZ).strftime("%Y-%m-%d")
        end = pd.Timestamp(hi, unit="ms", tz=TZ).strftime("%Y-%m-%d")
        self.refresh_calendar(start, end)

    def _extend_calendar(self, cal_ms: int, direction: str) -> None:
        """前向/后向延伸填充（无 candidate 时的兜底）：刷新 [cal±1d, cal±20d] 区间。"""
        d0 = pd.Timestamp(cal_ms, unit="ms", tz=TZ)
        if direction == "next":
            start = (d0 + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            end = (d0 + pd.Timedelta(days=20)).strftime("%Y-%m-%d")
        else:
            start = (d0 - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
            end = (d0 - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        self.refresh_calendar(start, end)

    def get_trade_days(self, start_date: str, end_date: str) -> List[int]:
        """返回 [start,end] 区间内开市日 cal_date（epoch-ms）升序列表。

        阻断 1 修复：必须先**证明整个自然日区间完整缓存**：
        - 完整 → 返回 is_open 日；
        - 部分缺失 + 有 provider → 刷新缺失范围（完整自然日）后再查；
        - 部分缺失 + 无 provider → 抛 LookupError（禁止返回部分结果冒充完整日历）。
        """
        lo = _at(start_date[:10], 0, 0, 0)
        hi = _at(end_date[:10], 0, 0, 0)
        conn = self._connect()
        try:
            covered = _range_fully_covered(conn, lo, hi)
        finally:
            conn.close()
        if not covered:
            if self.provider is None:
                raise LookupError(
                    f"trade_calendar [{start_date},{end_date}] 未完整缓存且无 provider")
            self._refresh_missing(lo, hi)  # 持久化完整自然日（开市+闭市）
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT cal_date FROM trade_calendar "
                "WHERE cal_date BETWEEN ? AND ? AND is_open ORDER BY cal_date",
                [lo, hi],
            ).fetchall()
        finally:
            conn.close()
        return [int(r[0]) for r in rows]

    # ---- 交易日判定 / 步进 ----
    def is_trading_day(self, date_ms: int) -> bool:
        """给定任意时刻，判断其所在自然日是否开市（依据 trade_calendar）。

        阻断 1 修复：未知（未缓存）日期**不得静默当闭市**——
        有明确记录 → 返回 True/False；无记录 + 有 provider → 刷新该日再判；
        无记录 + 无 provider → 抛 LookupError。
        """
        cal = _day_midnight_ms(date_ms)
        conn = self._connect()
        try:
            r = conn.execute(
                "SELECT is_open FROM trade_calendar WHERE cal_date = ?", [cal]
            ).fetchone()
        finally:
            conn.close()
        if r is not None:
            return bool(r[0])
        if self.provider is None:
            raise LookupError(f"trade_calendar 未缓存 {cal}（未知日期，无 provider 不可判定）")
        self._refresh_missing(cal, cal)  # 刷新该单日（开市/闭市均可）
        conn = self._connect()
        try:
            r = conn.execute(
                "SELECT is_open FROM trade_calendar WHERE cal_date = ?", [cal]
            ).fetchone()
        finally:
            conn.close()
        # 防御（非阻断补充）：正常 _persist_range 应始终写入该自然日；
        # 若刷新后仍无记录（provider/持久化异常），应显式失败，不得静默当闭市。
        if r is None:
            raise LookupError(
                f"trade_calendar 刷新后仍无 {cal} 记录（provider/持久化异常），"
                f"未知日期不得静默当闭市")
        return bool(r[0])

    def next_trading_day(self, date_ms: int) -> int:
        """返回严格晚于 date_ms 所在日的下一个开市日 cal_date（epoch-ms 00:00）。"""
        return self._step_trading_day(_day_midnight_ms(date_ms), "next")

    def prev_trading_day(self, date_ms: int) -> int:
        """返回严格早于 date_ms 所在日的上一个开市日 cal_date（epoch-ms 00:00）。"""
        return self._step_trading_day(_day_midnight_ms(date_ms), "prev")

    def _step_trading_day(self, cal_ms: int, direction: str) -> int:
        """next/prev 统一步进：候选必须在**当前日与候选之间日历完整**时才是真正相邻交易日。

        阻断 1 修复：不能仅执行 ``SELECT min(cal_date) WHERE cal_date > ?`` 就把远处一条
        缓存当作相邻交易日。若候选与当前日之间存在未缓存自然日：
        - 有 provider → 刷新该区间后重新查询（可能暴露更早的开市日）；
        - 无 provider → 抛 LookupError。
        """
        MAX_ATTEMPTS = 64
        for _ in range(MAX_ATTEMPTS):
            candidate = self._query_adjacent(cal_ms, direction)
            if candidate is None:
                if self.provider is not None:
                    self._extend_calendar(cal_ms, direction)
                    continue
                raise LookupError(
                    f"trade_calendar 无 {cal_ms} 的{direction}开市日（且无 provider 可延伸）")
            lo = cal_ms + DAY_MS if direction == "next" else candidate
            hi = candidate if direction == "next" else cal_ms - DAY_MS
            conn = self._connect()
            try:
                ok = _range_fully_covered(conn, lo, hi)
            finally:
                conn.close()
            if ok:
                return candidate
            if self.provider is not None:
                self._refresh_missing(lo, hi)  # 填充缺失 → 下一轮重查（可能暴露更早开市日）
                continue
            raise LookupError(
                f"trade_calendar 区间 [{lo},{hi}] 未完整缓存且无 provider，"
                f"无法证明 {candidate} 为相邻{direction}交易日")
        raise LookupError("交易日历延伸超出上限（provider 持续返回空）")

    def _query_adjacent(self, cal_ms: int, direction: str) -> Optional[int]:
        conn = self._connect()
        try:
            if direction == "next":
                r = conn.execute(
                    "SELECT min(cal_date) FROM trade_calendar "
                    "WHERE cal_date > ? AND is_open", [cal_ms]).fetchone()
            else:
                r = conn.execute(
                    "SELECT max(cal_date) FROM trade_calendar "
                    "WHERE cal_date < ? AND is_open", [cal_ms]).fetchone()
        finally:
            conn.close()
        return int(r[0]) if r and r[0] is not None else None

    # ---- 日内期望 bar 时刻（存储层 241 根口径）----
    def expected_minute_times(self, day_str: str, freq: str = "1min") -> List[int]:
        """返回某开市日的存储层期望分钟 bar 时刻（epoch-ms）升序列表。

        1min：09:30（集合竞价） + [09:31, 11:30]（120 根） + [13:01, 15:00]（120 根）
              = 共 241 根。
        其它分钟 freq（如 5min）暂未在第一批实现，抛 NotImplementedError。
        """
        kind, n = _norm_freq(freq)
        if kind != "minute":
            raise ValueError(f"expected_minute_times 仅支持分钟 freq，收到 {freq!r}")
        if n != 1:
            raise NotImplementedError(
                f"CalendarService 第一批仅实现 1min 存储层期望 bar；freq={freq!r} 待后续批次")
        times: List[int] = [_at(day_str, *AUCTION_BAR)]  # 09:30
        # 连续竞价窗口直接用 intraday_windows 的 end-labeled 边界
        (am_lo, am_hi), (pm_lo, pm_hi) = _day_windows_ms(day_str)
        t = am_lo
        while t <= am_hi:
            times.append(t)
            t += 60_000
        t = pm_lo
        while t <= pm_hi:
            times.append(t)
            t += 60_000
        return times

    def is_expected_bar_time(self, ms: int, freq: str = "1min") -> bool:
        """判断 ms 是否为其所在开市日的合法存储层期望 bar 时刻。"""
        kind, _ = _norm_freq(freq)
        day_str, _, _, _ = _parts(ms)
        if kind == "daily":
            return ms == _day_midnight_ms(ms)
        return ms in set(self.expected_minute_times(day_str, freq))

    # ---- 核心：下一期望 bar 时刻 ----
    def next_expected_time(self, prev_ms: int, freq: str = "1min") -> int:
        """给定某 bar 的 time，返回下一根期望 bar 的 time（v5.1 §2.2 规则）。

        daily：下一交易日的日线 time（00:00:00）。
        1min：见模块 docstring 的分段规则（含 09:30 auction 与午休 / 跨日边界）。
        prev_ms 必须是合法期望 bar 时刻，否则抛 ValueError。
        """
        kind, n = _norm_freq(freq)
        if kind == "daily":
            return self.next_trading_day(prev_ms)
        if n != 1:
            raise NotImplementedError(
                f"next_expected_time 第一批仅实现 1min / daily；freq={freq!r} 待后续批次")

        day_str, hh, mm, ss = _parts(prev_ms)
        if ss != 0:
            raise ValueError(f"非法分钟 bar（秒非零）: {prev_ms}")
        clock = (hh, mm)

        if clock == (9, 30):                       # 集合竞价 → 首根连续竞价
            return _at(day_str, 9, 31)
        if clock == (11, 30):                       # 上午收盘 → 下午首根
            return _at(day_str, 13, 1)
        if clock == (15, 0):                        # 全日收盘 → 下一交易日集合竞价
            nd = self.next_trading_day(prev_ms)
            nd_str, _, _, _ = _parts(nd)
            return _at(nd_str, *AUCTION_BAR)
        # [09:31, 11:29] ∪ [13:01, 14:59] → +1min（含 11:29→11:30、14:59→15:00）
        after_am_open = (hh, mm) >= (9, 31)
        in_am = after_am_open and (hh, mm) <= (11, 29)
        in_pm = (13, 1) <= (hh, mm) <= (14, 59)
        if in_am or in_pm:
            return prev_ms + 60_000
        raise ValueError(f"非法/未知 1min bar 时刻 {hh:02d}:{mm:02d}（prev_ms={prev_ms}）")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    svc = CalendarService()
    day = "2026-07-24"
    ts = svc.expected_minute_times(day)
    print(f"{day} expected 1min bars: {len(ts)} (首={pd.Timestamp(ts[0],unit='ms',tz=TZ)}, "
          f"末={pd.Timestamp(ts[-1],unit='ms',tz=TZ)})")
