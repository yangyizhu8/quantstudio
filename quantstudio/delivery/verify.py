# -*- coding: utf-8 -*-
"""只读校验（W2）：四要素校验 → 四类失败分派 → 世代路由一致性断言。

契约见 `contracts/import_kernel.md` §2。**本模块 MUST NOT 修改 `DATA_ROOT` 下任何文件**，
也不得在交付包内留下任何文件（SQLite 一律 `immutable=1` 打开，见 `sqlite_ro_uri`）。

`self_check_bundle` 与导出侧共用（FR-007 / SC-002）。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import manifest as M
from . import probe as P
from . import stats as S
from .constants import (
    ALIGNMENT_RULES_REL,
    AUX_PATHS_REL,
    COLLECTOR_TASKS_REL,
    EXIT_BY_FAILURE_KIND,
    EXPORT_ALLOWED_SCHEMA,
    FailureKind,
    TABLE_MAP_REL,
    WHITELIST,
)

ProgressCB = Callable[[int, int, str], None]
CHUNK = 1 << 20
_REPO_ROOT = Path(__file__).resolve().parents[2]

_MESSAGES = {
    FailureKind.PACKAGE: "交付包不完整或已损坏，请重新获取",
    FailureKind.PROGRAM_VERSION: "程序版本与数据版本不匹配，请更新程序",
    FailureKind.CONFIG: "配置不匹配，请检查",
    FailureKind.MANIFEST_VERSION: "包由更新版本的程序生成，请先更新程序",
}


# ── 只读工具 ────────────────────────────────────────────────────────────────
def sqlite_ro_uri(path: Path) -> str:
    """SQLite 只读 URI（`immutable=1`）。

    关键：**必须 immutable** —— 普通 `mode=ro` 打开 WAL 模式的库会在**同目录创建
    `-shm`/`-wal` 边车文件**；对交付包做自检/校验时那等于往包里写非白名单文件（W1 首跑
    已被 SC-002 抓出）。immutable 声明"该文件不会变"，SQLite 因此不建边车。
    """
    return f"file:{Path(path).as_posix()}?mode=ro&immutable=1"


def _cancelled(cancel: Any) -> bool:
    if cancel is None:
        return False
    fn = getattr(cancel, "is_set", None)
    return bool(fn()) if callable(fn) else bool(cancel)


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _measure_schema_status(main_db: Path) -> str:
    import duckdb

    from quantstudio.pipeline.qfq_schema_status import detect_schema_status

    con = duckdb.connect(str(main_db), read_only=True)
    try:
        return str(detect_schema_status(con).value)
    finally:
        con.close()


# ── 红线自检（导出/校验共用）────────────────────────────────────────────────
def _iter_names(bundle_dir: Path) -> List[str]:
    root = Path(bundle_dir)
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _hits(names: List[str], label: str) -> List[str]:
    import re

    from .constants import EXCLUDE_PATTERNS

    pattern = next((p for lbl, p in EXCLUDE_PATTERNS if lbl == label), None)
    if pattern is None:
        return []
    return [n for n in names if re.search(pattern, n)]


def self_check_bundle(bundle_dir: Path) -> Dict:
    """产出目录红线自检（不修改任何文件、不留边车）。"""
    root = Path(bundle_dir)
    names = _iter_names(root)
    allowed = "quantstudio.db", "qfq_aux.db", "qfq_aux_mcp_gen1.db", "manifest.json"

    outside = [n for n in names if n not in allowed]
    env_hits = _hits(names, "env")
    shm_hits = _hits(names, "shm")
    import re as _re

    log_hits = [n for n in names if _re.search(r"(?i)\.log$", n) or _hits([n], "logs")]
    zero_leak = not outside and not env_hits and not shm_hits and not log_hits

    db_open: List[Dict] = []
    for name in WHITELIST:
        target = root / name
        if not target.exists():
            continue
        try:
            if name == "quantstudio.db":
                import duckdb

                con = duckdb.connect(str(target), read_only=True)
                try:
                    con.execute("SELECT 1").fetchone()
                    status = "ok"
                finally:
                    con.close()
            else:
                con = sqlite3.connect(sqlite_ro_uri(target), uri=True)
                try:
                    con.execute("SELECT count(*) FROM sqlite_master").fetchone()
                    status = "ok"
                finally:
                    con.close()
        except Exception as exc:  # 不静默：红检结果本身就是证据
            status = f"error: {type(exc).__name__}: {exc}"
        db_open.append({"path": name, "select1": status})

    wal_zero: List[Dict] = []
    for name in WHITELIST:
        wal = root / f"{name}-wal"
        present = wal.exists()
        wal_zero.append(
            {"path": f"{name}-wal", "present": present, "bytes": wal.stat().st_size if present else 0}
        )

    return {
        "red_line": {
            "outside_whitelist": len(outside),
            "env_hits": len(env_hits),
            "shm_hits": len(shm_hits),
            "log_hits": len(log_hits),
            "zero_leak": bool(zero_leak),
            "outside_files": outside,
        },
        "db_open": db_open,
        "wal_zero": wal_zero,
    }


# ── 报告对象 ────────────────────────────────────────────────────────────────
@dataclass
class VerifyReport:
    ok: bool
    package_dir: Optional[Path] = None
    failure_kind: Optional[str] = None
    message: str = ""
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    manifest_schema_version: Optional[int] = None
    files_checked: int = 0
    route: Dict = field(default_factory=dict)
    tables: List[Dict] = field(default_factory=list)
    table_set: Dict = field(default_factory=dict)
    elapsed_sec: float = 0.0
    exit_code: int = 0


def _fail(report: VerifyReport, kind: str, reason: str) -> VerifyReport:
    report.ok = False
    report.failure_kind = kind
    report.message = _MESSAGES.get(kind, "校验失败")
    report.reasons.append(reason)
    report.exit_code = EXIT_BY_FAILURE_KIND.get(kind, 4)
    return report


# ── 主流程 ──────────────────────────────────────────────────────────────────
def verify_bundle(
    *,
    import_dir: Path,
    config_dir: Optional[Path] = None,
    data_root: Optional[Path] = None,
    progress: Optional[ProgressCB] = None,
    cancel: Any = None,
    now: Optional[datetime] = None,
) -> VerifyReport:
    started = time.monotonic()
    config_root = Path(config_dir) if config_dir is not None else _REPO_ROOT / "config"
    root = Path(data_root) if data_root is not None else P.data_root()
    report = VerifyReport(ok=True)

    # ① 定位包（FR-038：恰一个）
    package_dir, problems = P.locate_package(import_dir)
    if package_dir is None:
        _fail(report, FailureKind.PACKAGE, ";".join(problems))
        report.elapsed_sec = round(time.monotonic() - started, 3)
        return report
    report.package_dir = package_dir

    # ② manifest 版本裁决（④/②/①）
    read = M.read_manifest(package_dir / "manifest.json")
    report.manifest_schema_version = (
        read.doc.get("manifest_schema_version") if isinstance(read.doc, dict) else None
    )
    report.warnings.extend(read.warnings)
    if not read.ok:
        _fail(report, read.failure_kind or FailureKind.PACKAGE, ";".join(read.reasons))
        report.elapsed_sec = round(time.monotonic() - started, 3)
        return report
    doc = read.doc or {}

    # ③ 逐文件校验（体积升序 fail-fast，FR-019）
    files = sorted(doc["files"], key=lambda f: (f["bytes"], f["path"]))
    total_bytes = sum(int(f["bytes"]) for f in files)
    done = 0
    for entry in files:
        if _cancelled(cancel):
            report.warnings.append("校验被取消（未完成）")
            _fail(report, FailureKind.PACKAGE, "校验被取消，未完成全量校验")
            report.elapsed_sec = round(time.monotonic() - started, 3)
            return report
        name = entry["path"]
        target = package_dir / name
        if not target.exists():
            _fail(report, FailureKind.PACKAGE, f"白名单文件缺失：{name}")
            report.elapsed_sec = round(time.monotonic() - started, 3)
            return report
        actual_bytes = target.stat().st_size
        if actual_bytes != int(entry["bytes"]):
            _fail(
                report,
                FailureKind.PACKAGE,
                f"{name} 字节数不符：manifest={entry['bytes']} 实际={actual_bytes}",
            )
            report.elapsed_sec = round(time.monotonic() - started, 3)
            return report
        actual_sha = M.sha256_path(target, CHUNK)
        if actual_sha != entry["sha256"]:
            _fail(
                report,
                FailureKind.PACKAGE,
                f"{name} SHA256 不符：manifest={str(entry['sha256'])[:12]}… 实际={actual_sha[:12]}…",
            )
            report.elapsed_sec = round(time.monotonic() - started, 3)
            return report
        if name != "quantstudio.db":
            head = M.sqlite_header(target)
            if head is None:
                report.warnings.append(f"{name} 头 16 字节不是 SQLite 魔数（可能不是有效 SQLite 库）")
        done += actual_bytes
        report.files_checked += 1
        if progress is not None:
            progress(done, total_bytes, "VERIFY")

    # ④ 主库 schema 实测（包内副本）vs 程序期望 vs manifest
    measured = _measure_schema_status(package_dir / "quantstudio.db")
    if measured not in EXPORT_ALLOWED_SCHEMA:
        _fail(
            report,
            FailureKind.PROGRAM_VERSION,
            f"包内主库 schema 实测 {measured!r} 不在程序放行集 {list(EXPORT_ALLOWED_SCHEMA)}",
        )
        report.elapsed_sec = round(time.monotonic() - started, 3)
        return report
    if measured != doc["main_db"]["schema_status"]:
        report.warnings.append(
            f"包可能被改动或导出时状态有误：manifest 记 {doc['main_db']['schema_status']}，实测 {measured}"
        )
        _fail(report, FailureKind.PACKAGE, "包内主库 schema 与 manifest 记录不一致")
        report.elapsed_sec = round(time.monotonic() - started, 3)
        return report

    # ⑤ 世代路由一致性断言（plan §6：用**包内主库**解析"导入后的世界"）
    route, route_fail = _assert_generation_consistency(
        package_dir=package_dir,
        doc=doc,
        config_root=config_root,
        data_root=root,
    )
    report.route = route
    if route_fail:
        report.warnings.extend(route.get("warnings", []))
        _fail(report, FailureKind.CONFIG, route_fail)
        report.elapsed_sec = round(time.monotonic() - started, 3)
        return report
    report.warnings.extend(route.get("warnings", []))

    # ⑥ 逐表统计（与导出同口径：map ∩ 实际存在）
    tables, table_set = _collect_tables(package_dir, config_root)
    report.tables = tables
    report.table_set = table_set
    # A3 强化②：包内声明的 estimate 必须在报告里显式标注，禁止与 exact 静默混排
    declared = {
        str(row.get("table")): str(row.get("row_count_method"))
        for row in doc.get("tables", [])
        if isinstance(row, dict)
    }
    missing_basis = [r["table"] for r in tables if r.get("date_basis_col") == "unknown"]
    if missing_basis:
        report.warnings.append(
            "以下表缺基准列声明（questdb_table_map 的 watermark_basis 与 alignment_rules 的 "
            f"time_key 均无），无法判定四态、已标 UNKNOWN-BASIS；请联系实施方补齐配置：{missing_basis}"
        )
    for row in tables:
        if declared.get(str(row["table"])) == "estimate":
            row["declared_row_count_method"] = "estimate"
            report.warnings.append(
                f"{row['table']} 行数为估算值（包内 method=estimate；本次复核实测 count={row.get('row_count')}），"
                "不可与 exact 混用判断数据完整性"
            )

    report.ok = True
    report.exit_code = 0
    report.elapsed_sec = round(time.monotonic() - started, 3)
    return report


def _assert_generation_consistency(
    *, package_dir: Path, doc: Dict, config_root: Path, data_root: Path
) -> tuple:
    """路由断言：客户配置 + 包内主库 ⇒ 客户实际会读的因子库路径必须落在 DATA_ROOT 且包内有该件。"""
    from quantstudio.pipeline.qfq_aux_router import resolve_runtime_aux_path

    warnings: List[str] = []
    cutover = (doc.get("qfq") or {}).get("qfq_active_cutover") or {}
    price_source = str(cutover.get("price_source") or "mcp")
    package_names = {Path(f["path"]).name for f in doc["files"]}
    config_path = config_root / AUX_PATHS_REL

    import duckdb

    def duckdb_read(sql: str, params: Any = None):
        con = duckdb.connect(str(package_dir / "quantstudio.db"), read_only=True)
        try:
            return con.execute(sql, params or []).fetchall()
        finally:
            con.close()

    resolved, reason = resolve_runtime_aux_path(
        main_db=data_root / "quantstudio.db",
        duckdb_read=duckdb_read,
        price_source=price_source,
        config_path=config_path,
    )
    resolved = Path(resolved)

    route = {
        "resolved_path": str(resolved),
        "target_path": str(data_root / resolved.name),
        "reason": reason,
        "matched": False,
        "legacy_security_note": "",
    }

    if str(reason).startswith("legacy:") and "released=false" not in str(reason):
        for code in ("no_aux_paths_config", "fail-secure"):
            if code in str(reason):
                route["legacy_security_note"] = (
                    "配置缺失或不可判定，已按 legacy 判定；包内 qfq_aux.db 必须校验通过；"
                    "如需 gen1 请修复 config"
                )
                warnings.append(route["legacy_security_note"])
                break
    elif str(reason) == "legacy:released=false":
        route["legacy_security_note"] = (
            "客户配置 released=false，按 legacy 判定；包内 qfq_aux.db 必须校验通过；如需 gen1 请修复 config"
        )

    if resolved.name not in package_names:
        return route, (
            f"运行时因子库路径与导入目标不一致：客户将读 {resolved}，"
            f"但包内只有 {sorted(package_names)}（目标目录 {data_root}）"
        )
    if data_root.resolve() not in resolved.resolve().parents:
        return route, (
            f"运行时因子库路径不在 DATA_ROOT 内：{resolved} 不在 {data_root} 之下"
        )
    route["matched"] = True
    return route, ""


def _collect_tables(package_dir: Path, config_root: Path) -> tuple:
    import duckdb

    table_map_path = config_root / TABLE_MAP_REL
    if not table_map_path.exists():
        raise FileNotFoundError(f"缺配置真源：{table_map_path}")
    table_map = S.load_table_map(table_map_path)

    tasks_doc = _read_json(config_root / COLLECTOR_TASKS_REL, {}) or {}
    tasks = tasks_doc.get("tasks") if isinstance(tasks_doc, dict) else None
    known_tasks = set(tasks) if isinstance(tasks, dict) else None

    con = duckdb.connect(str(package_dir / "quantstudio.db"), read_only=True)
    try:
        def query(sql: str):
            return con.execute(sql).fetchall()

        try:
            wm_rows = query("SELECT source, table_name, freq, last_date FROM source_watermark")
        except Exception:
            wm_rows = []
        wm_tables = {str(row[1]) for row in wm_rows}
        names = S.select_table_set(table_map, S.existing_tables(query), wm_tables)
        rows = S.collect_table_stats(
            query,
            table_map,
            names,
            S.load_watermark_rows(wm_rows),
            time_keys=S.load_alignment_time_keys(config_root / ALIGNMENT_RULES_REL),
            watermark_tables=wm_tables,
            known_tasks=known_tasks,
        )
    finally:
        con.close()
    configured = {str(spec.get("target_table") or name) for name, spec in table_map.items()}
    out_of_map = [
        str(t) for (_s, t, _f, _l) in wm_rows if str(t) not in configured
    ]
    return rows, S.summarize(rows, out_of_map=out_of_map)
