# -*- coding: utf-8 -*-
"""交付包（delivery）内核常量——**唯一权威定义处**（plan §1.3）。

其他模块 MUST 从这里 import；禁止在别处复制字面量（否则判据会静默失准，见
`knowledge/db/DB-2026-0003.md` 的教训）。

对应需求：FR-001（硬编码白名单）、FR-002（排除项）、FR-006（manifest 版本）、
FR-011（四类失败）、FR-018（四态 + 两个排除性标签）、FR-020（配置两项）。
"""
from __future__ import annotations

# ── 交付内容白名单（FR-001：硬编码，不可配置/不可扩展）───────────────────────
WHITELIST = ("quantstudio.db", "qfq_aux.db", "qfq_aux_mcp_gen1.db")
MANIFEST_NAME = "manifest.json"
BUNDLE_ALLOWED_NAMES = WHITELIST + (MANIFEST_NAME,)   # 产出目录允许的条目全集

# 引擎类型（manifest.files[].kind 的权威映射）
ENGINE_KIND = {
    "quantstudio.db": "duckdb",
    "qfq_aux.db": "sqlite",
    "qfq_aux_mcp_gen1.db": "sqlite",
}

# ── 排除项（FR-002：红线自检用；标签 → 正则）────────────────────────────────
EXCLUDE_PATTERNS = (
    ("batch_audit", r"(?i)^batch_audit\.db$"),
    ("quarantine", r"(?i)^quarantine\.db$"),
    ("task_resume", r"(?i)task_resume[\\/]"),
    ("daemon_status", r"(?i)^daemon_status\.json$"),
    ("snapshots", r"(?i)snapshots[\\/]"),
    ("logs", r"(?i)(^|[\\/])logs?[\\/]"),
    ("mcp_landing", r"(?i)mcp_landing[\\/]"),
    ("staging", r"(?i)^staging[^\\/]*[\\/]"),
    ("backup", r"(?i)^backup[^\\/]*[\\/]"),
    ("wp2_release", r"(?i)wp2_release[\\/]"),
    ("ptrade_fidelity", r"(?i)ptrade_fidelity[\\/]"),
    ("c4resume_staging", r"(?i)_c4resume_staging\.db"),
    ("env", r"(?i)\.env$"),
    ("shm", r"(?i)-shm$"),
)

# ── 版本常量 ────────────────────────────────────────────────────────────────
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION_SUPPORTED = (1,)
CONFIG_VERSION = 1
JOURNAL_VERSION = 1
JOURNAL_NAME = "import_journal.json"
BACKUP_DIR_PREFIX = "backup_import_"
RESTORE_README_NAME = "RESTORE_README.txt"
LOCK_TASK_EXPORT = "delivery_export"
LOCK_TASK_IMPORT = "delivery_import"

# ── manifest 必填顶层字段（data-model §2.1；与 contracts/manifest-v1.schema.json 同步）──
MANIFEST_REQUIRED_TOP = (
    "manifest_schema_version",
    "generated_at",
    "producer",
    "files",
    "main_db",
    "qfq",
    "table_set",
    "tables",
    "row_count_degraded",
    "row_count_degraded_tables",
    "self_check",
    "export",
)
MANIFEST_REQUIRED_PRODUCER = ("app", "version", "python", "duckdb", "sqlite", "platform")
MANIFEST_REQUIRED_FILE = ("path", "kind", "bytes", "sha256")

# ── 配置/配置真源相对路径（相对 config/ 目录）────────────────────────────────
DELIVERY_CONFIG_NAME = "delivery_config.json"
TABLE_MAP_REL = "profiles/mcp_only/questdb_table_map.json"
AUX_PATHS_REL = "profiles/mcp_only/qfq_aux_paths.json"
COLLECTOR_TASKS_REL = "profiles/mcp_only/collector_tasks.json"
QUALITY_BASELINE_REL = "quality_baseline.json"

# ── 表集边界规则（Q2 裁定：①∩库内实际存在）────────────────────────────────
TABLE_SET_RULE = "(应推进水位的表 ∪ questdb_table_map 覆盖的表) ∩ 库内实际存在"
BASIS_SOURCE_MAP = "questdb_table_map.json"
BASIS_SOURCE_ALIGNER = "alignment_rules.time_key"
BASIS_SOURCE_NONE = "none"
ALIGNMENT_RULES_REL = "profiles/mcp_only/alignment_rules.json"
UNKNOWN_BASIS = "unknown"          # date_basis_col 的"未知"取值（FR-006 明写）

# 主库 schema 放行集（导出前置 FR-003）
EXPORT_ALLOWED_SCHEMA = ("complete_2_1", "complete_2_2")

# ── 逐表状态（FR-018 四态 + UNKNOWN-SOURCE + 两个排除性标签）────────────────
class TableState:
    """逐表状态标签。

    四态之一：OK / BEHIND / AHEAD / MISSING；另有 UNKNOWN-SOURCE（信息性）；
    三个**排除性标签**（不参与四态判定，spec FR-018 v5）：
    `EMPTY`（无数据无水位）/ `UNKNOWN-BASIS`（基准列未知，不猜）/
    `NO-WATERMARK-SYSTEM`（passthrough / 无水位体系，只报 row_count）。
    """

    OK = "OK"
    BEHIND = "BEHIND"
    AHEAD = "AHEAD"
    MISSING = "MISSING"
    UNKNOWN_SOURCE = "UNKNOWN-SOURCE"
    EMPTY = "EMPTY"
    UNKNOWN_BASIS = "UNKNOWN-BASIS"
    NO_WATERMARK_SYSTEM = "NO-WATERMARK-SYSTEM"


ALL_STATES = frozenset(
    {
        TableState.OK,
        TableState.BEHIND,
        TableState.AHEAD,
        TableState.MISSING,
        TableState.UNKNOWN_SOURCE,
        TableState.EMPTY,
        TableState.UNKNOWN_BASIS,
        TableState.NO_WATERMARK_SYSTEM,
    }
)
EXCLUSIONARY_STATES = frozenset(
    {TableState.EMPTY, TableState.UNKNOWN_BASIS, TableState.NO_WATERMARK_SYSTEM}
)
DEFECT_STATES = frozenset({TableState.MISSING, TableState.BEHIND, TableState.AHEAD})

# ── 四类失败（FR-011）──────────────────────────────────────────────────────
class FailureKind:
    PACKAGE = "1_package"                       # ① 包问题
    PROGRAM_VERSION = "2_program_version"       # ② 程序版本不匹配
    CONFIG = "3_config"                         # ③ 配置不匹配
    MANIFEST_VERSION = "4_manifest_version"     # ④ 包格式代差


# ── 退出码（contracts/cli.md §2）────────────────────────────────────────────
class Exit:
    OK = 0
    USAGE = 2
    REFUSED = 3            # 前置检查拒绝（零改动）
    PACKAGE = 4
    CONFIG = 5
    VERSION = 6
    RECOVERY_NEEDED = 7
    INTERNAL = 8
    ROLLED_BACK = 9        # 失败但已回滚
    ROLLBACK_FAILED = 10   # 失败且回滚失败
    INTERRUPTED = 130      # Ctrl+C（shell 惯例，非业务码）


EXIT_BY_FAILURE_KIND = {
    FailureKind.PACKAGE: Exit.PACKAGE,
    FailureKind.CONFIG: Exit.CONFIG,
    FailureKind.PROGRAM_VERSION: Exit.VERSION,
    FailureKind.MANIFEST_VERSION: Exit.VERSION,
}
