"""水位溯源覆盖审计 + 缺失补建（v3.1 停止语义 V6 缺陷 ④ 根因工具）。

背景（2026-09-12）：etf_minutes 出现「有数据、无水印行」——增量拉取因取不到水位
而走全窗/异常路径。本工具对 collector_tasks.json 的全部启用任务表做**溯源覆盖审计**：
数据存在但 source_watermark 无对应行 = 同类陷阱，逐一列出。

补建口径与框架一致：初始水位 = 表内 MAX(time_key)（与 ResidentCollector._max_date
同一归一化：str(int(max_val))），经 writer.advance_watermark 走**正式水位写入路径**
（8 列 DDL + 审计列哨兵 + 写锁），不裸写 SQL。

用法：
    python scripts/audit_watermark_coverage.py            # 默认 dry-run（只读，零副作用）
    python scripts/audit_watermark_coverage.py --apply    # 实际补建缺失水位行

前置条件（重要）：主库为单写者文件锁——**必须关闭 GUI 与回填进程**后运行，
否则 DuckDB 直接拒绝打开（IOException: File is already open in ...）。
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONFIG_DIR = ROOT / "config" / "profiles" / "mcp_only"
SOURCE = "mcp"


def load_enabled_tasks():
    cfg = json.loads((CONFIG_DIR / "collector_tasks.json").read_text(encoding="utf-8"))
    return [t for t in cfg.get("tasks", []) if t.get("enabled", True)]


def main() -> int:
    ap = argparse.ArgumentParser(description="水位溯源覆盖审计（默认 dry-run）")
    ap.add_argument("--apply", action="store_true",
                    help="实际补建缺失水位行（缺省只审计不写盘）")
    ap.add_argument("--source", default=SOURCE)
    args = ap.parse_args()

    from quantstudio.pipeline.writers import create_writer
    from quantstudio.pipeline.aligner import FieldAligner

    data_cfg = json.loads((CONFIG_DIR / "data_config.json").read_text(encoding="utf-8"))
    aligner = FieldAligner.from_config(CONFIG_DIR / "alignment_rules.json")

    try:
        writer = create_writer(data_cfg)
    except Exception as e:
        print("[BLOCKED] 无法打开主库（单写者锁）：", type(e).__name__)
        print("          ", str(e)[:300].replace("\n", " "))
        print("[ACTION] 请先关闭 GUI（main_gui.py）与回填进程，再重跑本工具。")
        return 2

    tasks = load_enabled_tasks()
    print("ENABLED_TASKS =", len(tasks))
    print("MODE =", "APPLY" if args.apply else "DRY-RUN (read-only)")
    print()

    rows = []
    for t in tasks:
        table = t.get("table")
        if not table:
            continue
        freq = t.get("freq", "daily")
        time_col = (aligner.schemas.get(table) or {}).get("time_key") or "time"
        try:
            desc = writer.execute_read(f'DESCRIBE "{table}"')
        except Exception:
            rows.append((table, freq, "NO-TABLE", None, None, None))
            continue
        try:
            cnt = writer.execute_read(f'SELECT COUNT(*) FROM "{table}"')[0][0]
            maxv = writer.execute_read(f'SELECT MAX("{time_col}") FROM "{table}"')[0][0]
        except Exception as e:
            rows.append((table, freq, "QUERY-FAIL:" + type(e).__name__, None, None, None))
            continue
        wm = writer.get_last_date(args.source, table, freq)
        if cnt == 0:
            status = "EMPTY(无数据,跳过)"
        elif wm:
            status = "OK"
        else:
            status = "MISSING(有数据无水印)"
        rows.append((table, freq, status, cnt, maxv, wm))

    print("%-28s %-8s %-22s %-10s %s" % ("TABLE", "FREQ", "STATUS", "ROWS", "WATERMARK"))
    print("-" * 96)
    missing = []
    for table, freq, status, cnt, maxv, wm in rows:
        shown = "-" if wm is None else str(wm)
        print("%-28s %-8s %-22s %-10s %s" % (table, freq, status, cnt, shown))
        if status.startswith("MISSING"):
            missing.append((table, freq, maxv))

    print()
    print("MISSING_COUNT =", len(missing))
    for table, freq, maxv in missing:
        try:
            candidate = str(int(maxv))
        except (TypeError, ValueError):
            print("  [SKIP] %s/%s MAX(%s)=%r 无法归一化为水位" % (table, freq, time_col, maxv))
            continue
        print("  [TO-BUILD] %s/%s -> watermark=%s" % (table, freq, candidate))
        if args.apply:
            batch_id = "watermark_backfill_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            writer.advance_watermark(args.source, table, freq, candidate, batch_id)
            print("             [DONE] 已补建（batch_id=%s）" % batch_id)

    if missing and not args.apply:
        print()
        print("[NOTE] 以上为 dry-run。确认无误后加 --apply 实际补建。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
