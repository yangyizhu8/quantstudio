# -*- coding: utf-8 -*-
"""DRY-RUN 差异对照：159307 样本 + 差异行抽样。"""
import io
import sqlite3
import sys

import duckdb
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 1) 159307 期望值核对
con = duckdb.connect("data/quantstudio.db", read_only=True)
row = con.execute(
    "SELECT code, time, close, close_front FROM etf_daily WHERE code='159307' "
    "AND strftime(epoch_ms(time), '%Y-%m-%d')='2026-05-26'").fetchall()
print("159307 @ 2026-05-26:", row)

# 2) 因子
ac = sqlite3.connect("data/qfq_aux.db")
ac.execute("PRAGMA query_only = ON")
fr = ac.execute(
    "SELECT time, adj_factor FROM fund_adj WHERE code='159307' ORDER BY time DESC LIMIT 5").fetchall()
print("159307 fund_adj 最近 5:", fr)
ac.close()

# 3) 我判定偏离但 ZCode CSV 没有的差异行抽样（对比 join 口径）
codes_csv = set()
with open("data/logs/front_corruption_etf_daily.csv", encoding="utf-8", errors="replace") as f:
    f.readline()
    for ln in f:
        p = ln.split(",")
        if p and p[0]:
            codes_csv.add((p[0], p[1]))  # code, month

# 用主库重新跑判定（close 偏离），输出 3 个"判定偏离但 (code,month) 不在 CSV"的行
factors = duckdb.connect("data/quantstudio.db", read_only=True)
factors.execute("ATTACH 'data/qfq_aux.db' AS auxdb (TYPE sqlite, READ_ONLY)")
q = """
WITH dev AS (
  SELECT tgt.code, strftime(epoch_ms(tgt.time), '%Y-%m') AS month,
         tgt.close, tgt.close_front, tgt.time,
         abs(tgt.close_front - tgt.close * f.adj_factor / f.adj_latest) / tgt.close_front AS dev
  FROM etf_daily tgt
  JOIN (
    SELECT code, time, adj_factor,
           MAX(adj_factor) OVER (PARTITION BY code) AS adj_latest
    FROM auxdb.fund_adj
  ) f
  ON tgt.code = f.code AND tgt.time = f.time
  WHERE tgt.close_front IS NOT NULL
    AND abs(tgt.close_front - tgt.close * f.adj_factor / f.adj_latest) / tgt.close_front > 1e-6
)
SELECT * FROM dev LIMIT 5
"""
try:
    for r in factors.execute(q).fetchall():
        in_csv = (r[0], r[1]) in codes_csv
        print(f"偏离行: code={r[0]} month={r[1]} close={r[2]} close_front={r[3]} dev={r[5]:.6f} in_csv={in_csv}")
except Exception as e:
    print("ATTACH sqlite 查询失败:", str(e)[:200])
factors.close()
con.close()
