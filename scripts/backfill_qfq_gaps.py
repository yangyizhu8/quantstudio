# -*- coding: utf-8 -*-
"""QFQ 缺口定向回填驱动（v2 方案 · 2026-11-09）。

设计（与方案件 docs/rebase-history-exempt-design-20261109.md 一致）
--------------------------------------------------------------------
1. **只补「源端有」的缺口**：按 QuestDB 实测覆盖边界过滤，边界外的死信标注
   `no_source`（永久无源），不动。
2. **按 (资产, 日期) 分组**：一天的多个死信 code 合成一次 QuestDB 查询，
   避免 14,125 次单点查询（3,608 天 × 3-5 秒 ≈ 3-5 小时）。
3. **复用已验证链路**：`backfill_minutes_from_questdb.backfill_day`（含时区修复、
   复权自举、writer 幂等、data_source 标注），不重写。
4. **不动判据**：fail-closed 全保留（裁②：factor_equivalent 不扩面）。
5. **DRY-RUN 默认**：`--apply` 才写库。

QuestDB 覆盖边界（实测 2026-11-09 23:50）
-----------------------------------------
  stock_minutes  min=2024-05-06
  etf_minutes    min=2025-01-02
  stock_daily    min=2000-01-21
  etf_daily      min=2010-01-04

用法
----
    # 干跑（默认）：列出本轮会补哪些日期、各多少 code
    python scripts/backfill_qfq_gaps.py --asset STOCK --kind minute --limit 10

    # 真写（须先停 daemon + 声明占用窗）
    python scripts/backfill_qfq_gaps.py --asset STOCK --kind minute --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from collections import defaultdict
from datetime import timezone, timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "data" / "quantstudio.db"
CST = timezone(timedelta(hours=8))

# QuestDB 覆盖边界（epoch ms）—— 实测值，变更须重测
BOUNDARY = {
    ("STOCK", "minute"): int(dt.datetime(2024, 5, 6, tzinfo=CST).timestamp() * 1000),
    ("ETF", "minute"): int(dt.datetime(2025, 1, 2, tzinfo=CST).timestamp() * 1000),
    ("STOCK", "daily"): int(dt.datetime(2000, 1, 21, tzinfo=CST).timestamp() * 1000),
    ("ETF", "daily"): int(dt.datetime(2010, 1, 4, tzinfo=CST).timestamp() * 1000),
}

KIND_PAT = {"minute": "%分钟%覆盖不全%", "daily": "%日线覆盖不全%"}


def load_groups(asset: str, kind: str) -> tuple[list[dict], int, int]:
    """读死信，按 (日期) 分组；返回 (可补组, 无源行数, 无源代码数)。"""
    bound = BOUNDARY[(asset, kind)]
    pat = KIND_PAT[kind]
    con = duckdb.connect(str(PROD), read_only=True)
    try:
        rows = con.execute("""
            SELECT CAST(to_timestamp(effective_date/1000) AS DATE) dd,
                   effective_date, code
            FROM qfq_trigger_queue
            WHERE status='dead_letter' AND asset_type=? AND last_error LIKE ?
              AND effective_date >= ?
            ORDER BY effective_date, code
        """, [asset, pat, bound]).fetchall()
        no_src = con.execute("""
            SELECT COUNT(*), COUNT(DISTINCT code) FROM qfq_trigger_queue
            WHERE status='dead_letter' AND asset_type=? AND last_error LIKE ?
              AND effective_date < ?
        """, [asset, pat, bound]).fetchone()
    finally:
        con.close()

    by_day: dict[str, list[str]] = defaultdict(list)
    for dd, _, code in rows:
        by_day[str(dd)].append(str(code))
    groups = [{"day": d, "codes": sorted(set(cs))} for d, cs in sorted(by_day.items())]
    return groups, int(no_src[0] or 0), int(no_src[1] or 0)


def main() -> int:
    ap = argparse.ArgumentParser(description="QFQ 缺口定向回填（v2）")
    ap.add_argument("--asset", required=True, choices=["STOCK", "ETF"])
    ap.add_argument("--kind", required=True, choices=["minute", "daily"])
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个日期（0=全部）")
    ap.add_argument("--apply", action="store_true", help="真写库（默认 dry-run）")
    ap.add_argument("--checkpoint-every", type=int, default=20,
                    help="每 N 个日期做一次 CHECKPOINT（防 ART 膨胀，铁律 23）")
    args = ap.parse_args()

    table = f"{args.asset.lower()}_{'minutes' if args.kind == 'minute' else 'daily'}"

    print("=" * 74)
    print(f"[plan] asset={args.asset} kind={args.kind} table={table} "
          f"mode={'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"[bound] 源端起点 = {dt.datetime.fromtimestamp(BOUNDARY[(args.asset, args.kind)]/1000, CST):%Y-%m-%d}")
    print("=" * 74)

    groups, no_src_rows, no_src_codes = load_groups(args.asset, args.kind)
    if args.limit:
        groups = groups[: args.limit]

    tot_codes = sum(len(g["codes"]) for g in groups)
    print(f"  可补: {len(groups):,} 个日期 / {tot_codes:,} 个 (日期,code)")
    print(f"  无源: {no_src_rows:,} 行 / {no_src_codes:,} code（不改动，待归档）")

    if args.kind == "daily":
        print("\n  ⚠ 日线回填需「stock_daily + stock_daily_basic」两表 JOIN + 复权计算，")
        print("    本脚本暂只支持 minute；daily 分支需先实现（见方案件 Q1/Q2）。")
        return 3

    if not groups:
        print("  无可补日期，退出。")
        return 0

    # 日线分支未实现，此处只处理 minute
    sys.path.insert(0, str(ROOT / "scripts"))
    from backfill_minutes_from_questdb import backfill_day, build_stack  # noqa: E402

    print("\n[stack] 构造 ResidentCollector（复用 daemon 链路）...")
    col = build_stack()
    print("  ✅ 构造完成")

    batch_id = f"qfgap_{args.asset}_{args.kind}_{dt.datetime.now():%Y%m%d_%H%M%S}"
    reports = []
    t_all = time.time()
    ok = fail = skip = 0

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
        if st == "applied" or st == "dry_run_ok":
            ok += 1
        elif st == "skip_source_empty":
            skip += 1
        else:
            fail += 1

        if i % 10 == 0 or i == len(groups):
            el = time.time() - t_all
            eta = el / i * (len(groups) - i)
            print(f"  [{i}/{len(groups)}] ok={ok} skip={skip} fail={fail} "
                  f"已用 {el/60:.1f}min  ETA {eta/60:.1f}min")

        # CHECKPOINT（防 ART 膨胀）
        if args.apply and i % args.checkpoint_every == 0:
            try:
                import duckdb as _d
                c = _d.connect(str(PROD))
                c.execute("CHECKPOINT")
                c.close()
            except Exception:
                pass

    out = ROOT / "docs" / "evidence" / f"qfq_gap_backfill_{args.asset}_{args.kind}_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "asset": args.asset, "kind": args.kind, "table": table,
        "mode": "APPLY" if args.apply else "DRY-RUN",
        "source_boundary": dt.datetime.fromtimestamp(
            BOUNDARY[(args.asset, args.kind)] / 1000, CST).strftime("%Y-%m-%d"),
        "groups_processed": len(groups),
        "ok": ok, "skip_source_empty": skip, "fail": fail,
        "no_source_rows": no_src_rows, "no_source_codes": no_src_codes,
        "elapsed_min": round((time.time() - t_all) / 60, 1),
        "reports": reports,
    }
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[evidence] {out.name}")
    print(f"[summary] ok={ok} skip={skip} fail={fail} 用时 {summary['elapsed_min']}min")
    return 2 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
