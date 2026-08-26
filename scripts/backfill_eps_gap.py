"""P-A3 CLI：fin_indicator.eps 跨表回补与门禁（scripts/backfill_eps_gap.py）。

用法：
  python scripts/backfill_eps_gap.py --check               # 门禁检查（gap 行数，只读）
  python scripts/backfill_eps_gap.py --backfill           # dry-run 预览（不落库）
  python scripts/backfill_eps_gap.py --backfill --apply   # 正式回补（落库，幂等）
  python scripts/backfill_eps_gap.py --db <path>          # 指定库（默认 data/quantstudio.db）
  python scripts/backfill_eps_gap.py --revert             # 回补可逆（还原打标行 eps 为 NULL）

硬约束（SNAP_003 期间禁跑 --apply）：当前库正被快照 create/verify/protect 持有读连接时，
任何写操作都会造成 DuckDB 写冲突与快照撕裂——执行前确认无并发写者。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _open_conn(db_path: str, read_only: bool = False):
    import duckdb
    return duckdb.connect(db_path, read_only=read_only)


def main() -> int:
    parser = argparse.ArgumentParser(description="P-A3 eps 跨表回补门禁/执行 CLI")
    parser.add_argument("--check", action="store_true", help="门禁检查：输出 gap 行数（只读）")
    parser.add_argument("--backfill", action="store_true", help="回补（默认 dry-run；--apply 落库）")
    parser.add_argument("--apply", action="store_true", help="与 --backfill 连用：正式落库")
    parser.add_argument("--revert", action="store_true", help="回补可逆：还原打标行 eps 为 NULL")
    parser.add_argument("--db", default=str(Path("data/quantstudio.db")),
                        help="DuckDB 路径（默认 data/quantstudio.db）")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[EpsBackfill] 数据库不存在: {db_path}", file=sys.stderr)
        return 2

    from quantstudio.pipeline.eps_backfill import (
        backfill_eps_gap,
        check_eps_backfill_gap,
        revert_backfill,
    )

    mode_count = sum(bool(f) for f in (args.check, args.backfill, args.revert))
    if mode_count != 1:
        print("[EpsBackfill] 必须且只能指定一个模式: --check | --backfill | --revert",
              file=sys.stderr)
        return 2

    if args.check:
        conn = _open_conn(db_path, read_only=True)
        try:
            gap = check_eps_backfill_gap(conn)
        finally:
            conn.close()
        print(f"[EpsBackfill] check: gap={gap} "
              f"({'OK（免疫闭环）' if gap == 0 else 'GAP（回补未生效/源端变化）'})")
        return 0 if gap == 0 else 1

    # --backfill / --revert 都是写路径：执行前确认无并发写（SNAP_003 纪律）
    if args.apply:
        # 保守二次确认：可改为环境变量注入避免交互阻塞（计划任务场景）
        print("[EpsBackfill] --apply 将修改数据库（幂等回补 或 还原）。"
              "确认当前无快照 create/verify/protect 并发写后继续。")
    conn = _open_conn(db_path, read_only=False)
    try:
        if args.revert:
            n = revert_backfill(conn)
            print(f"[EpsBackfill] revert: 还原 {n} 行（backfill_eps_source 打标行 eps→NULL）")
            return 0
        result = backfill_eps_gap(conn, dry_run=not args.apply)
        print(f"[EpsBackfill] {'apply' if args.apply else 'dry-run'}: {result.summary()}")
        if not args.apply and result.rows_updated > 0:
            print(f"[EpsBackfill] 预览确认无误后加 --apply 落库。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())