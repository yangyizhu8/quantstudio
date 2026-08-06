"""QFQ reanchor schema 显式 2.0→2.1 migration runner（v2.4 B-3b）。

本模块实现安全、显式、可重复、可中断恢复的 legacy 2.0→target 2.1 本地迁移。
**绝对硬拒绝正式生产库**（``data/quantstudio.db``），即使 ``--allow-production`` 也不绕过。

迁移策略（设计 mcp-cutover-design-v2.md §3.2.5 + B-3b 工作包）：

- **重建全部发生物理变化的 9 张 QFQ 表 + source_watermark**：仅 ALTER ADD COLUMN 会
  把新列追加到末尾，得不到 target 冻结的精确列顺序。故用 shadow 表（``<table>__b3b_v2``）
  按 target DDL 建全 → 映射复制 legacy 行（含历史回填）→ 校验 → 事务内统一 RENAME swap
  （``<table>`` → ``<table>__b3b_legacy``，``<table>__b3b_v2`` → ``<table>``）。
- **新建 4 张 B-3 表**（discovery_baseline/source_cutover/active_cutover/cycle_lease），
  迁移完成时为空表，不自动激活 cutover。
- **保留不重建**：qfq_bootstrap_item、trade_calendar（须验证与 target 一致，否则 fail-closed）。
- **单一原子事务**：BEGIN → 全部 shadow 建+复制+校验+新表+swap+legacy 清理+fingerprint 回读 → COMMIT。
  任一步失败 ROLLBACK → 重开库必须恢复为完整 COMPLETE_2_0。
- **历史回填**：trigger_id_version=1、price_source=xtquant（trigger/cycle/bootstrap/backfill/cursor）
  或保留原值（event/anchor/intent/fresh_capture/watermark）、source_generation/cutover_id 固定
  legacy 哨兵；source_watermark 按 table_name 分类回填（QFQ 价格表 vs 非 QFQ）。

中断恢复：13 个故障注入点；前 12 个失败后重开库 = COMPLETE_2_0；第 13 个（COMMIT 后报告前）
重跑 = COMPLETE_2_1 already_current。

详见 mcp-cutover-design-v2.md §3.2.5、b3r-schema-exploration.md §12.10。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import duckdb

from quantstudio.pipeline.qfq_schema_contracts import (
    LEGACY_MAIN_DB_2_0_FINGERPRINT,
    TARGET_MAIN_DB_2_1_FINGERPRINT,
    TARGET_QFQ_2_1_FINGERPRINT,
    LEGACY_QFQ_2_0_FINGERPRINT,
    TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT,
    B3_NEW_TABLES,
    KNOWN_SHADOW_TABLES,
    SOURCE_WATERMARK_2_1_DDL,
    parse_physical_contract,
    pre_cutover_generation,
    verify_fingerprint,
    QFQ_PRICE_TABLES,
)
from quantstudio.pipeline.qfq_reanchor_schema import DDL_DUCKDB
from quantstudio.pipeline.qfq_schema_status import (
    detect_schema_status, SchemaStatus, _list_tables)

logger = logging.getLogger(__name__)

SCHEMA_VERSION_FROM = "reanchor-2.0"
SCHEMA_VERSION_TO = "reanchor-2.1"

# 正式生产库路径（绝对硬拒绝；与 _paths.db_path() 同源）
from quantstudio._paths import db_path as _prod_db_path


# ---------------------------------------------------------------------------
# 重建表清单 + 历史回填映射
# ---------------------------------------------------------------------------

# 需重建的 9 张 QFQ 表（物理列顺序/NOT NULL/约束变化）
REBUILD_QFQ_TABLES: Tuple[str, ...] = (
    "qfq_anchor_state", "qfq_reanchor_event", "qfq_pending_backfill",
    "qfq_bootstrap_run", "qfq_cycle_run", "qfq_trigger_queue",
    "qfq_watermark_intent", "qfq_fresh_capture", "qfq_observation_cursor",
)
# 其中 4 张同时改 PK
PK_CHANGED_TABLES: Tuple[str, ...] = (
    "qfq_anchor_state", "qfq_pending_backfill",
    "qfq_watermark_intent", "qfq_observation_cursor",
)
# 需重建的共享表
REBUILD_SHARED_TABLES: Tuple[str, ...] = ("source_watermark",)
# 保留不重建（须验证与 target 一致）
KEEP_TABLES: Tuple[str, ...] = ("qfq_bootstrap_item", "trade_calendar")
# 新建 4 张 B-3 表（迁移完成时为空）
NEW_TABLES: Tuple[str, ...] = B3_NEW_TABLES

# shadow 表后缀（事务内临时；成功 COMMIT 后全部不存在）
SHADOW_V2_SUFFIX = "__b3b_v2"
SHADOW_LEGACY_SUFFIX = "__b3b_legacy"


def _legacy_filler(table: str) -> Dict[str, object]:
    """历史回填映射：返回该表新增列的固定 legacy 值（键=列名，值=回填值）。

    规则（B-3b 工作包 §五）：
    - trigger_id_version = 1（仅 trigger_queue）
    - price_source = 'xtquant'（trigger_queue/cycle_run/bootstrap_run/pending_backfill/observation_cursor）
    - source_generation = 'xtquant-legacy'（全部重建表）
    - cutover_id = 'legacy-xtquant-pre-cutover'（除 observation_cursor/anchor_state/source_watermark 外）
    - retired_at/retire_reason = NULL（trigger_queue）
    保留原值的列（event.price_source、anchor.price_source、intent.source、fresh_capture.source、
    watermark.source）由 SELECT 复制时原样带，不在此覆盖。
    """
    fill: Dict[str, object] = {
        "source_generation": "xtquant-legacy",
    }
    if table == "qfq_trigger_queue":
        fill.update({
            "trigger_id_version": 1,
            "price_source": "xtquant",
            "cutover_id": "legacy-xtquant-pre-cutover",
            "retired_at": None,
            "retire_reason": None,
        })
    elif table in ("qfq_cycle_run", "qfq_bootstrap_run"):
        fill.update({"price_source": "xtquant", "cutover_id": "legacy-xtquant-pre-cutover"})
    elif table == "qfq_pending_backfill":
        fill.update({"price_source": "xtquant"})
    elif table == "qfq_observation_cursor":
        fill.update({"price_source": "xtquant"})  # 无 cutover_id 列
    elif table == "qfq_reanchor_event":
        fill.update({"cutover_id": "legacy-xtquant-pre-cutover"})  # 保留原 price_source
    elif table == "qfq_anchor_state":
        pass  # 仅 source_generation；保留原 price_source；无 cutover_id
    elif table == "qfq_watermark_intent":
        fill.update({"cutover_id": "legacy-xtquant-pre-cutover"})  # 保留原 source
    elif table == "qfq_fresh_capture":
        fill.update({"cutover_id": "legacy-xtquant-pre-cutover"})  # 保留原 source
    elif table == "source_watermark":
        # source_watermark 按 table_name 分类回填，不在固定 fill；由专用路径处理
        return {}
    return fill


# ---------------------------------------------------------------------------
# 内容 hash 规范（固定列序/PK排序/NULL编码/timestamp/float/SHA-256）
# ---------------------------------------------------------------------------

CONTENT_HASH_VERSION = "b3b-sha256-v1"

# Report lifecycle states (B-3b.3 frozen contract).
REPORT_STATUS_PENDING = "PENDING"
REPORT_STATUS_DRY_RUN_COMPLETE = "DRY_RUN_COMPLETE"
REPORT_STATUS_ROLLED_BACK = "ROLLED_BACK"
REPORT_STATUS_MIGRATION_COMMITTED = "MIGRATION_COMMITTED"
REPORT_STATUS_ALREADY_CURRENT = "ALREADY_CURRENT"
REPORT_STATUS_FAILED_PRECHECK = "FAILED_PRECHECK"


def _encode_value(v) -> str:
    """内容 hash 的单值规范化编码（确定性，非 NULL 值）。

    规范：
    - str → 原样（UTF-8）
    - bool → ``True``/``False``（先于 int 判定，因 Python bool 是 int 子类）
    - int → 十进制字符串
    - float → ``repr(v)``（区分 NaN/+inf/-inf/-0.0；有限值给最短明确表示）
    - 其它（datetime/date/bytes 等）→ ``str(v)``，对 bytes 用 latin-1 解码占位

    NULL 由 ``_encode_row`` 用独立标记处理（见其 docstring），不由此函数编码。
    """
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        # repr 区分 NaN/+inf/-inf/-0.0，有限值给最短明确表示
        import math
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        if v == 0.0:
            return "-0.0" if math.copysign(1.0, v) < 0 else "0.0"
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.decode("latin-1")
    return str(v)


# NULL 标记（与任何字符串值可区分：_encode_row 对 NULL 用此标记，非 NULL 用 <len>:<value>）
_NULL_TOKEN = "\x00NULL"  # NUL 字节 + NULL，正常文本值不会以 NUL 开头


def _encode_row(values) -> str:
    """单行内容的无碰撞编码。

    每个值编为：
    - NULL → ``\\x00NULL``（独立标记；NUL 字节不出现在正常文本值中）
    - 非 NULL → ``<len>:<encoded>``（长度前缀）

    值间用 ``\\x1f``，行末用 ``\\x1e``。这样：
    - NULL 与真实字符串 ``\\N`` / ``<len>:...`` 形态完全不同（无碰撞）；
    - 字段值含 ``\\x1f``/``\\x1e``/``:`` 也不碰撞（长度前缀界定）；
    - bool/float(NaN/inf/-0.0)/int/str 各有规范化形态。
    """
    parts = []
    for v in values:
        if v is None:
            parts.append(_NULL_TOKEN)
        else:
            enc = _encode_value(v)
            parts.append(f"{len(enc)}:{enc}")
    return "\x1f".join(parts) + "\x1e"


def _content_hash(conn, table: str, *, columns: List[str], pk_cols: List[str]) -> str:
    """对表内容做确定性 SHA-256（fail-closed：查询失败抛异常，不静默吞）。

    规范（CONTENT_HASH_VERSION = "b3b-sha256-v1"）：
    - 固定列序（= 调用方传入的 columns，来自显式 source/target 指纹）；
    - 按 PK 排序（pk_cols；空则按首列）；
    - NULL → ``\\N``（长度前缀编码消歧）；
    - timestamp/float/bool/NaN/inf/-0.0 规范化（见 _encode_value）；
    - 长度前缀编码消除分隔符碰撞（见 _encode_row）；
    - UTF-8 → SHA-256。

    **显式接收 columns**（不猜测 target/legacy）：迁移前用 legacy 指纹列，
    迁移后用 target 指纹列。查询失败向上传播（fail-closed）。
    """
    col_list = ", ".join(f'"{c}"' for c in columns)
    order = ", ".join(f'"{c}"' for c in (pk_cols or columns[:1]))
    rows = conn.execute(
        f'SELECT {col_list} FROM "{table}" ORDER BY {order}'
    ).fetchall()
    h = hashlib.sha256()
    for row in rows:
        h.update(_encode_row(row).encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# MigrationReport
# ---------------------------------------------------------------------------

@dataclass
class MigrationReport:
    db_path: str
    source_status: str
    target_status: str
    dry_run: bool
    applied: bool
    already_current: bool
    content_hash_version: str = CONTENT_HASH_VERSION
    report_path: str = ""
    report_status: str = REPORT_STATUS_PENDING
    report_error: str = ""
    tables_rebuilt: List[str] = field(default_factory=list)
    tables_created: List[str] = field(default_factory=list)
    row_counts_before: Dict[str, int] = field(default_factory=dict)
    row_counts_after: Dict[str, int] = field(default_factory=dict)
    hashes_before: Dict[str, str] = field(default_factory=dict)
    hashes_after: Dict[str, str] = field(default_factory=dict)
    validation_results: Dict = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class QfqMigrationError(RuntimeError):
    """迁移失败的基类（preflight 拒绝、状态非法、校验失败等）。"""


class QfqMigrationProductionRefused(QfqMigrationError):
    """正式生产库硬拒绝（即使 --allow-production 也不绕过）。"""


class MigrationCommittedReportError(QfqMigrationError):
    """The database migration committed but final report processing failed.

    Callers must not assume the database is still 2.0 or blindly retry apply.
    Reopen the database and use the COMPLETE_2_1 already-current audit path.
    """

    def __init__(self, message: str, *, db_path: str, report_path: str):
        super().__init__(message)
        self.db_path = db_path
        self.report_path = report_path
        self.migration_committed = True


# ---------------------------------------------------------------------------
# 正式库硬拒绝 + allowed-root 校验
# ---------------------------------------------------------------------------

def _resolve_canonical(path: str | Path) -> Path:
    """resolve 到绝对路径（跟随 symlink/junction；Windows 大小写归一到真实路径）。"""
    return Path(path).resolve(strict=False)


def _is_production_db(db_path: Path) -> bool:
    """判断目标路径是否与正式生产库（主库或 aux 库）同一文件（os.path.samefile 处理绝对/相对/../
    大小写/symlink/junction/别名）。文件不存在时按 canonical 字符串（小写归一）比对。"""
    prod = _resolve_canonical(_prod_db_path())
    target = _resolve_canonical(db_path)
    # 同时检查主库与 aux 库（任一同一文件即判正式库）
    for prod_path in (prod, _prod_aux_path()):
        try:
            if prod_path.exists() and target.exists():
                if os.path.samefile(str(prod_path), str(target)):
                    return True
        except OSError:
            pass
        if str(prod_path).lower() == str(target).lower():
            return True
    return False


def _prod_aux_path() -> Path:
    """正式辅助库路径（qfq_aux.db，与主库同目录）。"""
    return _resolve_canonical(_prod_db_path()).parent / "qfq_aux.db"


def _assert_not_production(db_path: Path) -> None:
    """正式生产库硬拒绝（主库或 aux 库）。在打开 read-write 连接前触发。"""
    if _is_production_db(db_path):
        raise QfqMigrationProductionRefused(
            f"迁移 runner 绝对硬拒绝正式生产库：{db_path} == {_prod_db_path()}（或 aux）。"
            "B-3b migration runner 不接受任何生产绕过开关（--allow-production 无效）。"
            "正式库迁移须由用户显式批准的受控流程处理。")


def _assert_allowed_root(db_path: Path, allowed_root: Path) -> None:
    """目标路径必须位于 allowed_root 子路径下。canonical 比对（小写归一）。"""
    root = _resolve_canonical(allowed_root)
    target = _resolve_canonical(db_path)
    root_s = str(root).lower().rstrip("\\/")
    target_s = str(target).lower()
    if target_s == root_s:
        # 边界：目标路径本身等于 allowed_root（拒绝，不能以根为 db 文件）
        raise QfqMigrationError(
            f"目标 db 路径不得等于 allowed_root：{db_path} == {allowed_root}")
    if not target_s.startswith(root_s + os.sep):
        raise QfqMigrationError(
            f"目标 db 路径不在 allowed_root 子路径下：{db_path} 不在 {allowed_root} 下。"
            "迁移 runner 只允许在显式 staging/临时根目录内操作。")


# ---------------------------------------------------------------------------
# report 路径安全预检（P0-1，B-3b.2）
# ---------------------------------------------------------------------------

def _same_file(a: Path, b: Path) -> bool:
    """两路径是否同一文件（os.path.samefile 处理 hardlink/symlink/junction/别名）。
    文件不存在时按 canonical 字符串（小写）比对。"""
    try:
        if a.exists() and b.exists():
            return os.path.samefile(str(a), str(b))
    except OSError:
        pass
    return str(a).lower() == str(b).lower()


def _assert_report_path_safe(report_path: Path, db_path: Path, allowed_root: Path) -> None:
    """Validate report path identity and containment without creating it.

    Atomic ownership is acquired separately by ``_ReportReservation.reserve``
    with ``O_CREAT | O_EXCL`` before any database read-write connection.
    """
    rp = _resolve_canonical(report_path)
    db = _resolve_canonical(db_path)
    root = _resolve_canonical(allowed_root)
    root_s = str(root).lower().rstrip("\\/")
    rp_s = str(rp).lower()

    if _is_production_db(rp):
        raise QfqMigrationProductionRefused(
            f"report path points to a production database or alias: {report_path}")
    if _same_file(rp, db):
        raise QfqMigrationError(
            f"report path must not identify the target database: {report_path} == {db_path}")
    if rp_s == root_s:
        raise QfqMigrationError(f"report path must not equal allowed_root: {report_path}")
    if not rp_s.startswith(root_s + os.sep):
        raise QfqMigrationError(
            f"report path is outside allowed_root: {report_path} not under {allowed_root}")
    if rp.exists():
        raise QfqMigrationError(
            f"report path already exists; overwrite is forbidden: {report_path}")


class _ReportReservation:
    """Exclusive ownership of the final report file for one migration call.

    The final path itself is created with O_EXCL before any DB read-write
    operation. The same descriptor is retained and updated for the full call.
    There is no temporary-file publish step and no replace/overwrite window.
    """

    def __init__(self, path: Path, fd: int):
        self.path = path
        self.fd = fd
        self._closed = False
        st = os.fstat(fd)
        self._identity = (getattr(st, "st_dev", None), getattr(st, "st_ino", None))

    @classmethod
    def reserve(cls, report_path: Path, db_path: Path, allowed_root: Path,
                *, started_at: str) -> "_ReportReservation":
        rp = _resolve_canonical(report_path)
        root = _resolve_canonical(allowed_root)
        _assert_report_path_safe(rp, db_path, root)

        parent = rp.parent
        try:
            if parent.exists() and not parent.is_dir():
                raise QfqMigrationError(f"report parent is not a directory: {parent}")
            parent.mkdir(parents=True, exist_ok=True)
        except QfqMigrationError:
            raise
        except Exception as e:
            raise QfqMigrationError(
                f"report parent preparation failed before migration: {parent}: {e}") from e

        # Resolve again after parent creation to catch symlink/junction changes.
        rp = _resolve_canonical(rp)
        _assert_report_path_safe(rp, db_path, root)
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            fd = os.open(str(rp), flags, 0o600)
        except FileExistsError as e:
            raise QfqMigrationError(
                f"report path was concurrently claimed; exclusive create failed: {rp}") from e
        except Exception as e:
            raise QfqMigrationError(
                f"report path reservation failed before migration: {rp}: {e}") from e

        reservation = cls(rp, fd)
        try:
            reservation.write_payload({
                "db_path": str(_resolve_canonical(db_path)),
                "report_path": str(rp),
                "report_status": REPORT_STATUS_PENDING,
                "report_error": "",
                "content_hash_version": CONTENT_HASH_VERSION,
                "started_at": started_at,
                "finished_at": "",
            })
        except Exception:
            # Delete only the inode created by this call. If the final path was
            # replaced meanwhile, never unlink the replacement.
            reservation.unlink_if_owned()
            reservation.close()
            raise
        return reservation

    def _path_is_owned(self) -> bool:
        if self._closed:
            return False
        try:
            fst = os.fstat(self.fd)
            pst = self.path.stat()
        except OSError:
            return False
        return (
            getattr(fst, "st_dev", None), getattr(fst, "st_ino", None)
        ) == (
            getattr(pst, "st_dev", None), getattr(pst, "st_ino", None)
        ) == self._identity

    def unlink_if_owned(self) -> bool:
        """Remove the final path only when it still identifies our inode."""
        if not self._path_is_owned():
            return False
        try:
            self.path.unlink()
            return True
        except OSError:
            return False

    def _assert_owned(self) -> None:
        if self._closed:
            raise QfqMigrationError("report reservation is closed")
        if not self._path_is_owned():
            raise QfqMigrationError(
                f"report path identity changed; refusing to write: {self.path}")

    def write_payload(self, payload: Dict) -> None:
        """Rewrite the report through the descriptor owned by this call."""
        self._assert_owned()
        data = json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.ftruncate(self.fd, 0)
            view = memoryview(data)
            written = 0
            while written < len(view):
                n = os.write(self.fd, view[written:])
                if n <= 0:
                    raise OSError("report write returned zero bytes")
                written += n
            os.fsync(self.fd)
        except Exception as e:
            raise QfqMigrationError(
                f"report update through owned descriptor failed: {self.path}: {e}") from e

    def write_report(self, report: "MigrationReport") -> None:
        self.write_payload(_report_to_dict(report))

    def close(self) -> None:
        if not self._closed:
            try:
                os.close(self.fd)
            finally:
                self._closed = True


def _row_count(conn, table: str) -> int:
    """受管表行数查询。**fail-closed**：查询失败抛 QfqMigrationError（不静默返 -1）。"""
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except Exception as e:
        raise QfqMigrationError(f"行数查询失败 {table}（fail-closed，不返 -1）: {e}") from e


def _snapshot(conn, tables: List[str], fingerprint: Dict) -> Tuple[Dict[str, int], Dict[str, str]]:
    """返回 (row_counts, content_hashes) 快照。

    **显式接收 fingerprint**（不猜测 target/legacy）：迁移前传 LEGACY_MAIN_DB_2_0_FINGERPRINT，
    迁移后传 TARGET_MAIN_DB_2_1_FINGERPRINT。content hash 失败 **fail-closed**（向上抛，
    不静默写空串）——任一受管表 hash 失败必须让 dry-run/apply 报错停止。
    """
    counts = {t: _row_count(conn, t) for t in tables}
    hashes: Dict[str, str] = {}
    for t in tables:
        fp = fingerprint.get(t)
        if fp is None:
            raise QfqMigrationError(
                f"内容 hash 失败：表 {t} 不在指定指纹中（fingerprint keys={sorted(fingerprint)[:5]}...）")
        columns = [c[0] for c in fp["columns"]]
        pk = list(fp["primary_key"])
        # fail-closed：查询失败向上传播（不再 except → ""）
        hashes[t] = _content_hash(conn, t, columns=columns, pk_cols=pk)
    return counts, hashes


# ---------------------------------------------------------------------------
# shadow 建表 + 复制 SQL 构造
# ---------------------------------------------------------------------------

def _target_ddl_for(table: str) -> str:
    """返回该表的 target 2.1 DDL（从 DDL_DUCKDB 或 SOURCE_WATERMARK_2_1_DDL）。"""
    if table == "source_watermark":
        return SOURCE_WATERMARK_2_1_DDL
    return DDL_DUCKDB[table]


def _build_shadow_copy_sql(table: str, shadow_name: str) -> str:
    """构造 INSERT INTO shadow SELECT ... FROM legacy 的列映射 SQL。

    策略：shadow 按 target DDL 建全（含新列 NULL 默认）；从 legacy 复制公共列，
    新列用 _legacy_filler 的固定值（或 source_watermark 的分类回填 CASE）。
    """
    target_fp = TARGET_MAIN_DB_2_1_FINGERPRINT.get(table) or {
        "columns": [(c[0], c[1], c[2], c[3]) for c in
                    parse_physical_contract(_target_ddl_for(table))["columns"]],
        "primary_key": parse_physical_contract(_target_ddl_for(table))["primary_key"],
    }
    legacy_fp = LEGACY_MAIN_DB_2_0_FINGERPRINT.get(table)
    legacy_cols = {c[0] for c in legacy_fp["columns"]} if legacy_fp else set()
    target_cols = [c[0] for c in target_fp["columns"]]
    filler = _legacy_filler(table)

    select_exprs: List[str] = []
    for col in target_cols:
        if col in filler:
            # 固定回填值（字面量；字符串加引号，数字/None 原样）
            v = filler[col]
            select_exprs.append("NULL" if v is None else (f"'{v}'" if isinstance(v, str) else str(v)))
        elif col in legacy_cols:
            select_exprs.append(f'"{col}"')
        else:
            # 新列在 legacy 不存在且无固定回填 → NULL（target 非 NOT NULL 列）
            select_exprs.append("NULL")

    if table == "source_watermark":
        # source_watermark 按 table_name 分类回填 source_generation/cutover_id
        select_exprs = _source_watermark_select_exprs(target_cols, legacy_cols)

    cols_csv = ", ".join(f'"{c}"' for c in target_cols)
    select_csv = ", ".join(select_exprs)
    return f'INSERT INTO "{shadow_name}" ({cols_csv}) SELECT {select_csv} FROM "{table}"'


def _source_watermark_select_exprs(target_cols: List[str], legacy_cols: set) -> List[str]:
    """source_watermark 的 SELECT 表达式：公共列复制 + 审计列按 table_name 分类回填。"""
    price_tables_csv = ", ".join(f"'{t}'" for t in QFQ_PRICE_TABLES)
    exprs: List[str] = []
    for col in target_cols:
        if col == "source_generation":
            exprs.append(
                f"CASE WHEN table_name IN ({price_tables_csv}) THEN 'xtquant-legacy' "
                "ELSE 'not-qfq-managed' END")
        elif col == "cutover_id":
            exprs.append(
                f"CASE WHEN table_name IN ({price_tables_csv}) THEN 'legacy-xtquant-pre-cutover' "
                "ELSE 'not-applicable' END")
        elif col in legacy_cols:
            exprs.append(f'"{col}"')
        else:
            exprs.append("NULL")
    return exprs


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _validate_rebuilt_table(conn, table: str, shadow_name: str,
                            row_count_before: int) -> Dict:
    """校验单张重建表（行数/PK无NULL/PK唯一/NOT NULL无NULL）。返回完整校验摘要。"""
    result: Dict = {
        "table": table, "ok": False, "errors": [],
        "row_count_before": row_count_before, "row_count_after": -1,
        "pk_null_count": -1, "pk_distinct_count": -1,
        "not_null_columns_checked": [], "not_null_violation_count": -1,
        "not_null_counts_by_column": {},
    }
    target_fp = TARGET_MAIN_DB_2_1_FINGERPRINT[table]
    pk = target_fp["primary_key"]
    try:
        n_after = _row_count(conn, shadow_name)
        result["row_count_after"] = n_after
        if n_after != row_count_before:
            result["errors"].append(f"行数不一致: before={row_count_before} after={n_after}")
        # PK 无 NULL
        pk_null_total = 0
        for c in pk:
            cnt = conn.execute(
                f'SELECT COUNT(*) FROM "{shadow_name}" WHERE "{c}" IS NULL').fetchone()[0]
            pk_null_total += cnt
            if cnt > 0:
                result["errors"].append(f"PK 列 {c} 有 {cnt} 个 NULL")
        result["pk_null_count"] = pk_null_total
        # PK 唯一（行数 == PK 去重数）
        pk_csv = ", ".join(f'"{c}"' for c in pk)
        distinct = conn.execute(
            f'SELECT COUNT(*) FROM (SELECT DISTINCT {pk_csv} FROM "{shadow_name}")').fetchone()[0]
        result["pk_distinct_count"] = distinct
        if distinct != n_after:
            result["errors"].append(f"PK 不唯一: rows={n_after} distinct_pk={distinct}")
        # NOT NULL 列无 NULL（P0-2：完整摘要记入报告）
        not_null_checked: List[str] = []
        not_null_violation_total = 0
        not_null_counts: Dict[str, int] = {}
        for (cname, ctype, cnotnull, cdefault) in target_fp["columns"]:
            if cnotnull:
                not_null_checked.append(cname)
                cnt = conn.execute(
                    f'SELECT COUNT(*) FROM "{shadow_name}" WHERE "{cname}" IS NULL').fetchone()[0]
                not_null_counts[cname] = cnt
                not_null_violation_total += cnt
                if cnt > 0:
                    result["errors"].append(f"NOT NULL 列 {cname} 有 {cnt} 个 NULL")
        result["not_null_columns_checked"] = not_null_checked
        result["not_null_violation_count"] = not_null_violation_total
        result["not_null_counts_by_column"] = not_null_counts
        result["ok"] = not result["errors"]
    except Exception as e:
        result["errors"].append(f"校验异常: {e}")
    return result


# ---------------------------------------------------------------------------
# 故障注入钩子
# ---------------------------------------------------------------------------

# 13 个故障注入点（B-3b 工作包 §九）
FAILURE_POINTS: Tuple[str, ...] = (
    "before_begin", "after_first_shadow_create", "after_partial_shadow_create",
    "during_first_copy", "after_all_copy", "before_validation", "after_validation",
    "after_first_rename", "after_partial_rename", "after_new_tables_create",
    "before_final_fingerprint", "before_commit", "after_commit_before_report",
)


class _FailureInjector:
    """故障注入：在指定点抛 _InjectedFailure（模拟中断）。"""
    def __init__(self, point: Optional[str]):
        self.point = point
        self._first_shadow_done = False
        self._first_copy_done = False
        self._first_rename_done = False

    def fire(self, point: str):
        if self.point is None:
            return
        if point == self.point:
            raise QfqMigrationError(f"[故障注入] {point}")


# ---------------------------------------------------------------------------
# 主迁移函数
# ---------------------------------------------------------------------------

def migrate_reanchor_2_0_to_2_1(
    db_path: str | Path,
    *,
    allowed_root: str | Path,
    apply: bool = False,
    failure_injection: Optional[str] = None,
    report_path: Optional[str | Path] = None,
) -> MigrationReport:
    """Run the explicit local/staging reanchor 2.0 -> 2.1 migration.

    B-3b.3 report contract:
    - the final report path is atomically reserved with O_EXCL before any DB
      read-write connection;
    - the same owned descriptor records PENDING and the terminal state;
    - no temporary-file publish and no overwrite/replace operation is used;
    - a report failure after COMMIT raises MigrationCommittedReportError.
    """
    import datetime as _dt
    import uuid as _uuid

    started = _dt.datetime.now().isoformat()
    db = _resolve_canonical(db_path)
    root = _resolve_canonical(allowed_root)

    # Hard safety checks precede report reservation and all DB write access.
    _assert_not_production(db)
    _assert_allowed_root(db, root)
    if report_path is None:
        stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S%f")
        report_path = root / f"qfq_migration_report_{stamp}_{_uuid.uuid4().hex[:8]}.json"
    report_path = _resolve_canonical(report_path)

    reservation = _ReportReservation.reserve(
        report_path, db, root, started_at=started)
    report_path = reservation.path
    source_status: Optional[SchemaStatus] = None
    txn_started = False
    committed = False
    counts_before: Dict[str, int] = {}
    hashes_before: Dict[str, str] = {}
    txn_validation: List[Dict] = []
    final_fingerprint_ok = False

    def _error_report(status: str, error: BaseException) -> MigrationReport:
        source_value = source_status.value if source_status is not None else "unknown"
        target_value = (SchemaStatus.COMPLETE_2_1.value if committed
                        else SchemaStatus.COMPLETE_2_0.value if txn_started
                        else "not-applied")
        return MigrationReport(
            db_path=str(db), source_status=source_value, target_status=target_value,
            dry_run=not apply, applied=committed, already_current=False,
            content_hash_version=CONTENT_HASH_VERSION, report_path=str(report_path),
            report_status=status, report_error=str(error),
            row_counts_before=counts_before, hashes_before=hashes_before,
            validation_results={
                "final_fingerprint_ok": final_fingerprint_ok,
                "per_table": txn_validation,
                "migration_committed": committed,
            },
            started_at=started, finished_at=_dt.datetime.now().isoformat())

    try:
        if failure_injection == "before_begin":
            raise QfqMigrationError("[failure injection] before_begin")

        # Read-only schema preflight.
        if db.exists():
            ro = duckdb.connect(str(db), read_only=True)
            try:
                existing = _list_tables(ro)
                for table in existing:
                    if table.endswith(SHADOW_V2_SUFFIX) or table.endswith(SHADOW_LEGACY_SUFFIX):
                        raise QfqMigrationError(
                            f"migration residue table exists before migration: {table}")
                    if table.lower() in {name.lower() for name in KNOWN_SHADOW_TABLES}:
                        raise QfqMigrationError(
                            f"known B-3 shadow table exists before migration: {table}")
                source_status = detect_schema_status(ro)
            finally:
                ro.close()
        else:
            source_status = SchemaStatus.EMPTY_OR_NEW

        if source_status == SchemaStatus.COMPLETE_2_1:
            managed = (list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES)
                       + list(KEEP_TABLES) + list(NEW_TABLES))
            ro = duckdb.connect(str(db), read_only=True)
            try:
                ac_counts, ac_hashes = _snapshot(
                    ro, managed, TARGET_MAIN_DB_2_1_FINGERPRINT)
                ac_fingerprint_ok = verify_fingerprint(
                    ro, TARGET_MAIN_DB_2_1_FINGERPRINT, reject_extra=True)
                ac_shadow = [row[0] for row in ro.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name LIKE '%__b3b%'").fetchall()]
            finally:
                ro.close()
            report = MigrationReport(
                db_path=str(db), source_status=source_status.value,
                target_status=SchemaStatus.COMPLETE_2_1.value,
                dry_run=not apply, applied=False, already_current=True,
                content_hash_version=CONTENT_HASH_VERSION,
                report_path=str(report_path), report_status=REPORT_STATUS_ALREADY_CURRENT,
                row_counts_before=ac_counts, row_counts_after=ac_counts,
                hashes_before=ac_hashes, hashes_after=ac_hashes,
                validation_results={
                    "final_status": SchemaStatus.COMPLETE_2_1.value,
                    "final_fingerprint_ok": ac_fingerprint_ok,
                    "shadow_residue_count": len(ac_shadow),
                    "legacy_residue_count": len(ac_shadow),
                },
                started_at=started, finished_at=_dt.datetime.now().isoformat())
            reservation.write_report(report)
            return report

        if source_status != SchemaStatus.COMPLETE_2_0:
            raise QfqMigrationError(
                f"migration accepts COMPLETE_2_0 or COMPLETE_2_1 only; "
                f"current status is {source_status.value}")

        managed_before = (list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES)
                          + list(KEEP_TABLES))
        ro = duckdb.connect(str(db), read_only=True)
        try:
            counts_before, hashes_before = _snapshot(
                ro, managed_before, LEGACY_MAIN_DB_2_0_FINGERPRINT)
        finally:
            ro.close()

        if not apply:
            report = MigrationReport(
                db_path=str(db), source_status=source_status.value,
                target_status="(dry-run; apply=False)",
                dry_run=True, applied=False, already_current=False,
                content_hash_version=CONTENT_HASH_VERSION,
                report_path=str(report_path),
                report_status=REPORT_STATUS_DRY_RUN_COMPLETE,
                tables_rebuilt=list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES),
                tables_created=list(NEW_TABLES),
                row_counts_before=counts_before, hashes_before=hashes_before,
                started_at=started, finished_at=_dt.datetime.now().isoformat())
            reservation.write_report(report)
            return report

        injector = _FailureInjector(failure_injection)
        reservation._assert_owned()
        conn = duckdb.connect(str(db))
        try:
            conn.execute("BEGIN TRANSACTION")
            txn_started = True
            try:
                _do_migrate_in_txn(conn, injector, counts_before, txn_validation)
                injector.fire("before_final_fingerprint")
                final_fingerprint_ok = verify_fingerprint(
                    conn, TARGET_MAIN_DB_2_1_FINGERPRINT, reject_extra=True)
                if not final_fingerprint_ok:
                    raise QfqMigrationError(
                        "in-transaction target fingerprint verification failed")
                injector.fire("before_commit")
                reservation._assert_owned()
                conn.execute("COMMIT")
                committed = True
                # A true process crash must occur after durable COMMIT but before
                # normal DuckDB connection cleanup/report update.  On Windows, closing
                # the connection before os._exit can intermittently terminate inside
                # DuckDB with 0xC0000005, masking the intended crash boundary.
                injector.fire("after_commit_before_report")
            except Exception:
                if not committed:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

        # Normal/control flow reaches this point only after the write connection closed.

        # Record the durable COMMIT before post-commit audit.
        checkpoint = MigrationReport(
            db_path=str(db), source_status=source_status.value,
            target_status=SchemaStatus.COMPLETE_2_1.value,
            dry_run=False, applied=True, already_current=False,
            content_hash_version=CONTENT_HASH_VERSION, report_path=str(report_path),
            report_status=REPORT_STATUS_MIGRATION_COMMITTED,
            tables_rebuilt=list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES),
            tables_created=list(NEW_TABLES), row_counts_before=counts_before,
            hashes_before=hashes_before,
            validation_results={
                "final_fingerprint_ok": final_fingerprint_ok,
                "per_table": txn_validation, "audit_complete": False,
            },
            started_at=started, finished_at=_dt.datetime.now().isoformat())
        reservation.write_report(checkpoint)

        post = duckdb.connect(str(db), read_only=True)
        try:
            final_status = detect_schema_status(post)
            all_tables = (list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES)
                          + list(KEEP_TABLES) + list(NEW_TABLES))
            counts_after, hashes_after = _snapshot(
                post, all_tables, TARGET_MAIN_DB_2_1_FINGERPRINT)
            shadow_residue = [row[0] for row in post.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE '%__b3b%'").fetchall()]
            legacy_residue = [row[0] for row in post.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE '%__b3b_legacy'").fetchall()]
        finally:
            post.close()

        report = MigrationReport(
            db_path=str(db), source_status=source_status.value,
            target_status=final_status.value, dry_run=False, applied=True,
            already_current=False, content_hash_version=CONTENT_HASH_VERSION,
            report_path=str(report_path), report_status=REPORT_STATUS_MIGRATION_COMMITTED,
            tables_rebuilt=list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES),
            tables_created=list(NEW_TABLES),
            row_counts_before=counts_before, row_counts_after=counts_after,
            hashes_before=hashes_before, hashes_after=hashes_after,
            validation_results={
                "final_status": final_status.value,
                "final_fingerprint_ok": final_fingerprint_ok,
                "per_table": txn_validation,
                "shadow_residue_count": len(shadow_residue),
                "legacy_residue_count": len(legacy_residue),
                "audit_complete": True,
            },
            started_at=started, finished_at=_dt.datetime.now().isoformat())
        reservation.write_report(report)
        return report

    except Exception as error:
        if committed:
            committed_report = _error_report(REPORT_STATUS_MIGRATION_COMMITTED, error)
            try:
                reservation.write_report(committed_report)
            except Exception as report_error:
                message = (
                    f"database migration COMMITTED but report update failed; "
                    f"db={db}; report={report_path}; migration_error={error}; "
                    f"report_error={report_error}")
            else:
                message = (
                    f"database migration COMMITTED; post-commit/report processing failed; "
                    f"db={db}; report={report_path}; error={error}")
            raise MigrationCommittedReportError(
                message, db_path=str(db), report_path=str(report_path)) from error

        terminal_status = (REPORT_STATUS_ROLLED_BACK if txn_started
                           else REPORT_STATUS_FAILED_PRECHECK)
        failure_report = _error_report(terminal_status, error)
        try:
            reservation.write_report(failure_report)
        except Exception as report_error:
            raise QfqMigrationError(
                f"migration failed before COMMIT and failure report update also failed; "
                f"migration_error={error}; report_error={report_error}") from error
        if isinstance(error, QfqMigrationError):
            raise
        raise QfqMigrationError(
            f"migration failed before COMMIT ({terminal_status}): {error}") from error
    finally:
        reservation.close()


def _do_migrate_in_txn(conn, injector: _FailureInjector, counts_before: Dict[str, int],
                       txn_validation: Optional[List[Dict]] = None) -> None:
    """事务内执行全部迁移步骤（建 shadow → 复制 → 校验 → 新表 → swap → 清理）。

    任一步失败由调用方 ROLLBACK。失败后重开库必须恢复为 COMPLETE_2_0。
    若传入 ``txn_validation``，逐表校验结果（含 before/after 行数、PK NULL 数、
    PK distinct 数、NOT NULL 检查）会 append 进该列表，供报告引用。
    """
    rebuild = list(REBUILD_QFQ_TABLES) + list(REBUILD_SHARED_TABLES)

    # (a) 建 shadow target 表（按 target DDL，命名 <table>__b3b_v2）
    first_shadow = True
    for table in rebuild:
        ddl = _rewrite_ddl_table_name(
            _target_ddl_for(table), table, f"{table}{SHADOW_V2_SUFFIX}")
        # shadow 表用 CREATE TABLE（非 IF NOT EXISTS），残留已在预检拒绝
        ddl = ddl.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE", 1)
        conn.execute(ddl)
        if first_shadow:
            injector._first_shadow_done = True
            injector.fire("after_first_shadow_create")
            first_shadow = False
        else:
            injector.fire("after_partial_shadow_create")

    # (b) 复制 legacy 行到 shadow（含历史回填）
    first_copy = True
    for table in rebuild:
        injector.fire("during_first_copy") if first_copy else None
        shadow = f"{table}{SHADOW_V2_SUFFIX}"
        copy_sql = _build_shadow_copy_sql(table, shadow)
        conn.execute(copy_sql)
        if first_copy:
            injector._first_copy_done = True
            first_copy = False
    injector.fire("after_all_copy")

    # (c) 校验每张重建表（结果收集进 txn_validation 供报告引用）
    injector.fire("before_validation")
    for table in rebuild:
        shadow = f"{table}{SHADOW_V2_SUFFIX}"
        res = _validate_rebuilt_table(conn, table, shadow, counts_before.get(table, -1))
        if txn_validation is not None:
            txn_validation.append(res)
        if not res["ok"]:
            raise QfqMigrationError(f"校验失败 {table}: {res['errors']}")
    injector.fire("after_validation")

    # (d) 新建 4 张 B-3 表（空表）
    for table in NEW_TABLES:
        conn.execute(DDL_DUCKDB[table])
    injector.fire("after_new_tables_create")

    # (e) swap：原表 → __b3b_legacy，shadow __b3b_v2 → 原名
    first_rename = True
    for table in rebuild:
        legacy_tmp = f"{table}{SHADOW_LEGACY_SUFFIX}"
        shadow = f"{table}{SHADOW_V2_SUFFIX}"
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy_tmp}"')
        conn.execute(f'ALTER TABLE "{shadow}" RENAME TO "{table}"')
        if first_rename:
            injector._first_rename_done = True
            injector.fire("after_first_rename")
            first_rename = False
        else:
            injector.fire("after_partial_rename")

    # (f) 清理事务内临时 legacy 表（DROP）
    for table in rebuild:
        legacy_tmp = f"{table}{SHADOW_LEGACY_SUFFIX}"
        conn.execute(f'DROP TABLE "{legacy_tmp}"')


def _rewrite_ddl_table_name(ddl: str, old_name: str, new_name: str) -> str:
    """重写 DDL 的表名（CREATE TABLE IF NOT EXISTS <old> → <new>）。"""
    # 匹配 CREATE TABLE [IF NOT EXISTS] <name>
    return re.sub(
        rf"(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)){re.escape(old_name)}\b",
        rf"\g<1>{new_name}",
        ddl, count=1, flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# Report lifecycle: exclusive final-path reservation; no temporary publish/replace.
# ---------------------------------------------------------------------------

# B-3b.3 report lifecycle is implemented by _ReportReservation above.
# There is deliberately no post-migration temporary publish helper.


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _report_to_dict(report: MigrationReport) -> Dict:
    """完整 report 字典（含 hashes/content_hash_version/report_path/validation 全字段）。"""
    return {
        "db_path": report.db_path,
        "source_status": report.source_status,
        "target_status": report.target_status,
        "dry_run": report.dry_run,
        "applied": report.applied,
        "already_current": report.already_current,
        "content_hash_version": report.content_hash_version,
        "report_path": report.report_path,
        "report_status": report.report_status,
        "report_error": report.report_error,
        "tables_rebuilt": report.tables_rebuilt,
        "tables_created": report.tables_created,
        "row_counts_before": report.row_counts_before,
        "row_counts_after": report.row_counts_after,
        "hashes_before": report.hashes_before,
        "hashes_after": report.hashes_after,
        "validation_results": report.validation_results,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
    }


def _cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m quantstudio.pipeline.qfq_schema_migration",
        description="QFQ reanchor 显式 2.0→2.1 migration runner（B-3b）。默认 dry-run。")
    parser.add_argument("--db", required=True, help="目标库路径（必须在 --allowed-root 下）")
    parser.add_argument("--allowed-root", required=True, help="允许的根目录（staging/临时）")
    parser.add_argument("--apply", action="store_true",
                        help="真正迁移（默认 dry-run，0 写）")
    parser.add_argument("--allow-production", action="store_true",
                        help="（无效）正式库硬拒绝，此开关不接受")
    parser.add_argument("--report", default=None,
                        help="report JSON 输出路径（默认：allowed-root 下确定性时间戳文件）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        report = migrate_reanchor_2_0_to_2_1(
            args.db, allowed_root=args.allowed_root, apply=args.apply,
            report_path=args.report)
    except MigrationCommittedReportError as e:
        print(f"[COMMITTED_REPORT_ERROR] {e}", file=sys.stderr)
        return 3
    except QfqMigrationProductionRefused as e:
        print(f"[REFUSED] {e}", file=sys.stderr)
        return 2
    except QfqMigrationError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    # migrate updates the exclusively reserved final report through its owned descriptor.
    # CLI 仅打印 report 内容到 stdout。
    payload = _report_to_dict(report)
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli_main())
