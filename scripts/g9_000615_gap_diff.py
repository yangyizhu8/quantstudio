#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""000615 fresh-vs-库内 逐行比对（甲1a · R-20261112-02）。

输入：`docs/evidence/g9-000615-fresh-mcp.json`（已重拉的 mcp fresh 日线，2,112 行）
对照：生产库 `stock_daily` 的 `000615`（只读）
输出：`docs/evidence/g9-000615-gap-diff.json` + 控制台清单

判据（三者并列，不合并）：
  1. **fresh 独有日期** = fresh 有而库内无 ⇒ 这才是「缺 N 行」的真实含义
  2. **库内独有日期** = 库内有而 fresh 无（含 fresh 上游滞后段）
  3. **共同日期的 OHLC 不一致**（|Δ|>1e-9）= 口径差异（源切换后数值口径不同）

支持守候模式（`--wait-lock`）：锁释放后自动执行，避免人工盯窗。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

EPS = 1e-9
CODE = "000615"


CST_OFFSET = 8 * 3600  # 库内 stock_daily.time 存「北京零点」的 epoch-ms（= UTC 前一日 16:00）


def ms2date(v: int) -> str:
    """epoch-ms → **北京日期**。

    ⚠️ 2026-11-12 实测教训（本条 bug 曾造成 1,659 条虚假 OHLC 差异）：
      stock_daily.time=1515513600000 用 utcfromtimestamp 得 2018-01-09（错），
      加 8h 后得 2018-01-10（对，与 mcp fresh 一致）。
      **跨源比对的双方必须用同一时区解析**；若「独有日期」全落在同一星期几，
      几乎一定是基准错位而非真差异。
    """
    return dt.datetime.utcfromtimestamp(v / 1000 + CST_OFFSET).strftime("%Y-%m-%d")


def try_open(db: str, timeout: int = 30):
    try:
        import duckdb
        return duckdb.connect(db, read_only=True), None
    except Exception as e:
        msg = str(e)
        if "另一个程序正在使用此文件" in msg or "already open" in msg.lower():
            return None, "locked"
        return None, f"other:{type(e).__name__}"


def do_compare(db: str, fresh_path: Path, out_path: Path) -> int:
    import duckdb
    fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
    fmap = {r["date"]: r for r in fresh["data"]}
    print(f"fresh: {len(fmap)} 行，{fresh['first']} ~ {fresh['last']}（source={fresh['source']}）")

    con, why = try_open(db)
    if con is None:
        print(f"FATAL: 库不可读（{why}）")
        return 3
    try:
        rows = con.execute(
            "SELECT time, open, high, low, close FROM stock_daily WHERE code=? ORDER BY time",
            [CODE]).fetchall()
    finally:
        con.close()

    dmap = {}
    for t, o, h, l, c in rows:
        dmap[ms2date(int(t))] = {"open": float(o), "high": float(h),
                                 "low": float(l), "close": float(c)}
    print(f"库内: {len(dmap)} 行，{min(dmap)} ~ {max(dmap)}")

    fset, dset = set(fmap), set(dmap)
    only_fresh = sorted(fset - dset)     # fresh 有、库内无 ⇒ 「缺 N 行」的真身
    only_db = sorted(dset - fset)        # 库内有、fresh 无
    common = sorted(fset & dset)

    diffs = []
    for d in common:
        a, b = fmap[d], dmap[d]
        for col in ("open", "high", "low", "close"):
            if abs(a[col] - b[col]) > EPS:
                diffs.append({"date": d, "col": col, "fresh": a[col], "db": b[col],
                              "delta": a[col] - b[col]})
                break

    rep = {
        "code": CODE,
        "fresh": {"rows": len(fmap), "first": fresh["first"], "last": fresh["last"],
                  "sha256": fresh["daily_sha256"], "source": fresh["source"]},
        "db": {"rows": len(dmap), "first": min(dmap), "last": max(dmap)},
        "only_fresh_count": len(only_fresh), "only_fresh_dates": only_fresh[:200],
        "only_db_count": len(only_db), "only_db_dates": only_db[:200],
        "common_count": len(common),
        "ohlc_diff_count": len(diffs), "ohlc_diff_samples": diffs[:50],
        "verdict": {
            "only_fresh_eq_32": len(only_fresh) == 32,
            "explains_missing_32": f"only_fresh={len(only_fresh)}（判据：应为 32 才印证「缺 32 行」）",
            "upstream_lag_days": (dt.date.fromisoformat(max(dmap)) -
                                  dt.date.fromisoformat(fresh["last"])).days,
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 比对结果 ===")
    print(f"  共同日期        = {len(common):,}")
    print(f"  **fresh 独有**  = {len(only_fresh):,}   ← 「缺 N 行」真身（判定：应为 32）")
    print(f"  库内独有        = {len(only_db):,}   （含上游滞后段 {fresh['last']} ~ {max(dmap)}）")
    print(f"  共同日期 OHLC 不一致 = {len(diffs):,}")
    print(f"  上游滞后        = {rep['verdict']['upstream_lag_days']} 天")
    if only_fresh:
        print(f"\n  fresh 独有日期（前 40）：{only_fresh[:40]}")
    if only_db:
        print(f"  库内独有日期（前 40）：{only_db[:40]}")
    if diffs:
        print("\n  OHLC 差异样例（前 10）：")
        for d in diffs[:10]:
            print(f"    {d['date']} {d['col']}: fresh={d['fresh']!r} db={d['db']!r} Δ={d['delta']!r}")
    print(f"\n已落盘 = {out_path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db")
    ap.add_argument("--fresh", default=r"D:\miniQMT策略实盘\QuantStudio\docs\evidence\g9-000615-fresh-mcp.json")
    ap.add_argument("--out", default=r"D:\miniQMT策略实盘\QuantStudio\docs\evidence\g9-000615-gap-diff.json")
    ap.add_argument("--wait-lock", action="store_true", help="锁释放后自动执行")
    ap.add_argument("--max-wait-hours", type=float, default=14.0)
    ap.add_argument("--poll", type=int, default=20)
    a = ap.parse_args(argv)

    fresh_path, out_path = Path(a.fresh), Path(a.out)
    if not fresh_path.exists():
        print(f"FATAL: fresh 文件不存在 {fresh_path}")
        return 2

    if not a.wait_lock:
        return do_compare(a.db, fresh_path, out_path)

    deadline = time.time() + a.max_wait_hours * 3600
    n = 0
    while time.time() < deadline:
        n += 1
        con, why = try_open(a.db)
        if con is not None:
            con.close()
            print(f"[{time.strftime('%H:%M:%S')}] 第 {n} 次探测：锁已释放 ⇒ 开始比对")
            time.sleep(3)
            return do_compare(a.db, fresh_path, out_path)
        if n % 15 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] 第 {n} 次：仍锁（{why}）", flush=True)
        time.sleep(a.poll)
    print(f"超时 {a.max_wait_hours}h 未释放")
    return 4


if __name__ == "__main__":
    sys.exit(main())
