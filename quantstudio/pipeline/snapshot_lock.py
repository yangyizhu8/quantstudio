# -*- coding: utf-8 -*-
"""共享写锁（3A 写锁收口 + 死亡自愈）—— DuckDB/SQLite 写路径与快照 create 的权威互斥协议。

设计依据：
  - docs/governance-3a-write-lock-design.md（3A 写锁收口，DSH 终审通过）
  - docs/write-lock-selfheal-design.md（批一：写锁死亡自愈，2026-09-17）

语义（批一修订，2026-09-17）：
  - 协作文件锁 data/snapshots/.write_lock（payload：PID/task_id/心跳；v2 增 host /
    pid_create_time / token / acquired_at）；
  - 互斥原语：os.open(O_CREAT|O_EXCL) 原子创建（跨平台，Windows 兼容——不用 fcntl）；
  - acquire 失败/超时抛 WriteLockHeld（含持有者信息 + 判定结论，禁止静默）；
  - **陈锁语义修订**（原「仅告警不自动清除」在无人值守场景会把一次进程终止放大为永久
    全量写入阻断，2026-09-17 两客户事故根因）：
      · 持有者**进程存活** → 仍 fail-closed，**不回收**（红线）；
      · 持有者**进程已不存在**（同主机；或 PID 复用已被 create_time 排除）→ acquire 侧
        **安全回收**：reclaim 互斥 + CAS 三字段比对 + 审计留痕 + 告警；
      · 跨主机 / legacy 且 pid 缺失 / psutil 不可用 → 不回收，报诊断结论；
      · 开关 QS_WRITE_LOCK_SELFHEAL=0 退回旧「仅告警」行为（回退/审计用）。
  - 心跳：WriteLock.heartbeat() 手动刷新（API 不变；**不引入后台线程**——回收判据以
    「进程存活」为准，心跳仅作辅助前置条件）；
  - 所有权校验：持锁方写入前可调 WriteLock.assert_still_owner() / 模块级
    assert_lock_owner()；锁被外部删除或替换 → WriteLockLost（把静默双写变成可观测失败）；
  - CLI 包裹器：python -m quantstudio.pipeline.snapshot_lock run <cmd...>（语义不变，
    透传退出码/stdout/stderr/环境变量；子进程不继承锁——写操作须在包裹器进程内完成）。

诚实边界：本模块**不覆盖** SIGKILL / 断电 / 强制重启（atexit 与信号处理器均无法执行）
——那正是回收判据存在的理由。

行为等价（铁律）：无竞争时立即获得锁，单线程行为与接入前完全一致；不改变任何写入
内容/顺序/语义。控制台输出不使用 emoji（Windows GBK 控制台约定）。
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_NAME = ".write_lock"
STALE_SECONDS = 600  # 10 分钟无心跳视为陈锁（回收判据的前置条件之一）
SELFHEAL_ENV = "QS_WRITE_LOCK_SELFHEAL"      # 默认 1=启用；0=退回旧「仅告警」行为
AUDIT_LOG_NAME = "write_lock_reclaim.log"    # data/snapshots/ 下，单行文本审计
AUDIT_LOG_ENV = "QS_WRITE_LOCK_AUDIT_LOG"    # 可选：审计落盘路径覆盖（测试/运维）
CAS_FIELDS = ("pid", "heartbeat", "task_id", "token")

# 判定结论（diagnose_holder 的 verdict 取值）
V_FREE = "free"                 # 无锁
V_LIVE_FRESH = "live_fresh"     # 心跳新鲜：正常竞争
V_STALE_DEAD = "stale_dead"     # 陈锁 + 持有者进程已不存在 → 可回收
V_STALE_ALIVE = "stale_alive"   # 陈锁 + 持有者进程仍存活 → 不回收（红线）
V_CROSS_HOST = "cross_host"     # 跨主机锁 → 不回收
V_UNDECIDABLE = "undecidable"   # 无法判定（不可解析 / pid 缺失 / psutil 不可用）→ 不回收


def _lock_dir() -> Path:
    """锁目录解析（默认 = 仓库 `data/snapshots`，与接入前逐位一致）。

    `QS_WRITE_LOCK_DIR`：测试/运维显式重定向（验收须「零碰生产快照目录」时使用）。

    **刻意不耦合 `QUANTSTUDIO_DATA_ROOT`**：影子根场景下若新旧版本进程对该变量感知不一致，
    会各自解析出不同的锁文件 → 互斥被静默打破（混版运行期风险）。锁目录仅在**显式设置本
    专用变量**时改变，从而保证生产解析路径对所有版本恒定、互斥不被版本差击穿。
    """
    override = os.environ.get("QS_WRITE_LOCK_DIR")
    if override:
        return Path(override)
    root = Path(__file__).resolve().parent.parent.parent
    return root / "data" / "snapshots"


def lock_path() -> Path:
    d = _lock_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / LOCK_NAME


def _reclaim_path() -> Path:
    """回收互斥文件（与锁文件同目录，名字固定，便于人工审计现场）。"""
    return lock_path().parent / (LOCK_NAME + ".reclaim")


def _audit_path() -> Path:
    override = os.environ.get(AUDIT_LOG_ENV)
    if override:
        p = Path(override)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return p
    return lock_path().parent / AUDIT_LOG_NAME


def _hostname() -> str:
    try:
        return socket.gethostname() or "unknown"
    except Exception:
        return "unknown"


class WriteLockHeld(RuntimeError):
    """锁被他人持有（含持有者信息与判定结论，禁止静默失败）。"""

    def __init__(self, holder: dict, stale: bool = False,
                 diagnosis: Optional[dict] = None):
        self.holder = holder
        self.stale = stale
        self.diagnosis = diagnosis or {}
        pid = holder.get("pid")
        task = holder.get("task_id")
        hb = holder.get("heartbeat")
        base = f"写锁被持有: pid={pid} task={task} heartbeat={hb}"
        tail = _held_tail(self.diagnosis)
        super().__init__(base + tail)


def _held_tail(diag: dict) -> str:
    """按判定结论生成尾注（不可回收时必须给出结论与处置路径）。"""
    verdict = (diag or {}).get("verdict")
    if verdict == V_STALE_DEAD:
        return ("（陈锁：持有者进程已不存在；自动回收未生效——"
                "QS_WRITE_LOCK_SELFHEAL=0 或回收竞争失败。"
                "处置路径见《客户使用说明》锁残留处置章节，或联系技术支持）")
    if verdict == V_STALE_ALIVE:
        return ("（陈锁：持有者进程仍存活但心跳停更——疑长任务或进程卡死，未自动回收；"
                "请确认该 pid 后再处置。处置路径见《客户使用说明》锁残留处置章节，"
                "或联系技术支持）")
    if verdict == V_CROSS_HOST:
        return (f"（跨主机锁：payload host={(diag or {}).get('host')} 与本机不符，"
                f"拒绝自动回收。处置路径见《客户使用说明》锁残留处置章节，"
                f"或联系技术支持）")
    if verdict == V_UNDECIDABLE:
        return (f"（无法判定持有者存活：{(diag or {}).get('detail') or '-'}；未自动回收。"
                f"处置路径见《客户使用说明》锁残留处置章节，或联系技术支持）")
    if diag.get("stale"):
        return "（陈锁：心跳超时，请人工确认持有进程后清理）"
    return ""


class WriteLockLost(RuntimeError):
    """本进程名义上持锁，但锁文件已被删除或已被他人替换（防止静默双写）。"""

    def __init__(self, task_id: str, current: Optional[dict]):
        self.task_id = task_id
        self.current = current
        super().__init__(
            f"写锁所有权已丢失: task={task_id}；当前锁文件内容={current}。"
            f"本进程已停止写入（fail-closed），请排查是否有外部删除/回收。")


@dataclass
class WriteLock:
    """已获得的写锁句柄。release 幂等；建议长任务周期调用 heartbeat()。
    shallow=True 表示重入句柄（同进程已持锁）：release 只减计数，不释放文件锁。"""

    task_id: str
    _path: Path = None
    _released: bool = False
    _shallow: bool = False
    token: Optional[str] = None
    _payload: dict = field(default_factory=dict)

    def _write_payload(self):
        payload = {
            "v": 2,
            "pid": os.getpid(),
            "task_id": self.task_id,
            "heartbeat": time.time(),
            "host": _hostname(),
            "pid_create_time": _pid_create_time(os.getpid()),
            "acquired_at": self._payload.get("acquired_at") or time.time(),
            "token": self.token,
        }
        self._payload = payload
        self._path.write_text(json.dumps(payload), encoding="utf-8")

    def heartbeat(self):
        if self._released or self._path is None or self._shallow:
            return
        self._write_payload()

    def _owns(self, holder: Optional[dict]) -> bool:
        return bool(holder) and bool(self.token) and holder.get("token") == self.token

    def _recreate(self) -> bool:
        """锁文件被外部删除后，以 O_EXCL 原子重建并写回本句柄 payload。"""
        try:
            fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, OSError):
            return False
        os.close(fd)
        try:
            self._write_payload()
        except OSError:
            return False
        return True

    def assert_still_owner(self):
        """校验写锁仍归本进程所有；不可确认时先尝试就地恢复，仍不成立才抛 WriteLockLost。

        判定顺序（fail-closed，同时避免误伤）：
          1) 文件存在且 token == 自己 → 通过；
          2) 文件存在但 token != 自己 → **已被他人持有** → 抛 WriteLockLost（防双写，硬判据）；
          3) 文件缺失（被外部删除/人工清理）→ 以 O_EXCL 原子重建并写回自己的 payload：
             成功 → 恢复持有（记 WARNING）后继续；失败（他人刚取得）→ 重读仍非自己 → 抛错。
        说明：第 3 条是实施期 refine（2026-09-17）——「文件缺失」本身不构成双写事实，
        而**他人持锁**才构成；线性化点仍是文件创建的 O_EXCL，故互斥性不变。
        """
        if self._released or self._shallow or self._path is None:
            return
        cur = read_holder()
        if self._owns(cur):
            return
        if cur is not None:
            raise WriteLockLost(self.task_id, cur)
        if self._recreate():
            logger.warning(
                "[write-lock] 锁文件被外部删除，已原子重建恢复持有: lock_path=%s "
                "holder pid=%s task=%s", self._path, os.getpid(), self.task_id)
            return
        cur2 = read_holder()
        if self._owns(cur2):
            return
        raise WriteLockLost(self.task_id, cur2)

    def release(self):
        if self._released:
            return
        self._released = True
        global _depth, _current_lock
        if _depth > 0:
            _depth -= 1
        if self._shallow or _depth > 0:
            return  # 重入层：文件锁由最外层持有者释放
        _unregister_handle(self)
        if _current_lock is self:
            _current_lock = None
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass
        _restore_graceful_handlers()

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


def _pid_alive(pid) -> Optional[bool]:
    """持有者进程是否存活；None = 无法判定（psutil 不可用 / pid 缺失）。

    Windows 陷阱：**禁用 os.kill(pid, 0)**——CPython 在 Windows 上对任意信号都会
    真正终止目标进程（仅 CTRL_C_EVENT/CTRL_BREAK_EVENT 例外），故一律走 psutil。
    """
    if pid is None:
        return None
    try:
        import psutil  # 运行期导入：未安装一律 fail-closed（不回收）
    except Exception:
        return None
    try:
        return bool(psutil.pid_exists(int(pid)))
    except Exception:
        return None


def _pid_create_time(pid) -> Optional[float]:
    """进程启动时间（秒）；用于排除 PID 复用。取不到返回 None。"""
    if pid is None:
        return None
    try:
        import psutil
    except Exception:
        return None
    try:
        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return None


def diagnose_holder(holder: Optional[dict], now: Optional[float] = None) -> dict:
    """只读诊断当前锁状态（不写任何文件）。

    返回 {verdict, predicate, reclaimable, stale, heartbeat_age, host, detail}。
    reclaimable 仅表示「判据成立」；是否真正回收还取决于开关与 CAS（见 try_reclaim_stale）。
    """
    now = time.time() if now is None else now
    if not holder:
        return {"verdict": V_FREE, "predicate": None, "reclaimable": False,
                "stale": False, "heartbeat_age": None, "host": None, "detail": "无锁"}
    hb = holder.get("heartbeat")
    try:
        age = now - float(hb)
    except (TypeError, ValueError):
        age = None
    stale = age is not None and age > STALE_SECONDS
    pid = holder.get("pid")
    version = holder.get("v")
    host = holder.get("host")
    task = holder.get("task_id")
    out = {"verdict": V_UNDECIDABLE, "predicate": None, "reclaimable": False,
           "stale": bool(stale), "heartbeat_age": age, "host": host, "detail": ""}

    if task == "<unreadable>":
        out["detail"] = "锁文件内容不可解析"
        return out
    if age is None:
        out["detail"] = "payload 无有效 heartbeat"
        return out
    if not stale:
        out.update(verdict=V_LIVE_FRESH, detail="心跳新鲜（正常写竞争）")
        return out
    legacy = (version != 2)
    if not legacy:
        if host and host != _hostname():
            out.update(verdict=V_CROSS_HOST, detail=f"payload host={host} != 本机")
            return out
    alive = _pid_alive(pid)
    if alive is None:
        out["detail"] = ("psutil 不可用或 payload 无 pid" if pid is not None
                         else "payload 无 pid")
        return out
    if alive:
        ct = holder.get("pid_create_time")
        local_ct = _pid_create_time(pid)
        if (not legacy) and ct and local_ct and abs(float(ct) - float(local_ct)) > 1.0:
            out.update(verdict=V_STALE_DEAD, reclaimable=True,
                       predicate="v2_pid_reused",
                       detail=f"pid={pid} 已被复用（create_time 不符），原持有者已终止")
            return out
        out.update(verdict=V_STALE_ALIVE,
                   detail=f"pid={pid} 仍存活但心跳停更 {int(age)}s")
        return out
    out.update(verdict=V_STALE_DEAD, reclaimable=True,
               predicate=("legacy_weak" if legacy else "v2_local_dead"),
               detail=f"pid={pid} 已不存在（心跳停更 {int(age)}s）")
    return out


def _selfheal_enabled() -> bool:
    v = os.environ.get(SELFHEAL_ENV)
    if v is None:
        return True
    return str(v).strip().lower() not in ("0", "false", "off", "no", "")


def _same_holder(a: Optional[dict], b: Optional[dict]) -> bool:
    """CAS 比对：四字段逐位一致（legacy 无 token → 双方 None 亦算一致）。"""
    if not a or not b:
        return False
    for k in CAS_FIELDS:
        if a.get(k) != b.get(k):
            return False
    return True


def _fmt_age(age) -> str:
    return "-" if age is None else str(int(age))


def _audit_reclaim(holder: dict, diag: dict, reclaimer_task: str) -> None:
    """回收审计：单行文本，失败不影响回收（但会记 debug）。"""
    try:
        ts = datetime.now().astimezone().isoformat()
        line = (
            f"{ts} reclaim STALE+DEAD "
            f"holder={{pid={holder.get('pid')},task={holder.get('task_id')},"
            f"heartbeat_age={_fmt_age(diag.get('heartbeat_age'))}s,"
            f"host={holder.get('host') or 'legacy'}}} "
            f"predicate={diag.get('predicate')} "
            f"reclaimer={{pid={os.getpid()},task={reclaimer_task}}} "
            f"token={holder.get('token') or '-'}"
        )
        with open(_audit_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        logger.debug("[write-lock] 回收审计写盘失败（不影响回收）", exc_info=True)


def try_reclaim_stale(holder: dict, diag: dict,
                      reclaimer_task: str = "unnamed") -> bool:
    """安全回收「陈锁 + 持有者进程已不存在」的残留锁。

    协议（原子）：取回收互斥 → 重读 payload 与判定快照 CAS 比对 → 复核判定 → unlink
    → 释放互斥。任一步不成立即放弃（退回正常等待），绝不强删。
    """
    if not _selfheal_enabled() or not (diag or {}).get("reclaimable"):
        return False
    rp = _reclaim_path()
    try:
        fd = os.open(str(rp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False  # 已有回收者在进行：交由正常等待路径（下轮再看）
    except OSError:
        return False
    try:
        os.close(fd)
        try:
            rp.write_text(json.dumps({"pid": os.getpid(),
                                      "task_id": reclaimer_task,
                                      "heartbeat": time.time()}), encoding="utf-8")
        except OSError:
            pass
        cur = read_holder()
        if not _same_holder(cur, holder):
            return False  # CAS 失败：锁已被刷新/换人（持有者复活或已被回收）
        d2 = diagnose_holder(cur)
        if d2.get("verdict") != V_STALE_DEAD or not d2.get("reclaimable"):
            return False  # 复核：判定已变化（如 pid 复活）
        lock_path().unlink(missing_ok=True)
        _audit_reclaim(holder, d2, reclaimer_task)
        logger.warning(
            "[write-lock] 回收残留写锁（持有者进程已不存在）: lock_path=%s "
            "持有者 pid=%s task=%s heartbeat_age=%ss 判据=%s 回收者 pid=%s task=%s",
            lock_path(), holder.get("pid"), holder.get("task_id"),
            _fmt_age(d2.get("heartbeat_age")), d2.get("predicate"),
            os.getpid(), reclaimer_task)
        return True
    except OSError:
        return False
    finally:
        try:
            rp.unlink(missing_ok=True)
        except OSError:
            pass


_depth = 0  # 同进程重入深度（CLI 守卫 + 内部 ensure 共存场景）
_current_lock: Optional["WriteLock"] = None   # 本进程当前的外层持锁句柄（所有权校验对象）
_live_handles: list = []          # 本进程持有的非重入句柄（供 atexit/信号尽力释放）
_ensure_handles: list = []        # ensure_write_lock 配对栈
_prev_signal_handlers: dict = {}


def _register_handle(lk: "WriteLock") -> None:
    _live_handles.append(lk)


def _unregister_handle(lk: "WriteLock") -> None:
    try:
        _live_handles.remove(lk)
    except ValueError:
        pass


def assert_lock_owner() -> None:
    """校验本进程当前持有的写锁仍归自己所有（未持锁时为空操作）。

    接入点：writers._write_locked 入口——防止「锁被他人取得后继续写」造成静默双写；
    锁文件仅被外部删除时由 WriteLock.assert_still_owner 原子重建恢复（见其 docstring）。
    """
    lk = _current_lock
    if lk is not None:
        lk.assert_still_owner()


def release_all_write_locks() -> None:
    """尽力释放本进程持有的全部写锁（atexit / 信号处理器用；幂等）。"""
    global _current_lock
    for lk in list(_live_handles):
        try:
            lk.release()
        except Exception:
            pass
    while _ensure_handles:
        try:
            _ensure_handles.pop().release()
        except Exception:
            break
    _current_lock = None


def _graceful_handler(signum, frame):
    """信号处理器：先尽力释放写锁，再交回原处理器（保持既有退出语义）。"""
    try:
        release_all_write_locks()
    except Exception:
        pass
    prev = _prev_signal_handlers.get(signum, signal.SIG_DFL)
    if callable(prev):
        return prev(signum, frame)
    try:
        signal.signal(signum, prev if prev is not None else signal.SIG_DFL)
    except Exception:
        pass
    if signum == getattr(signal, "SIGINT", None):
        raise KeyboardInterrupt  # 保持默认 Ctrl+C 语义
    try:
        os.kill(os.getpid(), signum)
    except Exception:
        pass


def _install_graceful_handlers() -> None:
    """持锁期间安装（仅主线程）；不覆盖他人已装处理器，释放时按原值还原。"""
    if threading.current_thread() is not threading.main_thread():
        return
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None or sig in _prev_signal_handlers:
            continue
        try:
            _prev_signal_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _graceful_handler)
        except (ValueError, OSError, AttributeError):
            _prev_signal_handlers.pop(sig, None)


def _restore_graceful_handlers() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    for sig, prev in list(_prev_signal_handlers.items()):
        try:
            # 只在当前处理器仍是本模块的情况下还原，避免覆盖期间他人安装的处理器
            if signal.getsignal(sig) is _graceful_handler:
                signal.signal(sig, prev)
        except (ValueError, OSError, TypeError):
            pass
        _prev_signal_handlers.pop(sig, None)


atexit.register(release_all_write_locks)


def acquire_write_lock(task_id: str = "unnamed", timeout_s: float = 30.0,
                       poll_s: float = 0.5) -> WriteLock:
    """获取写锁；失败抛 WriteLockHeld（fail-closed）。

    同进程重入安全：已持锁时返回浅句柄（release 只减计数）——消除 CLI 守卫与内部
    ensure_write_lock 的自死锁。
    陈锁 + 持有者进程已不存在 → 由本函数安全回收后立即重试（批一自愈）。
    """
    global _depth, _current_lock
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
            diag = diagnose_holder(holder)
            if diag.get("reclaimable") and try_reclaim_stale(
                    holder, diag, reclaimer_task=task_id):
                continue  # 回收成功：立即重试获取
            if time.time() - t0 >= timeout_s:
                raise WriteLockHeld(
                    holder or {"pid": None, "task_id": "unknown"},
                    stale=bool(diag.get("stale")), diagnosis=diag)
            time.sleep(poll_s)
            continue
        os.close(fd)
        lock = WriteLock(task_id=task_id, _path=p, token=uuid.uuid4().hex)
        try:
            lock._write_payload()
        except OSError:
            logger.debug("[write-lock] payload 写入失败（继续持锁）", exc_info=True)
        _depth = 1
        _current_lock = lock
        _register_handle(lock)
        _install_graceful_handlers()
        return lock


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
