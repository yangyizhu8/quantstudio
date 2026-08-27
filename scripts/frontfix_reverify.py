# -*- coding: utf-8 -*-
"""修正口径复验：上海日期过滤 159307 + 剩余偏离行 join 诊断。"""
import io
import sys

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

con = duckdb.connect("data/quantstudio.db", read_only=True)
con.execute("ATTACH 'data/qfq_aux.db' AS auxdb (TYPE sqlite, READ_ONLY)")

# 上海日期 = UTC + 8h 的日期
SH = "strftime(epoch_ms(time + 28800000), '%Y-%m-%d')"

# 1) 159307 上海 05-26 行
row = con.execute(f"""
SELECT time, close, close_front FROM etf_daily WHERE code='159307' AND {SH}='2026-05-26'
""").fetchall()
print("159307 上海05-26 行:", row)

# 2) 该行正确因子（join 因子表）
row2 = con.execute(f"""
SELECT t.time, t.close, t.close_front, f.adj_factor,
       (SELECT adj_factor FROM auxdb.fund_adj WHERE code='159307' ORDER BY time DESC LIMIT 1) AS adj_latest
FROM etf_daily t
JOIN auxdb.fund_adj f ON t.code = f.code AND t.time = f.time
WHERE t.code='159307' AND strftime(epoch_ms(t.time + 28800000), '%Y-%m-%d')='2026-05-26'
""").fetchall()
print("join 因子结果:", row2)

# 3) 剩余偏离行构成（join 失败 vs 无因子）
left = con.execute(f"""
SELECT COUNT(*) FROM (
  SELECT tgt.code, tgt.time FROM etf_daily tgt
  LEFT JOIN (
    SELECT code, time, adj_factor,
           MAX(adj_factor) OVER (PARTITION BY code) AS adj_latest
    FROM auxdb.fund_adj
  ) f ON tgt.code = f.code AND tgt.time = f.time
  WHERE tgt.close_front IS NOT NULL AND f.code IS NULL
    AND abs(tgt.close_front - tgt.close) / NULLIF(tgt.close_front, 0) > 1e-6
) x
""").fetchone()[0]
print(f"剩余偏离中『因子无该 time 行』的行数: {left}")

# 4) 抽样 3 个剩余偏离行（因子 join 失败）
samp = con.execute(f"""
SELECT tgt.code, tgt.time, tgt.close, tgt.close_front,
       strftime(epoch_ms(tgt.time + 28800000), '%Y-%m-%d') AS sh_day
FROM etf_daily tgt
LEFT JOIN (
  SELECT code, time, adj_factor,
         MAX(adj_factor) OVER (PARTITION BY code) AS adj_latest
  FROM auxdb.fund_adj
) f ON tgt.code = f.code AND tgt.time = f.time
WHERE tgt.close_front IS NOT NULL AND f.code IS NULL
  AND abs(tgt.close_front - tgt.close) / NULLIF(tgt.close_front, 0) > 1e-6
LIMIT 5
""").fetchall()
print("剩余偏离抽样（join 失败行）:")
for r in samp:
    print(f"  code={r[0]} time={r[1]} sh_day={r[4]} close={r[2]} close_front={r[3]}")
con.close()
