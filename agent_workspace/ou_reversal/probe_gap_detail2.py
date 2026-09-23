
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
con = duckdb.connect(r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db", read_only=True)
def bj(ms):
    return (datetime.datetime(1970,1,1) + datetime.timedelta(milliseconds=int(ms)) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
# correct lower bound: Beijing midnight of 2025-07-01 = 1751299200000
LO, HI = 1751299200000, 1788192000000
cal = [r[0] for r in con.execute("select cal_date from trade_calendar where is_open and cal_date >= ? and cal_date <= ? order by 1", [LO, HI]).fetchall()]
idx = set(r[0] for r in con.execute("select distinct time from index_daily where code='000300' and time >= ? and time <= ?", [LO, HI]).fetchall())
miss = [c for c in cal if c not in idx]
print("window 2025-07-01..2026-09-01 : open_days=%d  index_days=%d  missing=%d %s" % (len(cal), len(idx), len(miss), [bj(m) for m in miss]))
print("first open day =", bj(cal[0]), "| last =", bj(cal[-1]))
print()
print("== 缺口影响定位（执行日 D / 信号日 S / 择时门消费末行）==")
def to_ms(dstr):
    d = datetime.datetime.strptime(dstr, "%Y-%m-%d")
    return int((d - datetime.datetime(1970,1,1)).total_seconds()*1000) - 8*3600*1000
for exec_day in ["2026-07-31", "2026-08-03", "2026-08-04", "2026-08-05"]:
    ms = to_ms(exec_day)
    p = con.execute("select max(cal_date) from trade_calendar where is_open and cal_date < ?", [ms]).fetchone()[0]
    row = con.execute("select max(time) from index_daily where code='000300' and time <= ?", [p + 86399000]).fetchone()[0]
    flag = "" if bj(p) == bj(row) else "  <== 缺口命中（消费行比信号日旧）"
    print("   D=%s  S=%s  末行=%s%s" % (exec_day, bj(p), bj(row), flag))
con.close()
