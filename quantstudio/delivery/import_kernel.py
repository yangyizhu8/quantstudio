# -*- coding: utf-8 -*-
"""导入内核（W3）：九步状态机 + 三模式 + 备份/覆盖/回滚/复验 + journal 恢复。

契约见 `contracts/import_kernel.md` §3/§4；红线：**W3 全部测试只在 `tmp_path` fixture 内**
（一律不碰生产 `DATA_ROOT`）。

九步（FR-013，顺序不可交换）：
    LOAD_CONFIG → PRECHECK → VERIFY_READONLY → (fail ⇒ 零改动返回)
    → ACQUIRE_AND_JOURNAL → BACKUP → OVERWRITE → REVERIFY → RELEASE_AND_PASS
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config as delivery_config
from .config import resolve_config_root
from . import journal as J
from . import manifest as M
from . import probe as P
from . import stats as S
from . import verify as V
from . import winenv as W
from .constants import (
    BACKUP_DIR_PREFIX,
    ENGINE_KIND,
    Exit,
    FailureKind,
    LOCK_TASK_IMPORT,
    QUALITY_BASELINE_REL,
    RESTORE_README_NAME,
    TABLE_MAP_REL,
    WHITELIST,
)

ProgressCB = Callable[[int, int, str], None]
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 异常 ────────────────────────────────────────────────────────────────────
class ImportRefused(Exception):
    """前置检查拒绝（零改动）。"""

    def __init__(self, code: str, message: str, readings: Optional[Dict] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.readings = readings or {}


class RecoveryRequired(Exception):
    """检测到残留 journal ⇒ 必须先走 recover_journal（禁止重推模式）。"""

    def __init__(self, summary: J.JournalSummary):
        super().__init__(f"检测到未完成的导入（相位 {summary.phase}，{summary.done_items}/{summary.total_items} 已完成）")
        self.summary = summary


# ── 结果 ────────────────────────────────────────────────────────────────────
@dataclass
class ImportResult:
    ok: bool
    mode: str = ""
    backup: Dict = field(default_factory=dict)
    files: List[Dict] = field(default_factory=list)
    reverify: Dict = field(default_factory=dict)
    forensic: Optional[Dict] = None
    baseline: Dict = field(default_factory=dict)
    journal_finalized: bool = False
    warnings: List[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    exit_code: int = 0
    failure_kind: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    rollback_ok: Optional[bool] = None


@dataclass
class RecoveryResult:
    ok: bool
    journal_found: bool = False
    phase_before: str = ""
    action: str = ""
    completed_items: int = 0
    restored_files: List[str] = field(default_factory=list)
    deleted_created_files: List[str] = field(default_factory=list)
    reverify: Dict = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    exit_code: int = 0


# ── 测试注入点 ──────────────────────────────────────────────────────────────
def _acquire_lock(task_id: str = LOCK_TASK_IMPORT, timeout_s: float = 30.0):
    from quantstudio.pipeline.snapshot_lock import acquire_write_lock

    return acquire_write_lock(task_id, timeout_s=timeout_s)


def _release_lock() -> None:
    from quantstudio.pipeline.snapshot_lock import release_write_lock

    release_write_lock()


def _replace(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def _copy(src: Path, dst: Path) -> None:
    """分块复制（可注入 ENOSPC）。"""
    with Path(src).open("rb") as fin, Path(dst).open("wb") as fout:
        while True:
            block = fin.read(1 << 20)
            if not block:
                break
            fout.write(block)


def _hook_after_replace(count: int) -> None:
    """崩溃注入：QS_DELIVERY_TEST_HOOKS=1 + QS_DELIVERY_TEST_KILL_AFTER_REPLACES=N ⇒ 替换 N 项后真崩溃。"""
    if os.environ.get("QS_DELIVERY_TEST_HOOKS") != "1":
        return
    limit = os.environ.get("QS_DELIVERY_TEST_KILL_AFTER_REPLACES")
    if limit and count >= int(limit):
        os._exit(9)


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now().astimezone()


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _readable(path: Path) -> bool:
    try:
        if path.suffix == ".db" and path.name == "quantstudio.db":
            return V._measure_schema_status(path) != "unknown"
        con = __import__("sqlite3").connect(V.sqlite_ro_uri(path), uri=True)
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            con.close()
        return True
    except Exception:
        return False


def detect_mode(data_root: Path) -> str:
    """FR-014 三模式 + 恢复优先（残留 journal ⇒ 抛 RecoveryRequired，不重推模式）。"""
    summary = J.detect_pending_journal(data_root)
    if summary is not None:
        raise RecoveryRequired(summary)
    main = Path(data_root) / "quantstudio.db"
    if not main.exists():
        return "install"
    if _readable(main):
        return "update"
    return "repair"


# ── 备份 / 覆盖 / 回滚 ──────────────────────────────────────────────────────
def _backup(
    *, data_root: Path, journal: J.Journal, mode: str, manifest_doc: Dict,
    progress: Optional[ProgressCB], warnings: List[str],
) -> None:
    J.advance(journal, "backup_started")
    J.save(journal, data_root)

    stamp = _now().strftime("%Y%m%d_%H%M%S")
    backup_dir = data_root / f"{BACKUP_DIR_PREFIX}{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    journal.backup_dir = str(backup_dir)

    same_volume = P.same_volume(data_root, backup_dir)
    journal.files = []
    for name in WHITELIST:
        source = data_root / name
        exists = source.exists()
        entry = J.JournalFile(
            target_name=name,
            tmp_name=f"{name}.incoming",
            # 只有"导入前存在"的文件才有备份；不存在者属创建（安装/补齐），回滚时删除
            backup_name=name if exists else None,
            size=source.stat().st_size if exists else None,
            state="pending",
        )
        if not exists:
            journal.created_files.append(name)
            journal.files.append(entry)
            J.save(journal, data_root)
            continue
        readable_before = _readable(source)
        if mode == "repair" and name == "quantstudio.db" and not readable_before:
            # 修复模式：取证摘要（探测失败 ≠ 数据无价值；原件必须保留）
            entry.sha256 = _try_sha256(source)
            journal.forensic = {
                "bytes": entry.size,
                "sha256": entry.sha256,
                "open_error": "无法只读打开（schema 探测失败）",
                "schema_status": "unknown",
            }
        try:
            if same_volume:
                _replace(source, backup_dir / name)
            else:
                _copy(source, backup_dir / name)
                source.unlink()
            if progress:
                progress(0, 0, f"BACKUP:{name}")
        except Exception as exc:
            if readable_before:
                raise
            warnings.append(f"{name} 备份失败但源本就不可读，继续：{exc}")
        journal.files.append(entry)
        J.save(journal, data_root)

    (backup_dir / RESTORE_README_NAME).write_text(
        "本目录是导入前旧库的原件（同卷移动而来，零额外占用）。\n"
        "确认新库运行正常前请勿删除。\n"
        "恢复方法：把本目录内的文件移回 data/ 覆盖同名文件。\n"
        "确认无误后可删除本目录释放空间（释放权在客户）。\n",
        encoding="utf-8",
        newline="\n",
    )
    J.advance(journal, "backup_done")
    J.save(journal, data_root)


def _try_sha256(path: Path) -> Optional[str]:
    try:
        return M.sha256_path(path)
    except Exception:
        return None


def _overwrite(
    *, data_root: Path, journal: J.Journal, package_dir: Path,
    progress: Optional[ProgressCB],
) -> None:
    J.advance(journal, "overwrite_started")
    J.save(journal, data_root)

    total = sum((package_dir / f.target_name).stat().st_size for f in journal.files)
    done_bytes = 0
    for item in journal.files:                      # 先全部落 *.incoming
        src = package_dir / item.target_name
        tmp = data_root / item.tmp_name
        _copy(src, tmp)
        done_bytes += src.stat().st_size
        if progress:
            progress(done_bytes, total, "STAGE")

    replaced = 0
    for item in journal.files:                      # 再逐个 os.replace
        _replace(data_root / item.tmp_name, data_root / item.target_name)
        item.state = "done"
        replaced += 1
        J.save(journal, data_root)
        _hook_after_replace(replaced)
        if progress:
            progress(done_bytes, total, f"REPLACE:{item.target_name}")

    J.advance(journal, "overwrite_done")
    J.save(journal, data_root)


def _rollback(*, data_root: Path, journal: J.Journal, warnings: List[str]) -> Tuple[bool, List[str]]:
    """按 journal 反向回滚；返回 (ok, restored)。删除动作仅限 created_files（FR-017）。"""
    restored: List[str] = []
    try:
        J.advance(journal, "rolling_back")
        J.save(journal, data_root)
        backup_dir = Path(journal.backup_dir) if journal.backup_dir else None
        # 关键：备份是 rename（同卷），一旦备份完成原件即不在原位——回滚不能只看 state=done，
        # 必须把所有"有备份且备份仍在"的文件都还原（否则失败于 staging 阶段会丢主库）。
        for item in journal.files:
            target = data_root / item.target_name
            if backup_dir and item.backup_name and (backup_dir / item.backup_name).exists():
                _replace(backup_dir / item.backup_name, target)
                restored.append(item.target_name)
                item.state = "pending"
            elif item.backup_name is None and target.exists():
                target.unlink(missing_ok=True)     # 导入前不存在 ⇒ 删除本次创建
        for item in journal.files:
            (data_root / item.tmp_name).unlink(missing_ok=True)   # 清理 staging 临时件
        J.save(journal, data_root)
        J.advance(journal, "rolled_back")
        J.save(journal, data_root)
        J.delete(data_root)
        return True, restored
    except Exception as exc:
        warnings.append(f"回滚失败：{type(exc).__name__}: {exc}")
        return False, restored


def _reverify(*, data_root: Path, config_root: Path, manifest_doc: Dict) -> Dict:
    """复验：再跑 FR-010 四要素中的统计口径，产出逐表四态与「可增量」结论。"""
    rows, table_set = V._collect_tables(data_root, config_root)
    can = S.can_incremental(rows)
    baseline = {"stale": False, "note": ""}
    baseline_path = config_root / QUALITY_BASELINE_REL
    doc = _read_json(baseline_path, {}) or {}
    generated_at = str(doc.get("generated_at") or "")[:10]
    newest = max((r.get("max_date") or "" for r in rows), default="")
    if generated_at and newest and newest > generated_at:
        baseline = {
            "stale": True,
            "note": f"巡检基线（{generated_at}）早于包内数据（{newest}）：继续沿用会产生误报，请联系实施方获取新基线",
        }
    else:
        baseline = {"stale": False, "note": "基线可能过时（建议定期向实施方索取新基线）"}
    return {
        "tables": rows,
        "table_set": table_set,
        "summary": f"{len(rows)} 表：可增量" if can else f"{len(rows)} 表：存在需关注项",
        "can_incremental": can,
        "baseline": baseline,
    }


# ── 主流程 ──────────────────────────────────────────────────────────────────
def import_bundle(
    *,
    import_dir: Path,
    config_dir: Optional[Path] = None,
    data_root: Optional[Path] = None,
    dry_run: bool = False,
    progress: Optional[ProgressCB] = None,
    cancel: Any = None,
    allow_repair: bool = True,
    now: Optional[datetime] = None,
) -> ImportResult:
    started = time.monotonic()
    config_root = resolve_config_root(
        Path(config_dir) if config_dir is not None else _REPO_ROOT / "config"
    )
    root = Path(data_root) if data_root is not None else P.data_root()
    result = ImportResult(ok=False)

    def finish(**kwargs) -> ImportResult:
        for key, value in kwargs.items():
            setattr(result, key, value)
        result.elapsed_sec = round(time.monotonic() - started, 3)
        return result

    # ① LOAD_CONFIG
    cfg, cfg_warnings = delivery_config.load(config_root)
    result.warnings.extend(cfg_warnings)
    violations = P.paths_disjoint(
        {"DATA_ROOT": root, "import_dir": Path(import_dir),
         **({"export_dir": cfg.export_path()} if cfg.export_path() else {})}
    )
    if violations:
        return finish(failure_kind=FailureKind.CONFIG, reasons=violations, exit_code=Exit.CONFIG)

    # ② PRECHECK
    try:
        mode = detect_mode(root)
    except RecoveryRequired as exc:
        result.warnings.append(str(exc))
        summary = exc.summary
        return finish(
            failure_kind="recovery_required",
            reasons=[f"残留 journal：相位 {summary.phase}（{summary.done_items}/{summary.total_items}）"],
            exit_code=Exit.RECOVERY_NEEDED,
            mode=summary.mode,
        )
    if mode == "repair" and not allow_repair:
        return finish(failure_kind=FailureKind.CONFIG, reasons=["检测到坏库但未允许修复模式"], exit_code=Exit.CONFIG)
    result.mode = mode

    package_dir, problems = P.locate_package(import_dir)
    if package_dir is None:
        return finish(failure_kind=FailureKind.PACKAGE, reasons=problems, exit_code=Exit.PACKAGE)

    daemon = P.daemon_running()
    if daemon.running:
        return finish(
            failure_kind="refused", reasons=["采集正在运行，请先停止采集"],
            exit_code=Exit.REFUSED,
        )
    holder = P.write_lock_holder()
    if holder:
        return finish(
            failure_kind="refused",
            reasons=[f"写锁被持有：{holder.get('task_id')}（pid={holder.get('pid')}）"],
            exit_code=Exit.REFUSED,
        )
    ok, detail = W.probe_dir_writable(root)
    if not ok:
        return finish(failure_kind="refused", reasons=[f"数据目录不可写：{detail}"], exit_code=Exit.REFUSED)

    # ③ VERIFY_READONLY（零改动）
    report = V.verify_bundle(import_dir=import_dir, config_dir=config_root, data_root=root, progress=progress)
    result.warnings.extend(report.warnings)
    if not report.ok:
        return finish(
            failure_kind=report.failure_kind,
            reasons=report.reasons,
            exit_code=report.exit_code or Exit.PACKAGE,
        )
    manifest_doc = report.doc if hasattr(report, "doc") else None
    if manifest_doc is None:
        read = M.read_manifest(package_dir / "manifest.json")
        manifest_doc = read.doc or {}
    if dry_run:
        return finish(ok=True, mode=mode, exit_code=Exit.OK, reverify={"dry_run": True})

    # 磁盘空间（备份前，FR-015）
    bundle_bytes = sum(int(f["bytes"]) for f in manifest_doc.get("files", []))
    same_vol = P.same_volume(root, root)
    required = P.required_import_bytes(bundle_bytes, same_volume=True,
                                       backup_bytes=0 if mode == "install" else bundle_bytes)
    free = P.disk_free_bytes(root)
    if free < required:
        return finish(
            failure_kind="refused",
            reasons=[f"空间不足：需要 {required} B，可用 {free} B"],
            exit_code=Exit.REFUSED,
        )

    # ⑤ ACQUIRE_AND_JOURNAL
    from quantstudio.pipeline.snapshot_lock import WriteLockHeld

    try:
        _acquire_lock()
    except WriteLockHeld as exc:
        return finish(failure_kind="refused", reasons=[f"写锁被持有：{exc}"], exit_code=Exit.REFUSED)

    journal = J.Journal(
        phase="lock_acquired",
        mode=mode,
        data_root=str(root),
        package_dir=str(package_dir),
        package_manifest_sha256=M.sha256_path(package_dir / "manifest.json"),
        manifest_schema_version=int(manifest_doc.get("manifest_schema_version") or 1),
        host={"hostname": os.environ.get("COMPUTERNAME", ""), "pid": os.getpid()},
    )
    try:
        J.save(journal, root)

        # ⑥ BACKUP（安装模式跳过）
        backup_info: Dict = {"dir": None, "bytes": 0, "method": "none"}
        if mode != "install":
            _backup(
                data_root=root, journal=journal, mode=mode,
                manifest_doc=manifest_doc, progress=progress, warnings=result.warnings,
            )
            backup_dir = Path(journal.backup_dir)
            backup_bytes = sum(f.stat().st_size for f in backup_dir.glob("*") if f.is_file() and f.name != RESTORE_README_NAME)
            backup_info = {
                "dir": str(backup_dir),
                "bytes": backup_bytes,
                "method": "rename" if P.same_volume(root, backup_dir) else "copy",
            }
        else:
            # 安装模式：无文件可备份，但仍须把三件登记为"创建"（否则 overwrite 无事可做）
            journal.files = [
                J.JournalFile(
                    target_name=name,
                    tmp_name=f"{name}.incoming",
                    backup_name=None,
                    size=None,
                    state="pending",
                )
                for name in WHITELIST
            ]
            journal.created_files = list(WHITELIST)
            J.advance(journal, "backup_started")
            J.advance(journal, "backup_done")
            J.save(journal, root)

        # ⑦ OVERWRITE
        try:
            _overwrite(data_root=root, journal=journal, package_dir=package_dir, progress=progress)
        except BaseException as exc:
            ok_rb, restored = _rollback(data_root=root, journal=journal, warnings=result.warnings)
            return finish(
                ok=False,
                backup=backup_info,
                files=[{"name": f.target_name, "state": f.state} for f in journal.files],
                rollback_ok=ok_rb,
                reasons=[f"覆盖失败并已回滚：{type(exc).__name__}: {exc}"] if ok_rb else [f"覆盖失败且回滚失败：{exc}"],
                exit_code=Exit.ROLLED_BACK if ok_rb else Exit.ROLLBACK_FAILED,
            )

        # ⑧ REVERIFY
        J.advance(journal, "reverify_started")
        J.save(journal, root)
        reverify = _reverify(data_root=root, config_root=config_root, manifest_doc=manifest_doc)

        # ⑨ RELEASE_AND_PASS
        J.advance(journal, "done")
        J.save(journal, root)
        J.delete(root)
        result.forensic = journal.forensic
        return finish(
            ok=True,
            backup=backup_info,
            files=[
                {"name": f.target_name, "action": "created" if f.target_name in journal.created_files else "replaced",
                 "bytes": f.size, "sha256_ok": None}
                for f in journal.files
            ],
            reverify=reverify,
            baseline=reverify.get("baseline", {}),
            journal_finalized=True,
            exit_code=Exit.OK,
        )
    finally:
        try:
            _release_lock()
        except Exception as exc:
            result.warnings.append(f"释放写锁失败：{type(exc).__name__}: {exc}")


# ── 恢复 ────────────────────────────────────────────────────────────────────
def recover_journal(
    *,
    action: str,
    data_root: Optional[Path] = None,
    config_dir: Optional[Path] = None,
    progress: Optional[ProgressCB] = None,
) -> RecoveryResult:
    config_root = resolve_config_root(
        Path(config_dir) if config_dir is not None else _REPO_ROOT / "config"
    )
    root = Path(data_root) if data_root is not None else P.data_root()
    result = RecoveryResult(ok=False, action=action)

    journal = J.load(root)
    if journal is None:
        result.reasons.append("无残留 journal")
        result.exit_code = Exit.RECOVERY_NEEDED
        return result

    result.journal_found = True
    result.phase_before = journal.phase
    try:
        plan = J.plan_recovery(journal, action)
    except J.JournalError as exc:
        result.reasons.append(str(exc))
        result.exit_code = Exit.RECOVERY_NEEDED
        return result

    try:
        _acquire_lock()
    except Exception as exc:
        result.reasons.append(f"恢复取锁失败：{exc}")
        result.exit_code = Exit.REFUSED
        return result

    try:
        if action == "rollback":
            ok_rb, restored = _rollback(data_root=root, journal=journal, warnings=result.reasons)
            result.restored_files = restored
            result.ok = ok_rb
            result.exit_code = Exit.OK if ok_rb else Exit.ROLLBACK_FAILED
            return result

        # continue
        if plan.reverify_only:
            manifest_doc = M.read_manifest(Path(journal.package_dir) / "manifest.json").doc or {}
            result.reverify = _reverify(data_root=root, config_root=config_root, manifest_doc=manifest_doc)
        else:
            if journal.phase in ("lock_acquired", "backup_started"):
                _backup(
                    data_root=root, journal=journal, mode=journal.mode,
                    manifest_doc={}, progress=progress, warnings=result.reasons,
                )
            manifest_doc = M.read_manifest(Path(journal.package_dir) / "manifest.json").doc or {}
            _overwrite(
                data_root=root, journal=journal, package_dir=Path(journal.package_dir), progress=progress
            )
            J.advance(journal, "reverify_started")
            J.save(journal, root)
            result.reverify = _reverify(data_root=root, config_root=config_root, manifest_doc=manifest_doc)
        J.advance(journal, "done")
        J.save(journal, root)
        J.delete(root)
        result.ok = True
        result.exit_code = Exit.OK
        return result
    finally:
        try:
            _release_lock()
        except Exception:
            pass
