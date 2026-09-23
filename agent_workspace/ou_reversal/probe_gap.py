
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
con = duckdb.connect(r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db", read_only=True)
def bj(ms):
    return (datetime.datetime(1970,1,1) + datetime.timedelta(milliseconds=int(ms)) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
cal = [r[0] for r in con.execute("select cal_date from trade_calendar where is_open and cal_date >= 1735660800000 and cal_date <= 1788192000000 order by 1").fetchall()]
idx = set(r[0] for r in con.execute("select distinct time from index_daily where code='000300' and time >= 1735660800000 and time <= 1788192000000").fetchall())
# index_daily time is Beijing-midnight? test with a known date
sample = con.execute("select time, close from index_daily where code='000300' order by time desc limit 3").fetchall()
print("index_daily sample:", [(t, bj(t), c) for t, c in sample])
missing = [c for c in cal if c not in idx]
print("calendar open days:", len(cal), "index days:", len(idx))
print("missing dates (raw ms -> bj):", [(m, bj(m)) for m in missing])
print()
print("stock_daily missing in window:")
sd = set(r[0] for r in con.execute("select distinct time from stock_daily where time >= 1735660800000 and time <= 1788192000000").fetchall())
print("  stock_daily days:", len(sd), "missing:", [(m, bj(m)) for m in [c for c in cal if c not in sd]])
con.close()
