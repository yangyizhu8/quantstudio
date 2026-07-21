"""PR4 契约测试：run_daily 精确时刻调度（黄金用例 6）。

验证目标（主计划 7.25 + 7.26 黄金 6）：
1. run_daily(time='10:00') 在 10:00 Bar 触发（end-labeled：覆盖 09:59:01-10:00:00）
2. time 格式兼容 '9:31' / '09:30' / '9:31:00'
3. 同一任务每日只触发一次
4. 跨日 reset_day()
5. 15:00 bar 触发 run_daily('15:00')
"""
import pytest
import pandas as pd

from quantstudio.backtest.minute_scheduler import _MinuteScheduler


def _bar_ts(day, hh, mm):
    return pd.Timestamp(f"{day} {hh:02d}:{mm:02d}:00").tz_localize("Asia/Shanghai")


# ========== time 格式兼容 ==========

def test_normalize_hhmm_accepts_variants():
    """time 格式兼容 '9:31' / '09:30' / '9:31:00'"""
    assert _MinuteScheduler._normalize_hhmm("9:31") == "09:31"
    assert _MinuteScheduler._normalize_hhmm("09:30") == "09:30"
    assert _MinuteScheduler._normalize_hhmm("9:31:00") == "09:31"
    assert _MinuteScheduler._normalize_hhmm("10:00") == "10:00"


# ========== 精确时刻触发（黄金 6）==========

def test_run_daily_triggers_at_exact_bar_time():
    """run_daily('10:00') 在 10:00 Bar 触发，不在其他 bar 触发"""
    fired = []
    tasks = [((lambda ctx: fired.append(ctx.current_dt)), "10:00")]
    scheduler = _MinuteScheduler(tasks)
    scheduler.reset_day()
    # 遍历 09:31-10:01 的 bar
    for hh, mm in [(9, 31), (9, 50), (10, 0), (10, 1)]:
        ctx = type("C", (), {"current_dt": _bar_ts("2026-01-05", hh, mm)})()
        scheduler.dispatch_if_match(ctx, _bar_ts("2026-01-05", hh, mm))
    # 只在 10:00 触发一次
    assert len(fired) == 1
    assert fired[0] == _bar_ts("2026-01-05", 10, 0)


def test_run_daily_fires_only_once_per_day():
    """同一任务每日只触发一次（即使后续 bar 也匹配）"""
    fired = []
    tasks = [((lambda ctx: fired.append(1)), "10:00")]
    scheduler = _MinuteScheduler(tasks)
    scheduler.reset_day()
    bar = _bar_ts("2026-01-05", 10, 0)
    ctx = type("C", (), {"current_dt": bar})()
    scheduler.dispatch_if_match(ctx, bar)
    scheduler.dispatch_if_match(ctx, bar)   # 同一时刻再触发
    scheduler.dispatch_if_match(ctx, bar)
    assert len(fired) == 1   # 只一次


def test_run_daily_resets_across_days():
    """跨日 reset_day()：第二日可再次触发"""
    fired = []
    tasks = [((lambda ctx: fired.append(1)), "10:00")]
    scheduler = _MinuteScheduler(tasks)
    # 第一日
    scheduler.reset_day()
    bar1 = _bar_ts("2026-01-05", 10, 0)
    scheduler.dispatch_if_match(type("C", (), {"current_dt": bar1})(), bar1)
    # 第二日 reset
    scheduler.reset_day()
    bar2 = _bar_ts("2026-01-06", 10, 0)
    scheduler.dispatch_if_match(type("C", (), {"current_dt": bar2})(), bar2)
    assert len(fired) == 2   # 两日各一次


# ========== 15:00 bar 触发（清理项）==========

def test_run_daily_at_1500_triggers_at_close_bar():
    """run_daily('15:00') 在 15:00 收盘 bar 触发"""
    fired = []
    tasks = [((lambda ctx: fired.append(1)), "15:00")]
    scheduler = _MinuteScheduler(tasks)
    scheduler.reset_day()
    for hh, mm in [(14, 59), (15, 0)]:
        bar = _bar_ts("2026-01-05", hh, mm)
        scheduler.dispatch_if_match(type("C", (), {"current_dt": bar})(), bar)
    assert len(fired) == 1   # 只在 15:00


# ========== 多任务 ==========

def test_multiple_run_daily_tasks_independent():
    """多个 run_daily 任务各自独立触发"""
    fired_a, fired_b = [], []
    tasks = [((lambda ctx: fired_a.append(1)), "09:31"),
             ((lambda ctx: fired_b.append(1)), "14:00")]
    scheduler = _MinuteScheduler(tasks)
    scheduler.reset_day()
    for hh, mm in [(9, 31), (10, 0), (14, 0), (15, 0)]:
        bar = _bar_ts("2026-01-05", hh, mm)
        scheduler.dispatch_if_match(type("C", (), {"current_dt": bar})(), bar)
    assert len(fired_a) == 1
    assert len(fired_b) == 1
