
import os, json, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)
def q(sql):
    try: return con.execute(sql).fetchall()
    except Exception as e: return [["ERR", str(e)[:400]]]
def d(ms):
    return datetime.datetime.utcfromtimestamp(ms/1000).strftime("%Y-%m-%d") if ms else None

print("== index_daily coverage by code (all) ==")
for r in q("select code, count(*) n, min(time) t0, max(time) t1, count(distinct time) nd, any_value(data_source) src from index_daily group by 1 order by 1"):
    print("  %-12s rows=%-7s %s .. %s dates=%-5s src=%s" % (r[0], r[1], d(r[2]), d(r[3]), r[4], r[5]))
print()
print("== index_daily 000300 monthly counts 2024-2026 ==")
for r in q("""select strftime(to_timestamp(time/1000),'%Y-%m') m, count(*) n, min(time) a, max(time) b from index_daily
              where code like '000300%' and time >= 1704067200000 group by 1 order by 1"""):
    print("  ", r[0], "n=%s" % r[1], d(r[2]), "..", d(r[3]))
print()
print("== stock_daily coverage ==")
for r in q("select count(*) n, min(time) t0, max(time) t1, count(distinct code) codes, count(distinct time) days from stock_daily"):
    print("  rows=%s %s .. %s codes=%s days=%s" % (r[0], d(r[1]), d(r[2]), r[3], r[4]))
print()
print("== complete snapshots per year for 000300 ==")
for r in q("""select strftime(to_timestamp(time/1000),'%Y') y, count(*) n from index_constituents_snapshot_meta
              where index_code like '000300%' and status='complete' group by 1 order by 1"""):
    print("  ", r[0], "complete=%s" % r[1])
print()
print("== trade_calendar coverage ==")
for r in q("select count(*) n, min(cal_date) a, max(cal_date) b, sum(case when is_open then 1 else 0 end) opens from trade_calendar"):
    print("  rows=%s %s .. %s open_days=%s" % (r[0], d(r[1]), d(r[2]), r[3]))
con.close()
