# -*- coding: utf-8 -*-
"""补录 manifest Part1 逐表行数（消除 88 表勾稽盲区，2026-09-12）"""
import json
from pathlib import Path
import duckdb

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
PKG = QS / "quantstudio_data_package_20260912.db"
MF = PKG.with_suffix(".manifest.json")

mf = json.loads(MF.read_text(encoding="utf-8"))
con = duckdb.connect(str(PKG), read_only=True)
tabs = [r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_catalog=current_catalog() AND table_schema='main' ORDER BY table_name").fetchall()]
part2 = {c["table"] for c in mf["part2"]["coverage"]}
part1 = [t for t in tabs if t not in part2]

details = []
for t in part1:
    n = con.execute('SELECT count(*) FROM "' + t + '"').fetchone()[0]
    details.append(dict(table=t, rows=int(n)))
con.close()

mf["part1"]["details"] = details
mf["part1"]["tables"] = len(details)
mf["part1"]["rows"] = sum(d["rows"] for d in details)
mf["part1"]["note"] = (str(mf["part1"].get("note", "")) +
    " / 逐表行数已补录（2026-09-12 盘点时补：file-copy 模式原不含 per-table details，"
    "导致 88 表勾稽对 Part1 段为空判）")
MF.write_text(json.dumps(mf, ensure_ascii=False, indent=1), encoding="utf-8")

print("Part1 details 补录:", len(details), "表   rows=", format(mf["part1"]["rows"], ","))
print("Part2 表数:", len(part2), " Part1 表数:", len(details),
      " 合计:", len(part2) + len(details))
