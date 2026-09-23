# -*- coding: utf-8 -*-
"""F 系列修复单测（F-3/F-4，方案 docs/f-series-mcp-export-alignment-design.md）。

覆盖：
- R1/R3：daemon 直连路径分钟安全窗（10 天估算 160 万 → 实测 1000 万行/批的错误修正）
- 预算错误识别（MCPExportBudgetError 含 hint/suggested_shards）
- get_manifest 异步 status 兼容（failed/running 拒绝消费）
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_mod(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# mcp_adapter 依赖包内相对导入，走包导入路径
sys.path.insert(0, str(PROJECT_ROOT))
from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter as MCPSourceAdapter  # noqa: E402
from quantstudio.pipeline.mcp.errors import MCPExportBudgetError  # noqa: E402


class _MinimalAdapter(MCPSourceAdapter):
    """绕过 __init__（避免真实连接），仅用 _export_batches 静态逻辑。"""

    def __init__(self):
        pass


A = _MinimalAdapter()


# ---------- R1/R3：分钟安全窗（直连路径同样生效）----------

def test_minute_safe_window_stock_minutes():
    """est=480M → 日行数≈1.975M → 安全窗=2 天（原 10 天窗 10M 行超 5M row_limit）。"""
    batches = A._export_batches("2026-08-28", "2026-09-07", is_minute=True,
                                est_rows=480_000_000, grid_aligned=False)
    # 每批跨度 ≤ 安全窗（2 天）
    from datetime import datetime, timedelta
    for bs, be in batches:
        d = (datetime.strptime(be, "%Y-%m-%d") - datetime.strptime(bs, "%Y-%m-%d")).days
        assert d <= 3, f"批次 {bs}~{be} 跨度 {d} 天超安全窗"
    # 全覆盖无空洞
    assert batches[0][0] == "2026-08-28"
    assert batches[-1][1] == "2026-09-07"
    for (_, e1), (s2, _) in zip(batches, batches[1:]):
        from datetime import datetime as _dt
        gap = (_dt.strptime(s2, "%Y-%m-%d") - _dt.strptime(e1, "%Y-%m-%d")).days
        assert gap == 1, f"批次间 {e1} → {s2} 空洞 {gap} 天"


def test_minute_safe_window_etf_minutes():
    """est=120M → 日行数≈49.4 万 → 安全窗=8 天。"""
    batches = A._export_batches("2026-08-01", "2026-08-31", is_minute=True,
                                est_rows=120_000_000, grid_aligned=False)
    from datetime import datetime
    for bs, be in batches:
        d = (datetime.strptime(be, "%Y-%m-%d") - datetime.strptime(bs, "%Y-%m-%d")).days
        assert d <= 9, f"etf 批次 {bs}~{be} 跨度 {d} 天超安全窗"


def test_small_table_single_batch_unchanged():
    """est < 150 万 → 单批（回归：既有行为不变）。"""
    batches = A._export_batches("2026-08-01", "2026-09-07", is_minute=False,
                                est_rows=210_000, grid_aligned=False)
    assert batches == [("2026-08-01", "2026-09-07")]


def test_daily_window_unchanged():
    """日线表（非分钟）：365 天窗不变（回归）。"""
    batches = A._export_batches("2025-01-01", "2026-09-07", is_minute=False,
                                est_rows=14_000_000, grid_aligned=False)
    assert len(batches) == 2  # 615 天 / 365 天窗 = 2 批
    from datetime import datetime
    for bs, be in batches:
        d = (datetime.strptime(be, "%Y-%m-%d") - datetime.strptime(bs, "%Y-%m-%d")).days
        assert d <= 366


def test_grid_aligned_still_works():
    """grid_aligned 路径回归：网格对齐 + 尾部不截断语义保持。"""
    batches = A._export_batches("2026-08-28", "2026-09-07", is_minute=True,
                                est_rows=480_000_000, grid_aligned=True)
    assert len(batches) >= 1
    # 网格批终点=起点+window（不截断到 end）
    from datetime import datetime
    bs, be = batches[0]
    d = (datetime.strptime(be, "%Y-%m-%d") - datetime.strptime(bs, "%Y-%m-%d")).days
    assert d >= 1


# ---------- 客户反馈包 2026-09-23 问题1：窗口纳入时间预算 + 可配置 ----------

def _window_span(batches):
    from datetime import datetime
    return max((datetime.strptime(be, "%Y-%m-%d")
                - datetime.strptime(bs, "%Y-%m-%d")).days for bs, be in batches)


def test_minute_window_time_budget_default_is_one_day():
    """默认配置（minute_export_window_days=1 / target_rows=2.5M）→ 分钟窗 = 1 天。

    机理：服务端单作业 60s 软时间预算，2 天窗实测仍会瞬时触顶
    （客户两次死于批 44/87 与 70/89，export_exceeds_time_budget）。
    """
    assert _window_span(A._export_batches(
        "2026-08-01", "2026-08-10", is_minute=True,
        est_rows=480_000_000, grid_aligned=False)) == 1
    # etf_minutes 同样被 1 天上限约束（其行数约束本可放宽到 4 天）
    assert _window_span(A._export_batches(
        "2026-08-01", "2026-08-10", is_minute=True,
        est_rows=120_000_000, grid_aligned=False)) == 1


def test_minute_window_target_rows_budget_math():
    """关闭 cfg 窗上限后：窗口由**时间预算目标行数**反算（2.5M/(日行数×1.2)）。"""
    A.minute_export_window_days = 0          # 仅留 target_rows 约束
    A.minute_export_target_rows = 2_500_000
    try:
        # stock_minutes：日行数≈1.975M → int(2.5M/(1.975M×1.2)) = 1 天
        assert _window_span(A._export_batches(
            "2026-08-01", "2026-08-20", is_minute=True,
            est_rows=480_000_000, grid_aligned=False)) == 1
        # etf_minutes：日行数≈0.494M → int(2.5M/(0.494M×1.2)) = 4 天
        assert _window_span(A._export_batches(
            "2026-08-01", "2026-08-20", is_minute=True,
            est_rows=120_000_000, grid_aligned=False)) == 4
    finally:
        del A.minute_export_window_days
        del A.minute_export_target_rows


def test_minute_window_config_can_restore_old_behaviour():
    """回退护栏：cfg 键置 0/负值 → 退回纯行数约束（旧行为可恢复）。"""
    A.minute_export_window_days = 0
    A.minute_export_target_rows = 0
    try:
        assert _window_span(A._export_batches(
            "2026-08-01", "2026-08-20", is_minute=True,
            est_rows=480_000_000, grid_aligned=False)) == 2
    finally:
        del A.minute_export_window_days
        del A.minute_export_target_rows


def test_daily_table_window_not_affected_by_minute_budget():
    """日线表不受分钟预算约束影响（回归：365 天窗不变）。"""
    A.minute_export_window_days = 1
    A.minute_export_target_rows = 100_000
    try:
        batches = A._export_batches("2025-01-01", "2026-09-07", is_minute=False,
                                    est_rows=14_000_000, grid_aligned=False)
        assert len(batches) == 2
    finally:
        del A.minute_export_window_days
        del A.minute_export_target_rows


# ---------- R4：预算错误与 manifest status ----------
def test_export_budget_error_attrs():
    e = MCPExportBudgetError("budget exceeded", error_code="export_exceeds_time_budget",
                             hint="shard by date", suggested_shards=[{"a": 1}])
    assert e.error_code == "export_exceeds_time_budget"
    assert e.hint == "shard by date"
    assert e.suggested_shards == [{"a": 1}]
    assert isinstance(e, Exception)


def test_create_export_job_budget_error(monkeypatch):
    """服务端返回结构化错误 → MCPExportBudgetError（非 MCPProtocolError）。"""
    from quantstudio.pipeline.mcp.client import MCPClient
    c = object.__new__(MCPClient)  # 绕过 __init__
    c._lock = __import__("threading").Lock()
    c._job_cache = {}
    monkeypatch.setattr(c, "_call_with_retry",
                        lambda fn, tool, args: {"error": "export_exceeds_time_budget",
                                                "hint": "shard by date",
                                                "suggested_shards": [{"time_start": "x"}]})
    with pytest.raises(MCPExportBudgetError) as ei:
        c.create_export_job("qdb.stock_minutes", time_start="2026-09-02T00:00:00")
    assert "export_exceeds_time_budget" in str(ei.value)
    assert ei.value.suggested_shards == [{"time_start": "x"}]


def test_get_manifest_failed_status(monkeypatch):
    from quantstudio.pipeline.mcp.client import MCPClient
    from quantstudio.pipeline.mcp.errors import MCPProtocolError
    c = object.__new__(MCPClient)
    monkeypatch.setattr(c, "_call_with_retry",
                        lambda fn, tool, args: {"status": "failed", "error": "oom"})
    with pytest.raises(MCPProtocolError, match="failed"):
        c.get_manifest("j1")


def test_get_manifest_running_status(monkeypatch):
    from quantstudio.pipeline.mcp.client import MCPClient
    from quantstudio.pipeline.mcp.errors import MCPProtocolError
    c = object.__new__(MCPClient)
    monkeypatch.setattr(c, "_call_with_retry",
                        lambda fn, tool, args: {"status": "running"})
    with pytest.raises(MCPProtocolError, match="running"):
        c.get_manifest("j1")


def test_get_manifest_ready_passthrough(monkeypatch):
    """status=ready 正常消费（回归）。"""
    from quantstudio.pipeline.mcp.client import MCPClient
    c = object.__new__(MCPClient)
    monkeypatch.setattr(c, "_call_with_retry",
                        lambda fn, tool, args: {"status": "ready", "job_id": "j1",
                                                "total_rows": 1256815, "shards": []})
    m = c.get_manifest("j1")
    assert m.total_rows == 1256815


# ---------- 客户反馈包 2026-09-23 问题1：async_mode 透传与轮询 ----------

def test_create_export_job_async_mode_passthrough(monkeypatch):
    """async_mode=True → 请求带 async_mode，且不写 job 缓存（异步作业不可复用）。"""
    import threading
    from quantstudio.pipeline.mcp.client import MCPClient
    seen = {}

    def _fake(fn, tool, args):
        seen.update(args)
        return {"job_id": "j_async_1", "status": "running"}

    c = object.__new__(MCPClient)
    c._lock = threading.Lock()
    c._job_cache = {}
    monkeypatch.setattr(c, "_call_with_retry", _fake)
    ref = c.create_export_job("qdb.stock_minutes", time_start="2026-09-08T00:00:00",
                              time_end="2026-09-10T00:00:00", row_limit=5_000_000,
                              async_mode=True)
    assert ref == "j_async_1"
    assert seen["async_mode"] is True
    assert c._job_cache == {}


def test_create_export_job_sync_mode_no_async_key(monkeypatch):
    """async_mode 默认 False → 请求**不含** async_mode 键（同步路径零影响，回归）。"""
    import threading
    from quantstudio.pipeline.mcp.client import MCPClient
    seen = {}

    def _fake(fn, tool, args):
        seen.update(args)
        return {"manifest_ref": "j_sync_1"}

    c = object.__new__(MCPClient)
    c._lock = threading.Lock()
    c._job_cache = {}
    monkeypatch.setattr(c, "_call_with_retry", _fake)
    ref = c.create_export_job("qdb.stock_minutes", time_start="2026-09-08T00:00:00")
    assert ref == "j_sync_1"
    assert "async_mode" not in seen
    assert c._job_cache  # 同步路径仍写缓存（既有幂等行为不变）


def test_get_manifest_await_ready_polls_until_ready(monkeypatch):
    """await_ready=True：running → 轮询到 ready（异步作业消费路径）。"""
    from quantstudio.pipeline.mcp.client import MCPClient
    calls = {"n": 0}

    def _fake(fn, tool, args):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"job_id": "j1", "status": "running"}
        return {"status": "ready", "job_id": "j1", "total_rows": 7, "shards": []}

    c = object.__new__(MCPClient)
    monkeypatch.setattr(c, "_call_with_retry", _fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    m = c.get_manifest("j1", await_ready=True, poll_interval_sec=0.01,
                       poll_timeout_sec=5)
    assert m.total_rows == 7
    assert calls["n"] == 3


def test_get_manifest_await_ready_timeout_raises(monkeypatch):
    """await_ready=True 且超时 → 抛错，**不静默返回半成品**。"""
    from quantstudio.pipeline.mcp.client import MCPClient
    from quantstudio.pipeline.mcp.errors import MCPProtocolError
    c = object.__new__(MCPClient)
    monkeypatch.setattr(c, "_call_with_retry",
                        lambda fn, tool, args: {"job_id": "j1", "status": "running"})
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(MCPProtocolError, match="轮询超时"):
        c.get_manifest("j1", await_ready=True, poll_interval_sec=0.01,
                       poll_timeout_sec=0)


def test_get_manifest_running_without_await_ready_still_raises(monkeypatch):
    """await_ready 默认 False → running 仍抛错（既有防线不被削弱，回归）。"""
    from quantstudio.pipeline.mcp.client import MCPClient
    from quantstudio.pipeline.mcp.errors import MCPProtocolError
    c = object.__new__(MCPClient)
    monkeypatch.setattr(c, "_call_with_retry",
                        lambda fn, tool, args: {"status": "running"})
    with pytest.raises(MCPProtocolError, match="running"):
        c.get_manifest("j1")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
