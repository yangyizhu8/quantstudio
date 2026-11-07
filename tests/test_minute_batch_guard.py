# -*- coding: utf-8 -*-
"""1.5 动态分片下限 + 1.4 逐日覆盖对账：单测（V1-V5 的纯函数部分）。

对应方案件 v4 §5：
- V1 增量上界断言（本文件覆盖其「窗口日集」基础）
- V2 A4 与主增量并集（本文件覆盖其「日集展开」基础）
- V3 当日零分片告警（由 is_shard_complete 支撑）
- V5b/V5c 两场景（b 场景：全窗口展开；c' 场景：8/26 判不完整）
"""

from __future__ import annotations

import pytest

from quantstudio.pipeline.minute_batch_guard import (
    DEFAULT_COMPLETENESS_RATIO,
    SHARDS_PER_TRADING_DAY,
    classify_batch_days,
    expected_trading_days,
    is_shard_complete,
    min_shards_required,
    reconcile_daily_coverage,
)

# 交易日历样例：2026-10-19(一) ~ 2026-11-05(四)，剔除周末与国庆
CAL = [
    "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23",
    "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30",
    "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05",
]


class TestMinShardsRequired:
    """1.5 动态下限：核心是「按批次交易日数算」，不是固定 23。"""

    def test_single_day_batch(self):
        """单日批 26 × 0.9 = 23.4 → 24。"""
        assert min_shards_required(1) == 24

    def test_two_day_batch(self):
        """双日批 52 × 0.9 = 46.8 → 47（固定 23 会漏判）。"""
        assert min_shards_required(2) == 47

    def test_three_day_batch(self):
        assert min_shards_required(3) == 71  # 78 × 0.9 = 70.2 → 71

    def test_ratio_boundary(self):
        """ratio=1.0 ⇒ 严格全量。"""
        assert min_shards_required(1, ratio=1.0) == 26
        assert min_shards_required(2, ratio=1.0) == 52

    @pytest.mark.parametrize("bad_days", [0, -1])
    def test_invalid_trading_days(self, bad_days):
        with pytest.raises(ValueError):
            min_shards_required(bad_days)

    def test_invalid_ratio(self):
        with pytest.raises(ValueError):
            min_shards_required(1, ratio=0)
        with pytest.raises(ValueError):
            min_shards_required(1, ratio=1.5)

    def test_invalid_shards_per_day(self):
        with pytest.raises(ValueError):
            min_shards_required(1, shards_per_day=0)


class TestIsShardComplete:
    """1.5 判定：11-05 轮实证 8/26 必须判不完整。"""

    def test_empirical_1104_batch_incomplete(self):
        """实证：11-04 只返回 8 片（单日批应 ≥24）⇒ 不完整。"""
        assert is_shard_complete(8, trading_days=1) is False

    def test_empirical_full_day_passes(self):
        """实证：26 片（完整单日）⇒ 达标。"""
        assert is_shard_complete(26, trading_days=1) is True

    def test_two_day_batch_partial_fails(self):
        """双日批只到 25 片（>单日 24 但 <双日 47）⇒ 必须判不完整。
        这是「固定 23」会漏判的场景。"""
        assert is_shard_complete(25, trading_days=2) is False

    def test_two_day_batch_full_passes(self):
        assert is_shard_complete(51, trading_days=2) is True

    def test_zero_shards_always_incomplete(self):
        assert is_shard_complete(0, trading_days=1) is False

    def test_negative_shards_raises(self):
        with pytest.raises(ValueError):
            is_shard_complete(-1, trading_days=1)


class TestExpectedTradingDays:
    """1.4 对账基础：窗口内交易日展开。"""

    def test_single_day(self):
        assert expected_trading_days("2026-11-04", "2026-11-04", CAL) == ["2026-11-04"]

    def test_two_day(self):
        assert expected_trading_days("2026-10-19", "2026-10-20", CAL) == [
            "2026-10-19", "2026-10-20"]

    def test_span_weekend_excludes_weekend(self):
        """10-23(五) ~ 10-26(一) 中间 10-24/25 是周末 ⇒ 只返回 2 个交易日。"""
        got = expected_trading_days("2026-10-23", "2026-10-26", CAL)
        assert got == ["2026-10-23", "2026-10-26"]

    def test_full_window(self):
        got = expected_trading_days("2026-10-17", "2026-11-05", CAL)
        assert len(got) == len(CAL)  # 日历内 14 个
        assert got[0] == "2026-10-19"
        assert got[-1] == "2026-11-05"

    def test_dedup_and_sort(self):
        got = expected_trading_days("2026-11-03", "2026-11-04",
                                    ["2026-11-04", "2026-11-03", "2026-11-04"])
        assert got == ["2026-11-03", "2026-11-04"]


class TestReconcileDailyCoverage:
    """1.4 执行级逐日覆盖对账：缺日必报。"""

    def test_full_coverage(self):
        ok, missing = reconcile_daily_coverage(
            "2026-11-02", "2026-11-03", CAL, {"2026-11-02", "2026-11-03"})
        assert ok is True
        assert missing == []

    def test_missing_day_reported(self):
        """实证场景：11-05 轮缺 11-05 当天。"""
        ok, missing = reconcile_daily_coverage(
            "2026-11-03", "2026-11-05", CAL,
            {"2026-11-03", "2026-11-04"})  # 缺 11-05
        assert ok is False
        assert missing == ["2026-11-05"]

    def test_multiple_missing(self):
        ok, missing = reconcile_daily_coverage(
            "2026-10-30", "2026-11-05", CAL, set())
        assert ok is False
        assert missing == ["2026-10-30", "2026-11-02", "2026-11-03",
                           "2026-11-04", "2026-11-05"]

    def test_extra_days_ignored(self):
        """多拉的历史日不影响完整性判定。"""
        ok, missing = reconcile_daily_coverage(
            "2026-11-04", "2026-11-04", CAL,
            {"2026-10-01", "2026-11-04"})
        assert ok is True
        assert missing == []


class TestClassifyBatchDays:
    """批次窗口 → 交易日列表（供 min_shards_required 用）。"""

    def test_single_day_batch(self):
        assert classify_batch_days("2026-11-04", "2026-11-04", CAL) == ["2026-11-04"]

    def test_two_day_batch(self):
        assert classify_batch_days("2026-10-19", "2026-10-20", CAL) == [
            "2026-10-19", "2026-10-20"]

    def test_weekend_only_batch_empty(self):
        """实证：批次 1/10 是 10-17→10-18（周末）⇒ 交易日为空。"""
        assert classify_batch_days("2026-10-17", "2026-10-18", CAL) == []

    def test_constants_exposed(self):
        assert SHARDS_PER_TRADING_DAY == 26
        assert 0 < DEFAULT_COMPLETENESS_RATIO <= 1
