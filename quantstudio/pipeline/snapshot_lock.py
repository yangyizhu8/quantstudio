# -*- coding: utf-8 -*-
"""共享写锁（3A 写锁收口）—— DuckDB/SQLite 写路径与快照 create 的权威互斥协议。

设计依据：docs/governance-3a-write-lock-design.md（DSH 终审通过）
语义：
  - 协作文件锁 data/snapshots/.write_lock（内容：PID/task_id/心跳时间戳）；
  - 互斥原语：os.open(O_CREAT|O_EXCL) 原子创建（跨平台，Windows 兼容——不用 fcntl）；
  - acquire 失败/超时抛 WriteLockHeld（含持有者信息，禁止静默）；
  - 心跳由持锁方调用（长任务建议每 60s）；陈锁（>10min 无心跳）仅告警不自动清除；
  - CLI 包裹器：python -m quantstudio.pipeline.snapshot_lock run <cmd...>
    （透传退出码/stdout/stderr/环境变量；子进程不继承锁——写操作须在包裹器进程内完成）。
行为等价（铁律）：无竞争时立即获得锁，单线程行为与接入前完全一致；不改变任何写入内容/顺序/语义。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOCK_NAME = ".write_lock"
STALE_SECONDS = 600  # 10 分钟无心跳视为陈锁


def lock_path() -> Path:
    root = Path(__file__).resolve().parent.parent.parent
    d = root / "data" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d / LOCK_NAME


class WriteLockHeld(RuntimeError):
    """锁被他人持有（含持有者信息，禁止静默失败）。"""

    def __init__(self, holder: dict, stale: bool = False):
        self.holder = holder
        self.stale = stale
        pid = holder.get("pid")
        task = holder.get("task_id")
        hb = holder.get("heartbeat")
        super().__init__(
            f"写锁被持有: pid={pid} task={task} heartbeat={hb}"
            + ("（陈锁：心跳超时，请人工确认持有进程后清理）" if stale else ""))


@dataclass
class WriteLock:
    """已获得的写锁句柄。release 幂等；建议长任务周期调用 heartbeat()。
    shallow=True 表示重入句柄（同进程已持锁）：release 只减计数，不释放文件锁。"""

    task_id: str
    _path: Path = None
    _released: bool = False
    _shallow: bool = False

    def _write_payload(self):
        self._path.write_text(json.dumps(
            {"pid": os.getpid(), "task_id": self.task_id,
             "heartbeat": time.time()}), encoding="utf-8")

    def heartbeat(self):
        if self._released or self._path is None:
            return
        self._write_payload()

    def release(self):
        if self._released:
            return
        self._released = True
        global _depth
        if _depth > 0:
            _depth -= 1
        if self._shallow or _depth > 0:
            return  # 重入层：文件锁由最外层持有者释放
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def read_holder() -> Optional[dict]:
    p = lock_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"pid": None, "task_id": "<unreadable>", "heartbeat": 0}


_depth = 0  # 同进程重入深度（CLI 守卫 + 内部 ensure 共存场景）


def acquire_write_lock(task_id: str = "unnamed", timeout_s: float = 30.0,
                       poll_s: float = 0.5) -> WriteLock:
    """获取写锁；失败抛 WriteLockHeld（fail-closed）。
    同进程重入安全：已持锁时返回浅句柄（release 只减计数）——消除 CLI 守卫与
    内部 ensure_write_lock 的自死锁。"""
    global _depth
    if _depth > 0:
        _depth += 1
        return WriteLock(task_id=task_id, _shallow=True)
    t0 = time.time()
    while True:
        p = lock_path()
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = read_holder()
            _hb = (holder or {}).get("heartbeat")
            stale = _hb is not None and time.time() - _hb > STALE_SECONDS
            if time.time() - t0 >= timeout_s:
                raise WriteLockHeld(holder or {"pid": None, "task_id": "unknown"},
                                    stale=stale)
            time.sleep(poll_s)
            continue
        os.close(fd)
        lock = WriteLock(task_id=task_id, _path=p)
        lock._write_payload()
        _depth = 1
        return lock


# ---------- 进程级引用计数（与 acquire_write_lock 共享 _depth，同进程重入安全） ----------
_ensure_handles: list = []


def ensure_write_lock(task_id: str = "unnamed") -> None:
    """确保本进程持有写锁（幂等、可嵌套；与 acquire_write_lock 同一计数域）。"""
    _ensure_handles.append(acquire_write_lock(task_id))


def release_write_lock() -> None:
    """配对释放（计数归零才真正释放文件锁；幂等安全）。"""
    if _ensure_handles:
        _ensure_handles.pop().release()


def lock_held_locally() -> bool:
    return _depth > 0


class locked_connect:
    """连接级写锁守卫：with locked_connect(factory) as conn —— 一行替换原
    `with duckdb.connect(...) as conn` / `conn = ...` 连接点，锁生命周期严格等于
    连接生命周期（引用计数，嵌套安全）。用于函数出口多、逐点 finally 不可行的场景。"""

    def __init__(self, factory, task_id: str = "conn"):
        self._factory = factory
        self._task_id = task_id
        self._conn = None

    def __enter__(self):
        ensure_write_lock(self._task_id)
        try:
            self._conn = self._factory()
            return self._conn
        except BaseException:
            release_write_lock()
            raise

    def __exit__(self, *exc):
        try:
            if self._conn is not None:
                close = getattr(self._conn, "close", None)
                if close:
                    close()
        finally:
            release_write_lock()
        return False


def with_write_lock(task_id: str):
    """装饰器：函数执行期间持锁。"""
    def deco(fn):
        def wrapper(*a, **kw):
            with acquire_write_lock(task_id):
                return fn(*a, **kw)
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def _cli(argv):
    if len(argv) >= 2 and argv[0] == "run":
        cmd = argv[1:]
        if not cmd:
            print("用法: run <cmd...>", file=sys.stderr)
            return 2
        task_id = f"wrap:{Path(cmd[0]).name}"
        try:
            lock = acquire_write_lock(task_id)
        except WriteLockHeld as e:
            print(f"[snapshot_lock] {e}", file=sys.stderr)
            return 2
        try:
            # 透传 stdin/stdout/stderr；子进程不继承锁（限制：写操作须在子进程内完成——
            # 本包裹器持有锁至子进程退出，覆盖子进程全部写时段）
            rc = subprocess.call(cmd)
        finally:
            lock.release()
        return rc
    print("用法: python -m quantstudio.pipeline.snapshot_lock run <cmd...>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
