# -*- coding: utf-8 -*-
"""journal 相位机与崩溃恢复（W3；SC-014 + FR-036 决策表）。全部 tmp_path。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quantstudio.delivery import import_kernel as I
from quantstudio.delivery import journal as J
from quantstudio.delivery import probe as P
from quantstudio.delivery import verify as V
from tests.fixtures.delivery import Env

RUNNER = Path(__file__).resolve().parent / "fixtures" / "delivery" / "crash_runner.py"


# ── 相位机与决策表（纯函数，逐行覆盖）──────────────────────────────────────
def _journal(phase: str, done: int = 0, total: int = 3) -> J.Journal:
    files = [
        J.JournalFile(target_name=f"t{i}.db", tmp_name=f"t{i}.db.incoming", backup_name=f"t{i}.db",
                      state="done" if i < done else "pending")
        for i in range(total)
    ]
    return J.Journal(
        phase=phase, mode="update", data_root="/tmp/x", package_dir="/tmp/p",
        package_manifest_sha256="a" * 64, manifest_schema_version=1, files=files,
    )


def test_phases_cannot_go_backwards():
    journal = _journal("backup_done")
    J.advance(journal, "overwrite_started")
    with pytest.raises(J.JournalError):
        J.advance(journal, "backup_started")


@pytest.mark.parametrize(
    "phase,action,expect_restore,expect_pending,expect_created",
    [
        ("lock_acquired", "continue", 0, 3, 0),
        ("backup_started", "rollback", 0, 0, 1),
        ("backup_done", "continue", 0, 2, 0),
        ("overwrite_started", "rollback", 1, 0, 1),
        ("overwrite_done", "continue", 0, 0, 0),
        ("reverify_started", "rollback", 1, 0, 1),
    ],
)
def test_plan_recovery_table(phase, action, expect_restore, expect_pending, expect_created):
    journal = _journal(phase, done=1)
    journal.created_files = ["new.db"]
    plan = J.plan_recovery(journal, action)
    assert len(plan.restore) == expect_restore
    assert len(plan.pending) == expect_pending
    assert len(plan.delete_created) == expect_created


def test_plan_recovery_refuses_continue_while_rolling_back():
    with pytest.raises(J.JournalError):
        J.plan_recovery(_journal("rolling_back"), "continue")


def test_journal_roundtrip_and_detect_pending(tmp_path):
    journal = _journal("overwrite_started", done=1)
    journal.data_root = str(tmp_path)
    J.save(journal, tmp_path)
    loaded = J.load(tmp_path)
    assert loaded is not None and loaded.phase == "overwrite_started"
    summary = J.detect_pending_journal(tmp_path)
    assert summary.done_items == 1 and summary.total_items == 3
    J.delete(tmp_path)
    assert J.detect_pending_journal(tmp_path) is None


def test_journal_rejects_foreign_data_root(tmp_path):
    journal = _journal("backup_done")
    journal.data_root = str(tmp_path / "other")
    J.save(journal, tmp_path)          # 文件落在 tmp_path，但内容声明的是别处的数据根
    with pytest.raises(J.JournalError):
        J.load(tmp_path)


# ── SC-014：真崩溃 → 二选一恢复（子进程 + os._exit）────────────────────────
def _setup(tmp_path, monkeypatch):
    server = Env(tmp_path / "server", monkeypatch)
    bundle = server.make_package()
    client = server.make_client("update")
    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", client, raising=False)
    monkeypatch.setattr(I, "_acquire_lock", lambda **kw: {"task_id": "test"})
    monkeypatch.setattr(I, "_release_lock", lambda: None)
    monkeypatch.setattr(P, "daemon_running", lambda *a, **k: P.DaemonState(False, {}))
    monkeypatch.setattr(P, "write_lock_holder", lambda *a, **k: None)
    monkeypatch.setattr(P, "disk_free_bytes", lambda *a, **k: 10 ** 12)
    return server, bundle, client


def _crash(client: Path, config_root: Path, bundle: Path, replace_n: int = 1):
    lock_dir = client / "_lock_dir"
    if lock_dir.exists():
        shutil.rmtree(lock_dir, ignore_errors=True)
    lock_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "QUANTSTUDIO_DATA_ROOT": str(client),
            "QS_WRITE_LOCK_DIR": str(lock_dir),          # 强制覆盖：锁目录不得落到真实 data/snapshots
            "QS_DELIVERY_TEST_HOOKS": "1",
            "QS_DELIVERY_TEST_KILL_AFTER_REPLACES": str(replace_n),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        }
    )
    proc = subprocess.run(
        [sys.executable, str(RUNNER), str(client), str(config_root), str(bundle.parent)],
        capture_output=True,
        env=env,
        timeout=300,
    )
    return proc


def test_sc014_crash_then_rollback(tmp_path, monkeypatch):
    server, bundle, client = _setup(tmp_path, monkeypatch)
    schema_before = V._measure_schema_status(client / "quantstudio.db")

    proc = _crash(client, server.config_root, bundle, replace_n=1)
    assert proc.returncode != 0, "注入后必须真崩溃（非正常退出）"
    assert J.journal_path(client).exists(), (
        "崩溃后必须留下 journal（不能静默丢失）",
        proc.returncode,
        proc.stdout.decode("utf-8", "replace")[-800:],
        proc.stderr.decode("utf-8", "replace")[-1500:],
    )
    journal = J.load(client)
    assert journal.phase in ("overwrite_started", "overwrite_done")
    assert any(f.state == "done" for f in journal.files), "应至少有 1 项已完成替换"
    backup_dirs = list(client.glob("backup_import_*"))
    assert len(backup_dirs) == 1, "崩溃前只允许一次备份"

    # 恢复：回滚到导入前
    result = I.recover_journal(action="rollback", data_root=client, config_dir=server.config_root)
    assert result.ok, result.reasons
    assert not J.journal_path(client).exists()
    assert V._measure_schema_status(client / "quantstudio.db") == schema_before
    # 关键断言（审核重点）：不得出现"半新半旧被当原始态再备份一次"
    assert len(list(client.glob("backup_import_*"))) == 1


def test_sc014_crash_then_continue(tmp_path, monkeypatch):
    server, bundle, client = _setup(tmp_path, monkeypatch)
    proc = _crash(client, server.config_root, bundle, replace_n=1)
    assert proc.returncode != 0, proc.stderr.decode("utf-8", "replace")[-1200:]
    assert J.journal_path(client).exists(), (
        "崩溃后缺失 journal",
        proc.returncode,
        proc.stdout.decode("utf-8", "replace")[-600:],
        sorted(p.name for p in client.iterdir()),
    )

    result = I.recover_journal(action="continue", data_root=client, config_dir=server.config_root)
    assert result.ok, result.reasons
    assert not J.journal_path(client).exists()
    assert len(list(client.glob("backup_import_*"))) == 1, "继续完成不得二次备份"
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    from quantstudio.delivery import manifest as M

    for entry in manifest["files"]:
        assert M.sha256_path(client / entry["path"]) == entry["sha256"], f"{entry['path']} 与包不一致"


def test_import_refuses_when_journal_present(tmp_path, monkeypatch):
    server, bundle, client = _setup(tmp_path, monkeypatch)
    journal = J.Journal(
        phase="overwrite_started", mode="update", data_root=str(client),
        package_dir=str(bundle), package_manifest_sha256="a" * 64, manifest_schema_version=1,
    )
    J.save(journal, client)

    result = I.import_bundle(
        import_dir=bundle.parent, config_dir=server.config_root, data_root=client
    )
    assert result.ok is False
    assert result.exit_code == I.Exit.RECOVERY_NEEDED
    assert result.failure_kind == "recovery_required"
