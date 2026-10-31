# -*- coding: utf-8 -*-
"""导出内核（W1）：六相位 `PRECHECK → ACQUIRE → CHECKPOINT → WAL_ZERO → COPY → ATTEST`。

契约见 `contracts/export_kernel.md`。三条最容易做错、本实现专门守住的语义：
1. **复制与统计都在锁窗口内**（统计取**产出副本**）——否则 manifest 描述的不是交付件；
2. **WAL 非零即 fail-closed 且零产出**（不是"带着 WAL 发货"）；
3. **复制中断即作废整包**（不变式 9）——不留下"半包 + 无 manifest"的目录。

测试注入点（仅测试 monkeypatch，不暴露为参数）：`_acquire_lock` / `_release_lock` /
`_copy_file` / `_checkpoint_all`。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import manifest as M
from . import probe as P
from . import stats as S
from . import winenv as W
from . import verify as V
from .constants import (
    ALIGNMENT_RULES_REL,
    AUX_PATHS_REL,
    COLLECTOR_TASKS_REL,
    ENGINE_KIND,
    EXPORT_ALLOWED_SCHEMA,
    LOCK_TASK_EXPORT,
    TABLE_MAP_REL,
    WHITELIST,
)

ProgressCB = Callable[[int, int, str], None]
CHUNK = 1 << 20          # 1 MiB
PROGRESS_MIN_INTERVAL = 0.05
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 异常 ────────────────────────────────────────────────────────────────────
class ExportRefused(Exception):
    """前置检查/前置门拒绝（**零产出**）。"""

    def __init__(self, code: str, message: str, readings: Optional[Dict] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.readings = readings or {}


class ExportAborted(Exception):
    """用户取消（Phase 1–4 的取消语义：干净退出，不留产出）。"""


class ExportFailed(Exception):
    """Phase 1 之后的失败（IO/磁盘/自检），产物整体作废。"""

    def __init__(self, phase: str, cause: BaseException):
        super().__init__(f"{phase} 阶段失败：{type(cause).__name__}: {cause}")
        self.phase = phase
        self.cause = cause


@dataclass(frozen=True)
class ExportResult:
    bundle_dir: Path
    manifest_path: Path
    files: Tuple[Dict, ...]
    total_bytes: int
    duration_sec: float
    qualified: bool
    self_check: Dict
    warnings: Tuple[str, ...] = field(default_factory=tuple)


# ── 测试注入点 ──────────────────────────────────────────────────────────────
def _acquire_lock(task_id: str = LOCK_TASK_EXPORT, timeout_s: float = 30.0):
    from quantstudio.pipeline.snapshot_lock import acquire_write_lock

    return acquire_write_lock(task_id, timeout_s=timeout_s)


def _release_lock() -> None:
    from quantstudio.pipeline.snapshot_lock import release_write_lock

    release_write_lock()


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now().astimezone()


def _cancelled(cancel: Any) -> bool:
    if cancel is None:
        return False
    fn = getattr(cancel, "is_set", None)
    if callable(fn):
        return bool(fn())
    return bool(cancel)


def _read_schema_status(path: Path) -> str:
    import duckdb

    from quantstudio.pipeline.qfq_schema_status import detect_schema_status

    con = duckdb.connect(str(path), read_only=True)
    try:
        return str(detect_schema_status(con).value)
    finally:
        con.close()


def _sqlite_probe(path: Path) -> None:
    """只读可打开性探测（immutable：**不得**在源目录或包内创建 -shm/-wal 边车）。"""
    con = sqlite3.connect(V.sqlite_ro_uri(path), uri=True)
    try:
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        con.close()


def _wal_bytes(path: Path) -> int:
    wal = path.with_name(f"{path.name}-wal")
    return wal.stat().st_size if wal.exists() else 0


def _new_bundle_dir(export_dir: Path, now: datetime) -> Path:
    """强制新建时间戳子目录；已存在则追加序号（**永不覆盖旧包**，FR-034）。"""
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    for idx in range(0, 100):
        candidate = export_dir / stamp if idx == 0 else export_dir / f"{stamp}-{idx}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
    raise RuntimeError(f"无法在 {export_dir} 下创建唯一的时间戳子目录（{stamp} 已用尽序号）")


def _copy_file(
    src: Path,
    dst: Path,
    *,
    progress: Optional[ProgressCB] = None,
    cancel: Any = None,
    offset: int = 0,
    total: int = 0,
    last_report: Optional[List[float]] = None,
) -> Dict:
    """单文件字节精确复制 + 流式哈希（单次读盘）；可取消。"""
    digest = hashlib.sha256()
    written = 0
    state = last_report if last_report is not None else [0.0]
    with Path(src).open("rb") as fin, Path(dst).open("wb") as fout:
        while True:
            if _cancelled(cancel):
                raise ExportAborted("用户取消（复制阶段）")
            block = fin.read(CHUNK)
            if not block:
                break
            fout.write(block)
            digest.update(block)
            written += len(block)
            if progress is not None:
                now_ts = time.monotonic()
                if now_ts - state[0] >= PROGRESS_MIN_INTERVAL:
                    state[0] = now_ts
                    progress(offset + written, total, "COPY")
    if progress is not None:
        progress(offset + written, total, "COPY")
    return {"bytes": written, "sha256": digest.hexdigest()}


def _checkpoint_all(src: Dict[str, Path]) -> Dict:
    """DuckDB `CHECKPOINT` + SQLite `wal_checkpoint(TRUNCATE)`；busy≠0 即 fail-closed。"""
    import duckdb

    main = src["quantstudio.db"]
    con = duckdb.connect(str(main))
    try:
        con.execute("CHECKPOINT")
    finally:
        con.close()

    sqlite_report: List[Dict] = []
    for name in WHITELIST:
        if ENGINE_KIND[name] != "sqlite":
            continue
        path = src[name]
        sqlite_con = sqlite3.connect(str(path))
        try:
            row = sqlite_con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            sqlite_con.close()
        busy, log = (int(row[0]), int(row[1])) if row else (0, 0)
        if busy:
            raise ExportRefused(
                "CHECKPOINT_BUSY",
                "同刻一致未达成：SQLite WAL checkpoint 被占用（仍有并发读者/写者）",
                {"path": name, "busy": busy, "log": log},
            )
        sqlite_report.append({"path": name, "busy": busy, "log": log})
    return {"duckdb": "ok", "sqlite": sqlite_report}


def _app_version() -> str:
    try:
        from quantstudio import __version__ as version  # type: ignore

        return str(version)
    except Exception:
        return "unknown"


def _producer_env() -> Dict:
    try:
        import duckdb

        duckdb_version = duckdb.__version__
    except Exception:
        duckdb_version = "unknown"
    return {
        "app": "QuantStudio",
        "version": _app_version(),
        "python": sys.version.split()[0],
        "duckdb": duckdb_version,
        "sqlite": sqlite3.sqlite_version,
        "platform": W.platform_tag(),
    }


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _attest_stats(bundle_dir: Path, config_root: Path) -> Tuple[List[Dict], Dict]:
    """对**产出副本**做逐表统计（manifest 自证 = 客户将拿到的这个文件）。"""
    table_map_path = config_root / TABLE_MAP_REL
    if not table_map_path.exists():
        raise ExportFailed(
            "ATTEST", FileNotFoundError(f"缺配置真源：{table_map_path}")
        )
    table_map = S.load_table_map(table_map_path)
    time_keys = S.load_alignment_time_keys(config_root / ALIGNMENT_RULES_REL)

    tasks_doc = _read_json(config_root / COLLECTOR_TASKS_REL, {}) or {}
    tasks = tasks_doc.get("tasks") if isinstance(tasks_doc, dict) else None
    known_tasks = set(tasks) if isinstance(tasks, dict) else None

    import duckdb

    con = duckdb.connect(str(bundle_dir / "quantstudio.db"), read_only=True)
    try:
        def query(sql: str):
            return con.execute(sql).fetchall()

        present = S.existing_tables(query)
        try:
            wm_rows = query("SELECT source, table_name, freq, last_date FROM source_watermark")
        except Exception:
            wm_rows = []
        wm_tables = {str(row[1]) for row in wm_rows}
        names = S.select_table_set(table_map, present, wm_tables)
        watermark = S.load_watermark_rows(wm_rows)

        rows = S.collect_table_stats(
            query,
            table_map,
            names,
            watermark,
            time_keys=time_keys,
            watermark_tables=wm_tables,
            known_tasks=known_tasks,
        )
        configured = {str(spec.get("target_table") or name) for name, spec in table_map.items()}
        out_of_map = [
            str(table_name)
            for (_source, table_name, _freq, _last) in wm_rows
            if str(table_name) not in configured
        ]
    finally:
        con.close()
    return rows, S.summarize(rows, out_of_map=out_of_map)


def _read_cutover(bundle_dir: Path) -> Optional[Dict]:
    import duckdb

    try:
        con = duckdb.connect(str(bundle_dir / "quantstudio.db"), read_only=True)
    except Exception:
        return None
    try:
        rows = con.execute(
            "SELECT a.cutover_id, c.source_generation FROM qfq_active_cutover a "
            "JOIN qfq_source_cutover c ON c.cutover_id = a.cutover_id LIMIT 1"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    if not rows:
        return None
    return {
        "cutover_id": str(rows[0][0]),
        "source_generation": str(rows[0][1]),
        "price_source": "mcp",
    }


# ── 主流程 ──────────────────────────────────────────────────────────────────
def export_bundle(
    *,
    export_dir: Path,
    data_root: Optional[Path] = None,
    config_dir: Optional[Path] = None,
    progress: Optional[ProgressCB] = None,
    cancel: Any = None,
    accept_sync_dir: bool = False,
    verify_hashes: bool = False,
    now: Optional[datetime] = None,
) -> ExportResult:
    started = time.monotonic()
    export_dir = Path(export_dir)
    root = Path(data_root) if data_root is not None else P.data_root()
    config_root = Path(config_dir) if config_dir is not None else _REPO_ROOT / "config"
    warnings: List[str] = []
    ts = _now(now)

    def phase(name: str) -> None:
        if progress is not None:
            progress(0, 0, name)

    src = {name: root / name for name in WHITELIST}

    # ── Phase 0 PRECHECK（零产出；不取锁）────────────────────────────────────
    phase("PRECHECK")
    missing = sorted(name for name, path in src.items() if not path.exists())
    if missing:
        raise ExportRefused(
            "WHITELIST_FILE_MISSING",
            "白名单文件缺失，无法出包（请确认 DATA_ROOT 指向正确的数据目录）",
            {"missing": missing, "data_root": str(root)},
        )

    status = _read_schema_status(src["quantstudio.db"])
    if status not in EXPORT_ALLOWED_SCHEMA:
        raise ExportRefused(
            "SCHEMA_STATUS_INVALID",
            "库状态不合格，需先修复（空库亦不放行：本特性不存在空包交付路径）",
            {"schema_status": status, "allowed": list(EXPORT_ALLOWED_SCHEMA)},
        )

    for name in WHITELIST:
        if ENGINE_KIND[name] != "sqlite":
            continue
        try:
            _sqlite_probe(src[name])
        except Exception as exc:
            raise ExportRefused(
                "WHITELIST_FILE_UNREADABLE",
                "白名单文件无法只读打开",
                {"path": name, "error": f"{type(exc).__name__}: {exc}"},
            )

    holder = P.write_lock_holder()
    if holder:
        raise ExportRefused(
            "WRITE_LOCK_HELD",
            "写锁被其他任务持有，请等其结束后再导出",
            {"holder": holder},
        )

    daemon = P.daemon_running()
    if daemon.running:
        raise ExportRefused(
            "DAEMON_RUNNING",
            "采集正在运行，请先停止采集再导出（导出要求零写入窗口）",
            {"pid": daemon.detail.get("pid"), "started_at": daemon.detail.get("started_at")},
        )

    disjoint_basis = {"DATA_ROOT": root, "export_dir": export_dir}
    from . import config as delivery_config

    try:
        cfg, _cfg_warnings = delivery_config.load(config_root)
        if cfg.import_path() is not None:
            disjoint_basis["import_dir"] = cfg.import_path()
    except Exception:
        pass
    violations = P.paths_disjoint(disjoint_basis)
    if violations:
        raise ExportRefused(
            "PATH_OVERLAP", "导出目录与数据目录/交付包目录不得互相包含", {"violations": violations}
        )

    ok, detail = W.probe_dir_writable(export_dir)
    if not ok:
        raise ExportRefused(
            "TARGET_NOT_WRITABLE", "导出目录不可写或被占用（杀软/受保护目录）", {"detail": detail}
        )

    for name in WHITELIST:
        ok, detail = W.probe_file_handle(src[name])
        if not ok:
            raise ExportRefused(
                "TARGET_HANDLE_BUSY",
                "源文件被其他程序占用（杀软实时扫描 / Explorer 预览 / 同步客户端）",
                {"path": name, "detail": detail},
            )

    sync_hit, sync_reason = W.detect_sync_dir(export_dir)
    if sync_hit and not accept_sync_dir:
        raise ExportRefused(
            "SYNC_DIR_UNCONFIRMED",
            "导出目录在同步盘上，数十 GB 复制会触发同步风暴，请更换目录或显式确认后继续",
            {"reason": sync_reason},
        )
    if sync_hit:
        warnings.append(f"导出目录疑似同步盘（已确认继续）：{sync_reason}")

    sizes = [src[name].stat().st_size for name in WHITELIST]
    required = P.required_export_bytes(sizes)
    free = P.disk_free_bytes(export_dir)
    if free < required:
        raise ExportRefused(
            "DISK_INSUFFICIENT",
            "可用空间不足（导出需要包体积 × 1.1 + 余量）",
            {"required": required, "free": free},
        )

    # ── Phase 1 ACQUIRE ─────────────────────────────────────────────────────
    phase("ACQUIRE")
    from quantstudio.pipeline.snapshot_lock import WriteLockHeld

    try:
        _acquire_lock()
    except WriteLockHeld as exc:
        raise ExportRefused("WRITE_LOCK_HELD", "写锁被其他任务持有", {"error": str(exc)})
    lock_acquired_at = _now().isoformat(timespec="seconds")

    try:
        # ── Phase 2 CHECKPOINT ──────────────────────────────────────────────
        phase("CHECKPOINT")
        checkpoint = _checkpoint_all(src)

        # ── Phase 3 WAL_ZERO ────────────────────────────────────────────────
        phase("WAL_ZERO")
        wal = [
            {"path": f"{name}-wal", "bytes": _wal_bytes(src[name])} for name in WHITELIST
        ]
        bad = [item for item in wal if item["bytes"] > 0]
        if bad:
            raise ExportRefused(
                "WAL_NOT_ZERO",
                "同刻一致未达成：WAL 未归零（非空 WAL 不得装入交付包）",
                {"wal": wal},
            )

        # ── Phase 4 COPY（单次读盘 + 流式哈希；中断即作废整包）──────────────
        phase("COPY")
        if _cancelled(cancel):
            raise ExportAborted("用户取消（复制前）")
        bundle_dir = _new_bundle_dir(export_dir, ts)
        total = sum(sizes)
        copied: List[Dict] = []
        done = 0
        last_report = [0.0]
        try:
            for name in WHITELIST:
                meta = _copy_file(
                    src[name],
                    bundle_dir / name,
                    progress=progress,
                    cancel=cancel,
                    offset=done,
                    total=total,
                    last_report=last_report,
                )
                done += meta["bytes"]
                copied.append(
                    {
                        "path": name,
                        "kind": ENGINE_KIND[name],
                        "bytes": meta["bytes"],
                        "sha256": meta["sha256"],
                        "sqlite_header": M.sqlite_header(bundle_dir / name)
                        if ENGINE_KIND[name] == "sqlite"
                        else None,
                    }
                )
        except ExportAborted:
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise
        except BaseException as exc:
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise ExportFailed("COPY", exc) from exc

        # ── Phase 5 ATTEST ──────────────────────────────────────────────────
        phase("ATTEST")
        try:
            tables, table_set = _attest_stats(bundle_dir, config_root)
            self_check = V.self_check_bundle(bundle_dir)
            hash_mismatch = False
            if verify_hashes:
                for meta in copied:
                    actual = M.sha256_path(bundle_dir / meta["path"])
                    if actual != meta["sha256"]:
                        hash_mismatch = True
                        warnings.append(f"{meta['path']} 二次哈希不一致（copy_sha256_pass=False）")

            aux_cfg = _read_json(config_root / AUX_PATHS_REL, {}) or {}
            aux_cfg_path = config_root / AUX_PATHS_REL
            aux_sha = (
                hashlib.sha256(aux_cfg_path.read_bytes()).hexdigest()
                if aux_cfg_path.exists()
                else None
            )
            copy_ok = all(
                m["bytes"] == (bundle_dir / m["path"]).stat().st_size for m in copied
            ) and not hash_mismatch
            doc = M.build_manifest(
                generated_at=_now().isoformat(timespec="seconds"),
                producer=_producer_env(),
                files=copied,
                main_db={"path": "quantstudio.db", "schema_status": status},
                qfq={
                    "released": bool(aux_cfg.get("released", False))
                    if isinstance(aux_cfg, dict)
                    else False,
                    "qfq_active_cutover": _read_cutover(bundle_dir),
                    "aux_route_step6_expect": {
                        "legacy": "qfq_aux.db",
                        "gen1": "qfq_aux_mcp_gen1.db",
                    },
                    "aux_paths_config_sha256": aux_sha,
                },
                table_set=table_set,
                tables=tables,
                self_check=self_check,
                export={
                    "lock_task_id": LOCK_TASK_EXPORT,
                    "lock_acquired_at": lock_acquired_at,
                    "duration_sec": round(time.monotonic() - started, 3),
                    "checkpoint": checkpoint,
                    "copy_bytes_total": sum(m["bytes"] for m in copied),
                    "copy_sha256_pass": copy_ok,
                },
            )
            doc["bundle_id"] = M.bundle_id_of(doc)
            manifest_path = bundle_dir / "manifest.json"
            tmp = manifest_path.with_name(f"manifest.json.tmp{os.getpid()}")
            tmp.write_text(M.canonical_dumps(doc), encoding="utf-8", newline="\n")
            os.replace(tmp, manifest_path)
        except (ExportRefused, ExportAborted):
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise
        except BaseException as exc:
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise ExportFailed("ATTEST", exc) from exc

        qualified = bool(self_check["red_line"]["zero_leak"]) and all(
            item["select1"] == "ok" for item in self_check["db_open"]
        )
        if not qualified:
            warnings.append("红线自检未通过：产出保留供取证，但标记为不合格（qualified=False）")
    finally:
        try:
            _release_lock()
        except Exception as exc:  # 不静默
            warnings.append(f"释放写锁失败：{type(exc).__name__}: {exc}")

    return ExportResult(
        bundle_dir=bundle_dir,
        manifest_path=bundle_dir / "manifest.json",
        files=tuple(copied),
        total_bytes=sum(m["bytes"] for m in copied),
        duration_sec=round(time.monotonic() - started, 3),
        qualified=qualified,
        self_check=self_check,
        warnings=tuple(warnings),
    )
