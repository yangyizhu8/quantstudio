"""水位溯源覆盖审计 + 缺失补建（v3.1 停止语义 V6 缺陷 ④ 根因工具）。

背景（2026-09-12）：etf_minutes 出现「有数据、无水印行」——增量拉取因取不到水位
而走全窗/异常路径。本工具对 collector_tasks.json 的全部启用任务表做**溯源覆盖审计**。

三分类口径（2026-09-13 总调度裁定）：
  FILL    应补建：事件/时序增量语义表，无水位=每轮全量重拉（etf_minutes 陷阱复发型）
  EXEMPT  豁免：维表/快照/零时间列——全量语义本就是其正确态（无水位 → 增量回退全量）。
          **禁止**给维表补水位：其 update_time 是载入元数据不是业务日期，补建=语义错位，
          属「水位前移吞窗口」族事故。
  MANUAL  需人工裁定：既非时序可补、也不在豁免清单（如无时间列却疑似时序表）

补建口径与框架一致：初始水位 = 表内 MAX(时间列)，经 **aligner.to_ms_timestamp** 归一为
框架毫秒口径（该表实际串格式由 DESCRIBE 实测决定，不假设单一格式），再经
writer.advance_watermark 走**正式水位写入路径**（8 列 DDL + 审计列哨兵 + 写锁），不裸写 SQL。

用法：
    python scripts/audit_watermark_coverage.py            # 默认 dry-run（只读，零副作用）
    python scripts/audit_watermark_coverage.py --apply    # 仅对 FILL 类补建 + 抽样对账

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

# 豁免清单（总调度 2026-09-13 裁定）：维表/快照/零时间列 → 全量语义即正确态
EXEMPT_TABLES = {
    "stock_basic": "维表/快照：update_time=载入元数据（非业务日期），全量重刷语义正确",
    "industry_classification": "维表/快照：update_time=载入元数据，全量重刷语义正确",
    "broker_monthly": "零业务时间列：月度小表，全量语义正确",
}

# 时间列探测顺序（DESCRIBE 实测列名，避免 alignment_rules 与真实列不符）
_TIME_HINTS = ("trade_date", "scan_date", "timestamp", "date", "time")


def load_enabled_tasks():
    cfg = json.loads((CONFIG_DIR / "collector_tasks.json").read_text(encoding="utf-8"))
    return [t for t in cfg.get("tasks", []) if t.get("enabled", True)]


def normalize_watermark(max_value, to_ms_timestamp):
    """把表内 MAX(时间列) 归一为框架毫秒水位口径（str(int(ms))）。

    实测串格式决定转换路径：数值毫秒原样；datetime/日期串经 aligner.to_ms_timestamp
    （Asia/Shanghai）→ 毫秒。无法归一 → 返回 None（调用方按 MANUAL 处理，禁止瞎写）。
    """
    if max_value is None:
        return None
    try:
        if isinstance(max_value, (int, float)) or (
                isinstance(max_value, str) and max_value.strip().isdigit()):
            return str(int(max_value))
        return str(int(to_ms_timestamp(max_value)))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="水位溯源覆盖审计（默认 dry-run）")
    ap.add_argument("--apply", action="store_true",
                    help="对 FILL 类实际补建水位行（缺省只审计不写盘）")
    ap.add_argument("--source", default=SOURCE)
    args = ap.parse_args()

    from quantstudio.pipeline.writers import create_writer
    from quantstudio.pipeline.aligner import FieldAligner, to_ms_timestamp

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

    fill, exempt, manual, ok_rows = [], [], [], []
    seen = set()
    for t in tasks:
        table = t.get("table")
        if not table or table in seen:
            continue
        seen.add(table)
        freq = t.get("freq", "daily")

        try:
            cols = [r[0] for r in writer.execute_read(f'DESCRIBE "{table}"')]
        except Exception:
            ok_rows.append((table, freq, "NO-TABLE", None, None))
            continue

        wm = writer.get_last_date(args.source, table, freq)
        try:
            cnt = writer.execute_read(f'SELECT COUNT(*) FROM "{table}"')[0][0]
        except Exception as e:
            ok_rows.append((table, freq, "QUERY-FAIL:" + type(e).__name__, None, None))
            continue

        if cnt == 0:
            ok_rows.append((table, freq, "EMPTY(无数据)", cnt, wm))
            continue
        if wm:
            ok_rows.append((table, freq, "OK", cnt, wm))
            continue

        # 有数据、无水印 → 三分类
        if table in EXEMPT_TABLES:
            exempt.append((table, freq, EXEMPT_TABLES[table]))
            continue
        schema_key = (aligner.schemas.get(table) or {}).get("time_key")
        if schema_key in cols:
            time_col = schema_key
        else:
            time_col = next(
                (c for c in cols if any(k in c.lower() for k in _TIME_HINTS)), None)
        if time_col is None:
            manual.append((table, freq, "无时间列可依据，需人工裁定"))
            continue
        try:
            maxv = writer.execute_read(f'SELECT MAX("{time_col}") FROM "{table}"')[0][0]
        except Exception as e:
            manual.append((table, freq, "MAX 查询失败 " + type(e).__name__))
            continue
        candidate = normalize_watermark(maxv, to_ms_timestamp)
        if candidate is None:
            manual.append((table, freq, f"MAX({time_col})={maxv!r} 无法归一化为毫秒水位"))
            continue
        fill.append((table, freq, time_col, cnt, maxv, candidate))

    print("%-30s %-8s %-22s %-10s %s" % ("TABLE", "FREQ", "STATUS", "ROWS", "WATERMARK"))
    print("-" * 100)
    for table, freq, status, cnt, wm in ok_rows:
        print("%-30s %-8s %-22s %-10s %s" % (table, freq, status, cnt, "-" if wm is None else wm))

    print()
    print("=== FILL (应补建) = %d ===" % len(fill))
    for table, freq, time_col, cnt, maxv, candidate in fill:
        print("  %s/%s  time_col=%s rows=%s  MAX=%r -> watermark=%s"
              % (table, freq, time_col, cnt, maxv, candidate))
    print("=== EXEMPT (豁免·全量语义) = %d ===" % len(exempt))
    for table, freq, reason in exempt:
        print("  %s/%s  %s" % (table, freq, reason))
    print("=== MANUAL (需人工裁定) = %d ===" % len(manual))
    for table, freq, reason in manual:
        print("  %s/%s  %s" % (table, freq, reason))

    if args.apply:
        print()
        print("=== APPLY ===")
        batch_id = "watermark_backfill_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        for table, freq, time_col, cnt, maxv, candidate in fill:
            writer.advance_watermark(args.source, table, freq, candidate, batch_id)
            # 抽样对账：写回值 vs 期望值（同批立即复核，防写错表/写错口径）
            back = writer.get_last_date(args.source, table, freq)
            verdict = "MATCH" if str(back) == str(candidate) else "MISMATCH(%s)" % back
            print("  [DONE] %s/%s watermark=%s  reconcile=%s" % (table, freq, candidate, verdict))
        print("  batch_id =", batch_id)
    elif fill:
        print()
        print("[NOTE] 以上为 dry-run；确认无误后加 --apply 补建（仅 FILL 类）。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
