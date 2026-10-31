# -*- coding: utf-8 -*-
"""导出内核测试（T020–T025；SC-001/002/003/012 + FR-034/035/019）。

全部在 `tmp_path` 内跑：**不触生产 `data/`**、不取真实写锁（`_acquire_lock` 为注入点）、
不依赖运行中的 daemon（`probe.daemon_running` 被替换）。环境装配见
`tests/fixtures/delivery/env.py`（W1/W2/W3 共用，避免两套夹具）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest

from quantstudio.delivery import export_kernel as E
from quantstudio.delivery import manifest as M
from quantstudio.delivery import probe as P
from quantstudio.delivery import verify as V
from tests.fixtures.delivery import Env


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def _assert_no_output(export_dir: Path) -> None:
    assert not export_dir.exists() or list(export_dir.iterdir()) == [], (
        "fail-closed 场景必须零产出（导出目录下不得出现任何子目录/文件）"
    )


# ── SC-001 五场景：拦截有效性（各一次，产出必须为 0）──────────────────────
def test_sc001_schema_status_invalid(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, schema="partial_or_mixed")
    with pytest.raises(E.ExportRefused) as exc:
        env.export()
    assert exc.value.code == "SCHEMA_STATUS_INVALID"
    _assert_no_output(env.export_dir)


def test_sc001_write_lock_held(env, monkeypatch):
    monkeypatch.setattr(P, "write_lock_holder", lambda *a, **k: {"task_id": "daemon", "pid": 1})
    with pytest.raises(E.ExportRefused) as exc:
        env.export()
    assert exc.value.code == "WRITE_LOCK_HELD"
    _assert_no_output(env.export_dir)


def test_sc001_daemon_running(env, monkeypatch):
    monkeypatch.setattr(
        P, "daemon_running",
        lambda *a, **k: P.DaemonState(True, {"pid": 29152, "started_at": "2026-10-30T21:51:34"}),
    )
    with pytest.raises(E.ExportRefused) as exc:
        env.export()
    assert exc.value.code == "DAEMON_RUNNING"
    assert exc.value.readings.get("pid") == 29152
    _assert_no_output(env.export_dir)


def test_sc001_disk_insufficient(env, monkeypatch):
    monkeypatch.setattr(P, "disk_free_bytes", lambda *a, **k: 10)
    with pytest.raises(E.ExportRefused) as exc:
        env.export()
    assert exc.value.code == "DISK_INSUFFICIENT"
    assert exc.value.readings["required"] > exc.value.readings["free"]
    _assert_no_output(env.export_dir)


def test_sc001_wal_not_zero(env, monkeypatch):
    monkeypatch.setattr(E, "_checkpoint_all", lambda src: {"duckdb": "skipped", "sqlite": []})
    wal = env.root / "quantstudio.db-wal"
    wal.write_bytes(b"x" * 32)
    with pytest.raises(E.ExportRefused) as exc:
        env.export()
    assert exc.value.code == "WAL_NOT_ZERO"
    assert exc.value.readings["wal"][0]["bytes"] == 32
    _assert_no_output(env.export_dir)


# ── SC-002 零泄露 / SC-003 同刻一致 ────────────────────────────────────────
def test_sc002_zero_leak(env):
    result = env.export()
    names = sorted(p.name for p in result.bundle_dir.iterdir())
    assert names == sorted(["manifest.json", "quantstudio.db", "qfq_aux.db", "qfq_aux_mcp_gen1.db"])
    assert not list(result.bundle_dir.rglob("*.env"))
    assert not list(result.bundle_dir.rglob("*-shm"))
    assert result.self_check["red_line"]["zero_leak"] is True
    assert result.qualified is True


def test_sc003_same_instant_three_files_readable(env):
    result = env.export()
    bundle = result.bundle_dir
    main = duckdb.connect(str(bundle / "quantstudio.db"), read_only=True)
    try:
        assert main.execute("SELECT count(*) FROM source_watermark").fetchone()[0] >= 1
        assert main.execute("SELECT count(*) FROM qfq_source_cutover").fetchone()[0] >= 0
    finally:
        main.close()
    import sqlite3

    for name in ("qfq_aux.db", "qfq_aux_mcp_gen1.db"):
        con = sqlite3.connect(V.sqlite_ro_uri(bundle / name), uri=True)
        try:
            assert con.execute("SELECT count(*) FROM qfq_price_adj").fetchone()[0] >= 0
        finally:
            con.close()


# ── SC-012 体积为实测值 ────────────────────────────────────────────────────
def test_sc012_volume_readings_are_measured(env):
    result = env.export()
    actual = {p.name: p.stat().st_size for p in result.bundle_dir.iterdir() if p.suffix == ".db"}
    from_manifest = {
        f["path"]: f["bytes"]
        for f in json.loads((result.bundle_dir / "manifest.json").read_text(encoding="utf-8"))["files"]
    }
    assert from_manifest == actual
    assert result.total_bytes == sum(actual.values()) > 0


# ── FR-034 旧包永不触碰 / FR-035 路径互斥 ──────────────────────────────────
def test_fr034_old_bundle_untouched(env):
    old = env.export_dir / "20200101_000000"
    old.mkdir(parents=True)
    (old / "quantstudio.db").write_bytes(b"old-package")
    before = (old / "quantstudio.db").read_bytes()

    result = env.export()
    assert result.bundle_dir.name != old.name
    assert (old / "quantstudio.db").read_bytes() == before


def test_fr035_export_dir_inside_data_root_rejected(env):
    with pytest.raises(E.ExportRefused) as exc:
        E.export_bundle(
            export_dir=env.root / "export",
            config_dir=env.config_root,
            data_root=env.root,
        )
    assert exc.value.code == "PATH_OVERLAP"
    assert not (env.root / "export").exists()


# ── FR-019 进度 / 取消 / 中断即作废整包（不变式 9）──────────────────────────
def test_progress_is_monotonic(env):
    seen = []
    env.export(progress=lambda done, total, phase: seen.append((done, total, phase)))
    assert seen
    copy_points = [(d, t) for d, t, p in seen if p == "COPY"]
    assert copy_points == sorted(copy_points)
    assert copy_points[-1][0] == copy_points[-1][1] > 0


def test_cancel_before_copy_aborts_without_bundle(env):
    class Cancel:
        def is_set(self):
            return True

    with pytest.raises(E.ExportAborted):
        env.export(cancel=Cancel())
    _assert_no_output(env.export_dir)


def test_interrupt_voids_whole_bundle(env, monkeypatch):
    calls = {"n": 0}
    real_copy = E._copy_file

    def boom(src, dst, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(28, "No space left on device")
        return real_copy(src, dst, **kwargs)

    monkeypatch.setattr(E, "_copy_file", boom)
    with pytest.raises(E.ExportFailed):
        env.export()
    _assert_no_output(env.export_dir)


# ── manifest 自证与红线自检 ────────────────────────────────────────────────
def test_manifest_written_last_and_parses(env):
    result = env.export()
    read = M.read_manifest(result.bundle_dir / "manifest.json")
    assert read.ok, read.reasons
    doc = read.doc
    assert len(doc["tables"]) == 1
    assert doc["tables"][0]["table"] == "stock_daily"
    assert doc["tables"][0]["state"] == "OK"
    from quantstudio.delivery.constants import TABLE_SET_RULE

    assert doc["table_set"]["rule"] == TABLE_SET_RULE
    assert doc["row_count_degraded"] is False
    assert doc["export"]["copy_sha256_pass"] is True
    assert doc["main_db"]["schema_status"] == "complete_2_1"
    for name in ("quantstudio.db", "qfq_aux.db", "qfq_aux_mcp_gen1.db"):
        assert (result.bundle_dir / "manifest.json").stat().st_mtime >= (
            result.bundle_dir / name
        ).stat().st_mtime


def test_self_check_red_line_catches_stray_file(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "quantstudio.db").write_bytes(b"")
    (bundle / "secrets.env").write_text("TOKEN=x", encoding="utf-8")
    (bundle / "qfq_aux.db-shm").write_bytes(b"\x00")
    (bundle / "logs").mkdir()
    (bundle / "logs" / "run.log").write_text("x", encoding="utf-8")

    check = V.self_check_bundle(bundle)
    red = check["red_line"]
    assert red["outside_whitelist"] >= 1
    assert red["env_hits"] == 1
    assert red["shm_hits"] == 1
    assert red["log_hits"] >= 1
    assert red["zero_leak"] is False


def test_self_check_green_on_clean_bundle(env):
    result = env.export()
    check = V.self_check_bundle(result.bundle_dir)
    assert check["red_line"]["zero_leak"] is True
    assert all(item["select1"] == "ok" for item in check["db_open"])


def test_whitelist_missing_file_refused(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    os.unlink(env.root / "qfq_aux_mcp_gen1.db")
    with pytest.raises(E.ExportRefused) as exc:
        env.export()
    assert exc.value.code == "WHITELIST_FILE_MISSING"
    assert "qfq_aux_mcp_gen1.db" in exc.value.readings["missing"]
    _assert_no_output(env.export_dir)
