# -*- coding: utf-8 -*-
"""manifest v1：构建 / 解析 / 严格校验 / 规范化输出（FR-006、FR-011④、plan §8）。

三条不变式（plan §2）：
1. manifest **最后写**（由 export_kernel 保证）——目录内无 manifest 即视为包不完整；
2. 路径一律**相对包根、UTF-8**，拒绝绝对路径 / 盘符 / UNC / `..` / 反斜杠；
3. 自证可复算——`files[].sha256` 与实际文件逐字节一致。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from .constants import (
    ALL_STATES,
    BASIS_SOURCE_ALIGNER,
    BASIS_SOURCE_MAP,
    BASIS_SOURCE_NONE,
    ENGINE_KIND,
    EXPORT_ALLOWED_SCHEMA,
    FailureKind,
    MANIFEST_REQUIRED_FILE,
    MANIFEST_REQUIRED_PRODUCER,
    MANIFEST_REQUIRED_TOP,
    MANIFEST_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION_SUPPORTED,
    TABLE_SET_RULE,
    UNKNOWN_BASIS,
    WHITELIST,
)

_HEX = set("0123456789abcdef")
_SHA256_LEN = 64


# ── 规范化输出 ──────────────────────────────────────────────────────────────
def canonical_dumps(doc: Dict) -> str:
    """规范化 JSON（排序键、紧凑分隔、UTF-8 直写、结尾换行）。

    同一输入两次导出 ⇒ 除时间字段外逐字节一致（SC-009 的基础）。
    """
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


# ── 路径守卫 ────────────────────────────────────────────────────────────────
def path_error(value: object) -> Optional[str]:
    """校验 manifest 路径字段；合法返回 None，非法返回原因。"""
    if not isinstance(value, str) or not value:
        return "路径为空或非字符串"
    if "\\" in value:
        return f"路径含反斜杠（须用 / 相对形式）：{value!r}"
    if value.startswith("/") or value.startswith("~"):
        return f"路径为绝对路径：{value!r}"
    if len(value) >= 2 and value[1] == ":":
        return f"路径含盘符：{value!r}"
    if value.startswith("//"):
        return f"路径为 UNC：{value!r}"
    parts = PurePosixPath(value).parts
    if any(p in ("..", ".") for p in parts):
        return f"路径含相对跳转：{value!r}"
    return None


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LEN
        and all(ch in _HEX for ch in value)
    )


# ── v1 字段级校验 ───────────────────────────────────────────────────────────
def validate_v1(doc: Dict) -> List[str]:
    """返回错误清单（空 = 通过）。未知字段不算错误（同版本前向兼容，由读侧记 warning）。"""
    errors: List[str] = []

    for key in MANIFEST_REQUIRED_TOP:
        if key not in doc:
            errors.append(f"缺必填字段 {key}")

    if doc.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(
            f"manifest_schema_version 必须为 {MANIFEST_SCHEMA_VERSION}，"
            f"实际 {doc.get('manifest_schema_version')!r}"
        )

    if not isinstance(doc.get("generated_at"), str) or "T" not in str(doc.get("generated_at")):
        errors.append("generated_at 必须是 ISO8601 时间串")

    producer = doc.get("producer")
    if not isinstance(producer, dict):
        errors.append("producer 必须是对象")
    else:
        for key in MANIFEST_REQUIRED_PRODUCER:
            if not isinstance(producer.get(key), str) or not producer.get(key):
                errors.append(f"producer.{key} 缺失或非字符串")
        if producer.get("app") not in (None, "QuantStudio"):
            errors.append(f"producer.app 必须为 QuantStudio，实际 {producer.get('app')!r}")

    files = doc.get("files")
    if not isinstance(files, list) or not files:
        errors.append("files 必须是非空数组")
    else:
        seen = set()
        for idx, entry in enumerate(files):
            if not isinstance(entry, dict):
                errors.append(f"files[{idx}] 不是对象")
                continue
            for key in MANIFEST_REQUIRED_FILE:
                if key not in entry:
                    errors.append(f"files[{idx}] 缺字段 {key}")
            name = entry.get("path")
            err = path_error(name)
            if err:
                errors.append(f"files[{idx}].path 非法：{err}")
            elif name not in WHITELIST:
                errors.append(f"files[{idx}].path 不在白名单：{name!r}")
            elif name in seen:
                errors.append(f"files[{idx}].path 重复：{name!r}")
            else:
                seen.add(name)
                if entry.get("kind") != ENGINE_KIND.get(name):
                    errors.append(
                        f"files[{idx}].kind 应为 {ENGINE_KIND.get(name)!r}，"
                        f"实际 {entry.get('kind')!r}"
                    )
            if not isinstance(entry.get("bytes"), int) or entry.get("bytes", -1) < 0:
                errors.append(f"files[{idx}].bytes 必须是非负整数")
            if not _is_sha256(entry.get("sha256")):
                errors.append(f"files[{idx}].sha256 必须是 64 位小写 hex")
        missing = [w for w in WHITELIST if w not in seen]
        if missing:
            errors.append(f"files 缺白名单文件：{missing}")

    main_db = doc.get("main_db")
    if not isinstance(main_db, dict):
        errors.append("main_db 必须是对象")
    else:
        if path_error(main_db.get("path")):
            errors.append(f"main_db.path 非法：{path_error(main_db.get('path'))}")
        if main_db.get("schema_status") not in EXPORT_ALLOWED_SCHEMA:
            errors.append(
                f"main_db.schema_status 不在放行集：{main_db.get('schema_status')!r}"
            )

    qfq = doc.get("qfq")
    if not isinstance(qfq, dict):
        errors.append("qfq 必须是对象")
    else:
        if not isinstance(qfq.get("released"), bool):
            errors.append("qfq.released 必须是布尔")
        expect = qfq.get("aux_route_step6_expect")
        if not isinstance(expect, dict) or set(expect) != {"legacy", "gen1"}:
            errors.append("qfq.aux_route_step6_expect 必须含 legacy/gen1 两键")
        cutover = qfq.get("qfq_active_cutover")
        if cutover is not None:
            if not isinstance(cutover, dict):
                errors.append("qfq.qfq_active_cutover 必须是对象或 null")
            else:
                for key in ("cutover_id", "source_generation", "price_source"):
                    if not isinstance(cutover.get(key), str) or not cutover.get(key):
                        errors.append(f"qfq.qfq_active_cutover.{key} 缺失")

    table_set = doc.get("table_set")
    if not isinstance(table_set, dict):
        errors.append("table_set 必须是对象")
    else:
        if table_set.get("rule") != TABLE_SET_RULE:
            errors.append(
                f"table_set.rule 必须逐字等于 {TABLE_SET_RULE!r}，"
                f"实际 {table_set.get('rule')!r}"
            )
        for key in ("count", "empty_count"):
            if not isinstance(table_set.get(key), int) or table_set.get(key, -1) < 0:
                errors.append(f"table_set.{key} 必须是非负整数")
        for key in ("unknown_basis", "out_of_map"):
            if not isinstance(table_set.get(key), list):
                errors.append(f"table_set.{key} 必须是数组")

    tables = doc.get("tables")
    if not isinstance(tables, list) or not tables:
        errors.append("tables 必须是非空数组")
    else:
        if isinstance(table_set, dict) and table_set.get("count") != len(tables):
            errors.append(
                f"table_set.count({table_set.get('count')}) != len(tables)({len(tables)})"
            )
        degraded_names = set()
        for idx, row in enumerate(tables):
            if not isinstance(row, dict):
                errors.append(f"tables[{idx}] 不是对象")
                continue
            for key in ("table", "source", "freq", "date_basis_col", "basis_source"):
                if not isinstance(row.get(key), str) or not row.get(key):
                    errors.append(f"tables[{idx}].{key} 缺失或非字符串")
            if row.get("state") not in ALL_STATES:
                errors.append(f"tables[{idx}].state 非法：{row.get('state')!r}")
            basis = row.get("date_basis_col")
            if basis == UNKNOWN_BASIS:
                if row.get("max_date") is not None:
                    errors.append(f"tables[{idx}] basis 未知却输出了 max_date（禁止猜）")
                if row.get("basis_source") != BASIS_SOURCE_NONE:
                    errors.append(f"tables[{idx}] basis 未知时 basis_source 必须为 none")
            elif basis:
                if row.get("basis_source") not in (
                    BASIS_SOURCE_MAP, BASIS_SOURCE_ALIGNER, BASIS_SOURCE_NONE
                ):
                    errors.append(f"tables[{idx}].basis_source 非法")
            if row.get("row_count_method") not in ("exact", "estimate"):
                errors.append(f"tables[{idx}].row_count_method 非法")
            if row.get("row_count_method") == "estimate":
                degraded_names.add(row.get("table"))
        declared = doc.get("row_count_degraded_tables")
        if isinstance(declared, list) and set(declared) != degraded_names:
            errors.append(
                "row_count_degraded_tables 与实际 estimate 表集不一致："
                f"声明 {sorted(declared)} vs 实际 {sorted(x for x in degraded_names if x)}"
            )
        if isinstance(doc.get("row_count_degraded"), bool):
            if doc["row_count_degraded"] != bool(degraded_names):
                errors.append("row_count_degraded 与实际 estimate 表集不一致")

    self_check = doc.get("self_check")
    if not isinstance(self_check, dict):
        errors.append("self_check 必须是对象")
    else:
        red = self_check.get("red_line")
        if not isinstance(red, dict):
            errors.append("self_check.red_line 必须是对象")
        else:
            for key in ("outside_whitelist", "env_hits", "shm_hits", "log_hits"):
                if not isinstance(red.get(key), int) or red.get(key, -1) < 0:
                    errors.append(f"self_check.red_line.{key} 必须是非负整数")
            if not isinstance(red.get("zero_leak"), bool):
                errors.append("self_check.red_line.zero_leak 必须是布尔")

    export = doc.get("export")
    if not isinstance(export, dict):
        errors.append("export 必须是对象")
    else:
        if not isinstance(export.get("lock_task_id"), str) or not export.get("lock_task_id"):
            errors.append("export.lock_task_id 缺失")
        if not isinstance(export.get("duration_sec"), (int, float)) or export.get("duration_sec", -1) < 0:
            errors.append("export.duration_sec 必须是非负数")
        if not isinstance(export.get("copy_bytes_total"), int) or export.get("copy_bytes_total", -1) < 0:
            errors.append("export.copy_bytes_total 必须是非负整数")
        if not isinstance(export.get("copy_sha256_pass"), bool):
            errors.append("export.copy_sha256_pass 必须是布尔")
        ckpt = export.get("checkpoint")
        if not isinstance(ckpt, dict) or "duckdb" not in ckpt or "sqlite" not in ckpt:
            errors.append("export.checkpoint 必须含 duckdb/sqlite")

    return errors


# ── 读侧：版本裁决 → 字段校验 ───────────────────────────────────────────────
@dataclass
class ManifestRead:
    ok: bool
    doc: Optional[Dict] = None
    failure_kind: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


KNOWN_TOP_FIELDS = frozenset(MANIFEST_REQUIRED_TOP) | {"bundle_id"}


def parse_manifest(text: str) -> ManifestRead:
    """解析 + 版本裁决 + 字段校验。

    顺序固定（plan §8）：①JSON 合法性 ②版本字段存在/类型 ③版本高低裁决 ④字段级校验。
    版本**高于**支持集 ⇒ 第四类（提示更新程序，**不解析其余字段**）。
    """
    try:
        doc = json.loads(text)
    except Exception as exc:
        return ManifestRead(
            ok=False,
            failure_kind=FailureKind.PACKAGE,
            reasons=[f"manifest 不是合法 JSON：{exc}"],
        )
    if not isinstance(doc, dict):
        return ManifestRead(
            ok=False,
            failure_kind=FailureKind.PACKAGE,
            reasons=["manifest 根节点不是对象"],
        )

    if "manifest_schema_version" not in doc:
        return ManifestRead(
            ok=False,
            failure_kind=FailureKind.PACKAGE,
            reasons=["缺 manifest_schema_version（无法判断包格式版本）"],
        )
    version = doc.get("manifest_schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        return ManifestRead(
            ok=False,
            failure_kind=FailureKind.PACKAGE,
            reasons=[f"manifest_schema_version 必须是整数，实际 {version!r}"],
        )

    supported_max = max(MANIFEST_SCHEMA_VERSION_SUPPORTED)
    supported_min = min(MANIFEST_SCHEMA_VERSION_SUPPORTED)
    if version > supported_max:
        return ManifestRead(
            ok=False,
            failure_kind=FailureKind.MANIFEST_VERSION,
            reasons=[
                f"包由更新版本的程序生成（manifest_schema_version={version} > "
                f"本程序支持 {supported_max}），请先更新程序"
            ],
        )
    if version < supported_min:
        return ManifestRead(
            ok=False,
            failure_kind=FailureKind.PROGRAM_VERSION,
            reasons=[
                f"包格式过旧（manifest_schema_version={version} < 本程序最低 "
                f"{supported_min}），程序版本与数据版本不匹配"
            ],
        )

    warnings: List[str] = []
    unknown = sorted(set(doc) - KNOWN_TOP_FIELDS)
    if unknown:
        warnings.append(f"manifest 含未知字段（已忽略）：{unknown}")

    errors = validate_v1(doc)
    if errors:
        return ManifestRead(
            ok=False,
            doc=doc,
            failure_kind=FailureKind.PACKAGE,
            reasons=errors,
            warnings=warnings,
        )
    return ManifestRead(ok=True, doc=doc, warnings=warnings)


def read_manifest(path: Path) -> ManifestRead:
    path = Path(path)
    if not path.exists():
        return ManifestRead(
            ok=False,
            failure_kind=FailureKind.PACKAGE,
            reasons=[f"manifest.json 不存在：{path}"],
        )
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return ManifestRead(
            ok=False,
            failure_kind=FailureKind.PACKAGE,
            reasons=[f"manifest.json 无法以 UTF-8 读取：{exc}"],
        )
    return parse_manifest(text)


# ── 构建（export_kernel 在 W1 调用）──────────────────────────────────────────
def build_manifest(
    *,
    generated_at: str,
    producer: Dict,
    files: List[Dict],
    main_db: Dict,
    qfq: Dict,
    table_set: Dict,
    tables: List[Dict],
    self_check: Dict,
    export: Dict,
    bundle_id: Optional[str] = None,
) -> Dict:
    """组装 manifest 并**当场校验**（不合法即抛 ValueError，避免写出坏包）。"""
    degraded = [row.get("table") for row in tables if row.get("row_count_method") == "estimate"]
    doc: Dict = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": generated_at,
        "producer": producer,
        "files": files,
        "main_db": main_db,
        "qfq": qfq,
        "table_set": table_set,
        "tables": tables,
        "row_count_degraded": bool(degraded),
        "row_count_degraded_tables": sorted(x for x in degraded if x),
        "self_check": self_check,
        "export": export,
    }
    if bundle_id:
        doc["bundle_id"] = bundle_id
    errors = validate_v1(doc)
    if errors:
        raise ValueError("manifest 构建失败：" + "；".join(errors))
    return doc


def bundle_id_of(doc: Dict) -> str:
    """包标识：规范化 manifest（去掉 bundle_id 与全部时间字段）的 sha256。

    时间字段不参与标识——否则同一份数据的两次导出会得到不同 bundle_id，
    SC-009（双端逐位一致，时间除外）就无法比对。
    """
    stripped = dict(doc)
    stripped.pop("bundle_id", None)
    stripped.pop("generated_at", None)
    export = dict(stripped.get("export") or {})
    # 时间类字段一律不参与标识：含派生时间量 duration_sec（W2 实测：漏它会导致同内容两次导出 id 不同）
    for key in ("lock_acquired_at", "lock_released_at", "duration_sec"):
        export.pop(key, None)
    stripped["export"] = export
    return hashlib.sha256(canonical_dumps(stripped).encode("utf-8")).hexdigest()


def sha256_path(path: Path, chunk: int = 1 << 20) -> str:
    """流式文件哈希（复制/校验共用；单次读盘）。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sqlite_header(path: Path) -> Optional[str]:
    """SQLite 件头 16 字节（作为"有效库"旁证；非 SQLite 返回 None）。"""
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(16)
    except Exception:
        return None
    if head.startswith(b"SQLite format 3\x00"):
        return "SQLite format 3"
    return None


