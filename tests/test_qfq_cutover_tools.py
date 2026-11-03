# -*- coding: utf-8 -*-
"""N-子8 验收 V8-1..V8-5（S1 只读诊断 / S2 换代校正）。

判据（节点⑳ ②审裁定 + 方案 §9/§10）：
- V8-1 S1 **零写入**（git status + mtime 双向核）；
- V8-2 S2 缺备份 ⇒ 拒跑；
- V8-3 S2 CAS 不符 ⇒ 拒跑且**库零变化**；
- V8-4 S2 正常 ⇒ 新记录 active / 旧记录 superseded / 审计行落盘；
- V8-5 回滚：备份还原后记录状态与执行前逐位一致。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline import qfq_cutover_tools as CT

# 取"对当前宿主真正异平台"的路径：Windows 宿主 ⇒ POSIX 绝对；POSIX 宿主 ⇒ Windows 盘符绝对
FOREIGN_AUX = ("/Users/apple/data/qfq_aux_mcp_gen1.db" if os.name == "nt"
               else r"D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db")


def _build_env(tmp_path: Path, *, aux_exists: bool = True) -> tuple[Path, Path]:
    """构造项目结构：data/（含主库与 cutover 表）、config/profiles/mcp_only/qfq_aux_paths.json。"""
    data = tmp_path / "data"
    cfg_dir = tmp_path / "config" / "profiles" / "mcp_only"
    data.mkdir(parents=True, exist_ok=True)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "qfq_aux_paths.json").write_text(json.dumps({
        "released": False,
        "default": "../../../data/qfq_aux.db",
        "generations": {
            "xtquant-legacy": "../../../data/qfq_aux.db",
            "mcp-gen1": "../../../data/qfq_aux_mcp_gen1.db",
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    aux = data / "qfq_aux_mcp_gen1.db"
    if aux_exists:
        aux.write_bytes(b"aux")
    db = data / "quantstudio.db"
    con = duckdb.connect(str(db))
    try:
        con.execute("""
            CREATE TABLE qfq_source_cutover (
                cutover_id VARCHAR NOT NULL, price_source VARCHAR NOT NULL,
                source_generation VARCHAR NOT NULL, cutover_time TIMESTAMP NOT NULL,
                price_snapshot_version VARCHAR, factor_snapshot_version VARCHAR,
                baseline_version VARCHAR NOT NULL, schema_version VARCHAR NOT NULL,
                config_hash VARCHAR, aux_db_path VARCHAR, status VARCHAR NOT NULL,
                evidence_path VARCHAR, created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL, PRIMARY KEY (cutover_id))
        """)
        con.execute("""
            CREATE TABLE qfq_active_cutover (
                price_source VARCHAR NOT NULL, cutover_id VARCHAR NOT NULL,
                activated_at TIMESTAMP NOT NULL, PRIMARY KEY (price_source))
        """)
        con.execute(
            "INSERT INTO qfq_source_cutover VALUES ('old_1','mcp','mcp-gen1',"
            "TIMESTAMP '2026-08-07 00:00:00', NULL, NULL, 'b6', 'v1', NULL, ?, 'active',"
            "NULL, TIMESTAMP '2026-08-07 00:00:00', TIMESTAMP '2026-08-07 00:00:00')",
            [FOREIGN_AUX])
        con.execute(
            "INSERT INTO qfq_active_cutover VALUES ('mcp','old_1',TIMESTAMP '2026-08-07 00:00:00')")
    finally:
        con.close()
    return data, tmp_path / "config"


def _records(db: Path) -> list[dict]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT cutover_id, status, aux_db_path FROM qfq_source_cutover "
            "ORDER BY cutover_id").fetchall()
        return [{"cutover_id": r[0], "status": r[1], "aux_db_path": r[2]} for r in rows]
    finally:
        con.close()


def _active(db: Path):
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute("SELECT price_source, cutover_id FROM qfq_active_cutover").fetchall()
        return rows
    finally:
        con.close()


# ── V8-1：S1 零写入 ──────────────────────────────────────────────────────────
def test_v8_1_diagnose_is_read_only(tmp_path):
    data, cfg = _build_env(tmp_path)
    before_files = {p: p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file()}
    before_db = _records(data / "quantstudio.db")
    before_active = _active(data / "quantstudio.db")

    report = CT.diagnose(data_root=data, config_root=cfg)

    after_files = {p: p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file()}
    assert report["writes"] == 0
    assert after_files == before_files, "S1 不得新增/修改任何文件"
    assert _records(data / "quantstudio.db") == before_db, "S1 不得改库"
    assert _active(data / "quantstudio.db") == before_active
    # 判定：记录值是异平台路径 ⇒ 不可迁移；建议值来自本地 qfq_aux_paths.json
    rec = report["records"][0]
    assert rec["cutover_id"] == "old_1"
    assert rec["migratable"] is False
    assert any("异平台" in r for r in rec["reasons"])
    assert rec["suggested"]["aux_db_path"].endswith("qfq_aux_mcp_gen1.db")
    assert rec["suggested"]["exists"] is True


# ── V8-2：缺备份 ⇒ 拒跑 ─────────────────────────────────────────────────────
def test_v8_2_missing_backup_refuses(tmp_path):
    data, cfg = _build_env(tmp_path)
    # 制造"备份不可能"的确定性场景：data/backup 被同名**文件**占位 ⇒ mkdir 必失败
    blocked = data / "backup"
    blocked.write_bytes(b"not-a-dir")
    with pytest.raises(CT.CutoverToolError) as ei:
        CT.apply_rehost(
            data_root=data, config_root=cfg, cutover_id="old_1",
            expected_aux_db_path=FOREIGN_AUX,
            new_aux_db_path=str(data / "qfq_aux_mcp_gen1.db"),
            authorization="客户邮件确认 2026-11-03")
    assert "备份" in str(ei.value)
    # 库零变化
    assert _records(data / "quantstudio.db") == [
        {"cutover_id": "old_1", "status": "active", "aux_db_path": FOREIGN_AUX}]
    assert _active(data / "quantstudio.db") == [("mcp", "old_1")]


# ── V8-3：CAS 不符 ⇒ 拒跑且库零变化 ────────────────────────────────────────
def test_v8_3_cas_mismatch_refuses(tmp_path):
    data, cfg = _build_env(tmp_path)
    with pytest.raises(CT.CutoverToolError) as ei:
        CT.apply_rehost(
            data_root=data, config_root=cfg, cutover_id="old_1",
            expected_aux_db_path="D:\\somebody\\else\\qfq_aux_mcp_gen1.db",
            new_aux_db_path=str(data / "qfq_aux_mcp_gen1.db"),
            authorization="客户邮件确认 2026-11-03")
    assert "CAS" in str(ei.value)
    assert _records(data / "quantstudio.db") == [
        {"cutover_id": "old_1", "status": "active", "aux_db_path": FOREIGN_AUX}]
    assert _active(data / "quantstudio.db") == [("mcp", "old_1")]
    # 审计行应记录「拒绝」且含客户授权依据
    log = data / "logs" / CT.AUDIT_LOG_NAME
    assert log.exists()
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["result"] == "rejected"
    assert entry["authorization"] == "客户邮件确认 2026-11-03"


def test_v8_3b_missing_authorization_refuses(tmp_path):
    data, cfg = _build_env(tmp_path)
    with pytest.raises(CT.CutoverToolError) as ei:
        CT.apply_rehost(
            data_root=data, config_root=cfg, cutover_id="old_1",
            expected_aux_db_path=FOREIGN_AUX,
            new_aux_db_path=str(data / "qfq_aux_mcp_gen1.db"),
            authorization="   ")
    assert "授权依据" in str(ei.value)


# ── V8-4：正常换代 ──────────────────────────────────────────────────────────
def test_v8_4_apply_rehosts_record(tmp_path):
    data, cfg = _build_env(tmp_path)
    new_aux = str(data / "qfq_aux_mcp_gen1.db")
    result = CT.apply_rehost(
        data_root=data, config_root=cfg, cutover_id="old_1",
        expected_aux_db_path=FOREIGN_AUX, new_aux_db_path=new_aux,
        authorization="客户邮件确认 2026-11-03")
    assert result["ok"] is True
    recs = {r["cutover_id"]: r for r in _records(data / "quantstudio.db")}
    assert recs["old_1"]["status"] == "superseded"
    new_id = result["new_cutover_id"]
    assert recs[new_id]["status"] == "active"
    assert recs[new_id]["aux_db_path"] == new_aux
    assert _active(data / "quantstudio.db") == [("mcp", new_id)]
    # 备份三档：b 主 + c 必做（a 取决于余量）
    bk = result["backups"]
    assert "b_full_copy" in bk and "c_record_snapshot" in bk and "c_record_copy" in bk
    assert Path(bk["b_full_copy"]["path"]).exists()
    assert Path(bk["c_record_snapshot"]["path"]).exists()
    assert Path(bk["c_record_copy"]["path"]).exists()
    # 审计行含客户授权依据
    log = data / "logs" / CT.AUDIT_LOG_NAME
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["result"] == "ok"
    assert entry["authorization"] == "客户邮件确认 2026-11-03"
    assert entry["old_cutover_id"] == "old_1"


# ── V8-5：回滚（备份还原后逐位一致）────────────────────────────────────────
def test_v8_5_rollback_restores_exact_state(tmp_path):
    data, cfg = _build_env(tmp_path)
    before = _records(data / "quantstudio.db")
    before_active = _active(data / "quantstudio.db")
    result = CT.apply_rehost(
        data_root=data, config_root=cfg, cutover_id="old_1",
        expected_aux_db_path=FOREIGN_AUX,
        new_aux_db_path=str(data / "qfq_aux_mcp_gen1.db"),
        authorization="客户邮件确认 2026-11-03")
    assert _records(data / "quantstudio.db") != before
    # 回滚：还原 b 档（VACUUM INTO 快照）覆盖主库
    vac = Path(result["backups"]["b_full_copy"]["path"])
    shutil.copy2(vac, data / "quantstudio.db")
    assert _records(data / "quantstudio.db") == before
    assert _active(data / "quantstudio.db") == before_active


# ── 前置余量检查（§10.2）：free < need_b ⇒ 拒跑，无 force 通道 ───────────────
def test_space_precheck_refuses_when_insufficient(tmp_path):
    """前置余量闸：free < need_b ⇒ 拒跑且**无 force 通道**（§10.2 两小裁②）。"""
    data, _cfg = _build_env(tmp_path)
    plan = CT.plan_backup(data / "quantstudio.db", data, mode="auto")
    assert plan.need_b > 0
    plan.free = plan.need_b - 1                      # 模拟余量不足
    with pytest.raises(CT.CutoverToolError) as ei:
        CT._ensure_capacity(plan)
    msg = str(ei.value)
    assert "余量不足" in msg
    assert "force" in msg.lower()
    assert not hasattr(CT, "force_insufficient_space")
