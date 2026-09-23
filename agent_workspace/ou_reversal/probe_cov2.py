
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)
def q(sql):
    try: return con.execute(sql).fetchall()
    except Exception as e: return [["ERR", str(e)[:400]]]
def d(ms):
    try: return datetime.datetime.utcfromtimestamp(int(ms)/1000).strftime("%Y-%m-%d")
    except Exception: return str(ms)

print("== stock_daily coverage (cast) ==")
for r in q("select count(*) n, cast(min(time) as varchar) t0, cast(max(time) as varchar) t1, count(distinct code) codes, count(distinct time) days from stock_daily"):
    print("  rows=%s t0=%s t1=%s codes=%s days=%s" % (r[0], d(r[1]), d(r[2]), r[3], r[4]))
print()
print("== stock_daily time column type ==")
print(q("select data_type from information_schema.columns where table_name='stock_daily' and column_name='time'"))
print()
print("== 000300 complete snapshot dates (all) ==")
rows = q("select cast(time as varchar), n_constituents, status from index_constituents_snapshot_meta where index_code like '000300%' and status='complete' order by time")
print("  count=%d" % len(rows))
print("  dates: " + ", ".join(d(r[0]) for r in rows))
print()
print("== 000300 rows in index_constituents per snapshot date (2021..2026) ==")
for r in q("""select cast(time as varchar) t, count(*) n from index_constituents where index_code like '000300%'
              and time >= 1609459200000 group by 1 order by 1"""):
    print("  ", d(r[0]), "n=%s" % r[1])
con.close()