def expected_kind(name: str) -> str:
    return ENGINE_KIND[name]


def check_files_against_disk(bundle_dir: Path, files: List[Dict]) -> Tuple[List[str], List[str]]:
    """把 manifest.files 与磁盘实际比对：返回 (errors, warnings)。"""
    errors: List[str] = []
    warnings: List[str] = []
    for entry in files:
        rel = entry.get("path")
        target = Path(bundle_dir) / str(rel)
        if not target.exists():
            errors.append(f"白名单文件缺失：{rel}")
            continue
        size = target.stat().st_size
        if size != entry.get("bytes"):
            errors.append(f"{rel} 字节数不符：manifest={entry.get('bytes')} 实际={size}")
            continue
        actual = sha256_path(target)
        if actual != entry.get("sha256"):
            errors.append(f"{rel} SHA256 不符：manifest={entry.get('sha256')[:12]}… 实际={actual[:12]}…")
        if ENGINE_KIND.get(str(rel)) == "sqlite":
            head = sqlite_header(target)
            if head is None:
                warnings.append(f"{rel} 头 16 字节不是 SQLite 魔数（可能不是有效 SQLite 库）")
            elif entry.get("sqlite_header") not in (None, head):
                warnings.append(
                    f"{rel} sqlite_header 声明 {entry.get('sqlite_header')!r} 与实际不符"
                )
    return errors, warnings
