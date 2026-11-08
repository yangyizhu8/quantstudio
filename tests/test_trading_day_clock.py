# -*- coding: utf-8 -*-
"""1.6 钉子：latest_closed_trading_day 的「已收盘」语义（含晨间未收盘日用例）。

审核意见要求的补充用例：
> 批次 10 在 07:44 晨间语境下，最新已收盘日是 11-04 —— 判定与下限都应以它为准。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from quantstudio.pipeline.minute_gap_ledger import (
    ACTION_DEFER,
    ACTION_FAIL,
    classify_gap_action,
)
from quantstudio.pipeline.minute_batch_guard import min_shards_required
from quantstudio.pipeline.trading_day_clock import (
    is_day_closed,
    latest_closed_trading_day,
    trading_days_closed_between,
)

CAL = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]


class TestLatestClosedTradingDay:
    """核心：晨间不能把未收盘的当天算进去。"""

    def test_morning_excludes_today(self):
        """★ 审核要求的用例：07:44 晨间，最新已收盘日是 11-04（不是 11-05）。"""
        now = datetime(2026, 11, 5, 7, 44)
        assert latest_closed_trading_day(now, CAL) == "2026-11-04"

    def test_after_close_includes_today(self):
        now = datetime(2026, 11, 5, 16, 0)
        assert latest_closed_trading_day(now, CAL) == "2026-11-05"

    def test_exactly_at_close_includes_today(self):
        now = datetime(2026, 11, 5, 15, 0)
        assert latest_closed_trading_day(now, CAL) == "2026-11-05"

    def test_just_before_close_excludes_today(self):
        now = datetime(2026, 11, 5, 14, 59)
        assert latest_closed_trading_day(now, CAL) == "2026-11-04"

    def test_weekend_returns_last_friday(self):
        """周日 11-08（非交易日）⇒ 返回 11-06（周五）。"""
        now = datetime(2026, 11, 8, 10, 0)
        cal = CAL + ["2026-11-06"]
        assert latest_closed_trading_day(now, cal) == "2026-11-06"

    def test_empty_calendar(self):
        assert latest_closed_trading_day(datetime(2026, 11, 5, 16, 0), []) is None

    def test_no_past_days(self):
        """日历里只有未来日 ⇒ None。"""
        now = datetime(2026, 11, 1, 10, 0)
        assert latest_closed_trading_day(now, ["2026-11-05"]) is None


class TestIsDayClosed:
    def test_past_day_closed(self):
        assert is_day_closed("2026-11-04", datetime(2026, 11, 5, 7, 44)) is True

    def test_today_morning_not_closed(self):
        assert is_day_closed("2026-11-05", datetime(2026, 11, 5, 7, 44)) is False

    def test_today_afternoon_closed(self):
        assert is_day_closed("2026-11-05", datetime(2026, 11, 5, 16, 0)) is True

    def test_future_not_closed(self):
        assert is_day_closed("2026-11-06", datetime(2026, 11, 5, 16, 0)) is False


class TestTradingDaysClosedBetween:
    def test_morning_window_excludes_today(self):
        """07:44 晨间：窗口 11-04~11-05 只有 11-04 已收盘。"""
        got = trading_days_closed_between(
            "2026-11-04", "2026-11-05", datetime(2026, 11, 5, 7, 44), CAL)
        assert got == ["2026-11-04"]

    def test_after_close_window_includes_today(self):
        got = trading_days_closed_between(
            "2026-11-04", "2026-11-05", datetime(2026, 11, 5, 16, 0), CAL)
        assert got == ["2026-11-04", "2026-11-05"]

    def test_full_window(self):
        got = trading_days_closed_between(
            "2026-11-02", "2026-11-06", datetime(2026, 11, 6, 20, 0), CAL)
        assert got == CAL


class TestMorningScenarioIntegration:
    """端到端：晨间轮的下限与 defer 判定都必须以「已收盘日」为准。"""

    def test_morning_batch10_bounds(self):
        """07:44 晨间，批次 11-04→11-05：
        - 已收盘交易日只有 11-04 ⇒ 下限 = min_shards_required(1) = 24（不是双日 47）
        - 最新已收盘日 = 11-04 ⇒ 批次窗口【含】它 ⇒ 8 片应判 defer（不是 fail）
        """
        now = datetime(2026, 11, 5, 7, 44)
        closed = trading_days_closed_between("2026-11-04", "2026-11-05", now, CAL)
        assert closed == ["2026-11-04"]

        # 下限按「已收盘交易日数」算
        required = min_shards_required(len(closed))
        assert required == 24, f"晨间双日窗口的下限应为单日 24，实际 {required}"

        # defer 判定用「已收盘的最新交易日」
        latest = latest_closed_trading_day(now, CAL)
        assert latest == "2026-11-04"
        assert classify_gap_action("2026-11-04", "2026-11-05", latest, 8, required) == ACTION_DEFER

    def test_morning_historical_batch_still_fails(self):
        """晨间语境下，历史批（不含最新已收盘日）仍必须 fail。"""
        now = datetime(2026, 11, 5, 7, 44)
        latest = latest_closed_trading_day(now, CAL)  # 11-04
        required = min_shards_required(1)
        assert classify_gap_action("2026-11-02", "2026-11-03", latest, 8, required) == ACTION_FAIL

    def test_if_used_today_naively_floor_unreachable(self):
        """反例演示：若把未收盘的 11-05 也算进预期（双日 47 片下限），
        实际只能拿到 24 片（11-04 的量）⇒ 下限永不可达。"""
        naive_required = min_shards_required(2)  # 47
        realistic_shards = 24                    # 11-04 单日完整量
        assert realistic_shards < naive_required, "这正是「floor 永不可达」的机制"
