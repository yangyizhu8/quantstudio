#!/usr/bin/env python
"""清空 etf_daily 表 + watermark，为 xtquant 全量重拉做准备。

执行时机：stock_daily 重拉验证通过后，关闭 PyQt，运行此脚本，再重启 PyQt 拉 etf_daily。

前置条件：
  1. PyQt 已关闭（db 无写锁）
  2. 已备份 db（quantstudio.db.pre-xtquant-daily-20260722 已含 etf_daily 老数据）
  3. stock_daily 重拉已完成（本脚本不动 stock_daily）

回滚：若 etf_daily 重拉出问题，从备份恢复
  cp quantstudio.db.pre-xtquant-daily-20260722 quantstudio.db

用法：
  python scripts/clear_etf_daily_for_xtquant.py
  python scripts/clear_etf_daily_for_xtquant.py --dry-run   # 只看不删
"""
import argparse
import duckdb
from datetime import datetime
from pathlib import Path

# 走统一的 db_path() 解析（不再硬编码绝对路径，移交可移植）。
from quantstudio._paths import db_path
DB = db_path()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只显示状态，不执行删除")
    args = ap.parse_args()

    if not DB.exists():
        print(f"ERROR: db 不存在: {DB}")
        return 1

    # 检查备份是否存在（安全门）
    bak = DB.parent / "quantstudio.db.pre-xtquant-daily-20260722"
    if not bak.exists():
        print(f"WARNING: 备份不存在 {bak}，建议先备份再清空。")
        if not args.dry_run:
            ans = input("无备份，仍要继续清空？(yes/no): ").strip().lower()
            if ans != "yes":
                print("已取消。请先备份。")
                return 1

    conn = duckdb.connect(str(DB))
    try:
        # 清空前快照
        total = conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0]
        by_src = conn.execute(
            "SELECT data_source, COUNT(*) FROM etf_daily GROUP BY data_source").fetchall()
        wm = conn.execute(
            "SELECT source, table_name, freq, last_date FROM source_watermark "
            "WHERE table_name='etf_daily'").fetchall()
        mm = conn.execute("SELECT MIN(time), MAX(time) FROM etf_daily").fetchone()

        print("=== etf_daily 清空前状态 ===")
        print(f"  总行数: {total}")
        print(f"  按源: {by_src}")
        if mm[0]:
            print(f"  时间范围: {datetime.fromtimestamp(mm[0]/1000).strftime('%Y-%m-%d')}"
                  f" ~ {datetime.fromtimestamp(mm[1]/1000).strftime('%Y-%m-%d')}")
        print(f"  watermark: {wm}")
        print()

        if args.dry_run:
            print("--dry-run 模式，不执行删除。去掉 --dry-run 实际清空。")
            return 0

        # 确认
        ans = input(f"将删除 etf_daily 全部 {total} 行 + watermark，确认？(yes/no): ").strip().lower()
        if ans != "yes":
            print("已取消。")
            return 1

        # 1. 清空 etf_daily
        conn.execute("DELETE FROM etf_daily")
        after = conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0]
        print(f"✅ etf_daily 清空: {total} → {after} 行")

        # 2. 清空 etf_daily watermark（让 xtquant 从 2018-01-01 全量起步）
        wm_before = conn.execute(
            "SELECT COUNT(*) FROM source_watermark WHERE table_name='etf_daily'").fetchone()[0]
        conn.execute("DELETE FROM source_watermark WHERE table_name='etf_daily'")
        wm_after = conn.execute(
            "SELECT COUNT(*) FROM source_watermark WHERE table_name='etf_daily'").fetchone()[0]
        print(f"✅ etf_daily watermark 清空: {wm_before} → {wm_after} 条")

        # 确认其他表不受影响
        print()
        print("=== 剩余 watermark（其他表保持不动）===")
        rows = conn.execute(
            "SELECT table_name, COUNT(*) FROM source_watermark "
            "GROUP BY table_name ORDER BY table_name").fetchall()
        for r in rows:
            print(f"  {r[0]}: {r[1]} 条")

        # 确认 stock_daily 不受影响（如果它正在重拉，应该有 xtquant 数据）
        sd = conn.execute(
            "SELECT COUNT(*) FROM stock_daily").fetchone()[0]
        print()
        print(f"stock_daily（本次不动）: {sd} 行")

        print()
        print("✅ etf_daily 清空完成。重启 PyQt → 全量拉取 etf_daily（xtquant 从 2018-01-01 起）。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
