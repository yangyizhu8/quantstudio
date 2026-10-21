"""FP-5（firefly 水位线案，2026-10-20）：source_watermark 增列 last_run_id。

用途：``last_batch_id`` 既被当作批次号、又被 GUI 单任务路径写入 run_id
（``daemon.py:699`` → 提交 → ``_advance_watermark(batch_id=run_id)``），
导致"人工 pin"误读与追溯断链。本迁移**只增列**，不改既有列语义、不加约束。

幂等：列已存在 → 直接返回；``--dry-run`` 只探测不写。
回退：列留空即可（读取方均按"列不存在/值为 NULL"双兼容），无需 DROP。

用法：
    python scripts/migrate_source_watermark_last_run_id.py --db data/quantstudio.db [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb


def has_column(conn, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM duckdb_columns() WHERE table_name=? AND column_name=?",
        [table, column]).fetchone()
    return bool(row and int(row[0]) > 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-schema-contract-change", action="store_true",
                    help="必须显式确认：本迁移会改变 source_watermark 契约列集，"
                         "未同步更新 qfq_schema_contracts 时会把主库判为 partial_or_mixed，"
                         "导致 daemon 无法启动（2026-10-20 实弹）。")
    args = ap.parse_args(argv)

    if not args.dry_run and not args.allow_schema_contract_change:
        print("[FP-5] ✗ 拒执行：本迁移属**schema 契约变更**，不是普通加列。\n"
              "      2026-10-20 实证：单独 ALTER 后 detect_schema_status() 由\n"
              "      complete_2_1 → partial_or_mixed（契约按列集严格比对），\n"
              "      DuckDBWriter._init_tables 安全闸随即拒绝启动，采集停摆。\n"
              "      正确做法：契约表（qfq_schema_contracts）与 2.1→2.2 版本、迁移 runner、\n"
              "      相关测试同一 commit 落地，经 ②审计后再执行。\n"
              "      确需强制执行请加 --allow-schema-contract-change（不建议）。",
              file=sys.stderr)
        return 5

    db = Path(args.db)
    if not db.exists():
        print(f"[FP-5] 库不存在: {db}", file=sys.stderr)
        return 2

    conn = duckdb.connect(str(db))
    try:
        before = [r[0] for r in conn.execute(
            "select column_name from duckdb_columns() where table_name='source_watermark'").fetchall()]
        rows_before = conn.execute("select count(*) from source_watermark").fetchone()[0]
        print(f"[FP-5] 迁移前列: {before}")
        print(f"[FP-5] 迁移前行数: {rows_before}")

        if has_column(conn, "source_watermark", "last_run_id"):
            print("[FP-5] 已存在 last_run_id → 幂等跳过（无变更）")
            return 0
        if args.dry_run:
            print("[FP-5] DRY-RUN：将执行 ALTER TABLE source_watermark "
                  "ADD COLUMN last_run_id VARCHAR")
            return 0

        conn.execute("ALTER TABLE source_watermark ADD COLUMN last_run_id VARCHAR")
        after = [r[0] for r in conn.execute(
            "select column_name from duckdb_columns() where table_name='source_watermark'").fetchall()]
        rows_after = conn.execute("select count(*) from source_watermark").fetchone()[0]
        print(f"[FP-5] 迁移后列: {after}")
        print(f"[FP-5] 迁移后行数: {rows_after}")
        if rows_after != rows_before:
            print("[FP-5] ✗ 行数变化，迁移异常", file=sys.stderr)
            return 3
        if "last_run_id" not in after:
            print("[FP-5] ✗ 列未生成", file=sys.stderr)
            return 4
        # 幂等复跑验证
        assert has_column(conn, "source_watermark", "last_run_id")
        print("[FP-5] ✅ 完成（行数不变；列已生成；重复执行将幂等跳过）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
