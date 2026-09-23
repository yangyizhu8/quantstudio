
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
con = duckdb.connect(r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db", read_only=True)
def bj(ms):
    return (datetime.datetime(1970,1,1) + datetime.timedelta(milliseconds=int(ms)) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
print("== calendar open days 2026-07-28 .. 2026-08-10 ==")
cal = [r[0] for r in con.execute("select cal_date from trade_calendar where is_open and cal_date >= 1785196800000 and cal_date <= 1786406400000 order by cal_date").fetchall()]
idx = set(r[0] for r in con.execute("select distinct time from index_daily where code='000300' and time >= 1785196800000 and time <= 1786406400000").fetchall())
for c in cal:
    print("   %s  index_daily=%s" % (bj(c), "YES" if c in idx else "*** MISSING ***"))
print()
print("== full-window gap list (2025-07-01..2026-09-01) ==")
cal2 = [r[0] for r in con.execute("select cal_date from trade_calendar where is_open and cal_date >= 1751328000000 and cal_date <= 1788192000000 order by 1").fetchall()]
idx2 = set(r[0] for r in con.execute("select distinct time from index_daily where code='000300' and time >= 1751328000000 and time <= 1788192000000").fetchall())
miss = [c for c in cal2 if c not in idx2]
print("   open days=%d, index days=%d, missing=%d -> %s" % (len(cal2), len(idx2), len(miss), [bj(m) for m in miss]))
print()
print("== what the timing gate consumes on each execution day near the gap ==")
def prev_open(ms):
    r = con.execute("select max(cal_date) from trade_calendar where is_open and cal_date < ?", [ms]).fetchone()
    return r[0] if r else None
for exec_day in ["2026-07-31", "2026-08-03", "2026-08-04", "2026-08-05"]:
    d = datetime.datetime.strptime(exec_day, "%Y-%m-%d")
    ms = int((d - datetime.datetime(1970,1,1)).total_seconds()*1000) - 8*3600*1000  # bj midnight -> utc ms
    # prev trading day
    p = con.execute("select max(cal_date) from trade_calendar where is_open and cal_date < ?", [ms]).fetchone()[0]
    # index row available at or before prev day end
    row = con.execute("select max(time) from index_daily where code='000300' and time <= ?", [p + 86399000]).fetchone()[0]
    print("   执行日 %s -> 信号日 S=%s ; 择时门消费末行 = %s" % (exec_day, bj(p), bj(row)))
con.close()
