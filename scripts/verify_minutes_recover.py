#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A8 验收脚本：minute 表止点/追平核验（只读，绝不写库）。

用法：
  python scripts/verify_minutes_recover.py --db data/quantstudio.db --target 2026-10-22
  python scripts/verify_minutes_recover.py --db <path> --target <YYYY-MM-DD> --json

退出码：0 = 全部追平；1 = 未追平（输出缺口）；2 = 用法/环境错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta


def _ms_of_date(s: str) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=8)))
    return int(dt.timestamp() * 1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--target", required=True, help="目标日期 YYYY-MM-DD（当日）")
    ap.add_argument("--tables", default="etf_minutes,stock_minutes")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    target_ms = _ms_of_date(args.target)
    try:
        import duckdb
    except ImportError:
        print("ERROR: duckdb 未安装", file=sys.stderr)
        return 2
    out = {}
    try:
        con = duckdb.connect(args.db, read_only=True)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 打开库失败（daemon 可能占用）：{type(e).__name__}: {e}", file=sys.stderr)
        return 2
    try:
        for t in [x.strip() for x in args.tables.split(",") if x.strip()]:
            try:
                n, mx = con.execute(f"SELECT count(*), max(time) FROM {t}").fetchone()
                out[t] = {"rows": n, "max_time": mx, "ok": bool(mx and mx >= target_ms)}
            except Exception as e:  # noqa: BLE001
                out[t] = {"error": f"{type(e).__name__}: {e}", "ok": False}
    finally:
        con.close()
    ok = all(v.get("ok") for v in out.values())
    if args.json:
        print(json.dumps({"target": args.target, "ok": ok, "tables": out}, ensure_ascii=False))
    else:
        print(f"target={args.target} ({target_ms})")
        for t, v in out.items():
            print(f"  {t}: {v}")
        print("RESULT:", "ALL_CAUGHT_UP ✅" if ok else "GAP ❌（未追平：继续下一轮或回报）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
