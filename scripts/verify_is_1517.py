# -*- coding: utf-8 -*-
"""income_statement FY2015-2017 补拉验收（判据 ①②③）"""
import sys, json
from pathlib import Path
QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(QS))
import duckdb
from quantstudio.pipeline.mcp.client import MCPClient

con = duckdb.connect(str(QS / "data" / "quantstudio.db"), read_only=True)

print("=" * 78)
print("① 覆盖年份实测（本地，GROUP BY year(epoch_ms(end_date))）")
print("=" * 78)
rows = con.execute(
    "SELECT year(epoch_ms(end_date)) y, count(*) c, count(DISTINCT code) n "
    "FROM income_statement GROUP BY 1 ORDER BY 1").fetchall()
for y, c, n in rows:
    mark = "  <== 本次补入" if y in (2015, 2016, 2017) else ""
    print(f"  {y}   rows={c:>7,}   codes={n:>6,}{mark}")
print()
print("  年报行（end_date = 12月）按年：")
for y, c in con.execute(
        "SELECT year(epoch_ms(end_date)) y, count(*) FROM income_statement "
        "WHERE month(epoch_ms(end_date)) = 12 GROUP BY 1 ORDER BY 1").fetchall():
    mark = "  <== 本次补入" if y in (2015, 2016, 2017) else ""
    print(f"    {y}   {c:>6,}{mark}")
print()
print("  等价字符串日期口径：",
      con.execute("SELECT strftime(epoch_ms(min(end_date)),'%Y-%m-%d'), "
                  "strftime(epoch_ms(max(end_date)),'%Y-%m-%d') FROM income_statement").fetchone())
loc_1517 = con.execute(
    "SELECT count(*) FROM income_statement WHERE year(epoch_ms(end_date)) BETWEEN 2015 AND 2017"
).fetchone()[0]
loc_ann = con.execute(
    "SELECT count(*) FROM income_statement WHERE year(epoch_ms(end_date)) BETWEEN 2015 AND 2017 "
    "AND month(epoch_ms(end_date)) = 12").fetchone()[0]
print(f"  本地 2015-2017 行数 = {loc_1517:,}  其中年报 = {loc_ann:,}")
con.close()

print()
print("=" * 78)
print("② 云端独立对账（qdb.stock_income, time_start=2015-01-01 time_end=2018-01-01）")
print("=" * 78)
c = MCPClient(); c.handshake()
job = c.create_export_job("qdb.stock_income", 50000,
                          time_start="2015-01-01", time_end="2018-01-01")
print("  export job:", job)
mf = c.get_manifest(job)
print("  manifest keys:", list(mf.__dict__.keys()) if hasattr(mf, "__dict__") else type(mf))
shards = getattr(mf, "shards", None) or (mf.get("shards") if isinstance(mf, dict) else [])
total = getattr(mf, "total_rows", None) or (mf.get("total_rows") if isinstance(mf, dict) else None)
print("  shards:", len(shards), " total_rows:", total)
rows_all = []
for sh in shards:
    aid = getattr(sh, "artifact_id", None) or (sh.get("artifact_id") if isinstance(sh, dict) else None)
    art = c.get_artifact(job, aid)
    df = c.decode_parquet(art)
    rows_all.append(df)
import pandas as pd
cloud = pd.concat(rows_all, ignore_index=True) if rows_all else pd.DataFrame()
print(f"  云端实取行数 = {len(cloud):,}   列数 = {len(cloud.columns)}")
cloud.to_parquet(QS / "data/logs/_cloud_is_1517.parquet")
print("  云端快照已落盘: data/logs/_cloud_is_1517.parquet")
c.close()
