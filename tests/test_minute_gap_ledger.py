# -*- coding: utf-8 -*-
"""1.6 挂账 + 下轮重拉：单测（按【批次窗口】判定）。"""

from __future__ import annotations

import pytest

from quantstudio.pipeline.minute_gap_ledger import (
    ACTION_DEFER,
    ACTION_FAIL,
    ACTION_NONE,
    batch_contains_day,
    build_gap_record,
    classify_gap_action,
    ledger_days_to_retry,
    merge_forced_days,
    should_defer_gap,
)

LATEST = "2026-11-05"


class TestBatchContainsDay:
    def test_single_day(self):
        assert batch_contains_day("2026-11-05", "2026-11-05", "2026-11-05") is True

    def test_two_day_span(self):
        assert batch_contains_day("2026-11-04", "2026-11-05", "2026-11-05") is True
        assert batch_contains_day("2026-11-04", "2026-11-05", "2026-11-04") is True

    def test_outside(self):
        assert batch_contains_day("2026-10-19", "2026-10-20", "2026-11-05") is False

    def test_after(self):
        assert batch_contains_day("2026-11-06", "2026-11-07", "2026-11-05") is False


class TestShouldDeferGap:
    """核心：批次窗口含最新交易日 ⇒ 可挂账；历史批 ⇒ 必须失败。"""

    def test_complete_never_defers(self):
        assert should_defer_gap("2026-11-04", "2026-11-05", LATEST, 26, 24) is False
        assert should_defer_gap("2026-10-19", "2026-10-20", LATEST, 51, 47) is False

    def test_empirical_batch10_defers(self):
        """实证：批次 10 = 11-04→11-05 只得 8 片（需 24）⇒ 挂账。"""
        assert should_defer_gap("2026-11-04", "2026-11-05", LATEST, 8, 24) is True

    def test_single_latest_day_defers(self):
        assert should_defer_gap(LATEST, LATEST, LATEST, 0, 24) is True

    def test_historical_batch_fails_not_defers(self):
        """历史批不足 = 真失败，不许延后（否则旧洞永不清）。"""
        assert should_defer_gap("2026-10-19", "2026-10-20", LATEST, 8, 47) is False

    def test_historical_zero_fails(self):
        assert should_defer_gap("2026-10-23", "2026-10-24", LATEST, 0, 24) is False

    def test_weekend_batch_with_zero_shards(self):
        """周末批 0 片是正常的，但窗口不含最新交易日 ⇒ 判 fail 由调用方按
        「交易日为空」短路（本函数不感知日历，调用方须先过滤空批）。"""
        assert should_defer_gap("2026-10-17", "2026-10-18", LATEST, 0, 0) is False


class TestClassifyGapAction:
    def test_three_states(self):
        assert classify_gap_action("2026-11-04", "2026-11-05", LATEST, 26, 24) == ACTION_NONE
        assert classify_gap_action("2026-11-04", "2026-11-05", LATEST, 8, 24) == ACTION_DEFER
        assert classify_gap_action("2026-10-19", "2026-10-20", LATEST, 8, 47) == ACTION_FAIL

    def test_action_constants_distinct(self):
        assert len({ACTION_NONE, ACTION_DEFER, ACTION_FAIL}) == 3


class TestMergeForcedDays:
    def test_union_and_sort(self):
        got = merge_forced_days(
            ["2026-11-04", "2026-11-05"],
            ["2026-11-03", "2026-11-04"])
        assert got == ["2026-11-03", "2026-11-04", "2026-11-05"]

    def test_empty_ledger(self):
        assert merge_forced_days(["2026-11-05"], []) == ["2026-11-05"]

    def test_empty_window(self):
        assert merge_forced_days([], ["2026-10-20"]) == ["2026-10-20"]

    def test_both_empty(self):
        assert merge_forced_days([], []) == []


class TestBuildGapRecord:
    def test_record_fields(self):
        r = build_gap_record("stock_minutes", "2026-11-04", "2026-11-05", 8, 24,
                             ACTION_DEFER, "服务端未就绪")
        assert r["table_name"] == "stock_minutes"
        assert r["batch_start"] == "2026-11-04"
        assert r["batch_end"] == "2026-11-05"
        assert r["shards_actual"] == 8
        assert r["shards_required"] == 24
        assert r["action"] == "defer"

    def test_truncate_to_day(self):
        r = build_gap_record("etf_minutes", "2026-11-04T09:30:00",
                             "2026-11-05T15:00:00", 0, 24, ACTION_FAIL)
        assert r["batch_start"] == "2026-11-04"
        assert r["batch_end"] == "2026-11-05"


class TestLedgerDaysToRetry:
    def test_defer_and_fail_both_retry(self):
        recs = [
            {"batch_start": "2026-11-04", "batch_end": "2026-11-05", "action": ACTION_DEFER},
            {"batch_start": "2026-10-19", "batch_end": "2026-10-20", "action": ACTION_FAIL},
            {"batch_start": "2026-11-03", "batch_end": "2026-11-03", "action": ACTION_NONE},
        ]
        assert ledger_days_to_retry(recs) == {
            "2026-11-04", "2026-11-05", "2026-10-19", "2026-10-20"}

    def test_empty(self):
        assert ledger_days_to_retry([]) == set()

    def test_none_action_excluded(self):
        recs = [{"batch_start": "2026-11-05", "batch_end": "2026-11-05",
                 "action": ACTION_NONE}]
        assert ledger_days_to_retry(recs) == set()


class TestIntegrationScenario:
    """端到端推演：11-05 轮批次 10 的真实缺口 → 挂账 → 下轮重含 → 销账。"""

    def test_1105_batch10_scenario(self):
        # ① 批次 10：11-04→11-05，8 片（需 24），窗口含最新交易日 11-05
        action = classify_gap_action("2026-11-04", "2026-11-05", "2026-11-05", 8, 24)
        assert action == ACTION_DEFER

        # ② 记账
        rec = build_gap_record("stock_minutes", "2026-11-04", "2026-11-05",
                               8, 24, action, "服务端未就绪")
        assert rec["action"] == "defer"

        # ③ 下一轮：声明窗口 + 账本 → 强制重含 11-04/11-05
        next_window = ["2026-11-06", "2026-11-09"]
        forced = merge_forced_days(next_window, ledger_days_to_retry([rec]))
        assert "2026-11-04" in forced and "2026-11-05" in forced
        assert forced == ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]

        # ④ 补齐后销账（action=none ⇒ 不再重含）
        assert ledger_days_to_retry([{"batch_start": "2026-11-04",
                                      "batch_end": "2026-11-05",
                                      "action": ACTION_NONE}]) == set()

    def test_historical_batch_must_fail(self):
        """若某历史批始终不足，1.6 必须让它 fail（不能被无限挂账掩盖）。"""
        action = classify_gap_action("2026-10-19", "2026-10-20", "2026-11-05", 8, 47)
        assert action == ACTION_FAIL
