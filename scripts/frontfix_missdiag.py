# -*- coding: utf-8 -*-
"""诊断：CSV 有但我判定 miss 的行（-84,016）。"""
import io
import sys

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

con = duckdb.connect("data/quantstudio.db")
con.execute("ATTACH 'data/qfq_aux.db' AS auxdb (TYPE sqlite, READ_ONLY)")

# CSV 抽样 5 行（code, month）
samples = []
with open("data/logs/front_corruption_stock_minutes.csv", encoding="utf-8", errors="replace") as f:
    f.readline()
    for ln in f:
        p = ln.split(",")
        if p and p[0]:
            samples.append((p[0], p[1], p[2], p[3]))
        if len(samples) >= 6:
            break
print("CSV 抽样:", samples)

# 对每个样本查主库该 code 该月的偏离行 + 因子覆盖
for code, month, close_csv, front_csv in samples:
    print(f"\n=== {code} month={month} CSV close={close_csv} front={front_csv} ===")
    # 主库该 code 该月行（上海月）
    rows = con.execute(f"""
        SELECT time, close, close_front FROM stock_minutes
        WHERE code='{code}' AND strftime(epoch_ms(time + 28800000), '%Y-%m')='{month}'
        ORDER BY time LIMIT 3
    """).fetchall()
    for r in rows:
        print(f"  主库: time={r[0]} close={r[1]} close_front={r[2]}")
    # 因子该月覆盖（去重后按 UTC 日）
    fac = con.execute(f"""
        SELECT strftime(epoch_ms(time), '%Y-%m') AS m, COUNT(*), COUNT(DISTINCT strftime(epoch_ms(time), '%Y-%m-%d')) AS days
        FROM auxdb.adj_factor WHERE code='{code}' AND strftime(epoch_ms(time + 28800000), '%Y-%m')='{month}'
        GROUP BY 1
    """).fetchall()
    print(f"  因子(上海月 {month}): {fac}")
con.close()
