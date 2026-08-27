# -*- coding: utf-8 -*-
"""etf_minutes 修复进度检查：剩余偏离行（正确口径）。"""
import io
import sys

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
con = duckdb.connect("data/quantstudio.db")
con.execute("ATTACH 'data/qfq_aux.db' AS auxdb (TYPE sqlite, READ_ONLY)")

# 受影响 code（CSV）
codes = set()
with open("data/logs/front_corruption_etf_minutes.csv", encoding="utf-8", errors="replace") as f:
    f.readline()
    for ln in f:
        p = ln.split(",")
        if p and p[0]:
            codes.add(p[0].strip())
import pandas as pd
con.register("codes_t", pd.DataFrame({"code": sorted(codes)}))
con.execute(
    "CREATE TEMP TABLE factordf AS SELECT code, time, adj_factor FROM ("
    "  SELECT f.code, f.time, f.adj_factor,"
    "         row_number() OVER (PARTITION BY f.code, strftime(epoch_ms(f.time), '%Y-%m-%d') "
    "                          ORDER BY f.time DESC) AS rn"
    "  FROM auxdb.fund_adj f WHERE f.code IN (SELECT code FROM codes_t)"
    ") WHERE rn = 1")

left = con.execute("""
SELECT COUNT(*) FROM etf_minutes tgt
JOIN (
  SELECT code, time, adj_factor,
         MAX(adj_factor) OVER (PARTITION BY code) AS adj_latest
  FROM factordf
) f
ON tgt.code = f.code
   AND strftime(epoch_ms(tgt.time), '%Y-%m-%d') = strftime(epoch_ms(f.time), '%Y-%m-%d')
WHERE tgt.close_front IS NOT NULL
  AND abs(tgt.close_front - tgt.close * f.adj_factor / f.adj_latest) / tgt.close_front > 1e-6
""").fetchone()[0]
print(f"etf_minutes 剩余偏离行: {left}（目标 13,104,964，剩余 0 即完成）")
con.close()
