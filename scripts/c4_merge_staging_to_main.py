# -*- coding: utf-8 -*-
"""C-4 续跑合并脚本：staging 主库 → 正式主库（方案 A 收尾环节）。

用法:
  python scripts/c4_merge_staging_to_main.py --dry-run          # 只打印计划与行数核对
  python scripts/c4_merge_staging_to_main.py                    # 执行合并（自动备份 + 单事务）

合并范围（仅 run_id 相关的行，不动其他数据）:
  qfq_bootstrap_item   整 run 替换（DELETE + INSERT）
  qfq_bootstrap_run    整 run 替换
  qfq_anchor_state     按 run codes DELETE + INSERT（staging 行）
  qfq_fresh_capture    按 run codes DELETE + INSERT
  qfq_reanchor_event   按 run codes DELETE + INSERT
  qfq_trigger_queue    仅合并该 run 相关行（attempt_count 等）；无 run_id 关联则跳过并提示

安全:
  - 执行前自动备份主库（quantstudio.db -> quantstudio.db.bak_c4merge，已存在则跳过）
  - 单事务（任一失败整体回滚）
  - --dry-run 不写任何数据
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

import duckdb

MAIN_DB = Path("data/quantstudio.db")
STAGING_DB = Path("data/quantstudio_c4resume_staging.db")
RUN_ID = "bs_07b91d6bea"
BACKUP_SUFFIX = ".bak_c4merge"

# (表名, 主键/过滤列, 是否按 run_id 整替换)
TABLES_RUN_REPLACE = ["qfq_bootstrap_item", "qfq_bootstrap_run"]
TABLES_CODE_SCOPE = ["qfq_anchor_state", "qfq_fresh_capture", "qfq_reanchor_event"]
TABLES_WARN_IF_DIFF = ["qfq_trigger_queue", "qfq_pending_backfill", "qfq_cycle_run"]


def row_count(con, table, where=None):
    q = f"SELECT COUNT(*) FROM {table}"
    if where:
        q += f" WHERE {where}"
    return con.execute(q).fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写入")
    ap.add_argument("--main", default=str(MAIN_DB))
    ap.add_argument("--staging", default=str(STAGING_DB))
    ap.add_argument("--run-id", default=RUN_ID)
    args = ap.parse_args()

    main_p = Path(args.main)
    stag_p = Path(args.staging)
    if not main_p.exists() or not stag_p.exists():
        print(f"库文件缺失: main={main_p.exists()} staging={stag_p.exists()}")
        sys.exit(1)

    # 备份（仅正式执行时）
    if not args.dry_run:
        bak = main_p.with_name(main_p.name + BACKUP_SUFFIX)
        if not bak.exists():
            print(f"[备份] {main_p} -> {bak} ...")
            shutil.copy2(main_p, bak)
        else:
            print(f"[备份] 已存在，跳过: {bak}")

    con_main = duckdb.connect(str(main_p))
    con_main.execute(f"ATTACH '{stag_p}' AS staging_db (READ_ONLY)")

    con_stag = duckdb.connect(str(stag_p), read_only=True)
    rid = args.run_id

    # 1) run 内 codes
    codes = [r[0] for r in con_stag.execute(
        f"SELECT DISTINCT code FROM qfq_bootstrap_item WHERE bootstrap_run_id='{rid}'").fetchall()]
    print(f"[计划] run={rid} codes={len(codes)}")
    if not codes:
        print("run 无 item，退出")
        sys.exit(1)

    # 2) 每表行数核对（staging vs 主库现状）
    print("\n=== 行数核对（合并前）===")
    for t in TABLES_RUN_REPLACE + TABLES_CODE_SCOPE:
        m_rows = row_count(con_main, t, f"bootstrap_run_id='{rid}'") if t in TABLES_RUN_REPLACE else \
            row_count(con_main, t, f"code IN (SELECT code FROM qfq_bootstrap_item WHERE bootstrap_run_id='{rid}')")
        s_rows = row_count(con_stag, t, f"bootstrap_run_id='{rid}'") if t in TABLES_RUN_REPLACE else \
            row_count(con_stag, t, f"code IN (SELECT code FROM qfq_bootstrap_item WHERE bootstrap_run_id='{rid}')")
        print(f"  {t:24s} main={m_rows:6d}  staging={s_rows:6d}")

    for t in TABLES_WARN_IF_DIFF:
        m_rows = row_count(con_main, t)
        s_rows = row_count(con_stag, t)
        flag = "  <-- 注意: 两库差异" if m_rows != s_rows else ""
        print(f"  {t:24s} main={m_rows:6d}  staging={s_rows:6d}{flag}")

    # 3) 主库 run 状态确认（防止合并前主库 run 已变化）
    m_run = con_main.execute(
        f"SELECT status, completed_count, blocked_count, failed_count FROM qfq_bootstrap_run WHERE bootstrap_run_id='{rid}'").fetchone()
    s_run = con_stag.execute(
        f"SELECT status, completed_count, blocked_count, failed_count FROM qfq_bootstrap_run WHERE bootstrap_run_id='{rid}'").fetchone()
    print(f"\n[run 状态] main={m_run}")
    print(f"[run 状态] staging={s_run}")

    if args.dry_run:
        print("\n[dry-run] 计划：")
        for t in TABLES_RUN_REPLACE:
            print(f"  {t}: DELETE WHERE bootstrap_run_id='{rid}' + INSERT (staging)")
        for t in TABLES_CODE_SCOPE:
            print(f"  {t}: DELETE WHERE code IN (run codes) + INSERT (staging)")
        print("\n[dry-run] 未执行任何写入。")
        con_main.close(); con_stag.close()
        return

    # 4) 执行合并（单事务）
    print("\n=== 执行合并 ===")
    try:
        con_main.execute("BEGIN TRANSACTION")
        for t in TABLES_RUN_REPLACE:
            con_main.execute(f"DELETE FROM {t} WHERE bootstrap_run_id='{rid}'")
            con_main.execute(f"INSERT INTO {t} SELECT * FROM staging_db.{t} WHERE bootstrap_run_id='{rid}'")
            print(f"  {t}: 整 run 替换完成")
        code_list = ",".join(f"'{c}'" for c in codes)
        for t in TABLES_CODE_SCOPE:
            con_main.execute(f"DELETE FROM {t} WHERE code IN ({code_list})")
            con_main.execute(f"INSERT INTO {t} SELECT * FROM staging_db.{t} WHERE code IN ({code_list})")
            print(f"  {t}: code 范围替换完成")
        con_main.execute("COMMIT")
        print("  事务提交 OK")
    except Exception as e:
        con_main.execute("ROLLBACK")
        print(f"[失败] 已回滚: {e}")
        con_main.close(); con_stag.close()
        sys.exit(1)

    # 5) 合并后验证
    print("\n=== 合并后验证 ===")
    ok = True
    for t in TABLES_RUN_REPLACE:
        m = row_count(con_main, t, f"bootstrap_run_id='{rid}'")
        s = row_count(con_stag, t, f"bootstrap_run_id='{rid}'")
        same = m == s
        ok &= same
        print(f"  {t}: main={m} staging={s} {'OK' if same else 'MISMATCH!'}")
    for t in TABLES_CODE_SCOPE:
        m = row_count(con_main, t, f"code IN ({code_list})")
        s = row_count(con_stag, t, f"code IN ({code_list})")
        same = m == s
        ok &= same
        print(f"  {t}: main={m} staging={s} {'OK' if same else 'MISMATCH!'}")

    con_main.close(); con_stag.close()
    print(f"\n{'✅ 合并验证全部通过' if ok else '❌ 存在不一致，需人工核查'}")
    print(f"备份文件: {main_p}.bak_c4merge（确认无误后可删除）")


if __name__ == "__main__":
    main()
