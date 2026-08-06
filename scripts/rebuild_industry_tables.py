#!/usr/bin/env python
"""行业正式表安全重建工具（F4/F4b，2026-07-27 审核返工）。

与 output/ 下的一次性脚本不同，本工具是**可同步**的正式迁移工具：

安全契约（staging-first + atomic swap）：
1. 先把源端数据拉取 → transform（raw 保留，见
   quantstudio/pipeline/industry_membership_standardizer.py）→ align →
   validate → 写入 **staging 表**（industry_classification_staging /
   industry_membership_staging）；
2. 对 staging 执行质量门控（分类 31 行 SW2021 L1；成员表
   orphan=0、bad_ranges=0；重叠区间属原始事实，仅记录不阻断）；
3. 仅在全部通过后，用**单个短事务**原子交换正式表
   （DROP official + RENAME staging → official，同事务推进水位）；
4. 任何一步失败：staging 清理/保留供排查，**正式表完全不变**。

用法：
    python scripts/rebuild_industry_tables.py --db data/quantstudio.db \
        --start 2018-01-01 --end 2026-07-24
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger("rebuild_industry_tables")

STAGING = {"industry_classification": "industry_classification_staging",
           "industry_membership": "industry_membership_staging"}

CLASSIFICATION_GATE = {"system": "SW", "version": "SW2021", "level": "L1",
                       "expected_l1_count": 31}


class RebuildError(RuntimeError):
    """重建任一阶段失败（正式表保证不变）。"""


def _ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


def _create_staging(conn, official: str, staging: str) -> None:
    """按 DDL_DUCKDB 权威 schema 显式创建 staging 表（含完整 PRIMARY KEY）。

    P0（2026-07-27 审核）：禁止 CREATE TABLE ... AS SELECT（CTAS）——CTAS
    不保留主键约束，rename 后正式表将丢失 PK。
    """
    from quantstudio.pipeline.writers import DDL_DUCKDB
    ddl = DDL_DUCKDB[official]
    marker = f"CREATE TABLE IF NOT EXISTS {official}"
    if marker not in ddl:
        raise RebuildError(f"DDL_DUCKDB[{official}] 缺少预期 CREATE 语句")
    ddl = ddl.replace(marker, f"CREATE TABLE IF NOT EXISTS {staging}", 1)
    conn.execute(f"DROP TABLE IF EXISTS {staging}")
    conn.execute(ddl)


def _gate_classification(conn, staging: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT industry_code) FROM "
        f"{staging} WHERE classification_system=? AND classification_version=? "
        "AND industry_level=?",
        [CLASSIFICATION_GATE["system"], CLASSIFICATION_GATE["version"],
         CLASSIFICATION_GATE["level"]]).fetchone()
    ok = row[0] == CLASSIFICATION_GATE["expected_l1_count"] and \
        row[1] == CLASSIFICATION_GATE["expected_l1_count"]
    return {"rows": row[0], "distinct_codes": row[1], "ok": ok}


def _gate_membership(conn, staging: str, cls_staging: str) -> dict:
    bad = conn.execute(
        f"SELECT COUNT(*) FROM {staging} WHERE effective_to IS NOT NULL "
        "AND effective_from > effective_to").fetchone()[0]
    orphan = conn.execute(f"""
        SELECT COUNT(*) FROM {staging} m
        WHERE NOT EXISTS (
            SELECT 1 FROM {cls_staging} c
            WHERE c.classification_system = m.classification_system
              AND c.classification_version = m.classification_version
              AND c.industry_level = m.industry_level
              AND c.industry_code = m.industry_code)
    """).fetchone()[0]
    rows = conn.execute(f"SELECT COUNT(*) FROM {staging}").fetchone()[0]
    # 重叠属原始事实：记录，不阻断
    overlaps = conn.execute(f"""
        WITH m AS (
          SELECT rowid, code, industry_code, effective_from f,
                 COALESCE(effective_to, 9223372036854775807) t
          FROM {staging}
          WHERE classification_system='SW' AND industry_level='L1')
        SELECT COUNT(*) FROM m a JOIN m b ON a.code=b.code AND a.rowid<b.rowid
         AND a.industry_code <> b.industry_code
         AND LEAST(a.t,b.t) - GREATEST(a.f,b.f) > 0
         AND GREATEST(a.f,b.f) <= LEAST(a.t,b.t)
    """).fetchone()[0]
    # P0-B 硬门禁：同一 (system, version, level, code) 在 effective_to IS NULL
    # 下存在多条 -> multi_current（与 orphan 正交，必须阻断）。
    multi_current = conn.execute(f"""
        SELECT COUNT(*) FROM (
          SELECT code FROM {staging}
          WHERE effective_to IS NULL
          GROUP BY classification_system, classification_version,
                   industry_level, code
          HAVING COUNT(*) > 1)
    """).fetchone()[0]
    return {"rows": rows, "bad_ranges": bad, "orphan_rows": orphan,
            "multi_current_codes": int(multi_current),
            "raw_overlap_pairs": overlaps,
            "ok": rows > 0 and int(multi_current) == 0
                  and int(orphan) == 0 and int(bad) == 0}


class InjectedSwapFailure(RuntimeError):
    """交换事务中途失败注入（仅测试使用）。"""


def _atomic_swap(conn, watermark_ms: int, batch_id: str,
                 fail_inject: str = None) -> None:
    """单个短事务原子交换正式表并推进水位。

    fail_inject（测试注入点）：
    - "after_first_rename"：第一个表 rename 完成后立即失败；
    - "before_second_rename"：第二个表 DROP/RENAME 之前失败；
    - "watermark_mid"：第一条 watermark 写入后、第二条之前失败。
    任一失败 → ROLLBACK：正式表数据、PK 约束、水位全部不变。
    """
    import datetime
    now = datetime.datetime.now().isoformat()
    conn.execute("BEGIN TRANSACTION")
    try:
        for i, (official, staging) in enumerate(STAGING.items()):
            if fail_inject == "before_second_rename" and i == 1:
                raise InjectedSwapFailure("injected before second rename")
            conn.execute(f"DROP TABLE IF EXISTS {official}")
            conn.execute(f"ALTER TABLE {staging} RENAME TO {official}")
            if fail_inject == "after_first_rename" and i == 0:
                raise InjectedSwapFailure("injected after first rename")
        # 交换后 schema 断言：rename 后正式表即原 staging，再次按官方 DDL
        # 精确比对列名/类型/NOT NULL/PK，确认交换事务内 schema 一致；
        # 任一表 schema 不符即抛 RebuildError，整个事务 ROLLBACK。
        for official in STAGING:
            _assert_constraints(official, official, conn)
        for j, official in enumerate(STAGING):
            if fail_inject == "watermark_mid" and j == 1:
                raise InjectedSwapFailure("injected watermark failure")
            # v2.4 B-3a：source_watermark 8 列显式 INSERT。行业表为非 QFQ 数据集 →
            # 用哨兵 not-qfq-managed / not-applicable（不污染 QFQ 价格源审计）。
            conn.execute(
                "INSERT INTO source_watermark "
                "(source, table_name, freq, last_date, last_batch_id, updated_at, "
                " source_generation, cutover_id) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT (source, table_name, freq) DO UPDATE SET "
                "last_date=EXCLUDED.last_date, last_batch_id=EXCLUDED.last_batch_id, "
                "updated_at=EXCLUDED.updated_at, "
                "source_generation=EXCLUDED.source_generation, cutover_id=EXCLUDED.cutover_id",
                ["tushare", official, "daily", watermark_ms, batch_id, now,
                 "not-qfq-managed", "not-applicable"])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _parse_ddl(official_ddl: str):
    """从 CREATE TABLE DDL 解析 (列定义有序列表, 主键列列表)。

    仅支持本项目使用的受限 DDL 子集：列名 + 类型 + 可选 NOT NULL，
    以及 PRIMARY KEY (...)。解析失败抛 RebuildError。
    """
    import re as _re
    cols = []
    pk = []
    # 先以正则提取 PRIMARY KEY (...) 子句（其内含逗号，不能先按逗号切分）。
    m = _re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", official_ddl, _re.IGNORECASE)
    if m:
        for p in m.group(1).split(","):
            pk.append(p.strip().split()[0].strip('"`'))
    # 去掉 PRIMARY KEY 子句后再按逗号切分列定义，避免 PK 逗号干扰。
    body = official_ddl.split("(", 1)[-1].rsplit(")", 1)[0]
    body_no_pk = _re.sub(r"PRIMARY\s+KEY\s*\([^)]*\)", "", body,
                          flags=_re.IGNORECASE)
    for raw in body_no_pk.split(","):
        seg = raw.strip()
        if not seg:
            continue
        up = seg.upper()
        if up.startswith("UNIQUE") or up.startswith("FOREIGN") or up.startswith("CHECK"):
            continue
        parts = seg.split()
        if len(parts) < 2:
            continue
        name = parts[0].strip('"`')
        typ = parts[1]
        not_null = "NOT NULL" in up
        cols.append((name, typ, not_null))
    # DuckDB 对 PRIMARY KEY 列隐式强制 NOT NULL；DDL 文本常省略显式 NOT NULL，
    # 故将 PK 列视为 NOT NULL 以匹配 DuckDB 实际 schema。
    # DuckDB 实际 PRIMARY KEY 顺序遵循"列定义顺序"，而非 PRIMARY KEY 子句书写
    # 顺序，故以列定义顺序返回 PK 列，才能与 PRAGMA table_info 的 pk 顺序一致。
    pk_set = set(pk)
    cols = [(n, t, (nn or n in pk_set)) for (n, t, nn) in cols]
    pk_in_order = [n for (n, t, nn) in cols if n in pk_set]
    return cols, pk_in_order


def _assert_constraints(official, staging, conn):
    """精确比对 staging 实际 schema 与官方 DDL：列名/顺序/类型/NOT NULL/主键。

    任一不符即 RebuildError（绝不静默放过，确保 raw 1:1 保留）。
    """
    from quantstudio.pipeline.writers import DDL_DUCKDB
    cols, pk_cols = _parse_ddl(DDL_DUCKDB[official])
    actual = conn.execute(f"PRAGMA table_info({staging})").fetchall()
    actual_cols = [(r[1], r[2].upper(), bool(r[3])) for r in actual]
    expected_cols = [(c[0], c[1].upper(), c[2]) for c in cols]
    if actual_cols != expected_cols:
        raise RebuildError(
            f"[{official}] staging schema mismatch: "
            f"got={actual_cols} want={expected_cols}")
    actual_pk = [r[1] for r in actual if r[5]]
    if set(actual_pk) != set(pk_cols) or actual_pk != pk_cols:
        raise RebuildError(
            f"[{official}] PRIMARY KEY mismatch: "
            f"got={actual_pk} want={pk_cols}")


def rebuild_industry_tables(adapter, aligner, validator, writer,
                            start: str, end: str,
                            batch_id: str = "rebuild-industry",
                            fail_inject: str = None) -> dict:
    """执行安全重建。失败抛 RebuildError，正式表保证不变。返回审计 dict。"""
    audit: dict = {"stages": {}, "swapped": False}
    conn = writer._conn()
    try:
        # 0. staging 准备
        for official, staging in STAGING.items():
            _create_staging(conn, official, staging)
            _assert_constraints(official, staging, conn)
        # 1. 拉取（失败 → RebuildError，正式表不变）
        for table, staging in STAGING.items():
            try:
                raw, meta = adapter.fetch_table(table, start, end, codes=None)
            except Exception as e:
                raise RebuildError(f"fetch {table} failed: {e}") from e
            if raw is None or len(raw) == 0:
                raise RebuildError(f"fetch {table} returned empty (fail-closed)")
            audit["stages"].setdefault(table, {})["fetched"] = len(raw)
            audit["stages"][table]["interval_repair"] = meta.get("interval_repair")
            # 2. align + validate → staging
            std, _ = aligner.align(raw, table, "tushare")
            if "data_source" in writer._table_columns(table):
                std["data_source"] = "tushare"
            res = validator.validate(std, table, f"{batch_id}-{table}", "tushare")
            if len(res.passed_df) == 0:
                raise RebuildError(
                    f"{table}: 0 rows passed validation "
                    f"(rejected={len(res.rejected_rows)}, fail-closed)")
            conn.register("_stg_write", res.passed_df)
            cols = ", ".join(res.passed_df.columns)
            conn.execute(f"INSERT INTO {staging} ({cols}) SELECT * FROM _stg_write")
            conn.unregister("_stg_write")
            audit["stages"][table]["staged"] = len(res.passed_df)
        # 3. staging 质量门控
        g_cls = _gate_classification(conn, STAGING["industry_classification"])
        g_mem = _gate_membership(conn, STAGING["industry_membership"],
                                 STAGING["industry_classification"])
        audit["gates"] = {"classification": g_cls, "membership": g_mem}
        if not g_cls["ok"]:
            raise RebuildError(f"classification gate failed: {g_cls}")
        if not g_mem["ok"]:
            raise RebuildError(f"membership gate failed: {g_mem}")
        # 4. 原子交换
        _atomic_swap(conn, _ms(end), batch_id, fail_inject=fail_inject)
        audit["swapped"] = True
        return audit
    finally:
        # staging 清理（交换成功后 staging 已被 RENAME 消耗；失败时清理残留）
        try:
            for staging in STAGING.values():
                conn.execute(f"DROP TABLE IF EXISTS {staging}")
        except Exception:
            pass
        conn.close()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    import os
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description="安全重建行业正式表（staging+原子交换）")
    parser.add_argument("--db", required=True)
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", required=True)
    args = parser.parse_args(argv)

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        print("ABORT: TUSHARE_TOKEN not set")
        return 2
    from quantstudio.pipeline.sources.tushare_adapter import TushareAdapter
    from quantstudio.pipeline.aligner import FieldAligner
    from quantstudio.pipeline.validator import PreIngestValidator
    from quantstudio.pipeline.writers import DuckDBWriter

    root = Path(__file__).resolve().parents[1]
    adapter = TushareAdapter({"name": "tushare", "token": token})
    aligner = FieldAligner.from_config(root / "config" / "alignment_rules.json")
    validator = PreIngestValidator.from_config(root / "config" / "alignment_rules.json")
    writer = DuckDBWriter({"type": "duckdb", "path": args.db})
    try:
        audit = rebuild_industry_tables(adapter, aligner, validator, writer,
                                        args.start, args.end)
    except RebuildError as e:
        print(f"REBUILD FAILED (official tables untouched): {e}")
        return 3
    finally:
        writer.close()
    print("REBUILD OK:", audit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
