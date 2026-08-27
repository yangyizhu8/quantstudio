# -*- coding: utf-8 -*-
"""adj_factor/fund_adj 表结构与索引诊断。"""
import io
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ac = sqlite3.connect("data/qfq_aux.db")
ac.execute("PRAGMA query_only = ON")
for t in ["adj_factor", "fund_adj"]:
    cols = ac.execute(f"PRAGMA table_info({t})").fetchall()
    idx = ac.execute(f"PRAGMA index_list({t})").fetchall()
    print(f"{t}: 列={[(c[1], c[2]) for c in cols]}")
    print(f"  索引: {[(i[1], i[2]) for i in idx]}")
ac.close()
