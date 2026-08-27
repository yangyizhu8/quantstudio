# -*- coding: utf-8 -*-
"""检查 stock_minutes 是否被笛卡尔积 UPDATE 污染（只读）。"""
import io
import sys

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
con = duckdb.connect("data/quantstudio.db", read_only=True)
n = con.execute(
    "SELECT COUNT(*) FROM stock_minutes WHERE close_front IS NOT NULL AND abs(close_front - close)/close > 1e-6").fetchone()[0]
total = con.execute("SELECT COUNT(*) FROM stock_minutes").fetchone()[0]
print(f"stock_minutes: 总行数={total}, close_front!=close 的行={n} ({n/total*100:.1f}%)")
# 抽样 3 行看值
for r in con.execute(
    "SELECT code, time, close, close_front FROM stock_minutes "
    "WHERE close_front IS NOT NULL AND abs(close_front - close)/close > 1e-6 LIMIT 3").fetchall():
    print(r)
con.close()
