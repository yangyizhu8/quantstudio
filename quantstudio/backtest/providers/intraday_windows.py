"""PR3: 生成 A 股交易时段的 epoch 毫秒窗口。

【事实核实】分钟表 time 列是 13 位 epoch 毫秒戳（aligner.py 的 to_ms_timestamp =
pd.Timestamp(...).tz_localize("Asia/Shanghai").timestamp() * 1000）。任何 time % N
算术都与日内时刻无关。本模块在 Python 侧按日历日生成时段窗口的 epoch 毫秒区间，
传入可索引的 BETWEEN 条件（修正 v1 方案 time%1000000 的事实性 bug）。

【补齐 1 标注约定】假设 end-labeled：bar 标注时刻 = 该分钟结束时刻。
  - 09:31 bar = 09:30:01-09:31:00 的累积；15:00 bar = 14:59:01-15:00:00。
  - 故查询窗口为 [09:31, 11:30] ∪ [13:01, 15:00]（含两端）。
  - 此约定无法对真实数据验证（表空），列为 pr3 报告"真实数据冒烟首要验证项"。
  - 若实际为 start-labeled，整体平移一分钟，午休边界判断随之调整。

【补齐 2】end_cutoff_ms 截断只应用到 end 当天；区间内其余日使用完整时段窗口。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

TZ = "Asia/Shanghai"

# end-labeled 约定下的交易时段边界（HH:MM:SS）
MORNING_START = (9, 31, 0)    # 09:31:00 第一根 bar
MORNING_END = (11, 30, 0)     # 11:30:00 上午最后一根
AFTERNOON_START = (13, 1, 0)  # 13:01:00 下午第一根
AFTERNOON_END = (15, 0, 0)    # 15:00:00 下午最后一根


def _day_windows_ms(day_str: str) -> List[Tuple[int, int]]:
    """返回 day_str 的两个时段毫秒窗口 [(am_start_ms, am_end_ms), (pm_start_ms, pm_end_ms)]。"""
    windows = []
    for hh, mm, ss in (MORNING_START, MORNING_END, AFTERNOON_START, AFTERNOON_END):
        ts = pd.Timestamp(f"{day_str} {hh:02d}:{mm:02d}:{ss:02d}").tz_localize(TZ)
        windows.append(int(ts.value // 10**6))  # epoch 毫秒（13 位）
    return [(windows[0], windows[1]), (windows[2], windows[3])]


def build_intraday_sql_conditions(
    day_strs: List[str],
    end_cutoff_ms: Optional[int] = None,
) -> Tuple[str, List[int]]:
    """生成 SQL 的 time 过滤条件 + 参数列表（参数化防注入）。

    返回 (where_clause, params)。where_clause 形如：
      ((time BETWEEN ? AND ?) OR (time BETWEEN ? AND ?) OR ...)

    end_cutoff_ms：对最后一个 day 的两个窗口（上午+下午）都应用截断。
      - 区间内其余日使用完整时段窗口。
      - 最后一天的每个窗口上界 = min(窗口原上界, end_cutoff_ms)；
        若 end_cutoff_ms < 窗口开始，该窗口整体跳过。
      - PR3 日级用法：end_cutoff_ms = end_date 当天 23:59:59（不触发截断，全天窗口）。
      - PR4 分钟用法：end_cutoff_ms = current_bar_ts（可能落在上午如 10:00，
        截断上午窗口上界到 10:00，跳过 10:01 之后的 bar；下午窗口整体跳过）。
    """
    if not day_strs:
        # 无交易日：返回永假条件（避免返回任何数据）
        return "1=0", []
    clauses: List[str] = []
    params: List[int] = []
    last_idx = len(day_strs) - 1
    for idx, day in enumerate(day_strs):
        windows = _day_windows_ms(day)
        is_last = (idx == last_idx)
        for win_start, win_end in windows:
            lo, hi = win_start, win_end
            if is_last and end_cutoff_ms is not None:
                if end_cutoff_ms < win_start:
                    continue   # cutoff 早于窗口开始，跳过该窗口
                hi = min(hi, end_cutoff_ms)
                if hi < lo:
                    continue
            clauses.append("(time BETWEEN ? AND ?)")
            params.extend([lo, hi])
    where_clause = " OR ".join(clauses)
    return where_clause, params


_TRADING_DAYS_CACHE = {}  # (start, end, provider_id) -> List[str]


def iter_trading_days_in_range(
    start_date: str,
    end_date: str,
    calendar_provider=None,
) -> List[str]:
    """枚举区间内的交易日字符串列表（YYYY-MM-DD）。

    用 calendar_provider.get_trade_days（返回 datetime 列表）；若无 provider 则
    按日历日粗略枚举（仅用于无 calendar 的测试场景，生产路径必传 provider）。

    真实数据修复（2026-07-22）：加进程内缓存（按 start/end/provider 键）。
    原实现每次调用都查 calendar_provider，而 _load_minute_snapshots 全 universe
    批量化前会逐 code 调用，导致同一日历区间被重复查询数千次（叠加 GIL 崩溃）。
    日历区间结果在进程内确定（交易日固定），缓存安全。
    """
    cache_key = (start_date[:10], end_date[:10], id(calendar_provider))
    cached = _TRADING_DAYS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if calendar_provider is None:
        # 无 calendar 时的粗略枚举（仅测试用）：按自然日枚举，含周末（查询时自然无数据）
        days = pd.date_range(start_date[:10], end_date[:10], freq="D")
        result = [d.strftime("%Y-%m-%d") for d in days]
    else:
        try:
            days = calendar_provider.get_trade_days(start_date[:10], end_date[:10])
        except Exception:
            days = []
        result = [getattr(d, "strftime", lambda f: str(d))("%Y-%m-%d") for d in days]

    _TRADING_DAYS_CACHE[cache_key] = result
    return result
