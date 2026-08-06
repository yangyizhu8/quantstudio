"""Reusable, staging-only B-6 copy preparation.

The source paths are explicit and must themselves be staging/hermetic paths.
This module never defaults to or opens the configured formal QuantStudio DB.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict

from filelock import FileLock, Timeout

from quantstudio.pipeline.daemon_lifecycle import collector_run_lock_path, daemon_lock_path

BJ_TZ = timezone(timedelta(hours=8))
SIDECAR_SUFFIXES = (".wal", "-wal", "-journal", ".shm", ".tmp")


class StagingPrepError(RuntimeError):
    pass


def _now_ts() -> str:
    return datetime.now(BJ_TZ).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_evidence(path: Path) -> dict:
    path = path.resolve()
    st = path.stat()
    return {"path": str(path), "size": int(st.st_size),
            "mtime_ns": int(st.st_mtime_ns), "sha256": _sha256(path)}


def _sidecars(main_db: Path, aux_db: Path) -> list[dict]:
    candidates = [Path(str(main_db) + ".wal")]
    for suffix in ("-wal", "-journal", "-shm"):
        candidates.append(Path(str(aux_db) + suffix))
    return [{"path": str(p), "size": int(p.stat().st_size)}
            for p in candidates if p.exists() and p.stat().st_size > 0]


def _assert_not_formal(main_db: Path, aux_db: Path) -> None:
    try:
        from quantstudio._paths import db_path
        formal = Path(db_path()).resolve()
    except Exception as exc:
        raise StagingPrepError("cannot resolve formal DB path; fail-closed") from exc
    formal_aux = formal.parent / "qfq_aux.db"
    if main_db.resolve() == formal or aux_db.resolve() == formal_aux:
        raise StagingPrepError("cutover-prep-staging refuses the configured formal main/aux")


def _write_exclusive(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except FileExistsError as exc:
        raise StagingPrepError(f"refuse overwrite of existing marker/manifest: {path}") from exc
    try:
        path.chmod(0o444)
    except OSError:
        pass


def _hold_copy_locks():
    stack = ExitStack()
    daemon = FileLock(str(daemon_lock_path()), timeout=0)
    collector = FileLock(str(collector_run_lock_path()), timeout=0)
    try:
        stack.enter_context(daemon.acquire(timeout=0))
        stack.enter_context(collector.acquire(timeout=0))
    except Timeout as exc:
        stack.close()
        raise StagingPrepError("daemon/collector lock busy; refuse inconsistent staging copy") from exc
    return stack


def prepare_staging_copy(*, source_db: str | Path, source_aux: str | Path,
                         dest: str | Path) -> dict:
    """Copy an explicit staging/hermetic source under both framework locks."""
    source_db = Path(source_db).resolve()
    source_aux = Path(source_aux).resolve()
    root = Path(dest).resolve()
    if not source_db.is_file() or not source_aux.is_file():
        raise StagingPrepError(f"source main/aux missing: {source_db}, {source_aux}")
    _assert_not_formal(source_db, source_aux)
    if root.exists():
        if any(root.iterdir()):
            raise StagingPrepError(f"destination must be new and empty: {root}")
    else:
        root.mkdir(parents=True)
    if root == source_db.parent or root == source_aux.parent:
        raise StagingPrepError("destination must not be the source directory")
    sidecars = _sidecars(source_db, source_aux)
    if sidecars:
        raise StagingPrepError(f"non-empty WAL/journal sidecar; refuse copy: {sidecars}")
    staging = root / "staging"
    staging.mkdir(exist_ok=False)
    dst_db = staging / "quantstudio.db"
    dst_aux = staging / "qfq_aux_b6.db"
    lock_stack = _hold_copy_locks()
    try:
        locked_sidecars = _sidecars(source_db, source_aux)
        if locked_sidecars:
            raise StagingPrepError(
                f"non-empty WAL/journal sidecar appeared inside locked copy window: {locked_sidecars}")
        before = {"main": _file_evidence(source_db), "aux": _file_evidence(source_aux)}
        shutil.copy2(source_db, dst_db)
        shutil.copy2(source_aux, dst_aux)
        after_source = {"main": _file_evidence(source_db), "aux": _file_evidence(source_aux)}
        if before != after_source:
            raise StagingPrepError("source evidence changed during copy")
        staging_ev = {"main": _file_evidence(dst_db), "aux": _file_evidence(dst_aux)}
    finally:
        lock_stack.close()
    if before["main"]["sha256"] != staging_ev["main"]["sha256"] or \
            before["aux"]["sha256"] != staging_ev["aux"]["sha256"]:
        raise StagingPrepError("staging copy SHA-256 mismatch")
    manifest = {
        "kind": "quantstudio-b6-staging-copy",
        "created_at": _now_ts(),
        "source": before,
        "staging": staging_ev,
        "formal_write": False,
        "formal_migration": False,
        "mcp_gen1_activation": False,
        "locks": [str(daemon_lock_path()), str(collector_run_lock_path())],
        "sidecars_checked": True,
    }
    manifest_path = root / "copy_manifest.json"
    marker_path = root / ".quantstudio_b6_staging.json"
    _write_exclusive(manifest_path, manifest)
    _write_exclusive(marker_path, {
        "kind": "quantstudio-b6-staging",
        "copy_manifest": str(manifest_path.resolve()),
        "main_db": str(dst_db.resolve()),
        "aux_db": str(dst_aux.resolve()),
        "formal_write": False,
    })
    return {"root": str(root), "main_db": str(dst_db), "aux_db": str(dst_aux),
            "copy_manifest": str(manifest_path), "marker": str(marker_path),
            "manifest": manifest}
