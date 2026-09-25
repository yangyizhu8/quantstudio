# -*- coding: utf-8 -*-
"""Q2b ①③ 验收：因子刷新 degraded **分类留痕** + **死信通道**（默认仅告警）。

方案：docs/error-q2b-hold-trigger-forensics-and-plan.md（①③ 本次实施；② 附清单快审）

判据：
  C1 分类函数：已知因子链异常 → `known:<类型>`；未知 → `unknown:<类型>`
  C2 死信阈值：env `QS_QFQ_DEGRADED_DEADLETTER_N` 覆盖生效（默认 3）
  C3 连续**同因**达阈值 → 输出死信锚点 ERROR（含 kind 与 detail）；**未达阈值不告警**
  C4 成功（非 degraded）→ 计数清零（再次失败从 1 重新计数）
  C5 **不改变 hold 决策**：失败/异常路径仍返回 True（degraded）
  C6 编排器：传 kind 时 hold_reason 含 `(kind=…)`；**未传时文案与旧版逐字一致**
"""
from __future__ import annotations

import logging

import pytest

from quantstudio.pipeline import daemon as D


class _StubCollector(D.ResidentCollector):
    def __init__(self):
        pass


def test_c1_classify_known_and_unknown():
    from quantstudio.pipeline.mcp.errors import MCPToolError, MCPProtocolError

    assert D._classify_qfq_factor_error(MCPToolError("x")) == "known:MCPToolError"
    assert D._classify_qfq_factor_error(MCPProtocolError("x")) == "known:MCPProtocolError"
    assert D._classify_qfq_factor_error(ValueError("x")) == "known:ValueError"
    assert D._classify_qfq_factor_error(OSError("x")) == "known:OSError"

    class WeirdError(Exception):
        pass

    assert D._classify_qfq_factor_error(WeirdError("x")) == "unknown:WeirdError"


def test_c2_deadletter_limit_env(monkeypatch):
    monkeypatch.delenv(D.QFQ_DEGRADED_DEADLETTER_N_ENV, raising=False)
    assert D._qfq_degraded_deadletter_limit() == 3          # 默认 N=3
    monkeypatch.setenv(D.QFQ_DEGRADED_DEADLETTER_N_ENV, "5")
    assert D._qfq_degraded_deadletter_limit() == 5
    monkeypatch.setenv(D.QFQ_DEGRADED_DEADLETTER_N_ENV, "bad")
    assert D._qfq_degraded_deadletter_limit() == 3          # 非法值回落默认


def test_c3_threshold_emits_deadletter(caplog, monkeypatch):
    c = _StubCollector()
    monkeypatch.setenv(D.QFQ_DEGRADED_DEADLETTER_N_ENV, "3")
    with caplog.at_level(logging.ERROR, logger=D.logger.name):
        c._note_qfq_degraded("known:MCPToolError", "artifact error")
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "未达阈值不应告警"
        c._note_qfq_degraded("known:MCPToolError", "artifact error")
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        c._note_qfq_degraded("known:MCPToolError", "artifact error")     # 第 3 次 → 死信
    errs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errs) == 1 and "死信告警" in errs[0]
    assert "known:MCPToolError" in errs[0] and "N=3" in errs[0]


def test_c3b_different_kinds_counted_separately(caplog, monkeypatch):
    c = _StubCollector()
    monkeypatch.setenv(D.QFQ_DEGRADED_DEADLETTER_N_ENV, "3")
    with caplog.at_level(logging.ERROR, logger=D.logger.name):
        c._note_qfq_degraded("known:ValueError", "a")
        c._note_qfq_degraded("unknown:WeirdError", "b")
        c._note_qfq_degraded("known:ValueError", "c")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "异因不应合并计数"


def test_c4_reset_on_success(caplog, monkeypatch):
    c = _StubCollector()
    monkeypatch.setenv(D.QFQ_DEGRADED_DEADLETTER_N_ENV, "3")
    c._note_qfq_degraded("known:ValueError", "a")
    c._note_qfq_degraded("known:ValueError", "b")
    c._qfq_degraded_reset()
    with caplog.at_level(logging.ERROR, logger=D.logger.name):
        c._note_qfq_degraded("known:ValueError", "c")     # 清零后仅第 1 次
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_c5_hold_decision_unchanged(monkeypatch):
    """C5：失败/异常路径仍返回 True（degraded）——①③ 为纯留痕，不改 hold 决策。"""
    c = _StubCollector()

    class _Refresher:
        def __init__(self, **_k):
            pass

        def refresh(self, *_a, **_k):
            raise ValueError("boom")

    import quantstudio.pipeline.qfq_factor_refresh as FR
    import quantstudio.pipeline.qfq_maintenance as MT

    monkeypatch.setattr(FR, "QFQFactorRefresher", _Refresher)
    monkeypatch.setattr(MT, "get_stock_universe", lambda *_a, **_k: {"600519"})
    monkeypatch.setattr(MT, "get_etf_universe", lambda *_a, **_k: {"510300"})

    class _Orch:
        aux_db = "aux.db"

    # 适配器非 MCP → 不走短路；用桩替换 _get_adapter
    monkeypatch.setattr(c, "_get_adapter", lambda *_a, **_k: object(), raising=False)
    monkeypatch.setattr(c, "writer", type("W", (), {"db_path": "data.db"})(), raising=False)
    assert c._qfq_refresh_factors(_Orch()) is True


def test_c6_orchestrator_reason_kind(monkeypatch):
    """C6：传 kind → hold_reason 含 (kind=…)；未传 → 与旧版逐字一致。"""
    import inspect

    import quantstudio.pipeline.qfq_resident_orchestrator as O

    src = inspect.getsource(O.QFQResidentOrchestrator.run_post_ingest)
    assert "detector_degraded_kind" in src
    sig = inspect.signature(O.QFQResidentOrchestrator.run_post_ingest)
    assert sig.parameters["detector_degraded_kind"].default == ""
    # 文案分支：有 kind 才加后缀（静态断言，避免构造完整编排器）
    assert 'f"detector_degraded{_kind_suffix}: 因子刷新失败，价格水位强制 hold"' in src
