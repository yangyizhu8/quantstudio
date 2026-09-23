
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
COPY = r"D:\miniQMT策略实盘\QuantStudio\agent_workspace\backtest_readonly\quantstudio.db"
MAIN_BACKUP = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio_backup_20260912.db"
def bj(ms):
    return (datetime.datetime(1970,1,1)+datetime.timedelta(milliseconds=int(ms))+datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
for label, p in (("copy(backtest_readonly)", COPY), ("data/quantstudio_backup_20260912", MAIN_BACKUP)):
    if not os.path.exists(p):
        print(label, "MISSING"); continue
    c = duckdb.connect(p, read_only=True)
    r = c.execute("select count(*), min(time), max(time) from index_daily where code='000300'").fetchone()
    print("%-34s index_daily 000300: rows=%s %s .. %s" % (label, r[0], bj(r[1]), bj(r[2])))
    # in-window coverage
    LO, HI = 1751299200000, 1788192000000
    r2 = c.execute("select count(distinct time) from index_daily where code='000300' and time >= ? and time <= ?", [LO, HI]).fetchone()
    cal = c.execute("select count(*) from trade_calendar where is_open and cal_date >= ? and cal_date <= ?", [LO, HI]).fetchone()
    print("%-34s 窗口内: 开市日=%s  指数日=%s" % ("", cal[0], r2[0]))
    # stock_daily in-window
    r3 = c.execute("select count(distinct time), min(time), max(time) from stock_daily where time >= ? and time <= ?", [LO, HI]).fetchone()
    print("%-34s stock_daily 窗口内: 日数=%s %s .. %s" % ("", r3[0], bj(r3[1]), bj(r3[2])))
    # 2026-08 index coverage
    r4 = c.execute("select count(*) from index_daily where code='000300' and time >= 1785196800000").fetchone()
    print("%-34s 2026-08-01 之后指数行数=%s" % ("", r4[0]))
    print()
    c.close()
