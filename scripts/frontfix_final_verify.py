# -*- coding: utf-8 -*-
"""最终守恒验收：4 表 raw 不变性（vs 备份）+ 偏离行汇总。"""
import glob
import io
import sys

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

MAIN = "data/quantstudio.db"
bak = sorted(glob.glob("data/backup/quantstudio.db.bak_frontfix_*"))[-1]

con = duckdb.connect(MAIN, read_only=True)
cb = duckdb.connect(bak, read_only=True)

print("=== raw 列不变性（主库 vs 备份）===")
ok_all = True
for t in ["stock_daily", "etf_daily", "stock_minutes", "etf_minutes"]:
    for col in ["open", "high", "low", "close"]:
        a = con.execute(f"SELECT COUNT({col}), SUM({col}) FROM {t}").fetchone()
        b = cb.execute(f"SELECT COUNT({col}), SUM({col}) FROM {t}").fetchone()
        same = (a[0] == b[0]) and (abs((a[1] or 0) - (b[1] or 0)) < 1.0)
        if not same:
            ok_all = False
            print(f"  {t}.{col}: MISMATCH main=({a[0]},{a[1]}) bak=({b[0]},{b[1]})")
    print(f"  {t}: raw 4 列 OK" if ok_all else f"  {t}: 见上")
print(f"raw 不变性: {'ALL OK' if ok_all else 'FAIL'}")

print()
print("=== front 修复行数汇总（本会话执行记录）===")
summary = [
    ("etf_daily", 430547), ("stock_daily", 32424),
    ("stock_minutes", 848130), ("etf_minutes", 13104964),
]
tot = 0
for t, n in summary:
    print(f"  {t}: {n:,}")
    tot += n
print(f"  合计: {tot:,}")
con.close()
cb.close()
