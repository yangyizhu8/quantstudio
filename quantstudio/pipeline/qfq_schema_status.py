"""QFQ schema 物理状态识别（v2.4 B-3a 完整版）。

本模块是 **B-3a 普通 init 写前 fail-fast 闸门** 的只读探测层。它根据两个**独立冻结**
的版本化物理指纹（``LEGACY_MAIN_DB_2_0_FINGERPRINT`` / ``TARGET_MAIN_DB_2_1_FINGERPRINT``，
来自 ``qfq_schema_contracts.py``，**不派生自当前 DDL / SCHEMA_CONTRACT_DUCKDB /
SCHEMA_VERSION 字符串**）判定 DuckDB 主库相对最终 2.1 的物理状态。

状态机（B-3R §10 + 审核修订 §二）：

| 物理状态 | 判定 | 普通 init 行为 |
|---|---|---|
| ``EMPTY_OR_NEW`` | 无任何已知版本化对象（legacy/target/B-3新表/shadow/migration/共享表） | 代码侧 DDL==target 时创建完整 2.1；否则写前 fail-fast |
| ``COMPLETE_2_1`` | 物理结构与 ``TARGET_MAIN_DB_2_1_FINGERPRINT`` 逐字一致 | 严格只读 no-op（仅 verify_fingerprint，0 写操作） |
| ``COMPLETE_2_0`` | 物理结构与 ``LEGACY_MAIN_DB_2_0_FINGERPRINT`` 逐字一致 | 写前 fail-fast |
| ``PARTIAL_OR_MIXED`` | 其它（缺表/缺列/多列/类型错/NOT NULL 错/default 错/PK 错/shadow 残留/混合） | 写前 fail-fast |
| ``UNKNOWN`` | introspection 查询本身异常 | 写前 fail-fast |

关键：**不**用 ``SCHEMA_VERSION`` 常量判版本；**不**用当前 ``SCHEMA_CONTRACT_DUCKDB``
充当 2.1 定义。状态识别只看冻结指纹的物理逐字匹配。

本模块纯只读：``detect_schema_status`` / ``assert_init_allowed`` 仅发 introspection 查询
（``duckdb_constraints()`` / ``PRAGMA table_info``），不建表、不补列、不 init、不发
DDL/DML。
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Set

logger = logging.getLogger(__name__)

from quantstudio.pipeline.qfq_schema_contracts import (
    LEGACY_MAIN_DB_2_0_FINGERPRINT,
    TARGET_MAIN_DB_2_1_FINGERPRINT,
    KNOWN_VERSIONED_OBJECTS,
    KNOWN_SHADOW_TABLES,
    KNOWN_MIGRATION_TABLES,
    B3_NEW_TABLES,
    verify_fingerprint,
)
from quantstudio.pipeline.qfq_reanchor_schema import SCHEMA_VERSION


class SchemaStatus(str, Enum):
    """DuckDB 主库相对最终 2.1 的物理状态分类。"""

    EMPTY_OR_NEW = "empty_or_new"
    COMPLETE_2_1 = "complete_2_1"
    COMPLETE_2_0 = "complete_2_0"
    PARTIAL_OR_MIXED = "partial_or_mixed"
    UNKNOWN = "unknown"


# 已知版本化对象名集合（小写），用于空库判定。
# 注意：空库判定基准 = **QFQ 重锚子系统专属表**（qfq_* 前缀，含 B-3 新表）+
# shadow/migration 残留表。**不**含 source_watermark 与 trade_calendar——它们是与
# writers.py 框架 schema 共享的表，由框架在打开连接时即创建（先于 QFQ init），
# 单独存在不构成"已有 QFQ 重锚版本化结构"。source_watermark 的版本（8 列 vs 6 列）
# 仍由完整状态校验（verify_fingerprint against MAIN_DB 指纹）覆盖。
def _all_qfq_versioned_table_names() -> Set[str]:
    """全部 qfq_* 版本化表名（legacy 11 + B-3 新 4）+ shadow + migration。"""
    from quantstudio.pipeline.qfq_schema_contracts import (
        LEGACY_QFQ_MANAGED_TABLES, B3_NEW_TABLES)
    names: Set[str] = set()
    for t in LEGACY_QFQ_MANAGED_TABLES:
        if t != "trade_calendar":  # 共享表，从空库基准排除
            names.add(t.lower())
    for t in B3_NEW_TABLES:
        names.add(t.lower())
    return names


_QFQ_VERSIONED_TABLES_LOWER: Set[str] = _all_qfq_versioned_table_names()
_SHADOW_LOWER: Set[str] = {o.lower() for o in KNOWN_SHADOW_TABLES}
_MIGRATION_LOWER: Set[str] = {o.lower() for o in KNOWN_MIGRATION_TABLES}
_B3_NEW_LOWER: Set[str] = {o.lower() for o in B3_NEW_TABLES}


class QfqSchemaMigrationRequired(RuntimeError):
    """普通 init 遇版本化旧 schema / partial / unknown 时写前 fail-fast 抛出。

    提示调用方：不得用普通 init 自动补列/升级；必须用显式 migration runner（B-3b）。
    """


def _list_tables(conn) -> Set[str]:
    """只读列出 DuckDB 主库全部用户表名（小写）。"""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main'"
    ).fetchall()
    return {str(r[0]).lower() for r in rows}


def _has_any_known_qfq_object(conn) -> bool:
    """任一 QFQ 重锚子系统专属表（qfq_*，含 B-3 新表）/ shadow / migration 残留存在 → 非空库。

    **不**含 source_watermark 与 trade_calendar（与 writers.py 框架 schema 共享，由框架
    在打开连接时即创建，单独存在不构成"已有 QFQ 重锚版本化结构"）。
    """
    tables = _list_tables(conn)
    return bool(
        tables & _QFQ_VERSIONED_TABLES_LOWER
        or tables & _SHADOW_LOWER
        or tables & _MIGRATION_LOWER
    )


def _has_shadow_or_migration_or_b3new(conn) -> bool:
    """是否存在 shadow / migration / B-3 新表残留（任一存在 → 非空库、非 legacy 2.0）。"""
    tables = _list_tables(conn)
    return bool(
        tables & _SHADOW_LOWER
        or tables & _MIGRATION_LOWER
        or tables & _B3_NEW_LOWER
    )


def _shared_table_ok_or_absent(conn, table: str, target_fp: dict) -> bool:
    """P0-1：共享表（source_watermark / trade_calendar）状态判定。

    返回 True（可判 EMPTY_OR_NEW）当且仅当：表不存在，**或**已精确匹配 target 指纹。
    表存在但为 legacy/错误/partial → 返回 False（→ 调用方判 PARTIAL_OR_MIXED）。

    这样旧 6 列 source_watermark 或错误结构的 trade_calendar 不会让库被误判为空库，
    进而触发普通 init 在旧共享表上建 15 张 QFQ 2.1 表，留下 2.0/2.1 混合 schema。
    """
    from quantstudio.pipeline.qfq_schema_contracts import (
        _table_exists, verify_fingerprint)
    if not _table_exists(conn, table):
        return True  # 不存在 → 视同空库可放行（普通 init 会创建 target 结构）
    # 存在 → 必须精确匹配 target 指纹（列/类型/NOT NULL/DEFAULT/PK/UNIQUE/CHECK/FK）
    return verify_fingerprint(conn, {table: target_fp}, reject_extra=True)


def detect_schema_status(conn) -> SchemaStatus:
    """只读探测 DuckDB 主库相对最终 2.1 的物理状态。

    纯只读：仅 introspection 查询。introspection 异常 → ``UNKNOWN``（绝不误判空库）。
    """
    try:
        # 1. 空库判定：无任何已知版本化对象（qfq_*，含 B-3 新表）/shadow/migration
        if not _has_any_known_qfq_object(conn):
            # 但若有 shadow/migration/B-3 新表残留，仍判 partial（阻断 3：中断恢复场景）
            if _has_shadow_or_migration_or_b3new(conn):
                return SchemaStatus.PARTIAL_OR_MIXED
            # P0-1：共享表（source_watermark / trade_calendar）若存在但为 legacy/错误/
            # partial，不得判空库（否则普通 init 会在旧共享表上建 15 张 QFQ 2.1 表，
            # 留下 2.0/2.1 混合 schema）。仅当共享表不存在或已精确匹配 target 时才放行。
            from quantstudio.pipeline.qfq_schema_contracts import (
                TARGET_QFQ_2_1_FINGERPRINT, TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT)
            if not _shared_table_ok_or_absent(
                    conn, "source_watermark", TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT):
                return SchemaStatus.PARTIAL_OR_MIXED
            if not _shared_table_ok_or_absent(
                    conn, "trade_calendar", TARGET_QFQ_2_1_FINGERPRINT["trade_calendar"]):
                return SchemaStatus.PARTIAL_OR_MIXED
            return SchemaStatus.EMPTY_OR_NEW

        # 2. 完整 2.1：物理结构与 target 主库指纹（QFQ 15表 + source_watermark）逐字一致
        if verify_fingerprint(conn, TARGET_MAIN_DB_2_1_FINGERPRINT, reject_extra=True):
            return SchemaStatus.COMPLETE_2_1

        # 3. 完整 2.0：物理结构与 legacy 主库指纹逐字一致
        if verify_fingerprint(conn, LEGACY_MAIN_DB_2_0_FINGERPRINT, reject_extra=True):
            return SchemaStatus.COMPLETE_2_0

        # 4. 其它 → partial
        return SchemaStatus.PARTIAL_OR_MIXED
    except Exception as e:
        # introspection 异常 → UNKNOWN（阻断 6），绝不误判空库
        logger.warning("[qfq_schema_status] introspection 探测异常 → UNKNOWN: %s", e)
        return SchemaStatus.UNKNOWN


def assert_init_allowed(conn) -> SchemaStatus:
    """普通 ``init_duckdb_schema`` 写前安全闸。

    仅 ``EMPTY_OR_NEW`` 与 ``COMPLETE_2_1`` 放行；``COMPLETE_2_0`` / ``PARTIAL_OR_MIXED``
    / ``UNKNOWN`` 一律抛 ``QfqSchemaMigrationRequired``，**写前 fail-fast**。

    返回探测到的 SchemaStatus（COMPLETE_2_1 路径由调用方据此做只读 no-op）。
    """
    status = detect_schema_status(conn)
    if status in (SchemaStatus.EMPTY_OR_NEW, SchemaStatus.COMPLETE_2_1):
        return status
    raise QfqSchemaMigrationRequired(
        f"普通 init 拒绝在 {status.value} 状态的数据库上执行（代码 SCHEMA_VERSION="
        f"{SCHEMA_VERSION}）。该库需显式 migration runner 升级（B-3b 范围）。"
        f"完整 2.0 / 部分迁移 / 结构不一致 / 探测异常 的库，禁止普通 init 自动补列。")


def assert_code_ddl_matches_target_2_1() -> None:
    """代码侧预检门禁：DDL_DUCKDB + SOURCE_WATERMARK_2_1_DDL 必须与 TARGET 指纹逐字一致。

    在 ``init_duckdb_schema`` 执行第一条 DDL **之前**触发。若代码 DDL 与冻结的
    ``TARGET_MAIN_DB_2_1_FINGERPRINT`` 不一致（开发中间态 / DDL 漂移），即便空库也
    写前 fail-fast——禁止用旧 DDL 把空库建成错误结构后宣称目标是 2.1。

    B-3a 完成态：DDL 已升级到 target，本检查通过。B-3a 开发中间态：DDL 还是 2.0，
    本检查 fail（用于证明门禁存在；测试通过临时构造 DDL≠target 验证）。

    不连任何数据库，纯代码侧断言（parse(DDL) == TARGET）。
    """
    from quantstudio.pipeline.qfq_reanchor_schema import DDL_DUCKDB
    from quantstudio.pipeline.qfq_schema_contracts import (
        TARGET_QFQ_2_1_FINGERPRINT, SOURCE_WATERMARK_2_1_DDL,
        TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT, parse_physical_contract)

    # QFQ 15 表逐字一致
    if set(DDL_DUCKDB) != set(TARGET_QFQ_2_1_FINGERPRINT):
        raise QfqSchemaMigrationRequired(
            f"[qfq_schema] 代码 DDL_DUCKDB 表集合与 TARGET_QFQ_2_1_FINGERPRINT 不一致："
            f"DDL={sorted(DDL_DUCKDB)} vs TARGET={sorted(TARGET_QFQ_2_1_FINGERPRINT)}")
    for table, ddl in DDL_DUCKDB.items():
        parsed = parse_physical_contract(ddl)
        target = TARGET_QFQ_2_1_FINGERPRINT[table]
        parsed_cols = [(c[0].lower(), c[1], c[2], c[3]) for c in parsed["columns"]]
        target_cols = [(c[0].lower(), c[1], c[2], c[3]) for c in target["columns"]]
        if (parsed_cols != target_cols
                or parsed["primary_key"] != target["primary_key"]
                or parsed["foreign_keys"] != target["foreign_keys"]
                or sorted(sorted(u) for u in parsed.get("unique", []))
                    != sorted(sorted(u) for u in target.get("unique", []))
                or sorted(parsed.get("check", []))
                    != sorted(target.get("check", []))):
            raise QfqSchemaMigrationRequired(
                f"[qfq_schema] 代码 DDL_DUCKDB[{table}] 与 TARGET 指纹不一致"
                f"（columns/PK/FK/UNIQUE/CHECK 任一不符）。"
                f"DDL 必须与 qfq_schema_contracts.TARGET_QFQ_2_1_FINGERPRINT 逐字一致。")

    # source_watermark 共享 DDL 与 target 指纹一致
    sw_parsed = parse_physical_contract(SOURCE_WATERMARK_2_1_DDL)
    sw_target = TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT
    sw_parsed_cols = [(c[0].lower(), c[1], c[2], c[3]) for c in sw_parsed["columns"]]
    sw_target_cols = [(c[0].lower(), c[1], c[2], c[3]) for c in sw_target["columns"]]
    if (sw_parsed_cols != sw_target_cols
            or sw_parsed["primary_key"] != sw_target["primary_key"]
            or sorted(sorted(u) for u in sw_parsed.get("unique", []))
                != sorted(sorted(u) for u in sw_target.get("unique", []))
            or sorted(sw_parsed.get("check", []))
                != sorted(sw_target.get("check", []))):
        raise QfqSchemaMigrationRequired(
            "[qfq_schema] SOURCE_WATERMARK_2_1_DDL 与 TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT "
            "不一致（columns/PK/UNIQUE/CHECK 任一不符）。source_watermark DDL 必须与 target 指纹逐字一致。")
