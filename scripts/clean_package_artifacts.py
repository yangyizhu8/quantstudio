# -*- coding: utf-8 -*-
"""清理包内构建残留（分片 ledger / staging 临时表），2026-09-12"""
from pathlib import Path
import duckdb

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
PKG = QS / "quantstudio_data_package_20260912.db"

con = duckdb.connect(str(PKG))
tabs = [r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_catalog=current_catalog() AND table_schema='main' ORDER BY table_name").fetchall()]
print("包内表数:", len(tabs))

artifacts = [t for t in tabs if t.startswith("_pt_")]
for t in artifacts:
    n = con.execute('SELECT count(*) FROM "' + t + '"').fetchone()[0]
    print(f"  构建残留: {t}  rows={n}")
    if n == 0:
        con.execute('DROP TABLE "' + t + '"')
        print(f"    -> 已删除（空表）")
    else:
        print(f"    -> 保留（非空，需人工确认）")

left = [r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_catalog=current_catalog() AND table_schema='main'").fetchall()]
con.close()
print("清理后表数:", len(left))
