# -*- coding: utf-8 -*-
"""CASE-003 方案② 单测：全新源 QFQ 冷启动两道门。

C1 门 1：空 cutover 表 → 预切换哨兵（不抛错）；有记录但 cfg 不匹配 → 仍抛错
C2 门 2：空 bootstrap + 全新源 → 播种 completed + 通过；已有 failed 记录 → 不播种
C3 既有机器三类场景逐位一致：有 active / 无 active 有源记录（staging）/ cfg 不匹配抛错
"""
from __future__ import annotations

import sys
import importlib.util
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from quantstudio.pipeline.qfq_cutover import (  # noqa: E402
    is_fresh_source_for_qfq, resolve_runtime_identity)


@dataclass
class _Cfg:
    price_source: str = "mcp"
    source_generation: str = "mcp-gen1"
    cutover_id: str = "legacy-xtquant-pre-cutover"


SCHEMA = """
CREATE TABLE qfq_source_cutover (
    cutover_id VARCHAR PRIMARY KEY, price_source VARCHAR, source_generation VARCHAR,
    cutover_time TIMESTAMP, price_snapshot_version VARCHAR,
    factor_snapshot_version VARCHAR, baseline_version VARCHAR,
    schema_version VARCHAR, config_hash VARCHAR, aux_db_path VARCHAR,
    status VARCHAR, evidence_path VARCHAR,
    created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE TABLE qfq_active_cutover (
    price_source VARCHAR PRIMARY KEY, cutover_id VARCHAR, activated_at TIMESTAMP);
CREATE TABLE qfq_bootstrap_item (
    bootstrap_run_id VARCHAR, security_id VARCHAR, status VARCHAR,
    approved BOOLEAN, approved_reason VARCHAR, error_detail VARCHAR,
    updated_at TIMESTAMP);
CREATE TABLE qfq_bootstrap_run (
    bootstrap_run_id VARCHAR PRIMARY KEY, asset_type VARCHAR, params VARCHAR,
    resume_cursor VARCHAR, total_count BIGINT, completed_count BIGINT,
    blocked_count BIGINT, failed_count BIGINT, status VARCHAR,
    schema_version VARCHAR, config_hash VARCHAR, baseline_version VARCHAR,
    price_source VARCHAR, source_generation VARCHAR, cutover_id VARCHAR,
    started_at TIMESTAMP, updated_at TIMESTAMP);
"""


def _db():
    con = duckdb.connect()
    con.execute(SCHEMA)
    return con


# ---------- 谓词 ----------

def test_predicate_fresh_source():
    con = _db()
    assert is_fresh_source_for_qfq(con, "mcp") is True  # 零记录


def test_predicate_non_fresh_source():
    con = _db()
    con.execute("INSERT INTO qfq_source_cutover VALUES "
                "('c1','mcp','mcp-gen1',now(),NULL,NULL,'b1','s1',NULL,NULL,"
                "'baseline_building',NULL,now(),now())")
    assert is_fresh_source_for_qfq(con, "mcp") is False


def test_predicate_source_granularity():
    """源粒度语义：机器有 A 源历史，B 源零记录 → B 源全新（审计补强 1）。"""
    con = _db()
    con.execute("INSERT INTO qfq_source_cutover VALUES "
                "('c-xt','xtquant','xtquant-legacy',now(),NULL,NULL,'b','s',NULL,NULL,"
                "'active',NULL,now(),now())")
    assert is_fresh_source_for_qfq(con, "xtquant") is False
    assert is_fresh_source_for_qfq(con, "mcp") is True


# ---------- C1 门 1 ----------

def test_gate1_fresh_source_returns_sentinel():
    """空 cutover 表 → 预切换哨兵身份，不再抛 CutoverError（客户报障场景）。"""
    con = _db()
    ident = resolve_runtime_identity(con, _Cfg())
    assert ident["price_source"] == "mcp"  # 真实配置值保留
    assert ident["source_generation"] == "xtquant-legacy"  # 哨兵
    assert ident["cutover_id"] == "legacy-xtquant-pre-cutover"


