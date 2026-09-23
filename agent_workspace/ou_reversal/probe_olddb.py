
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
def bj(ms):
    return (datetime.datetime(1970,1,1)+datetime.timedelta(milliseconds=int(ms))+datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
P = os.path.join(ROOT, "data", "quantstudio.old_20260920.db")
c = duckdb.connect(P, read_only=True)
LO, HI = 1751299200000, 1788192000000
n_tables = c.execute("select count(*) from information_schema.tables").fetchone()[0]
print("tables=%d" % n_tables)
cal = c.execute("select count(*) from trade_calendar where is_open and cal_date >= ? and cal_date <= ?", [LO, HI]).fetchone()[0]
idx = c.execute("select count(distinct time), min(time), max(time) from index_daily where code='000300' and time >= ? and time <= ?", [LO, HI]).fetchone()
sd  = c.execute("select count(distinct time) from stock_daily where time >= ? and time <= ?", [LO, HI]).fetchone()[0]
print("窗口内: 开市日=%d 指数日=%d (%s..%s) 股票日=%d" % (cal, idx[0], bj(idx[1]), bj(idx[2]), sd))
print("index_daily 000300 全量: rows=%s max=%s" % c.execute("select count(*), max(time) from index_daily where code='000300'").fetchone()[0:2])
# key tables present?
for t in ("index_constituents", "index_constituents_snapshot_meta", "stock_basic", "stock_daily", "index_daily", "trade_calendar"):
    ok = c.execute("select count(*) from information_schema.tables where table_name=?", [t]).fetchone()[0]
    print("   %-34s %s" % (t, "YES" if ok else "MISSING"))
# 000300 complete snapshots in window
rows = c.execute("""select count(*) from index_constituents_snapshot_meta
                    where index_code='000300' and status='complete' and time >= ? and time <= ?""", [LO, HI]).fetchone()[0]
print("窗口内 complete 快照数=%s" % rows)
# front value spot-check vs main-db measured value
r = c.execute("select close, close_front from stock_daily where code='600519' and time=1735747200000").fetchone()
print("600519 @2025-01-02: close=%s front=%s  (主库实测 1488.0 / 1401.7967454286804)" % r)
c.close()
