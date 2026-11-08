# -*- coding: utf-8 -*-
"""T2.1 周期 CHECKPOINT / T2.3 入口 CHECKPOINT 单测。

②审条件②：单测 + 重启验证是进重建的前置门。
本文件覆盖单测部分；重启验证见 docs/evidence/。
"""

from __future__ import annotations

import os

import pytest

from quantstudio.pipeline.checkpoint_policy import (
    DEFAULT_CHECKPOINT_INTERVAL_S,
    next_last_ts,
    periodic_checkpoint_enabled,
    resolve_interval,
    should_run_periodic_checkpoint,
    format_on_open_log,
    format_periodic_log,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("QS_PERIODIC_CHECKPOINT", raising=False)
    yield


class TestSwitch:
    def test_default_enabled(self):
        assert periodic_checkpoint_enabled() is True

    @pytest.mark.parametrize("v", ["0", "false", "False", "off", "no"])
    def test_explicit_off(self, monkeypatch, v):
        monkeypatch.setenv("QS_PERIODIC_CHECKPOINT", v)
        assert periodic_checkpoint_enabled() is False

    @pytest.mark.parametrize("v", ["1", "true", "yes", "anything"])
    def test_explicit_on(self, monkeypatch, v):
        monkeypatch.setenv("QS_PERIODIC_CHECKPOINT", v)
        assert periodic_checkpoint_enabled() is True


class TestResolveInterval:
    def test_default_when_missing(self):
        assert resolve_interval({}) == DEFAULT_CHECKPOINT_INTERVAL_S
        assert resolve_interval(None) == DEFAULT_CHECKPOINT_INTERVAL_S

    def test_zero_disables(self):
        assert resolve_interval({"checkpoint_interval_sec": 0}) == 0

    def test_positive_adopted(self):
        assert resolve_interval({"checkpoint_interval_sec": 600}) == 600

    def test_floor_60(self):
        assert resolve_interval({"checkpoint_interval_sec": 5}) == 60

    def test_invalid_falls_back(self):
        assert resolve_interval({"checkpoint_interval_sec": "abc"}) == DEFAULT_CHECKPOINT_INTERVAL_S
        assert resolve_interval({"checkpoint_interval_sec": None}) == DEFAULT_CHECKPOINT_INTERVAL_S


class TestShouldRun:
    def test_first_tick_always_runs(self):
        """启动首 tick 强制收一次遗留 WAL（T2.3 的首 tick 形态）。"""
        assert should_run_periodic_checkpoint(1000.0, 0.0, 1800) is True
        assert should_run_periodic_checkpoint(1000.0, -1.0, 1800) is True

    def test_within_interval_skips(self):
        assert should_run_periodic_checkpoint(1000.0, 999.0, 1800) is False

    def test_at_interval_runs(self):
        assert should_run_periodic_checkpoint(2800.0, 1000.0, 1800) is True

    def test_beyond_interval_runs(self):
        assert should_run_periodic_checkpoint(9999.0, 1000.0, 1800) is True

    def test_zero_interval_never(self):
        assert should_run_periodic_checkpoint(9999.0, 0.0, 0) is False

    def test_switch_off_never(self, monkeypatch):
        monkeypatch.setenv("QS_PERIODIC_CHECKPOINT", "0")
        assert should_run_periodic_checkpoint(9999.0, 0.0, 1800) is False


class TestNextLastTs:
    def test_success_advances(self):
        assert next_last_ts(1234.0, True, 0.0) == 1234.0

    def test_failure_also_advances(self):
        """失败也推进——避免每 tick 反复失败刷噪声。"""
        assert next_last_ts(1234.0, False, 0.0) == 1234.0


class TestLogFormat:
    def test_periodic_ok(self):
        s = format_periodic_log("daemon_forever", True, "wal 100->0", 2.5)
        assert "[T2.1]" in s and "OK" in s and "daemon_forever" in s

    def test_periodic_skip(self):
        s = format_periodic_log("daemon_forever", False, "timeout>120s", 120.1)
        assert "SKIP" in s

    def test_on_open(self):
        s = format_on_open_log("once", True, "wal 0，无需检查点")
        assert "[T2.3]" in s and "once" in s


class TestEntryListDocumented:
    """②审条件①：入口清单必须逐入口一行、禁盲加。此处校验清单文件与实现一致。"""

    def test_plan_document_lists_seven_entries(self):
        from pathlib import Path

        p = Path(r"D:\miniQMT策略实盘\QuantStudio\docs\t21-t23-implementation-plan-20261108.md")
        assert p.exists(), "实施方案（条件①交付物）必须存在"
        txt = p.read_text(encoding="utf-8")
        # 七个入口必须都被提到
        for entry in ("daemon forever", "daemon once", "GUI 启动",
                      "回填工具", "对账工具", "一次性运维脚本", "pytest"):
            assert entry in txt, f"入口清单缺 {entry}"
        # 必须写明"不加"的理由（禁盲加）
        assert "不加" in txt
