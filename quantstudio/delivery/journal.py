# -*- coding: utf-8 -*-
"""导入 journal：相位机 + 原子写 + 恢复决策（FR-036 / FR-014 / T6）。

**恢复流程 MUST NOT 重新推断模式**——有残留 journal 一律走恢复二选一；
journal 是崩溃重入的唯一权威（按"文件是否存在"重推模式会把半新半旧当原始态，污染唯一回退资产）。

相位单调前进：
    lock_acquired → backup_started → backup_done → overwrite_started
    → overwrite_done → reverify_started → done
回滚：rolling_back → rolled_back（终态删除 journal）
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .constants import JOURNAL_NAME, JOURNAL_VERSION

PHASES = (
    "lock_acquired",
    "backup_started",
    "backup_done",
    "overwrite_started",
    "overwrite_done",
    "reverify_started",
    "done",
    "rolling_back",
    "rolled_back",
)
TERMINAL_PHASES = frozenset({"done", "rolled_back"})


class JournalError(RuntimeError):
    """journal 不可信/不匹配（fail-closed：拒绝自动恢复）。"""


@dataclass
class JournalFile:
    target_name: str
    tmp_name: str
    backup_name: Optional[str] = None
    size: Optional[int] = None
    sha256: Optional[str] = None
    state: str = "pending"          # pending | done


@dataclass
class Journal:
    phase: str
    mode: str                        # update | repair | install
    data_root: str
    package_dir: str
    package_manifest_sha256: str
    manifest_schema_version: int
    files: List[JournalFile] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)
    backup_dir: Optional[str] = None
    forensic: Optional[Dict[str, Any]] = None
    lock_task_id: str = "delivery_import"
    started_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    journal_version: int = JOURNAL_VERSION
    host: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        doc = asdict(self)
        doc["journal_version"] = self.journal_version
        return doc


def journal_path(data_root: Path) -> Path:
    return Path(data_root) / JOURNAL_NAME


def load(data_root: Path) -> Optional[Journal]:
    path = journal_path(data_root)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise JournalError(f"journal 无法解析（拒绝自动恢复）：{exc}") from exc
    version = doc.get("journal_version")
    if version != JOURNAL_VERSION:
        raise JournalError(f"未知 journal_version={version!r}（拒绝自动恢复，需人工处置）")
    phase = doc.get("phase")
    if phase not in PHASES:
        raise JournalError(f"journal.phase 非法：{phase!r}（拒绝自动恢复）")
    root = Path(doc.get("data_root") or "")
    if root.resolve() != Path(data_root).resolve():
        raise JournalError(
            f"journal 属于另一数据根（{root} ≠ {data_root}），拒绝自动恢复"
        )
    return Journal(
        phase=phase,
        mode=str(doc.get("mode") or "update"),
        data_root=str(doc.get("data_root")),
        package_dir=str(doc.get("package_dir") or ""),
        package_manifest_sha256=str(doc.get("package_manifest_sha256") or ""),
        manifest_schema_version=int(doc.get("manifest_schema_version") or 0),
        files=[JournalFile(**item) for item in doc.get("files") or []],
        created_files=list(doc.get("created_files") or []),
        backup_dir=doc.get("backup_dir"),
        forensic=doc.get("forensic"),
        lock_task_id=str(doc.get("lock_task_id") or "delivery_import"),
        started_at=str(doc.get("started_at") or ""),
        updated_at=str(doc.get("updated_at") or ""),
        journal_version=version,
        host=dict(doc.get("host") or {}),
    )


def save(journal: Journal, data_root: Optional[Path] = None) -> Path:
    """原子写（tmp + os.replace）：写失败即抛，调用方须中止并回滚。"""
    root = Path(data_root or journal.data_root)
    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    journal.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    tmp = path.with_name(f"{JOURNAL_NAME}.tmp{os.getpid()}")
    tmp.write_text(
        json.dumps(journal.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)
    return path


def delete(data_root: Path) -> None:
    journal_path(data_root).unlink(missing_ok=True)


def advance(journal: Journal, phase: str) -> None:
    """相位前进（只允许向后，禁止回退到更早相位，rolling_back 除外）。"""
    if phase not in PHASES:
        raise JournalError(f"未知相位：{phase}")
    current = PHASES.index(journal.phase) if journal.phase in PHASES else -1
    target = PHASES.index(phase)
    if journal.phase != "rolling_back" and target < current:
        raise JournalError(f"相位不得回退：{journal.phase} → {phase}")
    journal.phase = phase


@dataclass
class JournalSummary:
    phase: str
    mode: str
    done_items: int
    total_items: int
    backup_dir: Optional[str]
    updated_at: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def detect_pending_journal(data_root: Path) -> Optional[JournalSummary]:
    """残留 journal 摘要（GUI 启动/进入导入入口各调一次）。**只读**。"""
    journal = load(data_root)
    if journal is None or journal.phase in TERMINAL_PHASES:
        return None
    done = sum(1 for item in journal.files if item.state == "done")
    return JournalSummary(
        phase=journal.phase,
        mode=journal.mode,
        done_items=done,
        total_items=len(journal.files),
        backup_dir=journal.backup_dir,
        updated_at=journal.updated_at,
    )


@dataclass
class RecoveryPlan:
    action: str          # "continue" | "rollback"
    restore: List[JournalFile]
    pending: List[JournalFile]
    delete_created: List[str]
    reverify_only: bool = False


def plan_recovery(journal: Journal, action: str) -> RecoveryPlan:
    """按 data-model §3.3 决策表生成恢复计划（纯函数，便于逐行单测）。"""
    if action not in ("continue", "rollback"):
        raise JournalError(f"未知恢复动作：{action}")
    done = [f for f in journal.files if f.state == "done"]
    pending = [f for f in journal.files if f.state != "done"]

    if journal.phase in ("lock_acquired", "backup_started"):
        if action == "continue":
            return RecoveryPlan("continue", [], journal.files, [])
        return RecoveryPlan("rollback", [], [], list(journal.created_files))

    if journal.phase in ("backup_done", "overwrite_started"):
        if action == "continue":
            return RecoveryPlan("continue", [], pending, [])
        return RecoveryPlan("rollback", done, [], list(journal.created_files))

    if journal.phase in ("overwrite_done", "reverify_started"):
        if action == "continue":
            return RecoveryPlan("continue", [], [], [], reverify_only=True)
        return RecoveryPlan("rollback", done, [], list(journal.created_files))

    if journal.phase == "rolling_back":
        if action == "continue":
            raise JournalError("回滚未完成，禁止「继续完成」；请重跑回滚")
        return RecoveryPlan("rollback", done, [], list(journal.created_files))

    if journal.phase == "rolled_back":
        if action == "continue":
            raise JournalError("已回滚到导入前，无「继续完成」语义")
        return RecoveryPlan("rollback", [], [], [])

    raise JournalError(f"相位 {journal.phase} 不支持恢复")
