# -*- coding: utf-8 -*-
"""回测前停止采集并等待退出（口径：真停 + 由用户决定何时恢复）。

设计（specs/001-daemon-backtest-coexistence/plan.md）：
  · 复用**既有** `daemon_stop.request` 通道，只加 `reason` 字段（旧调用方不写 ⇒ 按 user）；
  · 分级停止由 daemon 侧既有边界消费点完成（任务边界 → 任务内单元边界），本模块不新增中断机制；
  · 回测结束**不重启采集**：写 `backtest_stop.flag` 留痕，界面/终端据此提示"请手动启动"；
  · 等待期间只报真实读数（已等秒数 + 当前阶段），不做 ETA。

本模块不 import daemon_lifecycle（避免循环依赖）：数据目录取 `quantstudio._paths.DATA_ROOT`，
daemon 存活判定用 status 文件 + psutil 双验（PID + create_time，防 PID 复用）。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from filelock import FileLock, Timeout

# ── 常量 ────────────────────────────────────────────────────────────────────
STOP_WAIT_SEC = 60          # 等待采集进程退出的上界（超时给三选项）
BOUNDARY_HINT_SEC = 30      # 阶段提示阈值：超过则显示"已请求中断当前任务"
POLL_SEC = 0.5
RUN_LOCK_NAME = ".collector_run.lock"
STOP_REQUEST_NAME = "daemon_stop.request"
STOP_FLAG_NAME = "backtest_stop.flag"
DAEMON_STATUS_NAME = "daemon_status.json"

PHASE_WAIT_ROUND = "等待采集收尾（任务边界）"
PHASE_WAIT_UNIT = "已请求中断当前任务（单元边界）"
PHASE_WAIT_EXIT = "等待采集进程退出"
PHASE_PRECHECK = "预检数据可用性"


def _data_root() -> Path:
    from quantstudio._paths import DATA_ROOT
    return DATA_ROOT


def run_lock_path() -> Path:
    return _data_root() / RUN_LOCK_NAME


def stop_request_path() -> Path:
    return _data_root() / STOP_REQUEST_NAME


def stop_flag_path() -> Path:
    return _data_root() / STOP_FLAG_NAME


def daemon_status_path() -> Path:
    return _data_root() / DAEMON_STATUS_NAME


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, doc: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ── 异常 ────────────────────────────────────────────────────────────────────
class WindowCancelled(Exception):
    """用户在等待采集停止期间取消回测（名字保留以兼容既有调用方）。"""


class CollectorStopTimeout(RuntimeError):
    """采集在等待窗口内未退出（界面据此给「继续等待 / 强制终止 / 取消」）。"""


# ── daemon status / 存活判定 ────────────────────────────────────────────────
def read_daemon_status() -> Optional[dict]:
    try:
        with daemon_status_path().open("r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def daemon_alive() -> bool:
    """daemon 是否存活：status 有 pid 且 psutil 双验（PID + create_time ±1s）。

    **`AccessDenied` 必须按"活着"处理**（2026-10-28 实证）：高完整性 daemon
    （如 `start_daemon_guarded.ps1` 提权启动）的 create_time/cmdline 读不到，
    若把"无权读"当成"已退出"，`wait_daemon_exit` 会 0 秒返回——等待形同虚设，
    回测可能在采集仍在轮内时抢库。判定退化的方向必须是保守（宁可多等）。
    """
    st = read_daemon_status()
    if not st or not st.get("pid"):
        return False
    try:
        pid = int(st["pid"])
    except (TypeError, ValueError):
        return False
    try:
        import psutil
    except ImportError:
        return False
    try:
        p = psutil.Process(pid)
        if not p.is_running():
            return False
        try:
            create = float(p.create_time())
        except psutil.AccessDenied:
            # 无权读创建时间 → 只要 pid 还在就按活着（无法排除 PID 复用，保守等待）
            return psutil.pid_exists(pid)
        if abs(create - float(st.get("create_time", 0))) > 1.0:
            return False        # PID 复用：不是原 daemon
        return True
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        try:
            return psutil.pid_exists(pid)
        except Exception:  # noqa: BLE001
            return True         # 查询失败也不得判死（保守）
    except Exception:  # noqa: BLE001  解析异常等
        return False


# ── 停止请求 ────────────────────────────────────────────────────────────────
def request_stop(reason: str = "backtest", owner_pid: Optional[int] = None) -> bool:
    """写 stop.request（只对当前 daemon 的 instance_token 生效）；无 daemon 返回 False。"""
    st = read_daemon_status()
    token = (st or {}).get("instance_token")
    if not st or not token:
        return False
    # 进程是否还在：`pid_exists` 只查进程表、不读 cmdline，因此对**高完整性** daemon 也成立
    # （不用 daemon_alive()：它做 create_time 身份校验，校验不过即判非本 daemon；
    #   此处只需「进程还在」——宁可多发停止请求交 daemon 侧 token 匹配裁决，不可漏停。
    #   注：F-5 后 daemon_alive 对 AccessDenied 已按"活着"处理，本行仅澄清二者分工差异。）
    try:
        import psutil
        _pid = int(st.get("pid") or 0)
        if _pid and not psutil.pid_exists(_pid):
            return False
    except Exception:  # noqa: BLE001  判定失败时保持既有行为（宁可发停止请求，不可漏停）
        pass
    _atomic_write_json(stop_request_path(), {
        "instance_token": str(token),
        "reason": str(reason),
        "requested_at": _now_iso(),
        "owner_pid": int(owner_pid if owner_pid is not None else os.getpid()),
    })
    return True


def clear_stop_request() -> None:
    try:
        stop_request_path().unlink(missing_ok=True)
    except OSError:
        pass


def read_stop_request() -> Optional[dict]:
    try:
        with stop_request_path().open("r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


# ── 停止留痕（跨界面进程可见）──────────────────────────────────────────────
def note_force_killed() -> None:
    """GUI「强制终止采集」成功后的归因补写（C1 配套）。

    强杀路径不经过 CollectorStopContext（超时异常已抛出、with 未进入），
    kill 成功后必须由此补写「因回测停止」flag，否则 GUI 退出状态只能显示
    「常驻进程异常退出」，丢失 FR-009 归因语义。
    """
    write_stop_flag("backtest", "停止等待超时后由用户强制终止采集进程")


def write_stop_flag(reason: str = "backtest", note: str = "") -> None:
    _atomic_write_json(stop_flag_path(), {
        "reason": reason, "at": _now_iso(), "note": note, "owner_pid": os.getpid(),
    })


def read_stop_flag() -> Optional[dict]:
    try:
        with stop_flag_path().open("r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def clear_stop_flag() -> None:
    try:
        stop_flag_path().unlink(missing_ok=True)
    except OSError:
        pass


# ── 等待 ────────────────────────────────────────────────────────────────────
def wait_daemon_exit(
    timeout: float = STOP_WAIT_SEC,
    *,
    on_wait: Optional[Callable[[float, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    poll: float = POLL_SEC,
    boundary_hint: float = BOUNDARY_HINT_SEC,
) -> float:
    """阻塞等待 daemon 退出；返回实际等待秒数。

    读数节律：首次立即报一次 → 此后每秒至少一次 → 阶段变化立即报（如实反映）。
    """
    t0 = time.monotonic()
    last: Optional[float] = None
    prev_phase: Optional[str] = None
    while True:
        if should_cancel is not None and should_cancel():
            raise WindowCancelled("用户取消回测（等待采集停止阶段）")
        if not daemon_alive():
            return time.monotonic() - t0
        waited = time.monotonic() - t0
        if waited >= timeout:
            raise CollectorStopTimeout(
                f"采集在 {timeout:.0f} 秒内未退出（可能仍在长任务中）")
        phase = PHASE_WAIT_ROUND if waited < boundary_hint else PHASE_WAIT_UNIT
        due = on_wait is not None and (
            last is None or (waited - last) >= 1.0 or phase != prev_phase)
        if due:
            last = waited
            prev_phase = phase
            try:
                on_wait(waited, phase)
            except Exception:  # noqa: BLE001  回调异常不得中断等待
                pass
        time.sleep(poll)


# ── 回测侧上下文 ────────────────────────────────────────────────────────────
class CollectorStopContext:
    """回测前停止采集并等待退出；结束时不重启采集。

    - `on_wait(waited_sec, phase)`：真实读数回调（GUI/CLI 共用）；
    - `should_cancel()`：True 则抛 `WindowCancelled`（已发生的停止不回滚）；
    - 拿 `.collector_run.lock` 作为第二道闸（防 GUI 手动采集在跑），持有到回测结束。
    """

    def __init__(
        self,
        strategy_path: str = "",
        *,
        timeout: float = STOP_WAIT_SEC,
        boundary_hint: float = BOUNDARY_HINT_SEC,
        poll: float = POLL_SEC,
        on_wait: Optional[Callable[[float, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ):
        self.strategy_path = str(strategy_path)
        self.timeout = float(timeout)
        self.boundary_hint = float(boundary_hint)
        self.poll = float(poll)
        self.on_wait = on_wait
        self.should_cancel = should_cancel
        self._lock: Optional[FileLock] = None
        self.stop_requested = False
        self.waited_sec = 0.0

    def _try_lock(self) -> bool:
        lock = FileLock(str(run_lock_path()), timeout=0, preserve_lock_file=True)
        try:
            lock.acquire()
        except Timeout:
            return False
        self._lock = lock
        return True

    def _acquire_lock(self) -> None:
        t0 = time.monotonic()
        while True:
            if self.should_cancel is not None and self.should_cancel():
                raise WindowCancelled("用户取消回测（等待互斥释放阶段）")
            if self._try_lock():
                return
            if time.monotonic() - t0 >= self.timeout:
                raise CollectorStopTimeout("采集互斥锁在等待窗口内未释放（可能仍有采集在写库）")
            time.sleep(self.poll)

    def __enter__(self) -> "CollectorStopContext":
        # 下个回测开始时清掉上一轮的"因回测停止"标记
        clear_stop_flag()
        self.stop_requested = request_stop("backtest")
        if self.stop_requested:
            try:
                self.waited_sec = wait_daemon_exit(
                    self.timeout, on_wait=self.on_wait, should_cancel=self.should_cancel,
                    poll=self.poll, boundary_hint=self.boundary_hint)
            except (CollectorStopTimeout, WindowCancelled) as e:
                # C1（终审阻断项 2026-10-28）：异常从 __enter__ 抛出 ⇒ with 体未进入 ⇒
                # __exit__ 不执行。但停止请求**已发出**，daemon 将在任务边界退出——
                # 若不在此补写归因 flag，GUI 只显示「未开启」，丢失 FR-009/FR-004
                # 「采集为什么停了」的语义；而超时/强杀恰是最需要说清归因的场景。
                note = ("等待超时：停止请求已发出，采集将在任务边界退出"
                        if isinstance(e, CollectorStopTimeout)
                        else "等待已取消：停止请求已发出，采集将在任务边界退出")
                write_stop_flag("backtest", note)
                raise
        self._acquire_lock()
        return self

    def _release_lock(self) -> None:
        if self._lock is not None:
            try:
                self._lock.release()
            except Exception:  # noqa: BLE001
                pass
            self._lock = None

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._release_lock()
        # 不重启采集：只在"确实向采集发过停止请求"时留痕（无采集在跑时不留假标记）
        if self.stop_requested:
            note = "回测结束，采集保持停止"
            if exc_type is not None:
                note = f"回测异常结束（{getattr(exc_type, '__name__', 'Exception')}），采集保持停止"
            write_stop_flag("backtest", note)
        return False