def test_gate1_staging_path_unchanged():
    """无 active 但有匹配 staging 记录 → 原逻辑返回该记录（既有场景一致）。"""
    con = _db()
    con.execute("INSERT INTO qfq_source_cutover VALUES "
                "('legacy-xtquant-pre-cutover','mcp','mcp-gen1',now(),NULL,NULL,"
                "'b1','s1',NULL,NULL,'baseline_building',NULL,now(),now())")
    ident = resolve_runtime_identity(con, _Cfg(), allow_prepared=False)
    assert ident["source_generation"] == "mcp-gen1"
    assert ident["cutover_id"] == "legacy-xtquant-pre-cutover"


def test_gate1_mismatched_cfg_still_fails():
    """有记录但 cfg 不匹配（cutover_id 找不到）→ 仍 fail-closed（保护保留）。"""
    from quantstudio.pipeline.qfq_cutover import CutoverError
    con = _db()
    con.execute("INSERT INTO qfq_source_cutover VALUES "
                "('some-other-id','mcp','mcp-gen1',now(),NULL,NULL,'b1','s1',NULL,NULL,"
                "'baseline_building',NULL,now(),now())")
    with pytest.raises(CutoverError, match="staging cutover"):
        resolve_runtime_identity(con, _Cfg())


def test_gate1_active_path_unchanged():
    """有 active 记录 → 原逻辑优先（既有机器场景一致）。"""
    con = _db()
    con.execute("INSERT INTO qfq_source_cutover VALUES "
                "('c-act','mcp','mcp-gen1',now(),NULL,NULL,'b','s',NULL,'p',"
                "'active',NULL,now(),now())")
    con.execute("INSERT INTO qfq_active_cutover VALUES ('mcp','c-act',now())")
    cfg = _Cfg(cutover_id="c-act")
    ident = resolve_runtime_identity(con, cfg)
    assert ident["cutover_id"] == "c-act"
    assert ident["source_generation"] == "mcp-gen1"


def test_gate1_legacy_generation_falls_through():
    """source_generation=xtquant-legacy → 直落哨兵（原路径，不受影响）。"""
    con = _db()
    cfg = _Cfg(source_generation="xtquant-legacy")
    ident = resolve_runtime_identity(con, cfg)
    assert ident["source_generation"] == "xtquant-legacy"


# ---------- C2 门 2（播种）----------

def _orch_with_conn(monkeypatch=None):
    """构造最小 orchestrator（绕过重初始化），绑定 _ident 与播种方法。"""
    from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator
    orch = object.__new__(QFQResidentOrchestrator)
    orch._ident = {"price_source": "mcp",
                   "source_generation": "xtquant-legacy",
                   "cutover_id": "legacy-xtquant-pre-cutover"}

    class _CfgObj:
        config_hash = None
        detector_baseline_version = None
    orch.cfg = _CfgObj()
    return orch, None


def test_gate2_seed_on_fresh_source():
    con = _db()
    orch, _ = _orch_with_conn()
    assert orch._seed_fresh_deploy_bootstrap(con) is True
    row = con.execute("SELECT status, params FROM qfq_bootstrap_run").fetchone()
    assert row[0] == "completed"
    assert "fresh-deploy-auto" in row[1]
    # 幂等：再次播种 → False（不重复）
    assert orch._seed_fresh_deploy_bootstrap(con) is False


def test_gate2_no_seed_when_records_exist():
    """已有 failed bootstrap 记录 → 不播种（防掩盖真实失败——审计 C2 要点）。"""
    con = _db()
    con.execute("INSERT INTO qfq_bootstrap_run VALUES "
                "('run1',NULL,NULL,NULL,0,0,0,1,'failed','s',NULL,NULL,"
                "'mcp','xtquant-legacy','legacy-xtquant-pre-cutover',now(),now())")
    orch, _ = _orch_with_conn()
    assert orch._seed_fresh_deploy_bootstrap(con) is False
    n = con.execute("SELECT count(*) FROM qfq_bootstrap_run").fetchone()[0]
    assert n == 1  # 未新增


def test_gate2_bootstrap_completed_after_seed():
    """播种后 bootstrap_completed 判定通过（版本字段 None 容错路径）。"""
    con = _db()
    orch, _ = _orch_with_conn()
    orch._seed_fresh_deploy_bootstrap(con)
    # schema_version 用 SCHEMA_VERSION 写入——completed 判定的版本校验应通过
    assert orch.bootstrap_completed(con) is True


def test_gate2_bootstrap_completed_false_when_no_seed():
    """零记录且不播种 → completed=False（原门槛语义）。"""
    con = _db()
    orch, _ = _orch_with_conn()
    assert orch.bootstrap_completed(con) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
