# -*- coding: utf-8 -*-
"""空间诊断 / unlock 自助回收 / COPY 心跳（2026-11-01 方案 v2 的 T1–T9）。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import quantstudio.delivery.export_kernel as E
from quantstudio.delivery import cli, probe as P
from quantstudio.delivery.constants import Exit
from quantstudio.pipeline import snapshot_lock as SL
from tests.fixtures.delivery import Env

_REAL_ACQUIRE = E._acquire_lock
_REAL_RELEASE = E._release_lock


def _lock_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "lock"
    d.mkdir(exist_ok=True)
    monkeypatch.setenv("QS_WRITE_LOCK_DIR", str(d))
    # conftest 会在会话级钉住审计路径；这里显式改到本用例的锁目录，避免断言看错地方
    monkeypatch.setenv("QS_WRITE_LOCK_AUDIT_LOG", str(d / SL.AUDIT_LOG_NAME))
    return d


def _dead_lock(lock_dir: Path, task: str = "delivery_export") -> Path:
    path = lock_dir / ".write_lock"
    path.write_text(json.dumps({
        "v": 2, "pid": 999_999, "task_id": task,
        "heartbeat": time.time() - 7200, "host": SL._hostname(),
        "pid_create_time": 1.0, "acquired_at": time.time() - 7200,
        "token": "deadbeef" * 4,
    }), encoding="utf-8")
    return path


# ── T1 / T2 / T3：unlock 三态 ───────────────────────────────────────────────
def test_t1_unlock_dry_run_does_not_delete(tmp_path, monkeypatch, capsys):
    lock_dir = _lock_dir(tmp_path, monkeypatch)
    path = _dead_lock(lock_dir)
    assert cli.main(["unlock", "--json"]) == Exit.OK
    assert path.exists(), "dry-run 绝不能删锁"
    doc = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith('{"ok"')][-1]
    assert json.loads(doc)["result"]["reclaimable"] is True


def test_t2_unlock_force_reclaims_dead_lock_with_audit(tmp_path, monkeypatch):
    lock_dir = _lock_dir(tmp_path, monkeypatch)
    path = _dead_lock(lock_dir)
    assert cli.main(["unlock", "--force", "--json"]) == Exit.OK
    assert not path.exists()
    audit = lock_dir / SL.AUDIT_LOG_NAME
    assert audit.exists(), "回收必须留审计行（禁裸删）"


def test_t3_unlock_force_refuses_live_holder(tmp_path, monkeypatch):
    lock_dir = _lock_dir(tmp_path, monkeypatch)
    path = lock_dir / ".write_lock"
    path.write_text(json.dumps({
        "v": 2, "pid": os.getpid(), "task_id": "someone_else",
        "heartbeat": time.time(), "host": SL._hostname(),
        "pid_create_time": SL._pid_create_time(os.getpid()),
        "acquired_at": time.time(), "token": "live" * 8,
    }), encoding="utf-8")
    assert cli.main(["unlock", "--force", "--json"]) == Exit.REFUSED
    assert path.exists(), "活锁一律不得删除"


# ── T4：自愈扩条（自身残留）──────────────────────────────────────────────────
def test_t4_heals_own_pid_residual(tmp_path, monkeypatch):
    lock_dir = _lock_dir(tmp_path, monkeypatch)
    path = lock_dir / ".write_lock"
    path.write_text(json.dumps({
        "v": 2, "pid": os.getpid(), "task_id": "delivery_export",
        "heartbeat": time.time() - 7200, "host": SL._hostname(),
        "pid_create_time": SL._pid_create_time(os.getpid()),
        "acquired_at": time.time() - 7200, "token": "own" * 8,
    }), encoding="utf-8")
    state = P.heals_stale_lock("delivery_export")
    # A-2 定谳：只诊断、不回收（pid 存活时 CAS 复核必然拒绝；硬删会绕过 CAS）
    assert state["held"] is True
    assert state["diagnosis"].get("self_residual") is True, state
    assert "本进程残留" in str(state["diagnosis"].get("detail"))
    assert path.exists(), "诊断路径不得删锁"


# ── T5 / T6：空间拒绝文案（门槛不放宽）───────────────────────────────────────
def test_t5_space_refusal_message_has_breakdown(tmp_path, monkeypatch):
    server = Env(tmp_path / "server", monkeypatch)
    bundle = server.make_package()
    client = server.make_client("update")
    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", client, raising=False)
    monkeypatch.setattr(P, "disk_free_bytes", lambda *a, **k: 1)       # 恒定"空间不足"
    from quantstudio.delivery import import_kernel as I

    monkeypatch.setattr(I, "_acquire_lock", lambda **kw: {"task_id": "t"})
    monkeypatch.setattr(I, "_release_lock", lambda: None)
    monkeypatch.setattr(P, "daemon_running", lambda *a, **k: P.DaemonState(False, {}))
    monkeypatch.setattr(P, "write_lock_holder", lambda *a, **k: None)

    result = I.import_bundle(import_dir=bundle.parent, config_dir=server.config_root, data_root=client)
    assert result.ok is False and result.exit_code == Exit.REFUSED
    text = "\n".join(result.reasons)
    for token in ("还差", "门槛", "可用", "安全余量"):
        assert token in text, f"缺少 {token}：{text}"
    assert "不会删除任何备份" in text or "只读" in text


def test_t6_space_report_lists_real_backup_candidates(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "backup").mkdir(parents=True)
    (root / "backup" / "quantstudio.db").write_bytes(b"x" * (3 << 20))
    rep = P.space_report(need=10 << 30, free=1 << 30, data_root=root)
    assert rep["shortfall"] == (10 << 30) - (1 << 30)
    assert rep["candidates"], "必须列出可清理位置"
    top = rep["candidates"][0]
    assert top["pattern"].startswith("backup")
    assert top["bytes"] >= (3 << 20)
    assert (root / "backup" / "quantstudio.db").exists(), "扫描不得删除任何文件"


# ── T9：COPY 期间心跳 ───────────────────────────────────────────────────────
def test_t9_copy_refreshes_heartbeat(tmp_path, monkeypatch):
    server = Env(tmp_path / "server", monkeypatch)
    _lock_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(E, "_acquire_lock", _REAL_ACQUIRE)
    monkeypatch.setattr(E, "_release_lock", _REAL_RELEASE)

    hits = {"n": 0}
    real_hb = E._heartbeat

    def counting_hb():
        hits["n"] += 1
        return real_hb()

    monkeypatch.setattr(E, "_heartbeat", counting_hb)
    monkeypatch.setattr(E, "_HB_BYTES", 4096)     # 阈值调小 → 任何非空文件都多次触发
    monkeypatch.setattr(E, "CHUNK", 1024)

    result = E.export_bundle(
        export_dir=tmp_path / "out", data_root=server.root,
        config_dir=server.config_root,
    )
    assert (result.bundle_dir / "manifest.json").exists()
    assert hits["n"] >= 2, f"COPY 期间应多次刷心跳，实际 {hits['n']}"
