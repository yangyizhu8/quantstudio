# -*- coding: utf-8 -*-
"""income_statement FY2015-2017 补拉验收（主库；判据 ①②③）"""
import sys, json
from pathlib import Path
QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(QS))
import duckdb, pandas as pd
from quantstudio.pipeline.mcp.client import MCPClient

MAIN = QS / "data" / "quantstudio.db"
con = duckdb.connect(str(MAIN), read_only=True)

print("=" * 80)
print("① 覆盖年份实测（主库 data/quantstudio.db）")
print("=" * 80)
print("  SQL: GROUP BY year(epoch_ms(end_date))")
for y, c, n in con.execute(
        "SELECT year(epoch_ms(end_date)) y, count(*) c, count(DISTINCT code) n "
        "FROM income_statement GROUP BY 1 ORDER BY 1").fetchall():
    mark = "   <== 本次补入" if y in (2015, 2016, 2017) else ""
    print(f"    {y}   rows={c:>7,}   codes={n:>6,}{mark}")
print()
print("  年报行（end_date = 12 月）按年：")
ann = con.execute(
    "SELECT year(epoch_ms(end_date)) y, count(*) FROM income_statement "
    "WHERE month(epoch_ms(end_date)) = 12 GROUP BY 1 ORDER BY 1").fetchall()
for y, c in ann:
    mark = "   <== 本次补入" if y in (2015, 2016, 2017) else ""
    print(f"    {y}   {c:>6,}{mark}")
loc1517 = con.execute("SELECT count(*) FROM income_statement "
                      "WHERE year(epoch_ms(end_date)) BETWEEN 2015 AND 2017").fetchone()[0]
loc_ann = {y: c for y, c in ann}
print()
print(f"  本地 2015-2017 行数 = {loc1517:,}")
print(f"  本地 2015/2016/2017 年报行 = "
      f"{loc_ann.get(2015,0):,} / {loc_ann.get(2016,0):,} / {loc_ann.get(2017,0):,}")
print(f"  end_date 范围 = ", con.execute(
    "SELECT strftime(epoch_ms(min(end_date)),'%Y-%m-%d'), "
    "strftime(epoch_ms(max(end_date)),'%Y-%m-%d') FROM income_statement").fetchone())

print()
print("=" * 80)
print("② 云端独立对账（qdb.stock_income 2015-01-01 ~ 2018-01-01）")
print("=" * 80)
c = MCPClient(); c.handshake()
job = c.create_export_job("qdb.stock_income", 50000,
                          time_start="2015-01-01", time_end="2018-01-01")
mf = c.get_manifest(job)
print(f"  job_id={job}  shards={mf.shard_count}  cloud total_rows={mf.total_rows:,}")
frames = []
for sh in mf.shards:
    art = c.get_artifact(job, sh.artifact_id)
    t = c.decode_parquet(art)
    frames.append(t.to_pandas() if hasattr(t, "to_pandas") else t)
cloud = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
cloud.to_parquet(QS / "data/logs/_cloud_is_1517.parquet")
print(f"  云端实取 = {len(cloud):,} 行  列数 = {len(cloud.columns)}")
print(f"  对账：本地 2015-2017 = {loc1517:,}  vs  云端 = {len(cloud):,}  ->  "
      f"{'一致 ✓' if loc1517 == len(cloud) else '差异 ✗'}")

print()
print("=" * 80)
print("③ 抽样逐值对拍（≥5 标的 × FY2015-2017 年度 np_parent_company_owners）")
print("=" * 80)
# 云端年度行（12月）
cc = cloud.copy()
import numpy as np
if "end_date" in cc.columns:
    cc["ed"] = pd.to_datetime(cc["end_date"], errors="coerce")
elif "end_date_ms" in cc.columns:
    cc["ed"] = pd.to_datetime(cc["end_date_ms"], unit="ms", errors="coerce")
print("  云端列:", list(cc.columns)[:12])
code_col = "code" if "code" in cc.columns else ("ts_code" if "ts_code" in cc.columns else None)
print("  代码列:", code_col)
sample = ["000001.SZ", "600000.SH", "000002.SZ", "600519.SH", "000651.SZ", "600036.SH"]
print()
print(f"  {'标的':<12} {'年':<6} {'本地 np_parent_company_owners':>28} {'云端':>20}  判定")
con.close()
con2 = duckdb.connect(str(MAIN), read_only=True)
ok = bad = 0
for s in sample:
    for y in (2015, 2016, 2017):
        lr = con2.execute(
            "SELECT np_parent_company_owners FROM income_statement "
            "WHERE code=? AND year(epoch_ms(end_date))=? AND month(epoch_ms(end_date))=12",
            [s, y]).fetchone()
        lv = lr[0] if lr else None
        cv = None
        if code_col and "ed" in cc.columns:
            sub = cc[(cc[code_col] == s) & (cc["ed"].dt.year == y) & (cc["ed"].dt.month == 12)]
            if len(sub):
                cv = sub["np_parent_company_owners"].iloc[0]
        same = (lv is not None and cv is not None and abs(float(lv) - float(cv)) < 0.01)
        ok += 1 if same else 0
        bad += 0 if same else 1
        lvs = f"{lv:,.2f}" if lv is not None else "—"
        cvs = f"{cv:,.2f}" if cv is not None else "—"
        print(f"  {s:<12} {y:<6} {lvs:>28} {cvs:>20}  {'✓' if same else '✗'}")
con2.close()
print()
print(f"  对拍结果: {ok} 一致 / {bad} 不一致")
