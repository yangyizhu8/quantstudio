# -*- coding: utf-8 -*-
"""阶段二：按探测结果（fillable）执行分钟回填。

与 backfill_qfq_gaps.py 的区别：不再用「日期边界」粗过滤，
而是读 qfq-gap-probe-20261110.json 的 fillable 列表（已与 QuestDB 实际数据求交）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from datetime import timedelta, timezone
from pathlib import Path

import duckdb

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
PROD = ROOT / "data" / "quantstudio.db"
PROBE = ROOT / "docs" / "evidence" / "qfq-gap-probe-20261110.json"
CST = timezone(timedelta(hours=8))


def main() -> int:
    ap = argparse.ArgumentParser(description="按探测结果执行分钟回填")
    ap.add_argument("--asset", required=True, choices=["STOCK", "ETF"])
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个日期")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--checkpoint-every", type=int, default=20)
    args = ap.parse_args()

    key = f"{args.asset}_minute"
    table = f"{args.asset.lower()}_minutes"
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    groups = probe[key]["fillable"]
    if args.limit:
        groups = groups[: args.limit]

    tot_pairs = sum(len(g["codes"]) for g in groups)
    print("=" * 74)
    print(f"[plan] {key} table={table} mode={'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"  待补: {len(groups):,} 个日期 / {tot_pairs:,} 个 (日期,code)")
    print("=" * 74)

    if not groups:
        print("  无可补日期")
        return 0

    sys.path.insert(0, str(ROOT / "scripts"))
    from backfill_minutes_from_questdb import backfill_day, build_stack  # noqa: E402

    print("[stack] 构造 ResidentCollector ...")
    col = build_stack()
    print("  ✅ 完成")

    batch_id = f"qfgap2_{key}_{dt.datetime.now():%Y%m%d_%H%M%S}"
    reports = []
    t_all = time.time()
    ok = skip = fail = 0
    rows_written = 0

    for i, g in enumerate(groups, 1):
        day, codes = g["day"], g["codes"]
        t0 = time.time()
        try:
            rep = backfill_day(col, table, day, "questdb", args.apply,
                               batch_id, 5, codes=codes)
        except Exception as e:  # noqa: BLE001
            rep = {"table": table, "day": day, "codes": len(codes),
                   "status": "fail_exception", "error": f"{type(e).__name__}: {e}"}
        rep["elapsed_sec"] = round(time.time() - t0, 1)
        reports.append(rep)
        st = str(rep.get("status", ""))
        if st in ("applied", "dry_run_ok"):
            ok += 1
            rows_written += int(rep.get("delta") or 0) if args.apply else int(rep.get("aligned_rows") or 0)
        elif st == "skip_source_empty":
            skip += 1
        else:
            fail += 1

        if i % 20 == 0 or i == len(groups):
            el = time.time() - t_all
            eta = el / i * (len(groups) - i)
            print(f"  [{i}/{len(groups)}] ok={ok} skip={skip} fail={fail} "
                  f"rows={rows_written:,} 已用 {el/60:.1f}min ETA {eta/60:.1f}min")

        if args.apply and i % args.checkpoint_every == 0:
            try:
                c = duckdb.connect(str(PROD))
                c.execute("CHECKPOINT")
                c.close()
            except Exception:
                pass

    out = ROOT / "docs" / "evidence" / f"qfq_gap_backfill2_{key}_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "asset": args.asset, "kind": "minute", "table": table,
        "mode": "APPLY" if args.apply else "DRY-RUN",
        "groups": len(groups), "expected_pairs": tot_pairs,
        "ok": ok, "skip_source_empty": skip, "fail": fail,
        "rows_written": rows_written,
        "elapsed_min": round((time.time() - t_all) / 60, 1),
        "reports": reports,
    }
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[evidence] {out.name}")
    print(f"[summary] ok={ok} skip={skip} fail={fail} rows={rows_written:,} "
          f"用时 {summary['elapsed_min']}min")
    return 2 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
