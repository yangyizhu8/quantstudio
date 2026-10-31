# -*- coding: utf-8 -*-
"""锁生命周期三缺陷回归（2026-11-01 hotfix）。

① 导出成功路径必须释放锁（用户实测：00:20:51 导出成功后锁残留 1.7h）
② PRECHECK 陈锁自愈（原先"有锁即拒、自愈只在 acquire 侧"，陈锁把 export/import 全堵死）
③ 导出心跳（原先 heartbeat == acquired_at，>600s 长导出会被判陈锁回收 ⇒ 双写风险）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import quantstudio.delivery.export_kernel as E
from quantstudio.pipeline import snapshot_lock as SL
from tests.fixtures.delivery import Env

# 在 Env 打桩**之前**抓住真实实现
_REAL_ACQUIRE = E._acquire_lock
_REAL_RELEASE = E._release_lock


def _setup(tmp_path, monkeypatch):
    server = Env(tmp_path / "server", monkeypatch)
    # 恢复真实锁路径（Env 默认把它打桩，避免碰生产 lock dir）
    monkeypatch.setattr(E, "_acquire_lock", _REAL_ACQUIRE)
    monkeypatch.setattr(E, "_release_lock", _REAL_RELEASE)
    lock_dir = tmp_path / "lock"
    lock_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("QS_WRITE_LOCK_DIR", str(lock_dir))
    return server, lock_dir


def test_export_success_releases_lock(tmp_path, monkeypatch):
    """① 成功路径必须释放（此前只断言了 abort/fail 三处的清理）。"""
    server, lock_dir = _setup(tmp_path, monkeypatch)
    assert SL.read_holder() is None

    bundle = server.make_package()          # 真实取锁 → 六相位 → 成功返回

    assert (bundle / "manifest.json").exists()
    assert SL.read_holder() is None, "导出成功后锁必须被释放（用户实测残留 1.7h 的根因）"
    assert not (lock_dir / ".write_lock").exists(), "锁文件不得残留"


def test_precheck_self_heals_stale_dead_lock(tmp_path, monkeypatch):
    """② PRECHECK 遇陈锁（持有者已死）应自愈一次并留 reclaimed 读数，而不是把入口堵死。"""
    server, lock_dir = _setup(tmp_path, monkeypatch)

    # 造一把"持有者已死"的陈锁：pid 用一个必然不存在的号
    stale = {
        "v": 2,
        "pid": 999_999,
        "task_id": "delivery_export",
        "heartbeat": time.time() - 7200,
        "host": SL._hostname(),
        "pid_create_time": 1.0,
        "acquired_at": time.time() - 7200,
        "token": "deadbeef" * 4,
    }
    (lock_dir / ".write_lock").write_text(json.dumps(stale), encoding="utf-8")

    bundle = server.make_package()

    assert (bundle / "manifest.json").exists(), "陈锁应被自愈，导出继续"
    result_warnings = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert result_warnings.get("files"), "产物完整"
    assert SL.read_holder() is None


def test_export_refreshes_heartbeat_between_phases(tmp_path, monkeypatch):
    """③ 长相位之间必须刷心跳（否则零心跳锁会被误判陈锁回收）。"""
    server, lock_dir = _setup(tmp_path, monkeypatch)

    seen = {}

    def spy(done, total, phase):
        path = lock_dir / ".write_lock"
        if phase in ("CHECKPOINT", "COPY") and path.exists() and phase not in seen:
            payload = json.loads(path.read_text(encoding="utf-8"))
            seen[phase] = float(payload.get("heartbeat") or 0)

    original = E.export_bundle

    def wrapped(*args, **kwargs):
        kwargs["progress"] = spy
        return original(*args, **kwargs)

    monkeypatch.setattr(E, "export_bundle", wrapped)
    result = wrapped(
        export_dir=tmp_path / "out",
        data_root=server.root,
        config_dir=server.config_root,
    )
    bundle = result.bundle_dir

    assert (bundle / "manifest.json").exists()
    assert seen, "未在长相位观测到锁文件"
    ordered = [seen[k] for k in sorted(seen)]
    assert ordered[0] > 0, f"长相位读到的心跳非法：{seen}"
    assert ordered[-1] >= ordered[0], f"心跳必须单调推进：{seen}"
