# -*- coding: utf-8 -*-
"""确认 etf_minutes OOM 后是否部分写入（只读）。"""
import io
import sys

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
con = duckdb.connect("data/quantstudio.db", read_only=True)
total = con.execute("SELECT COUNT(*) FROM etf_minutes").fetchone()[0]
print(f"etf_minutes 总行数: {total}")
con.close()
