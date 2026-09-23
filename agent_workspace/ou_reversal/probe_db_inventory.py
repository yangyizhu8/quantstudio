
import os, glob, datetime, duckdb
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
def bj(ms):
    return (datetime.datetime(1970,1,1)+datetime.timedelta(milliseconds=int(ms))+datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
cands = sorted(glob.glob(os.path.join(ROOT, "data", "*.db"))) + sorted(glob.glob(os.path.join(ROOT, "agent_workspace", "backtest_readonly", "*.db")))
print("%-58s %8s  %-12s %-12s" % ("path", "GB", "idx000300_max", "stock_max"))
for p in cands:
    try:
        sz = os.path.getsize(p)/1024**3
    except Exception:
        continue
    line = "%-58s %8.2f" % (os.path.relpath(p, ROOT), sz)
    try:
        c = duckdb.connect(p, read_only=True)
        try:
            r = c.execute("select max(time) from index_daily where code='000300'").fetchone()
            line += "  %-12s" % (bj(r[0]) if r and r[0] else "n/a")
        except Exception as e:
            line += "  %-12s" % "no-table"
        try:
            r2 = c.execute("select max(time) from stock_daily").fetchone()
            line += "  %-12s" % (bj(r2[0]) if r2 and r2[0] else "n/a")
        except Exception:
            line += "  %-12s" % "n/a"
        c.close()
    except Exception as e:
        line += "  LOCKED/ERR: " + str(e)[:60]
    print(line)
