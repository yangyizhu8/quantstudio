# -*- coding: utf-8 -*-
"""Phase 2 修复工具 v2：因子加载改 DuckDB sqlite scanner（避免 python sqlite IN 慢查）。

用法:
  python scripts/frontfix.py --table etf_daily --dry-run
  python scripts/frontfix.py --table etf_daily --apply
  python scripts/frontfix.py --table etf_minutes --apply --batch-size 200
"""
import argparse
import io
import sys
import time

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

MAIN = "data/quantstudio.db"
AUX = "data/qfq_aux.db"
CSV_DIR = "data/logs"
TOL = 1e-6

TABLE_META = {
    "etf_daily": {"csv": "front_corruption_etf_daily.csv", "asset": "ETF", "minute": False},
    "etf_minutes": {"csv": "front_corruption_etf_minutes.csv", "asset": "ETF", "minute": True},
    "stock_daily": {"csv": "front_corruption_stock_daily.csv", "asset": "STOCK", "minute": False},
    "stock_minutes": {"csv": "front_corruption_stock_minutes.csv", "asset": "STOCK", "minute": True},
}


def load_affected_codes(table):
    csv = f"{CSV_DIR}/{TABLE_META[table]['csv']}"
    codes = set()
    with open(csv, encoding="utf-8", errors="replace") as f:
        f.readline()
        for ln in f:
            parts = ln.split(",")
            if parts and parts[0]:
                codes.add(parts[0].strip())
    return codes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True, choices=list(TABLE_META))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--batch-size", type=int, default=200)
    args = ap.parse_args()
    if args.dry_run == args.apply:
        print("必须二选一: --dry-run 或 --apply")
        sys.exit(1)

    meta = TABLE_META[args.table]
    codes = load_affected_codes(args.table)
    print(f"[{args.table}] 受影响 code 数: {len(codes)}", flush=True)
    csv_n = sum(1 for _ in open(f"{CSV_DIR}/{meta['csv']}", encoding="utf-8", errors="replace")) - 1
    print(f"CSV 破坏行数(对账基准): {csv_n}", flush=True)

    con = duckdb.connect(MAIN)
    con.execute(f"ATTACH '{AUX}' AS auxdb (TYPE sqlite, READ_ONLY)")
    f_tbl = "auxdb.fund_adj" if meta["asset"] == "ETF" else "auxdb.adj_factor"

    # 受影响 code → TEMP 表 → 因子 TEMP 表（半连接，避免巨型 IN 字面量）
    import pandas as pd
    codes_df = pd.DataFrame({"code": sorted(codes)})
    con.register("codes_t", codes_df)
    t0 = time.time()
    if meta["minute"]:
        # 分钟表：因子去重到日粒度（每 code 每天一行），避免与分钟 bar 笛卡尔积
        con.execute(
            f"CREATE TEMP TABLE factordf AS "
            f"SELECT code, time, adj_factor FROM ("
            f"  SELECT f.code, f.time, f.adj_factor,"
            f"         row_number() OVER (PARTITION BY f.code, strftime(epoch_ms(f.time), '%Y-%m-%d') "
            f"                          ORDER BY f.time DESC) AS rn"
            f"  FROM {f_tbl} f WHERE f.code IN (SELECT code FROM codes_t)"
            f") WHERE rn = 1")
    else:
        con.execute(
            f"CREATE TEMP TABLE factordf AS "
            f"SELECT f.code, f.time, f.adj_factor FROM {f_tbl} f "
            f"WHERE f.code IN (SELECT code FROM codes_t)")
    nfac = con.execute("SELECT COUNT(*) FROM factordf").fetchone()[0]
    print(f"因子加载完成: {nfac} 行 ({time.time()-t0:.0f}s)", flush=True)

    if meta["minute"]:
        join_cond = ("tgt.code = f.code AND "
                     "strftime(epoch_ms(tgt.time), '%Y-%m-%d') = strftime(epoch_ms(f.time), '%Y-%m-%d')")
    else:
        join_cond = "tgt.code = f.code AND tgt.time = f.time"

    count_sql = f"""
    SELECT COUNT(*) FROM {args.table} tgt
    JOIN (
      SELECT code, time, adj_factor,
             MAX(adj_factor) OVER (PARTITION BY code) AS adj_latest
      FROM factordf
    ) f
    ON {join_cond}
    WHERE tgt.close_front IS NOT NULL
      AND abs(tgt.close_front - tgt.close * f.adj_factor / f.adj_latest) / tgt.close_front > {TOL}
    """
    n = con.execute(count_sql).fetchone()[0]
    print(f"待修行数(全历史偏离判定): {n}  vs CSV: {csv_n}  diff={n - csv_n}", flush=True)

    if args.dry_run:
        print("[dry-run] 不写入。")
        con.close()
        return

    upd_base = f"""
    UPDATE {args.table} tgt SET
      open_front = tgt.open * f.adj_factor / f.adj_latest,
      high_front = tgt.high * f.adj_factor / f.adj_latest,
      low_front  = tgt.low  * f.adj_factor / f.adj_latest,
      close_front = tgt.close * f.adj_factor / f.adj_latest
    FROM (
      SELECT code, time, adj_factor,
             MAX(adj_factor) OVER (PARTITION BY code) AS adj_latest
      FROM factordf
    ) f
    WHERE {join_cond}
      AND tgt.close_front IS NOT NULL
      AND abs(tgt.close_front - tgt.close * f.adj_factor / f.adj_latest) / tgt.close_front > {TOL}
    """
    t0 = time.time()
    fixed_total = 0
    batch = sorted(codes)
    for i in range(0, len(batch), args.batch_size):
        b = batch[i:i + args.batch_size]
        ph = ",".join("'" + c + "'" for c in b)
        upd = (upd_base + f" AND tgt.code IN ({ph})")
        r = con.execute(upd).fetchone()
        fixed_total += int(r[0]) if r else 0
        if (i // args.batch_size) % 10 == 0:
            print(f"  batch {i // args.batch_size}: 累计修复 {fixed_total}, 已耗时 {time.time()-t0:.0f}s", flush=True)
    print(f"[apply] {args.table} 修复行数: {fixed_total} (耗时 {time.time()-t0:.0f}s)", flush=True)
    con.close()


if __name__ == "__main__":
    main()
