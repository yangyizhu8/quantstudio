# -*- coding: utf-8 -*-
"""5 码主库分钟覆盖探测（捎带令 · 2026-10-28）

用法（daemon 停机时）：python scripts/probe_minute_coverage_5codes.py
判向：主库有 ⇒ 副本陈旧（策略研发刷新副本即可）；主库也缺 ⇒ 采集管线覆盖缺口（数据线工程立项）
"""
import duckdb, sys
DB = r"data\quantstudio.db"
CODES = ["600916", "000799", "002328", "002468", "002607"]
con = duckdb.connect(DB, read_only=True)
cols = [r[0] for r in con.execute("DESCRIBE stock_minutes").fetchall()]
tcol = "time" if "time" in cols else ("trade_time" if "trade_time" in cols else cols[1])
print(f"表列: {cols}")
print(f"时间列: {tcol}\n")
for c in CODES:
    rows = con.execute(
        f"SELECT CAST({tcol} AS DATE) AS d, COUNT(DISTINCT {tcol}) AS n "
        f"FROM stock_minutes WHERE code LIKE ? GROUP BY d ORDER BY d", [c + "%"]
    ).fetchall()
    if not rows:
        print(f"  {c}: **主库无任何分钟数据** ⇒ 采集管线覆盖缺口")
    else:
        tot = sum(r[1] for r in rows)
        print(f"  {c}: {len(rows)} 个交易日, 共 {tot} 根 (distinct time)")
        for d, n in rows:
            print(f"      {d}  {n} 根")
con.close()