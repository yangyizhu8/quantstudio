# -*- coding: utf-8 -*-
"""回滚与磁盘耗尽（W3；SC-006 / SC-017）。全部 tmp_path。"""
from __future__ import annotations

import errno
from pathlib import Path

import pytest

from quantstudio.delivery import import_kernel as I
from quantstudio.delivery import journal as J
from quantstudio.delivery import probe as P
from quantstudio.delivery import verify as V
from tests.fixtures.delivery import Env


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


def _run(client, bundle, config_root):
    return I.import_bundle(import_dir=bundle.parent, config_dir=config_root, data_root=client)


def test_sc006_rollback_on_replace_failure(tmp_path, monkeypatch):
    server, bundle, client = _setup(tmp_path, monkeypatch)
    schema_before = V._measure_schema_status(client / "quantstudio.db")
    rows_before = _count_rows(client / "quantstudio.db")

    calls = {"n": 0}
    real_replace = I._replace

    def flaky(src: Path, dst: Path):
        # 只让"覆盖阶段的正式替换"失败一次（备份 rename 与回滚还原必须放行）
        if str(dst) == str(client / "qfq_aux.db") and not calls.get("failed"):
            calls["failed"] = True
            raise OSError("injected replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(I, "_replace", flaky)
    result = _run(client, bundle, server.config_root)

    assert result.ok is False
    assert result.rollback_ok is True
    assert result.exit_code == I.Exit.ROLLED_BACK
    # 回滚后：主库可读、schema 与导入前一致、行数一致、journal 清理
    assert V._measure_schema_status(client / "quantstudio.db") == schema_before
    assert _count_rows(client / "quantstudio.db") == rows_before
    assert not J.journal_path(client).exists()
    assert not list(client.glob("*.incoming")), "回滚后不得残留 staging 临时文件"


def test_sc017_enospc_during_staging_rolls_back(tmp_path, monkeypatch):
    server, bundle, client = _setup(tmp_path, monkeypatch)
    schema_before = V._measure_schema_status(client / "quantstudio.db")
    size_before = (client / "quantstudio.db").stat().st_size

    written = {"total": 0}
    real_copy = I._copy

    def enospc(src: Path, dst: Path):
        if str(dst).endswith(".incoming"):
            written["total"] += 1024
            if written["total"] > 1024:        # 第 2 个文件即耗尽
                raise OSError(errno.ENOSPC, "No space left on device")
        return real_copy(src, dst)

    monkeypatch.setattr(I, "_copy", enospc)
    result = _run(client, bundle, server.config_root)

    assert result.ok is False
    assert result.rollback_ok is True
    assert (client / "quantstudio.db").stat().st_size == size_before
    assert V._measure_schema_status(client / "quantstudio.db") == schema_before
    assert not J.journal_path(client).exists(), "回滚完成后 journal 应被清理（或保留可恢复）"


def _count_rows(path: Path) -> int:
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute("SELECT count(*) FROM source_watermark").fetchone()[0]
    finally:
        con.close()
