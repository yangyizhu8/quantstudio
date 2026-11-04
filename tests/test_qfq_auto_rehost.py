# -*- coding: utf-8 -*-
"""D5-e 自动换代自愈验收（客户零操作）。

场景：客户机（macOS）上的 qfq_source_cutover.active 记录里是我方开发机（Windows）的
绝对路径 ⇒ 路由解析拼出畸形路径 ⇒ 三张核心表取数前崩溃。
期望：程序启动即自动换代到本机路径（旧记录 superseded），**客户无需任何操作**。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline import qfq_cutover_tools as CT


# 夹具必须取**对当前宿主真正异平台**的路径：
#   Windows 宿主 ⇒ POSIX 绝对路径（异平台）；POSIX 宿主 ⇒ Windows 绝对路径（异平台）。
# 2026-11-04 实测教训：写死 Windows 路径时，它在 Windows 开发机上**本机就存在**，
# 会被判成"本机路径已可用"而跳过 ⇒ 用例看起来失败，实则夹具失真。
FOREIGN = ("/Users/apple/Desktop/quantstudio-plus/data/qfq_aux_mcp_gen1.db"
           if os.name == "nt" else
           r"D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db")


def _env(tmp_path: Path, *, with_local_aux: bool = True) -> tuple[Path, Path]:
    """造 profile 目录（qfq_aux_paths.json）+ 本机候选 aux 库。"""
    cfg = tmp_path / "config" / "profiles" / "mcp_only"
    cfg.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (cfg / "qfq_aux_paths.json").write_text(json.dumps({
        "released": False,
        "default": "../../../data/qfq_aux.db",
        "generations": {"mcp-gen1": "../../../data/qfq_aux_mcp_gen1.db"},
    }), encoding="utf-8")
    if with_local_aux:
        (data / "qfq_aux_mcp_gen1.db").write_bytes(b"local-aux")
    return cfg, data


def _db(data: Path, *, aux: str = FOREIGN, status: str = "active") -> Path:
    db = data / "quantstudio.db"
    con = duckdb.connect(str(db))
    try:
        con.execute("""CREATE TABLE qfq_source_cutover (
            cutover_id VARCHAR NOT NULL, price_source VARCHAR NOT NULL,
            source_generation VARCHAR NOT NULL, cutover_time TIMESTAMP NOT NULL,
            price_snapshot_version VARCHAR, factor_snapshot_version VARCHAR,
            baseline_version VARCHAR NOT NULL, schema_version VARCHAR NOT NULL,
            config_hash VARCHAR, aux_db_path VARCHAR, status VARCHAR NOT NULL,
            evidence_path VARCHAR, created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL, PRIMARY KEY (cutover_id))""")
        con.execute("""CREATE TABLE qfq_active_cutover (
            price_source VARCHAR NOT NULL, cutover_id VARCHAR NOT NULL,
            activated_at TIMESTAMP NOT NULL, PRIMARY KEY (price_source))""")
        con.execute(
            "INSERT INTO qfq_source_cutover VALUES ('old_1','mcp','mcp-gen1',"
            "TIMESTAMP '2026-08-07 00:00:00',NULL,NULL,'b6','v1',NULL,?,'active',NULL,"
            "TIMESTAMP '2026-08-07 00:00:00',TIMESTAMP '2026-08-07 00:00:00')", [aux])
        con.execute(
            "INSERT INTO qfq_active_cutover VALUES ('mcp','old_1',"
            "TIMESTAMP '2026-08-07 00:00:00')")
    finally:
        con.close()
    return db


def _rows(db: Path):
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT cutover_id, status, aux_db_path FROM qfq_source_cutover "
            "ORDER BY cutover_id").fetchall()
        act = con.execute("SELECT price_source, cutover_id FROM qfq_active_cutover").fetchall()
    finally:
        con.close()
    return rows, act


def test_auto_rehost_replaces_foreign_record(tmp_path, monkeypatch):
    monkeypatch.delenv("QS_QFQ_AUTO_REHOST", raising=False)
    cfg, data = _env(tmp_path)
    db = _db(data)
    audit = data / "logs" / "qfq_cutover_repair_audit.log"

    con = duckdb.connect(str(db))
    try:
        rep = CT.ensure_local_cutover(con, config_dir=cfg, audit_path=audit)
    finally:
        con.close()

    assert rep["repaired"] == ["old_1"], rep
    rows, act = _rows(db)
    by = {r[0]: r for r in rows}
    assert by["old_1"][1] == "superseded", "旧记录必须保留为 superseded（只换代不改行）"
    new_id = act[0][1]
    assert by[new_id][1] == "active"
    assert by[new_id][2].endswith("qfq_aux_mcp_gen1.db")
    assert "D:" not in by[new_id][2], "新记录不得再含 Windows 绝对路径"
    assert audit.exists()
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["action"] == "auto-rehost" and entry["result"] == "ok"
    assert "authorization" in entry


def test_auto_rehost_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("QS_QFQ_AUTO_REHOST", raising=False)
    cfg, data = _env(tmp_path)
    db = _db(data)
    con = duckdb.connect(str(db))
    try:
        CT.ensure_local_cutover(con, config_dir=cfg)
        rep2 = CT.ensure_local_cutover(con, config_dir=cfg)
    finally:
        con.close()
    assert rep2["repaired"] == [], rep2
    assert rep2["skipped"], rep2


def test_no_local_candidate_means_no_change(tmp_path, monkeypatch):
    """本机没有候选（辅助库真缺失）⇒ 只报告，不猜路径、不改记录。"""
    monkeypatch.delenv("QS_QFQ_AUTO_REHOST", raising=False)
    cfg, data = _env(tmp_path, with_local_aux=False)
    db = _db(data)
    con = duckdb.connect(str(db))
    try:
        rep = CT.ensure_local_cutover(con, config_dir=cfg)
    finally:
        con.close()
    assert rep["repaired"] == []
    assert rep["needs_manual"], rep
    rows, act = _rows(db)
    assert act[0][1] == "old_1", "未换代时 active 指针不得变"


def test_env_switch_disables_autorehost(tmp_path, monkeypatch):
    monkeypatch.setenv("QS_QFQ_AUTO_REHOST", "0")
    cfg, data = _env(tmp_path)
    db = _db(data)
    con = duckdb.connect(str(db))
    try:
        rep = CT.ensure_local_cutover(con, config_dir=cfg)
    finally:
        con.close()
    assert rep["repaired"] == []
    assert "disabled by env" in " ".join(rep["skipped"])


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.delenv("QS_QFQ_AUTO_REHOST", raising=False)
    cfg, data = _env(tmp_path)
    db = _db(data)
    con = duckdb.connect(str(db))
    try:
        rep = CT.ensure_local_cutover(con, config_dir=cfg, dry_run=True)
    finally:
        con.close()
    assert rep["repaired"] == []
    assert rep["details"], rep
    rows, act = _rows(db)
    assert act[0][1] == "old_1"
