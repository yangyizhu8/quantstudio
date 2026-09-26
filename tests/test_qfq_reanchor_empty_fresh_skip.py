# -*- coding: utf-8 -*-
"""P1-2a 验收：`_reanchor_security` 的**空 fresh 前置跳过**（不传引擎、不崩整轮）。

方案：docs/jabberwock-four-findings-fix-design.md（P1-2a，过审）

判据：
  C1 空 fresh（daily/minute 均空）→ **引擎未被调用**，outcome.status=skipped /
     reason=empty_fresh_capture；WARNING 含 code 与区间
  C2 trigger **不被修改**（保持 pending；不写 last_event_id、不计失败）
  C3 计数可见：`_empty_fresh_skipped` 累加
  C4 非空路径**零变化**：引擎被调用且入参与改前一致（fresh_daily/fresh_minute 原样透传）
  C5 `_fresh_len` 判据正确（None/[]/空 DataFrame → 0；有内容 → >0）
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from quantstudio.pipeline import qfq_resident_orchestrator as O


def test_c5_fresh_len():
    assert O._fresh_len(None) == 0
    assert O._fresh_len([]) == 0
    assert O._fresh_len(pd.DataFrame()) == 0
    assert O._fresh_len([1, 2]) == 2
    assert O._fresh_len(pd.DataFrame({"a": [1]})) == 1
    assert O._fresh_len(123) == 0          # 不可 len 的对象 → 0（防御）


class _FakeConn:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return type("R", (), {"fetchall": staticmethod(lambda: [])})()


class _Orch(O.QFQResidentOrchestrator):
    """最小桩：只在 `_reanchor_security` 之前/之内被消费的属性与方法。"""

    def __init__(self, fresh_daily, fresh_minute, engine_calls):
        self._ident = {"price_source": "mcp", "source_generation": "g1", "cutover_id": "c1"}
        self.aux_db = "aux.db"
        self.calendar = None
        self.cfg = type("C", (), {"price_source": "mcp"})()
        self._fresh_daily, self._fresh_minute = fresh_daily, fresh_minute
        self._engine_calls = engine_calls

    # --- 被替换的依赖 ---
    def _already_committed(self, conn, tid):
        return None

    def _security_range(self, conn, asset_type, code):
        return (0, 1), (0, 1)

    def _security_effective_dates(self, conn, asset_type, code):
        return []


def _patch_capture_and_engine(monkeypatch, fresh_daily, fresh_minute, engine_calls):
    class _Cap:
        def __init__(self, _cfg):
            pass

        def capture(self, *_a, **_k):
            rec = type("Rec", (), {"capture_id": "cap1", "metadata_sha256": "sha",
                                  "source_generation": "", "cutover_id": ""})()
            return rec, fresh_daily, fresh_minute

        def mark_applied(self, *_a, **_k):
            """引擎 apply 成功后编排器会调用（桩：记录即可）。"""
            return None

    import quantstudio.pipeline.qfq_fresh_capture as FC
    import quantstudio.pipeline.qfq_reanchor_engine as RE

    # orchestrator 用 `from ... import FreshCapture`（模块级直接导入）→ 必须补丁**其模块内的名字**
    monkeypatch.setattr(O, "FreshCapture", _Cap)
    monkeypatch.setattr(FC, "FreshCapture", _Cap)          # 兜底：其他路径引用

    def _apply(*_a, **k):
        engine_calls.append(k)
        return type("Res", (), {"status": "committed", "reason": "", "event_id": "evt1"})()

    monkeypatch.setattr(RE, "apply_reanchor_for_security", _apply)


def test_c1_c2_c3_empty_fresh_skips_engine(monkeypatch, caplog):
    calls: list = []
    _patch_capture_and_engine(monkeypatch, pd.DataFrame(), [], calls)
    orch = _Orch(pd.DataFrame(), [], calls)
    conn = _FakeConn()
    with caplog.at_level(logging.WARNING, logger=O.logger.name):
        out = orch._reanchor_security(conn, run_id="r1", asset_type="STOCK", code="600519",
                                     trigger_ids=["t1"], effective_dates=[1], attempt=0,
                                     fetcher=None)
    assert out.status == "skipped" and out.reason == "empty_fresh_capture"      # C1
    assert calls == [], "空 fresh 不得调用引擎"                                  # C1
    assert not conn.sql, "空 fresh 不得改 trigger（不写 last_event_id）"          # C2
    assert getattr(orch, "_empty_fresh_skipped", 0) == 1                        # C3
    warns = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("fresh 采集为空" in m and "600519" in m for m in warns)            # C1 文案


def test_c4_nonempty_path_unchanged(monkeypatch):
    calls: list = []
    daily = pd.DataFrame({"a": [1]})
    minute = pd.DataFrame({"b": [2]})
    _patch_capture_and_engine(monkeypatch, daily, minute, calls)
    orch = _Orch(daily, minute, calls)
    conn = _FakeConn()
    out = orch._reanchor_security(conn, run_id="r1", asset_type="STOCK", code="600519",
                                  trigger_ids=["t1"], effective_dates=[1], attempt=0,
                                  fetcher=None)
    assert len(calls) == 1, "非空 fresh 应调用引擎一次"
    kw = calls[0]
    assert kw["fresh_daily"] is daily and kw["fresh_minutes"] is minute, "fresh 原样透传"
    assert out.status == "committed"
    assert getattr(orch, "_empty_fresh_skipped", 0) == 0
