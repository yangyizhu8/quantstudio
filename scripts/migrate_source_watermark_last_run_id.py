"""FP-5（firefly 水位线案）：schema 2.1 → 2.2 迁移 —— `source_watermark.last_run_id`。

契约级迁移 runner（**不是普通加列**）。2026-10-20 实弹教训：`source_watermark` 结构由
`qfq_schema_contracts` 指纹**逐字比对**（`reject_extra=True`），单独 `ALTER` 会让
`detect_schema_status()` 由 `complete_2_1` → `partial_or_mixed`，`DuckDBWriter._init_tables`
安全闸随即拒绝启动 ⇒ 采集全线停摆。故必须：

    预检 COMPLETE_2_1 → ALTER 加列 → 版本行 2.1→2.2 → 复检 COMPLETE_2_2 → 幂等 → 失败回退复检

前置（缺一不得执行）：
  · `qfq_schema_contracts.SOURCE_WATERMARK_2_2_DDL` / `TARGET_*_2_2_FINGERPRINT` 已落地；
  · `qfq_reanchor_schema.SCHEMA_VERSION == "reanchor-2.2"`（本脚本拒绝在旧代码上执行，
    否则迁移后旧代码的 2.1 契约会把新库判 partial）；
  · `qfq_schema_status.SchemaStatus.COMPLETE_2_2` 与 `writers` 安全闸放行集已就绪。

窗口纪律（②审追加）：迁移期间 daemon 与**一切 ops 类 CLI**（`--mode once`、GUI 跑任务、
`qfq_orchestrator_cli` 等会 `create_writer`/`init_schema` 的入口）全部禁跑，否则中间态会触发
`_WriterSchemaMigrationRequired` 或写入半态数据。窗口内只允许：只读探测、本脚本、迁移后验证。

用法：
    python scripts/migrate_source_watermark_last_run_id.py --db data/quantstudio.db [--dry-run]
    # 真执行必须显式确认契约变更：
    python scripts/migrate_source_watermark_last_run_id.py --db data/quantstudio.db \
        --allow-schema-contract-change [--execute]

退出码：0 成功/幂等；2 库不存在；3 行数变化；4 列未生成；5 契约闸拒绝（未确认/代码版本不符）；
        6 预检状态不符（非 COMPLETE_2_1 / 非 COMPLETE_2_2）；7 复检失败（→ 已尝试回退）。
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

TARGET_VERSION = "reanchor-2.2"
SOURCE_VERSION = "reanchor-2.1"
COLUMN = "last_run_id"
TABLE = "source_watermark"


def _columns(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "select column_name from duckdb_columns() where table_name=?", [TABLE]).fetchall()]


def _status(conn):
    from quantstudio.pipeline.qfq_schema_status import detect_schema_status
    return detect_schema_status(conn)


def _bootstrap_versions(conn) -> list[str]:
    try:
        rows = conn.execute(
            "select distinct schema_version from qfq_bootstrap_run "
            "where schema_version is not null").fetchall()
        return sorted({str(r[0]) for r in rows})
    except Exception:
        return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true", help="只探测，不写")
    ap.add_argument("--execute", action="store_true", help="与 --allow-schema-contract-change 同用，显式表达真跑")
    ap.add_argument("--allow-schema-contract-change", action="store_true",
                    help="必须显式确认：本迁移改变 source_watermark 契约列集（2.1→2.2）。")
    args = ap.parse_args(argv)

    dry = bool(args.dry_run)
    if not dry and not args.allow_schema_contract_change:
        print("[FP-5] ✗ 拒执行：本迁移属 **schema 契约变更**（2.1→2.2），不是普通加列。\n"
              "      2026-10-20 实证：单独 ALTER 后 detect_schema_status() 由\n"
              "      complete_2_1 → partial_or_mixed，DuckDBWriter._init_tables 安全闸拒绝启动，\n"
              "      采集停摆（DROP 后才恢复）。\n"
              "      正确做法：契约 + 版本常量 + 状态判定 + 安全闸放行集 + 本 runner + 测试\n"
              "      同一 commit 落地、②审通过、停服窗内执行。\n"
              "      确需执行请加 --allow-schema-contract-change（窗口纪律见脚本头部）。",
              file=sys.stderr)
        return 5

    # 代码版本自检：旧代码（SCHEMA_VERSION 仍为 2.1）不得执行本迁移
    from quantstudio.pipeline.qfq_reanchor_schema import (
        SCHEMA_VERSION, SCHEMA_VERSION_COMPAT)
    if SCHEMA_VERSION != TARGET_VERSION or TARGET_VERSION not in SCHEMA_VERSION_COMPAT:
        print(f"[FP-5] ✗ 拒执行：当前代码 SCHEMA_VERSION={SCHEMA_VERSION} 与迁移目标 "
              f"{TARGET_VERSION} 不一致（迁移后旧契约会把新库判 partial）。", file=sys.stderr)
        return 5

    db = Path(args.db)
    if not db.exists():
        print(f"[FP-5] 库不存在: {db}", file=sys.stderr)
        return 2

    conn = duckdb.connect(str(db))
    try:
        cols_before = _columns(conn)
        rows_before = int(conn.execute(f"select count(*) from {TABLE}").fetchone()[0])
        status_before = _status(conn)
        vers_before = _bootstrap_versions(conn)
        print(f"[FP-5] 迁移前：status={status_before.value} 列数={len(cols_before)} 行数={rows_before}")
        print(f"[FP-5] 迁移前 bootstrap 版本行={vers_before}")

        from quantstudio.pipeline.qfq_schema_status import SchemaStatus

        # —— 幂等：已是 2.2 → 零变更 ——
        if status_before == SchemaStatus.COMPLETE_2_2:
            print("[FP-5] 已是 COMPLETE_2_2 → 幂等跳过（无变更）")
            return 0

        # —— 预检：必须 COMPLETE_2_1 ——
        if status_before != SchemaStatus.COMPLETE_2_1:
            print(f"[FP-5] ✗ 预检失败：要求 COMPLETE_2_1，实际 {status_before.value}"
                  f"（不得在非 2.1 库上执行本迁移）", file=sys.stderr)
            return 6

        if dry:
            print(f"[FP-5] DRY-RUN：将执行\n"
                  f"        ALTER TABLE {TABLE} ADD COLUMN {COLUMN} VARCHAR\n"
                  f"        UPDATE qfq_bootstrap_run SET schema_version='{TARGET_VERSION}' "
                  f"WHERE schema_version='{SOURCE_VERSION}'\n"
                  f"        复检要求 → COMPLETE_2_2；行数 {rows_before} 不变")
            return 0

        # —— 迁移（失败回退）——
        def rollback(reason: str) -> int:
            """回退并复检；返回非零（7=回退成功但本次迁移未生效，8=回退失败）。"""
            print(f"[FP-5] 回退中（原因：{reason}）…", file=sys.stderr)
            try:
                if COLUMN in _columns(conn):
                    conn.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
                conn.execute(
                    "UPDATE qfq_bootstrap_run SET schema_version=? WHERE schema_version=?",
                    [SOURCE_VERSION, TARGET_VERSION])
                st = _status(conn)
                ok = st == SchemaStatus.COMPLETE_2_1
                print(f"[FP-5] 回退后 status={st.value}"
                      f"（要求 complete_2_1）→ {'OK' if ok else 'FAIL'}", file=sys.stderr)
                return 7 if ok else 8
            except Exception:
                traceback.print_exc()
                return 8

        try:
            conn.execute("BEGIN TRANSACTION")
            conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} VARCHAR")
            # 版本行同步（让 bootstrap 版本校验直接命中 2.2，而非长期依赖双接受窗口）
            conn.execute(
                "UPDATE qfq_bootstrap_run SET schema_version=? WHERE schema_version=?",
                [TARGET_VERSION, SOURCE_VERSION])
            conn.execute("COMMIT")
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            print(f"[FP-5] ✗ 迁移异常：{type(e).__name__}: {e}", file=sys.stderr)
            return rollback(f"{type(e).__name__}")

        # —— 复检 ——
        cols_after = _columns(conn)
        rows_after = int(conn.execute(f"select count(*) from {TABLE}").fetchone()[0])
        status_after = _status(conn)
        print(f"[FP-5] 迁移后：status={status_after.value} 列={cols_after} 行数={rows_after}")
        if rows_after != rows_before:
            print(f"[FP-5] ✗ 行数变化 {rows_before}→{rows_after}", file=sys.stderr)
            return rollback("行数变化")
        if COLUMN not in cols_after:
            print("[FP-5] ✗ 列未生成", file=sys.stderr)
            return rollback("列未生成")
        if status_after != SchemaStatus.COMPLETE_2_2:
            print(f"[FP-5] ✗ 复检失败：要求 COMPLETE_2_2，实际 {status_after.value}", file=sys.stderr)
            return rollback("复检非 2.2")

        # —— 幂等复跑验证（只探测）——
        print("[FP-5] 幂等复跑检查：再次执行将命中 COMPLETE_2_2 分支零变更")
        print(f"[FP-5] ✅ 完成：{SOURCE_VERSION} → {TARGET_VERSION}；行数 {rows_before} 不变；"
              f"bootstrap 版本行 {vers_before} → {_bootstrap_versions(conn)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
