# -*- coding: utf-8 -*-
"""000550 因子覆盖诊断（正确日期口径）。"""
import io
import sys

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
con = duckdb.connect("data/quantstudio.db", read_only=True)
con.execute("ATTACH 'data/qfq_aux.db' AS auxdb (TYPE sqlite, READ_ONLY)")

print("=== 000550 因子覆盖（上海日 06-25 ~ 07-05）===")
for r in con.execute("""
SELECT strftime(epoch_ms(time + 28800000), '%Y-%m-%d') AS sh_day,
       COUNT(*), MIN(adj_factor), MAX(adj_factor)
FROM auxdb.adj_factor WHERE code='000550'
  AND strftime(epoch_ms(time + 28800000), '%Y-%m-%d') BETWEEN '2026-06-25' AND '2026-07-05'
GROUP BY 1 ORDER BY 1
""").fetchall():
    print(r)

print()
print("=== 000550 主库分钟行（07-01 上海，前 3 行）+ 该行 join 因子 ===")
for r in con.execute("""
SELECT t.time, t.close, t.close_front,
       strftime(epoch_ms(t.time + 28800000), '%Y-%m-%d') AS sh_day
FROM stock_minutes t
WHERE t.code='000550' AND strftime(epoch_ms(t.time + 28800000), '%Y-%m-%d')='2026-07-01'
ORDER BY t.time LIMIT 3
""").fetchall():
    print(r)

print()
print("=== 因子表 000550 总行数与时间范围 ===")
for r in con.execute("""
SELECT COUNT(*), MIN(strftime(epoch_ms(time+28800000),'%Y-%m-%d')), MAX(strftime(epoch_ms(time+28800000),'%Y-%m-%d'))
FROM auxdb.adj_factor WHERE code='000550'
""").fetchall():
    print(r)
con.close()
