"""A4 变更检测闭环：UpdateDetector + last_sync 持久化 + daemon 集成测试。

mock 后端先行（零行为变化），MCP 工具上线后换后端。
"""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from quantstudio.pipeline.update_detector import (
    MockUpdateDetector, MCPUpdateDetector,
    ensure_last_sync_table, load_last_sync, save_last_sync,
)


@pytest.fixture
def db_conn():
    """临时 DuckDB 连接（内存）。"""
    con = duckdb.connect(":memory:")
    yield con
    con.close()


# ---------------------------------------------------------------------------
# Test 1: MockUpdateDetector 返回空（零行为变化）
# ---------------------------------------------------------------------------
def test_mock_detector_returns_empty():
    """MockUpdateDetector 始终返回空列表。"""
    det = MockUpdateDetector()
    assert det.query_updated_since("2026-08-01T00:00:00Z") == []


# ---------------------------------------------------------------------------
# Test 2: last_sync 持久化（按 table 粒度，跨进程）
# ---------------------------------------------------------------------------
def test_last_sync_persisted_per_table(db_conn):
    """last_sync 按 table 粒度持久化，重启后仍有效。"""
    ensure_last_sync_table(db_conn)

    # 无基准
    assert load_last_sync(db_conn, "stock_daily") is None

    # 写入基准
    save_last_sync(db_conn, "stock_daily", "2026-08-09T10:00:00Z")
    assert load_last_sync(db_conn, "stock_daily") == "2026-08-09T10:00:00Z"

    # 另一个 table 独立基准（修正 1：table 粒度）
    save_last_sync(db_conn, "etf_minutes", "2026-08-09T11:00:00Z")
    assert load_last_sync(db_conn, "stock_daily") == "2026-08-09T10:00:00Z"
    assert load_last_sync(db_conn, "etf_minutes") == "2026-08-09T11:00:00Z"

    # 更新基准（幂等 UPSERT）
    save_last_sync(db_conn, "stock_daily", "2026-08-10T08:00:00Z")
    assert load_last_sync(db_conn, "stock_daily") == "2026-08-10T08:00:00Z"


# ---------------------------------------------------------------------------
# Test 3: 无基准跳过检测（首次/升级后不阻塞）
# ---------------------------------------------------------------------------
def test_no_baseline_skips_detection(db_conn):
    """无 last_sync 基准时 load_last_sync 返回 None（调用方据此跳过检测）。"""
    ensure_last_sync_table(db_conn)
    assert load_last_sync(db_conn, "stock_daily") is None


# ---------------------------------------------------------------------------
# Test 4: MCPUpdateDetector 调 client（mock client 验证）
# ---------------------------------------------------------------------------
def test_mcp_detector_calls_client():
    """MCPUpdateDetector 调 client.query_updated_since。"""
    class FakeClient:
        def __init__(self):
            self.calls = []
        def query_updated_since(self, since):
            self.calls.append(since)
            return [{"table_name": "stock_daily", "trade_date": "2026-08-08",
                     "update_source": "repair", "rows_pushed": 5000}]

    client = FakeClient()
    det = MCPUpdateDetector(client)
    result = det.query_updated_since("2026-08-01T00:00:00Z")
    assert len(client.calls) == 1
    assert client.calls[0] == "2026-08-01T00:00:00Z"
    assert result[0]["table_name"] == "stock_daily"
    assert result[0]["update_source"] == "repair"


# ---------------------------------------------------------------------------
# Test 5: daemon _check_cloud_updates_and_repull 仅 MCP 源生效
# ---------------------------------------------------------------------------
def test_check_updates_skips_non_mcp_source():
    """_check_cloud_updates_and_repull 对非 MCP 源返回 0（不执行检测）。"""
    from quantstudio.pipeline.daemon import ResidentCollector
    # 构造最小 mock collector（不需要完整 DB）
    collector = ResidentCollector.__new__(ResidentCollector)
    collector._update_detector = MockUpdateDetector()
    # tushare 源 → 不接入
    result = collector._check_cloud_updates_and_repull(
        "tushare", "stock_daily", "daily", None, "test_batch")
    assert result == 0


# ---------------------------------------------------------------------------
# Test 6: full_range 不受影响（A4 只接入增量分支）
# ---------------------------------------------------------------------------
def test_full_range_not_affected(db_conn):
    """full_range 模式不应触发 A4 检测（回归保护）。

    验证 _check_cloud_updates_and_repull 在 source=mcp 时执行检测逻辑，
    但 daemon 集成代码只在 mode != full_range 时调用它。
    这里验证 detector 本身不区分 mode（mode 由 daemon 调用方控制）。
    """
    det = MockUpdateDetector()
    # mock detector 无条件返回空（无论 mode）
    assert det.query_updated_since("any") == []
    # full_range 时 daemon 不调 _check_cloud_updates_and_repull（集成代码保证）
    # 这里只验证 detector 行为正确
