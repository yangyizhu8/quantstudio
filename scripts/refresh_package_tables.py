# -*- coding: utf-8 -*-
"""包内指定表刷新（attach 主库拷贝，保约束）——2026-09-12 阶段 2 补齐配套

用法: python scripts/refresh_package_tables.py [--tables stock_daily,stock_daily_valuation]
采用 DELETE + INSERT SELECT（保持包内表既有 schema/约束，不用 CTAS ——CTAS 会丢
PK/NOT NULL/DEFAULT 并触发 writer schema 闸判 partial_or_mixed）。
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
import duckdb

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
PKG = QS / "quantstudio_data_package_20260912.db"
MAIN = QS / "data" / "quantstudio.db"
MF = PKG.with_suffix(".manifest.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", default="stock_daily,stock_daily_valuation")
    args = ap.parse_args()
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]

    con = duckdb.connect(str(PKG))
    con.execute(f"ATTACH IF NOT EXISTS '{MAIN}' AS m (READ_ONLY)")
    recs = []
    for t in tables:
        before = con.execute('SELECT count(*) FROM "' + t + '"').fetchone()[0]
        src = con.execute('SELECT count(*) FROM m."' + t + '"').fetchone()[0]
        con.execute('DELETE FROM "' + t + '"')
        con.execute('INSERT INTO "' + t + '" SELECT * FROM m."' + t + '"')
        after = con.execute('SELECT count(*) FROM "' + t + '"').fetchone()[0]
        r = con.execute(
            f'SELECT min(epoch_ms("time")), max(epoch_ms("time")) FROM "{t}"').fetchone()
        recs.append(dict(table=t, before=before, source=src, after=after,
                         match=(after == src),
                         date_min=str(r[0])[:10], date_max=str(r[1])[:10]))
        print(f"  {t}: 包 {before:,} -> {after:,} (主库 {src:,}) "
              f"{'OK' if after == src else 'MISMATCH'}  范围 {str(r[0])[:10]} ~ {str(r[1])[:10]}")
    con.close()

    # 更新 manifest：Part1 逐表行数 + 覆盖声明
    mf = json.loads(MF.read_text(encoding="utf-8"))
    upd = {r["table"]: r["after"] for r in recs}
    for d in mf["part1"].get("details", []):
        if d["table"] in upd:
            d["rows"] = upd[d["table"]]
    mf["part1"]["rows"] = sum(d["rows"] for d in mf["part1"].get("details", []))
    notes = mf.get("coverage_notes", [])
    notes = [n for n in notes if "stock_daily 已补齐" not in n]
    for r in recs:
        notes.append(f"{r['table']} 已补齐至 {r['date_max']}（2026-09-12 阶段 2 缩编补跑："
                     f"水位回拨 9/3 修『水位前移吞窗口』后增量拉取）")
    mf["coverage_notes"] = notes
    mf["refresh_log"] = mf.get("refresh_log", [])
    mf["refresh_log"].append(dict(ts=datetime.now().isoformat(timespec="seconds"),
                                  tables=recs,
                                  reason="阶段 2 缩编补跑（9/4-9/11 日表补齐）"))
    MF.write_text(json.dumps(mf, ensure_ascii=False, indent=1), encoding="utf-8")
    print("manifest 已更新；part1.rows =", format(mf["part1"]["rows"], ","))


if __name__ == "__main__":
    main()
