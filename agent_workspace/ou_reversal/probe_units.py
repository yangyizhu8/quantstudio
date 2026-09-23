
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
con = duckdb.connect(r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db", read_only=True)
def bj(ms):
    return (datetime.datetime(1970,1,1)+datetime.timedelta(milliseconds=int(ms))+datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
print("== 600519 recent rows: volume / amount / turn / pctChg ==")
for r in con.execute("""select time, close, volume, amount, turn, pctChg from stock_daily
                        where code='600519' order by time desc limit 4""").fetchall():
    print("  %s close=%s vol=%s amount=%s turn=%s pctChg=%s" % (bj(r[0]), r[1], r[2], r[3], r[4], r[5]))
print()
print("== 000001 平安银行 sample ==")
for r in con.execute("""select time, close, volume, amount, pctChg from stock_daily
                        where code='000001' order by time desc limit 3""").fetchall():
    print("  %s close=%s vol=%s amount=%s pctChg=%s" % (bj(r[0]), r[1], r[2], r[3], r[4]))
print()
print("== amount stats for 000300 members on a recent day ==")
r = con.execute("""select count(*), min(amount), median(amount), max(amount) from stock_daily
                   where time = (select max(time) from stock_daily where code='600519')""").fetchone()
print("  n=%s min=%s median=%s max=%s" % r)
con.close()
