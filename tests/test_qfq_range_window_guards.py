# -*- coding: utf-8 -*-
"""P1-2b 验收：range/窗口/缓存键 三道守卫（+ 先红矩阵作回归钉）。

方案：docs/jabberwock-four-findings-fix-design.md（P1-2b，过审 7c6f36a）
定谳：ms=0 → "19700101" → 被 %Y%m%d 接受 → 缓存键退化 `etf_minutes|1970-01-01|1970-01-02`

判据：
  C1 `_ms_to_yyyymmdd` 守卫：零/负/越界/不可转 → ValueError；合法 ms → 结果与旧实现逐字一致
  C2 `_export_batches` **出口健全性**：epoch 窗/逆序窗 → ValueError（**先红矩阵回归钉**：不再产出 1970 批）；
     合法窗 → 批次与修复前逐位一致
  C3 `_cache_key` 防腐：epoch/逆序/不可解析 → ValueError；合法（两种格式）→ 与修复前逐字一致
  C4 P1-2a 扩展：`_security_range` / `cap.capture` 抛 ValueError（range 非法）→
     **跳过该证券**（不调引擎、trigger 零修改、计数 invalid_range_skipped）
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from quantstudio.pipeline import qfq_resident_orchestrator as O
from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter

VALID_MS = 1789488000000        # 2026-09-16 附近（合法）


# ---------------- C1 ----------------
def test_c1_ms_to_yyyymmdd_guard_and_legacy_parity():
    assert O._ms_to_yyyymmdd(VALID_MS) == "20260916"
    for bad in (0, -1, None, "abc", 1, 4_102_444_800_000, 10**18):
        with pytest.raises(ValueError):
            O._ms_to_yyyymmdd(bad)
    # 边界：下界当日合法、下界前 1ms 非法
    assert O._ms_to_yyyymmdd(O._MS_LOWER_BOUND).startswith("1990")
    with pytest.raises(ValueError):
        O._ms_to_yyyymmdd(O._MS_LOWER_BOUND - 1)


# ---------------- C2 ----------------
def _stub_adapter() -> MCPAdapter:
    return object.__new__(MCPAdapter)


def test_c2_export_batches_rejects_epoch_and_reversed():
    stub = _stub_adapter()
    # 先红矩阵中的退化入口 —— 修复后必须显式失败（回归钉）
    with pytest.raises(ValueError):
        stub._export_batches("1970-01-01", "1970-01-02", True, est_rows=None,
                             grid_aligned=True, table="etf_minutes")
    with pytest.raises(ValueError):      # 逆序
        stub._export_batches("2026-09-25", "2026-09-20", False, est_rows=None,
                             grid_aligned=True, table="etf_daily")
    with pytest.raises(ValueError):      # 空/垃圾输入（解析器已抛）
        stub._export_batches("", "", True, est_rows=None, grid_aligned=True, table="etf_minutes")


def test_c2b_export_batches_legit_unchanged():
    stub = _stub_adapter()
    got = stub._export_batches("2026-09-20", "2026-09-25", True, est_rows=None,
                               grid_aligned=True, table="etf_minutes")
    assert got == [("2026-09-20", "2026-09-21"), ("2026-09-22", "2026-09-23"),
                   ("2026-09-24", "2026-09-25")], "合法窗批次应与修复前逐位一致"
    # 单日窗（s == e）合法，不被守卫误伤
    assert stub._export_batches("2026-09-25", "2026-09-25", False, est_rows=None,
                                grid_aligned=True, table="etf_daily")


# ---------------- C3 ----------------
def test_c3_cache_key_anti_corruption_and_parity():
    assert MCPAdapter._cache_key("etf_minutes", "2026-09-20", "2026-09-25") == \
        "etf_minutes|2026-09-20|2026-09-25"                     # 合法：与修复前逐字一致
    assert MCPAdapter._cache_key("t", "20260920", "20260925") == "t|20260920|20260925"  # %Y%m%d 亦接受
    for bad in [("t", "1970-01-01", "1970-01-02"),              # epoch
                ("t", "2026-09-25", "2026-09-20"),              # 逆序
                ("t", "xxx", "2026-09-25")]:                    # 不可解析
        with pytest.raises(ValueError):
            MCPAdapter._cache_key(*bad)


# ---------------- C4（P1-2a 扩展） ----------------
class _FakeConn:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return type("R", (), {"fetchall": staticmethod(lambda: [])})()


class _Orch(O.QFQResidentOrchestrator):
    def __init__(self, *, range_exc=None, capture_exc=None):
        self._ident = {"price_source": "mcp", "source_generation": "g1", "cutover_id": "c1"}
        self.aux_db = "aux.db"
        self.calendar = None
        self.cfg = type("C", (), {"price_source": "mcp"})()
        self._range_exc, self._capture_exc = range_exc, capture_exc

    def _already_committed(self, conn, tid):
        return None

    def _security_range(self, conn, asset_type, code):
        if self._range_exc:
            raise self._range_exc
        return (0, 1), (0, 1)


def _patch_capture(monkeypatch, capture_exc):
    class _Cap:
        def __init__(self, _cfg):
            pass

        def capture(self, *_a, **_k):
            if capture_exc:
                raise capture_exc
            return object(), pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr(O, "FreshCapture", _Cap)


@pytest.mark.parametrize("where", ["range", "capture"])
def test_c4_invalid_range_skips_security(monkeypatch, caplog, where):
    calls: list = []
    exc = ValueError("非法 range ms（零值/越界）: 0")
    _patch_capture(monkeypatch, exc if where == "capture" else None)
    orch = _Orch(range_exc=exc if where == "range" else None)
    conn = _FakeConn()
    with caplog.at_level(logging.WARNING, logger=O.logger.name):
        out = orch._reanchor_security(conn, run_id="r", asset_type="STOCK", code="600519",
                                     trigger_ids=["t1"], effective_dates=[1], attempt=0,
                                     fetcher=None)
    assert out.status == "skipped" and out.reason == "invalid_range_ms"
    assert not conn.sql, "不得改 trigger（不写 last_event_id）"
    assert getattr(orch, "_invalid_range_skipped", 0) == 1
    assert any("跳过引擎" in r.getMessage() and "invalid_range_ms" in r.getMessage()
               for r in caplog.records if r.levelno >= logging.WARNING)
