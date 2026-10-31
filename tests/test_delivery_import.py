# -*- coding: utf-8 -*-
"""导入主路径测试（W3；SC-004 / SC-007 / SC-011 / SC-015 / SC-016）。

红线：全部在 `tmp_path` fixture 内，**不碰生产 `DATA_ROOT`**。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from quantstudio.delivery import import_kernel as I
from quantstudio.delivery import journal as J
from quantstudio.delivery import manifest as M
from quantstudio.delivery import probe as P
from tests.fixtures.delivery import Env


def _client_env(tmp_path, monkeypatch, mode: str):
    """造"服务端数据 + 交付包 + 客户端数据根"，并把内核指向客户端根。"""
    server = Env(tmp_path / "server", monkeypatch)
    bundle = server.make_package()
    client = server.make_client(mode)
    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", client, raising=False)
    monkeypatch.setattr(I, "_acquire_lock", lambda **kw: {"task_id": "test"})
    monkeypatch.setattr(I, "_release_lock", lambda: None)
    monkeypatch.setattr(P, "daemon_running", lambda *a, **k: P.DaemonState(False, {}))
    monkeypatch.setattr(P, "write_lock_holder", lambda *a, **k: None)
    monkeypatch.setattr(P, "disk_free_bytes", lambda *a, **k: 10 ** 12)
    return server, bundle, client


def _import(client: Path, bundle: Path, config_root: Path, **kwargs):
    return I.import_bundle(
        import_dir=bundle.parent,
        config_dir=config_root,
        data_root=client,
        **kwargs,
    )


def test_sc004_update_happy_path(tmp_path, monkeypatch):
    server, bundle, client = _client_env(tmp_path, monkeypatch, "update")
    result = _import(client, bundle, server.config_root)

    assert result.ok, result.reasons
    assert result.mode == "update"
    assert result.exit_code == 0
    assert result.journal_finalized is True
    assert not J.journal_path(client).exists(), "成功后 journal 必须删除"
    assert result.backup["dir"] and Path(result.backup["dir"]).is_dir()
    assert (Path(result.backup["dir"]) / "RESTORE_README.txt").exists()
    assert result.reverify["can_incremental"] is True
    assert [f["name"] for f in result.files] == sorted(
        ["quantstudio.db", "qfq_aux.db", "qfq_aux_mcp_gen1.db"],
        key=lambda n: ["quantstudio.db", "qfq_aux.db", "qfq_aux_mcp_gen1.db"].index(n),
    )
    # 安装后主库可读且与包一致
    assert M.sha256_path(client / "quantstudio.db") == next(
        f["sha256"] for f in json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["files"]
        if f["path"] == "quantstudio.db"
    )


def test_sc007_install_mode_keeps_existing_assets(tmp_path, monkeypatch):
    server, bundle, client = _client_env(tmp_path, monkeypatch, "install")
    result = _import(client, bundle, server.config_root)

    assert result.ok, result.reasons
    assert result.mode == "install"
    assert result.backup == {"dir": None, "bytes": 0, "method": "none"}
    for name in ("snapshots", ".gitkeep", "README.md"):
        assert (client / name).exists(), f"安装模式不得删除既有件 {name}（FR-017）"
    assert (client / "quantstudio.db").exists()
    assert not J.journal_path(client).exists()


def test_sc015_repair_mode_on_zero_byte_db(tmp_path, monkeypatch):
    server, bundle, client = _client_env(tmp_path, monkeypatch, "repair")
    assert (client / "quantstudio.db").stat().st_size == 0

    result = _import(client, bundle, server.config_root)

    assert result.ok, result.reasons
    assert result.mode == "repair"
    assert result.forensic is not None, "修复模式必须给取证摘要"
    for key in ("bytes", "sha256", "open_error", "schema_status"):
        assert key in result.forensic
    # 原件必须保留在备份目录（探测失败 ≠ 数据无价值）
    backup = Path(result.backup["dir"])
    assert (backup / "quantstudio.db").exists()
    assert (backup / "quantstudio.db").stat().st_size == 0
    assert (client / "quantstudio.db").stat().st_size > 0


def test_sc016_idempotent_two_imports(tmp_path, monkeypatch):
    server, bundle, client = _client_env(tmp_path, monkeypatch, "update")
    first = _import(client, bundle, server.config_root)
    assert first.ok, first.reasons

    second = _import(client, bundle, server.config_root)
    assert second.ok, second.reasons
    assert first.reverify["tables"] == second.reverify["tables"], "同一包两次导入的复验四态必须逐字段一致"
    assert first.reverify["can_incremental"] == second.reverify["can_incremental"]


def test_sc011_backup_is_rename_with_evidence(tmp_path, monkeypatch):
    server, bundle, client = _client_env(tmp_path, monkeypatch, "update")
    main_before = client / "quantstudio.db"
    size_before = main_before.stat().st_size
    ino_before = main_before.stat().st_ino

    started = time.monotonic()
    result = _import(client, bundle, server.config_root)
    elapsed = time.monotonic() - started

    assert result.ok, result.reasons
    assert result.backup["method"] == "rename"
    backup_file = Path(result.backup["dir"]) / "quantstudio.db"
    # 证据链① inode 一致；② size + mtime_ns；③ 耗时 < 5 s（fixture 级）
    assert backup_file.stat().st_ino == ino_before
    assert backup_file.stat().st_size == size_before
    assert elapsed < 5.0, f"同卷 rename 备份应 O(1)，实测 {elapsed:.2f}s"
