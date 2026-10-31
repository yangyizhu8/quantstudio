# -*- coding: utf-8 -*-
"""前置探测原语（FR-003 / FR-012 / FR-035 / FR-013②）。

只做**判定与读数**，不产生副作用（唯一例外：可写探测会 create+delete 一个临时文件）。
导出与导入的内核共用这些原语，避免两套判据（plan §1.3）。
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 磁盘空间余量（manifest/临时文件/文件系统元数据）
SPACE_MARGIN_BYTES = 64 * 1024 * 1024
SPACE_COEFFICIENT = 1.1


class ProbeUnavailable(RuntimeError):
    """探测本身无法完成（如缺 psutil 无法判定进程存活）⇒ 调用方 MUST fail-closed。"""


@dataclass
class DaemonState:
    running: bool
    detail: Dict


def data_root() -> Path:
    """数据根唯一真相源（FR-012：不接受任何弹窗/参数覆盖）。"""
    from quantstudio._paths import DATA_ROOT

    return Path(DATA_ROOT)


def read_daemon_status(status_path: Optional[Path] = None) -> Optional[Dict]:
    path = Path(status_path) if status_path else data_root() / "daemon_status.json"
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"_unparsable": True, "_path": str(path)}
    return doc if isinstance(doc, dict) else {"_unparsable": True, "_path": str(path)}


def pid_alive(pid: int, create_time: Optional[float] = None) -> Optional[bool]:
    """进程存活判定（PID + create_time 双验，防 PID 复用）。无法判定返回 None。"""
    try:
        import psutil
    except Exception as exc:  # 无法判定 ⇒ 交调用方 fail-closed
        raise ProbeUnavailable(f"缺 psutil，无法判定进程存活：{exc}")
    try:
        proc = psutil.Process(int(pid))
    except Exception:
        return False
    try:
        if create_time is not None:
            actual = proc.create_time()
            if abs(float(actual) - float(create_time)) > 2.0:
                return False   # PID 复用
    except Exception:
        return None
    return True


def daemon_running(status_path: Optional[Path] = None) -> DaemonState:
    """DAEMON 是否在运行（status 文件 + PID 双验）。"""
    doc = read_daemon_status(status_path)
    if doc is None:
        return DaemonState(False, {"status_file": "absent"})
    pid = doc.get("pid")
    if not isinstance(pid, int):
        return DaemonState(True, {"status_file": "present", "pid": None, "note": "status 无有效 pid"})
    alive = pid_alive(pid, doc.get("create_time"))
    return DaemonState(
        bool(alive) or alive is None,
        {
            "status_file": "present",
            "pid": pid,
            "started_at": doc.get("started_at"),
            "alive": alive,
            "note": "psutil 无法判定 ⇒ fail-closed 视为在运行" if alive is None else "",
        },
    )


def write_lock_holder() -> Optional[Dict]:
    """当前写锁持有者（复用既有 `snapshot_lock.read_holder`，不改锁协议）。"""
    from quantstudio.pipeline.snapshot_lock import read_holder

    return read_holder()


def paths_disjoint(named: Dict[str, Optional[Path]]) -> List[str]:
    """三路径（或任意命名路径）两两互不包含校验；返回违反描述清单（空 = 通过）。"""
    resolved: Dict[str, Path] = {}
    for name, value in named.items():
        if value is None:
            continue
        resolved[name] = Path(value).expanduser().resolve()

    def _within(a: Path, b: Path) -> bool:
        try:
            a.relative_to(b)
            return True
        except ValueError:
            return False

    violations: List[str] = []
    names = sorted(resolved)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            a, b = resolved[left], resolved[right]
            if a == b:
                violations.append(f"{left} 与 {right} 是同一路径（{a}）")
            elif _within(a, b):
                violations.append(f"{left}（{a}）位于 {right}（{b}）之内")
            elif _within(b, a):
                violations.append(f"{right}（{b}）位于 {left}（{a}）之内")
    return violations


def disk_free_bytes(path: Path) -> int:
    target = Path(path)
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return int(shutil.disk_usage(str(probe)).free)


def required_export_bytes(file_sizes: List[int]) -> int:
    total = sum(int(x) for x in file_sizes)
    return int(total * SPACE_COEFFICIENT) + SPACE_MARGIN_BYTES


def required_import_bytes(bundle_bytes: int, *, same_volume: bool, backup_bytes: int = 0) -> int:
    if same_volume:
        return int(int(bundle_bytes) * SPACE_COEFFICIENT) + SPACE_MARGIN_BYTES
    return int(int(bundle_bytes) * SPACE_COEFFICIENT) + int(backup_bytes) + SPACE_MARGIN_BYTES


def same_volume(a: Path, b: Path) -> bool:
    """同卷判定（Windows 上 rename 跨卷会失败，必须先判 st_dev）。"""
    try:
        return Path(a).stat().st_dev == Path(b).stat().st_dev
    except Exception:
        # 目录可能尚不存在 ⇒ 用最近存在祖先比较
        def _dev(p: Path) -> int:
            probe = Path(p)
            while not probe.exists() and probe.parent != probe:
                probe = probe.parent
            return probe.stat().st_dev

        return _dev(a) == _dev(b)


def locate_package(import_dir: Path) -> Tuple[Optional[Path], List[str]]:
    """定位交付包目录（FR-038）。

    接受**两种**选择方式（实跑反馈：用户天然会直接指向包目录本身）：
      ① 所选目录本身就是包（自身含 `manifest.json`）→ 直接用它；
      ② 所选目录是包的**上一层** → 其下**恰好一个**含 `manifest.json` 的子目录。
    多个并列包仍判歧义（不猜）。

    返回 (包目录 or None, 问题清单)。
    """
    root = Path(import_dir)
    if not root.exists() or not root.is_dir():
        return None, [f"导入目录不存在或不是目录：{root}"]
    if (root / "manifest.json").exists():
        return root, []
    candidates = sorted(
        p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").exists()
    )
    if len(candidates) == 1:
        return candidates[0], []
    if not candidates:
        return None, [
            f"未找到交付包（{root} 下既无 manifest.json，也没有含 manifest.json 的子目录）。"
            "请选择交付包目录，或它所在的上一层目录"
        ]
    return None, [
        "检测到多个交付包，位置不明确："
        + "；".join(str(p.name) for p in candidates)
        + "。请让所选目录下只保留一个交付包（或直接选择其中一个包目录）"
    ]

def heals_stale_lock(task_id: str = "delivery_precheck") -> Dict:
    """PRECHECK 阶段的陈锁自愈（一次性）。

    背景（2026-11-01）：陈锁把 export/import 全部入口堵死——PRECHECK「有锁即拒」，
    而真正的自愈只在 `acquire` 侧触发，用户到不了那一步。
    行为：读锁 → 诊断；可回收（stale/reclaimable）⇒ 调 `try_reclaim_stale` 回收一次；
    返回 `{held, reclaimed, holder, diagnosis}` 供调用方写进读数与文案。
    """
    from quantstudio.pipeline import snapshot_lock as SL

    holder = SL.read_holder()
    if not holder:
        return {"held": False, "reclaimed": False, "holder": None, "diagnosis": {}}
    diag = SL.diagnose_holder(holder)
    # A-2 扩条：同一 host 上"持有者 pid 就是本进程"却没有被本进程句柄跟踪 ⇒ 判自身残留
    try:
        own = int(holder.get("pid") or -1)
        same_host = str(holder.get("host") or "") == SL._hostname()
        if same_host and own == os.getpid() and not SL.lock_held_locally():
            # 只诊断、不回收：持有者 pid 存活时 CAS 复核必然拒绝（verdict != stale_dead），
            # 硬回收等于绕过 CAS。给明确结论，让人/上层重启进程，而不是偷偷删锁。
            diag = dict(diag)
            diag["self_residual"] = True
            diag["detail"] = ("本进程残留迹象：锁文件 pid 与本进程相同，但本进程未持有句柄"
                              "（上次异常退出遗留）。请重启本进程后再试；或确认无写入后用 "
                              "`qs-deliver unlock --force`（仅当判定可回收时生效）")
    except Exception:
        pass
    if diag.get("reclaimable") or diag.get("stale"):
        try:
            SL.try_reclaim_stale(holder, diag, reclaimer_task=task_id)
        except Exception as exc:      # 回收失败不改判据：仍按"有锁"处理
            return {"held": True, "reclaimed": False, "holder": holder,
                    "diagnosis": diag, "error": f"{type(exc).__name__}: {exc}"}
        still = SL.read_holder()
        return {"held": bool(still), "reclaimed": not bool(still),
                "holder": still, "diagnosis": diag}
    return {"held": True, "reclaimed": False, "holder": holder, "diagnosis": diag}

# ── 空间诊断（B 段：门槛不动，只把"还差多少 / 该清哪里"说清楚）────────────────
_CLEANUP_PATTERNS = (
    ("backup_import_*", "导入备份（确认新库正常后可删）"),
    ("backup/*", "旧库备份（历史手工备份，按需保留；实测此处常是最大占用）"),
    ("snapshots/*.bak-*", "快照/锁的历史备份"),
)


def _tree_bytes(path: Path, *, depth: int = 1, limit_files: int = 400) -> Tuple[int, int]:
    """统计目录占用（**只读**，深度受限，避免拖慢拒绝路径）。返回 (bytes, files)。"""
    total, files = 0, 0
    try:
        stack = [(Path(path), 0)]
        while stack:
            cur, level = stack.pop()
            try:
                entries = list(os.scandir(cur))
            except OSError:
                continue
            for entry in entries:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if level < depth:
                        stack.append((Path(entry.path), level + 1))
                else:
                    total += st.st_size
                    files += 1
                    if files >= limit_files:
                        return total, files
    except Exception:
        pass
    return total, files


def space_report(need: int, free: int, data_root: Path) -> Dict:
    """空间不足时的结构化明细（供 CLI / GUI 共用；**只读扫描，绝不删除**）。

    用户裁定（2026-11-01）：门槛 `1.1×包体 + 64 MiB` **不放宽**；本函数只负责让拒绝可执行。
    """
    root = Path(data_root)
    items = []
    for pattern, note in _CLEANUP_PATTERNS:
        try:
            matches = sorted(Path(root).glob(pattern))
        except Exception:
            matches = []
        total, files = 0, 0
        for m in matches:
            if m.is_dir():
                b, f = _tree_bytes(m, depth=1)
            else:
                try:
                    b, f = m.stat().st_size, 1
                except OSError:
                    b, f = 0, 0
            total += b
            files += f
        if total > 0:
            items.append({
                "pattern": pattern,
                "note": note,
                "entries": len(matches),
                "files": files,
                "bytes": total,
            })
    items.sort(key=lambda x: x["bytes"], reverse=True)
    return {
        "need": int(need),
        "free": int(free),
        "shortfall": max(0, int(need) - int(free)),
        "candidates": items,
        "note": "只读扫描；程序不会删除任何备份目录（释放权在客户）",
    }

