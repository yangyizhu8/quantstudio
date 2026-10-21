"""FP-3 接线缺陷回归（2026-10-21 06:45 受控窗复核暴露）。

缺陷：`daemon.qfq_begin_cycle` 在调用 `reap_stale_cycles` **之前**未解析动态世代，
此时 `orch._ident` 仍是 pre_cutover 默认哨兵（xtquant-legacy/legacy-xtquant-pre-cutover），
与库里 mcp-gen1/b6_formal 的周期行不匹配 → 回收零命中且**无任何日志**
（10-20 受控窗实证：`cyc_e9e72441d15e` 未被回收，全日志 `FP-3` 计数=0）。

本文件锁定两条契约：
  B1  世代未解析（legacy 默认）时，reap 必须零命中（这正是缺陷的可复现形态）；
  B2  daemon 侧必须在 reap 前解析世代（源码顺序守卫：`_resolve_dynamic_identity`
      出现在 `reap_stale_cycles` 之前），否则缺陷会静默复发。
"""

from __future__ import annotations

import datetime as dt
import inspect

import duckdb
import pytest

from quantstudio.pipeline.qfq_resident_orchestrator import reap_stale_cycles

MCP_IDENT = {"price_source": "mcp", "source_generation": "mcp-gen1",
             "cutover_id": "b6_formal_20260807_v2"}
LEGACY_IDENT = {"price_source": "mcp", "source_generation": "xtquant-legacy",
                "cutover_id": "legacy-xtquant-pre-cutover"}
NOW = dt.datetime.now()


@pytest.fixture()
def con(tmp_path):
    c = duckdb.connect(str(tmp_path / "t.db"))
    c.execute(
        "CREATE TABLE qfq_cycle_run (cycle_id VARCHAR, status VARCHAR, phase VARCHAR, "
        "started_at TIMESTAMP, updated_at TIMESTAMP, finished_at TIMESTAMP, error VARCHAR, "
        "price_source VARCHAR, source_generation VARCHAR, cutover_id VARCHAR)")
    yield c
    c.close()


def _stale_cycle(con, ident=MCP_IDENT):
    con.execute(
        "INSERT INTO qfq_cycle_run VALUES ('cyc_stale','started','applying',?,?,NULL,NULL,?,?,?)",
        [NOW - dt.timedelta(days=2), NOW - dt.timedelta(hours=5),
         ident["price_source"], ident["source_generation"], ident["cutover_id"]])


def test_b1_legacy_ident_finds_nothing(con):
    """B1：世代未解析（legacy 默认）→ 零命中（缺陷可复现形态，必须保持为"行为定义"）。"""
    _stale_cycle(con, MCP_IDENT)
    got = reap_stale_cycles(con, ident=LEGACY_IDENT,
                            process_start_ms=int(NOW.timestamp() * 1000))
    assert got == [], "legacy ident 不得回收 mcp-gen1 周期（否则即跨世代误杀）"


def test_b1_mcp_ident_reaps_same_cycle(con):
    """B1 对照：解析到 mcp-gen1 后同一行必须命中（证明缺陷只是"没解析世代"）。"""
    _stale_cycle(con, MCP_IDENT)
    got = reap_stale_cycles(con, ident=MCP_IDENT,
                            process_start_ms=int(NOW.timestamp() * 1000))
    assert [x["cycle_id"] for x in got] == ["cyc_stale"]


def test_b2_daemon_resolves_identity_before_reaping():
    """B2：源码顺序守卫——解析世代必须先于 reap，否则静默零命中。"""
    from quantstudio.pipeline import daemon as dm
    src = inspect.getsource(dm.ResidentCollector.qfq_begin_cycle)
    i_resolve = src.find("_resolve_dynamic_identity")
    i_reap = src.find("reap_stale_cycles")
    assert i_resolve != -1, "qfq_begin_cycle 必须解析动态世代"
    assert i_reap != -1, "qfq_begin_cycle 必须调用 reap_stale_cycles"
    assert i_resolve < i_reap, "必须在 reap 之前解析世代（2026-10-21 缺陷）"
