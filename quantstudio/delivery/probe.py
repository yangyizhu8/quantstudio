# -*- coding: utf-8 -*-
"""前置探测原语（FR-003 / FR-012 / FR-035 / FR-013②）。

只做**判定与读数**，不产生副作用（唯一例外：可写探测会 create+delete 一个临时文件）。
导出与导入的内核共用这些原语，避免两套判据（plan §1.3）。
"""
from __future__ import annotations

import json
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
    """`import_dir` 下**恰好一个**含 manifest.json 的一层子目录（FR-038）。

    返回 (包目录 or None, 问题清单)。
    """
    root = Path(import_dir)
    if not root.exists() or not root.is_dir():
        return None, [f"导入目录不存在或不是目录：{root}"]
    candidates = sorted(
        p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").exists()
    )
    if len(candidates) == 1:
        return candidates[0], []
    if not candidates:
        return None, [
            f"未找到交付包（{root} 下没有含 manifest.json 的子目录）。"
            "请确认选择的是交付包的**上一层**目录"
        ]
    return None, [
        "检测到多个交付包，位置不明确："
        + "；".join(str(p.name) for p in candidates)
        + "。请让所选目录下只保留一个交付包"
    ]
