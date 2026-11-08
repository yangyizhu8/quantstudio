# -*- coding: utf-8 -*-
"""1.6 定义钉子：latest_trading_day 必须是「已收盘的最新交易日」（trade-calendar aware）。

审核意见（2026-11-08）：
> latest_trading_day 必须是"已收盘的最新交易日"（trade-calendar aware）。
> 否则日内轮会把未收盘的当天算进"窗口含最新日"判定与分片下限
> （floor 把没发生的行情也计入预期 ⇒ 永不可达）。
> 批次 10 在 07:44 晨间语境下，最新已收盘日是 11-04 —— 判定与下限都应以它为准。

本模块提供纯函数，供 minute_gap_ledger / minute_batch_guard 的调用方使用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional


def latest_closed_trading_day(
    now: datetime,
    trading_days: Iterable[str],
    market_close_hour: int = 15,
) -> Optional[str]:
    """返回「已收盘的最新交易日」'YYYY-MM-DD'。

    规则：
    - 若 now 的日期本身是交易日 且 now.hour >= market_close_hour ⇒ 返回该日（已收盘）
    - 否则返回严格早于 now 日期的最近一个交易日

    Args:
        now: 当前时间（须带 tz 或已为本地时间）
        trading_days: 交易日历（'YYYY-MM-DD' 集合）
        market_close_hour: 收盘小时（默认 15 点）

    Returns:
        已收盘的最新交易日；无可用交易日时返回 None

    Examples:
        >>> latest_closed_trading_day(
        ...     datetime(2026, 11, 5, 7, 44),   # 07:44 晨间
        ...     ["2026-11-03", "2026-11-04", "2026-11-05"])
        '2026-11-04'

        >>> latest_closed_trading_day(
        ...     datetime(2026, 11, 5, 16, 0),   # 收盘后
        ...     ["2026-11-03", "2026-11-04", "2026-11-05"])
        '2026-11-05'
    """
    today = now.strftime("%Y-%m-%d")
    norm = sorted({str(d)[:10] for d in trading_days})
    if not norm:
        return None

    if today in norm and now.hour >= market_close_hour:
        return today

    past = [d for d in norm if d < today]
    return past[-1] if past else None


def is_day_closed(
    day: str,
    now: datetime,
    market_close_hour: int = 15,
) -> bool:
    """判断某个交易日是否「已收盘」（可安全计入预期分片）。"""
    d = str(day)[:10]
    today = now.strftime("%Y-%m-%d")
    if d < today:
        return True
    if d == today:
        return now.hour >= market_close_hour
    return False


def trading_days_closed_between(
    start: str,
    end: str,
    now: datetime,
    trading_days: Iterable[str],
    market_close_hour: int = 15,
) -> List[str]:
    """窗口 [start, end] 内**已收盘**的交易日列表（供 min_shards_required 的 trading_days 用）。

    这是「floor 把没发生的行情也计入预期」的直接修复：
    未收盘的当天不计入预期，避免下限永不可达。
    """
    norm = sorted({str(d)[:10] for d in trading_days})
    return [d for d in norm
            if str(start)[:10] <= d <= str(end)[:10]
            and is_day_closed(d, now, market_close_hour)]
