"""PR4: run_daily(time='HH:MM') 精确时刻调度器。

主计划 7.24 生命周期：每 Bar 先更新 current_dt → 匹配该 Bar 时刻的 run_daily 任务触发
→ 再 handle_data。

end-labeled 约定：bar 标注时刻 = 分钟结束时刻。run_daily('10:00') 在 10:00 Bar 触发
（该 Bar 覆盖 09:59:01-10:00:00）。同一任务每日只触发一次，跨日状态由 reset_day() 重置。

time 格式兼容：'9:31' / '09:30' / '9:31:00'（用 pd.Timestamp 解析取 HH:MM）。

清理项：分钟 Profile scheduler 在 handle_data 之前触发（主计划 7.24）；
       日线 Profile tasks 在 handle_data 之后（现有 backtest_engine 行为）。
"""
from __future__ import annotations

from typing import Callable, List, Tuple

import pandas as pd


class _MinuteScheduler:
    """run_daily 精确时刻调度器（分钟 Profile 专用）。

    daily_tasks 来自 PtradeAPI._daily_tasks: list[(func, time_str)]。
    """

    def __init__(self, daily_tasks: List[Tuple[Callable, str]]):
        # 归一化 time 字符串到 'HH:MM'
        self._tasks: List[Tuple[Callable, str]] = [
            (func, self._normalize_hhmm(time_str))
            for func, time_str in (daily_tasks or [])
        ]
        self._fired_today: set = set()   # 已触发任务的 id(func)，每日 reset

    def reset_day(self) -> None:
        """每日开盘前重置触发状态（同一任务每日只触发一次）。"""
        self._fired_today.clear()

    def dispatch_if_match(self, ctx, bar_ts: pd.Timestamp) -> None:
        """在 bar_ts 时刻检查是否有匹配的 run_daily 任务需要触发。

        bar_ts 是 end-labeled 的 bar 标注时刻（如 10:00 bar 覆盖 09:59:01-10:00:00）。
        匹配条件：task 的 HH:MM == bar_ts 的 HH:MM，且该任务今日未触发。
        """
        bar_hhmm = bar_ts.strftime('%H:%M')
        for func, task_hhmm in self._tasks:
            func_id = id(func)
            if task_hhmm == bar_hhmm and func_id not in self._fired_today:
                try:
                    func(ctx)
                except Exception:
                    pass   # 任务异常不阻断主循环（与日线引擎 except 行为一致）
                self._fired_today.add(func_id)

    @staticmethod
    def _normalize_hhmm(time_str: str) -> str:
        """归一化 time 字符串到 'HH:MM'。

        兼容 '9:31' / '09:30' / '9:31:00' / '10:00' 等格式。
        用 pd.Timestamp 解析（补全日期占位），取 HH:MM。
        """
        if not time_str:
            return ""
        ts = pd.Timestamp(f"1970-01-01 {time_str}")
        return ts.strftime('%H:%M')
