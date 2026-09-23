# -*- coding: utf-8 -*-
"""F2-A（2026-09-23）：接线层 no-op（delta_below_one_lot）计数上报 QS_FILL_AUDIT。

回归背景：order_target_value 的接线层在 _qs_split_order 返回空列表时走 _qs_noop_target，
不经引擎 _finalize_immediate → 不进 _day_rejections → QS_FILL_AUDIT 出现
submitted=N / filled=M / rejected=K 的对账缺口（N != M+K）。实测缺口 201/574（ou_reversal R5）。

本测试锁定：
1. 上报后 _day_rejections 收到 (qmt_code, direction, 'delta_below_one_lot')；
2. 上报为纯日志可观测性——订单返回值、持仓、现金均不受影响；
3. 引擎未 attach 时不抛异常（静默跳过，行为与改动前等价）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.backtest import ptrade_api as pa  # noqa: E402


class _FakeEngine:
    """最小引擎替身：只提供 _day_rejections 与 _to_qmt（QS_FILL_AUDIT 采集所需）。"""

    def __init__(self):
        self._day_rejections = []

    @staticmethod
    def _to_qmt(bare):
        return bare + ".SH" if bare.startswith("6") else bare + ".SZ"


def test_noop_reports_into_day_rejections(monkeypatch):
    eng = _FakeEngine()
    monkeypatch.setattr(pa._api, "_engine", eng, raising=False)
    pa._qs_report_noop("600519.SS", "buy", "delta_below_one_lot")
    assert eng._day_rejections == [("600519.SH", "buy", "delta_below_one_lot")]


def test_noop_report_is_observability_only(monkeypatch):
    eng = _FakeEngine()
    monkeypatch.setattr(pa._api, "_engine", eng, raising=False)
    order = pa._qs_noop_target("600519.SS", 9700.0, "delta_below_one_lot")
    before = (order.status, order.reason, order.filled, order.filled_amount)
    pa._qs_report_noop("600519.SS", "buy", "delta_below_one_lot")
    after = (order.status, order.reason, order.filled, order.filled_amount)
    assert before == after == ("rejected", "delta_below_one_lot", 0.0, 0)
    assert bool(order) is False          # no-op 恒 falsy（F1-B 递补判定依赖）
    assert len(eng._day_rejections) == 1  # 仅日志侧计数


def test_noop_report_silent_without_engine(monkeypatch):
    monkeypatch.setattr(pa._api, "_engine", None, raising=False)
    pa._qs_report_noop("600519.SS", "buy", "delta_below_one_lot")  # 不得抛异常


def test_noop_report_silent_when_bucket_missing(monkeypatch):
    monkeypatch.setattr(pa._api, "_engine", object(), raising=False)  # 无 _day_rejections
    pa._qs_report_noop("600519.SS", "buy", "delta_below_one_lot")  # 不得抛异常
