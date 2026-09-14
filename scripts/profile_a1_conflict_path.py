
"""A1 探针 B：冲突路径（DO UPDATE）实测 + CST 交易日边界对齐（2026-09-14）。

探针 A 只覆盖纯 insert（EXISTING=0）；生产增量近乎全冲突，本探针预置同批行后
重写同一载荷，测 L721 的 ON CONFLICT DO UPDATE 真实成本。

CST 日界：框架 ms 口径下 00:00 CST ⇒ ms % 86400000 == 57600000（8h 偏移）。
故 CST 日桶起点 = time - ((time - 57600000) % 86400000)。
影子复用既有 staging 影子（只读），禁止新全量拷贝。
"""
import sys, time
from pathlib import Path
import duckdb, pandas as pd
ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
SHADOW = ROOT / "data" / "staging_shadow_20260913" / "quantstudio.db"
PROF = ROOT / "data" / "staging_shadow_20260913" / "profile_tmp_b.db"
TABLE = "etf_minutes"; PK = "(code, time, freq)"
CST_OFFSET = 57600000

if PROF.exists():
    PROF.unlink()
cs = duckdb.connect(str(SHADOW), read_only=True)
busy = cs.execute(
    "SELECT (time - ((time - ?) % 86400000)) AS d, COUNT(*) c FROM etf_minutes "
    "GROUP BY 1 ORDER BY c DESC LIMIT 1", [CST_OFFSET]).fetchone()
day, dayrows = busy[0], busy[1]
print("CST_DAY_BUCKET =", day, " ms%%86400000 =", day % 86400000, " rows =", dayrows)
t0 = time.perf_counter()
pdf = cs.execute("SELECT * FROM etf_minutes WHERE time >= ? AND time < ?",
                 [day, day + 86400000]).df()
print("PAYLOAD_ROWS = %d  LOAD_S = %.2f" % (len(pdf), time.perf_counter() - t0))
ddl = cs.execute("SELECT sql FROM duckdb_tables() WHERE table_name = ?", [TABLE]).fetchone()[0]
varchar = [r[0] for r in cs.execute(
    "SELECT column_name FROM duckdb_columns() WHERE table_name = ? AND data_type='VARCHAR'",
    [TABLE]).fetchall()]
cs.close()

cd = duckdb.connect(str(PROF)); cd.execute(ddl)
cols = ", ".join(pdf.columns); uset = ", ".join(f"{c}=EXCLUDED.{c}" for c in pdf.columns)
str_cols = set(varchar)

def pass_once(df_src, tag):
    df = df_src.copy()
    R = {}
    t0 = time.perf_counter(); df = df.drop_duplicates(subset=["code","time","freq"], keep="last")
    R["L628_dedup"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    for c in df.columns:
        if c in str_cols: continue
        if df[c].dtype == object: df[c] = pd.to_numeric(df[c], errors="coerce")
    R["L671_to_numeric"] = time.perf_counter() - t0
    t0 = time.perf_counter(); cd.register("_tmp_write", df); R["L679_register"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    n_exist = cd.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE {PK} IN (SELECT {PK} FROM _tmp_write)").fetchone()[0]
    R["L711_count_exist"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    cd.execute(f"INSERT INTO {TABLE} ({cols}) SELECT * FROM _tmp_write ON CONFLICT {PK} DO UPDATE SET {uset}")
    R["L721_insert_upsert"] = time.perf_counter() - t0
    tot = sum(R.values())
    print()
    print("[%s]  exist_rows=%d  rows=%d  total=%.3fs  吞吐=%.0f 行/s"
          % (tag, n_exist, len(df), tot, len(df)/tot))
    for k, v in R.items():
        print("   %-22s %8.3fs %6.1f%%" % (k, v, 100.0*v/tot))
    return R

RA = pass_once(pdf, "PASS-1 纯 insert（对照探针 A）")
RB = pass_once(pdf, "PASS-2 全冲突 DO UPDATE（生产增量近似）")
print()
print("冲突路径倍率 L721 = %.2fx ，整批 = %.2fx"
      % (RB["L721_insert_upsert"]/RA["L721_insert_upsert"], sum(RB.values())/sum(RA.values())))
cd.close()
