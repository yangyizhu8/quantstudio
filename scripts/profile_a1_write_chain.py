
"""A1 写前链 profile（Wave A 首步，2026-09-14）。

对象：DuckDBWriter.write() 写前处理链三段 —— L628 drop_duplicates(pk) /
L671 逐列 to_numeric(非 VARCHAR) / L711 SELECT COUNT 存在性确认。
方法：真实日批载荷（影子库逐日读），在**临时 DB** 上复刻代码实际语句计时；
影子库全程只读，不被污染。L721 INSERT..ON CONFLICT 同时计时作对照。
"""
import sys, time
from pathlib import Path
import duckdb, pandas as pd
ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
SHADOW = ROOT / "data" / "staging_shadow_20260913" / "quantstudio.db"
PROF = ROOT / "data" / "staging_shadow_20260913" / "profile_tmp.db"
TABLE = "etf_minutes"
PK = "(code, time, freq)"

if PROF.exists():
    PROF.unlink()
cs = duckdb.connect(str(SHADOW), read_only=True)
busy = cs.execute(
    "SELECT (time - (time % 86400000)) AS d, COUNT(*) c FROM etf_minutes "
    "GROUP BY 1 ORDER BY c DESC LIMIT 1").fetchone()
day, dayrows = busy[0], busy[1]
print("BUSIEST_MS_BUCKET =", day, " rows =", dayrows)
t0 = time.perf_counter()
pdf = cs.execute("SELECT * FROM etf_minutes WHERE time >= ? AND time < ?", [day, day + 86400000]).df()
t_load = time.perf_counter() - t0
print("PAYLOAD_ROWS =", len(pdf), " LOAD_S = %.2f" % t_load)
ddl = cs.execute("SELECT sql FROM duckdb_tables() WHERE table_name = ?", [TABLE]).fetchone()[0]
varchar = [r[0] for r in cs.execute(
    "SELECT column_name FROM duckdb_columns() WHERE table_name = ? AND data_type = 'VARCHAR'",
    [TABLE]).fetchall()]
cs.close()
print("VARCHAR_COLS =", varchar)

cd = duckdb.connect(str(PROF))
cd.execute(ddl)
cd.close()

R = {}
t0 = time.perf_counter(); _ = pdf.drop_duplicates(subset=["code", "time", "freq"], keep="last"); R["L628_dedup"] = time.perf_counter() - t0

df2 = pdf.copy()
t0 = time.perf_counter()
str_cols = set(varchar)
for c in df2.columns:
    if c in str_cols:
        continue
    if df2[c].dtype == object:
        df2[c] = pd.to_numeric(df2[c], errors="coerce")
R["L671_to_numeric"] = time.perf_counter() - t0

cd = duckdb.connect(str(PROF))
t0 = time.perf_counter(); cd.register("_tmp_write", df2); R["L679_register"] = time.perf_counter() - t0
t0 = time.perf_counter()
n_exist = cd.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE {PK} IN (SELECT {PK} FROM _tmp_write)").fetchone()[0]
R["L711_count_exist"] = time.perf_counter() - t0
cols = ", ".join(df2.columns); uset = ", ".join(f"{c}=EXCLUDED.{c}" for c in df2.columns)
t0 = time.perf_counter()
cd.execute(f"INSERT INTO {TABLE} ({cols}) SELECT * FROM _tmp_write ON CONFLICT {PK} DO UPDATE SET {uset}")
R["L721_insert_upsert"] = time.perf_counter() - t0
n_rows = cd.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
cd.close()

total = sum(R.values())
print()
print("%-22s %10s %8s" % ("SEGMENT", "SECONDS", "SHARE"))
print("-" * 44)
for k, v in R.items():
    print("%-22s %10.3f %7.1f%%" % (k, v, 100.0 * v / total))
print("-" * 44)
print("%-22s %10.3f %7.1f%%" % ("SUM", total, 100.0))
print()
print("THROUGHPUT_ROWS_PER_S = %.0f  (rows=%d)" % (len(df2) / total, len(df2)))
print("EXISTING_ROWS_AT_COUNT =", n_exist, " TABLE_ROWS_AFTER =", n_rows)
prewrite = R["L628_dedup"] + R["L671_to_numeric"] + R["L679_register"] + R["L711_count_exist"]
print("PREWRITE_CHAIN_SHARE = %.1f%%   SQL_INSERT_SHARE = %.1f%%"
      % (100.0 * prewrite / total, 100.0 * R["L721_insert_upsert"] / total))
print("VERDICT_711_MAIN_CULPRIT =", R["L711_count_exist"] > max(R["L628_dedup"], R["L671_to_numeric"], R["L721_insert_upsert"]))
