
import os, duckdb
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
MAIN = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
COPY = r"D:\miniQMT策略实盘\QuantStudio\agent_workspace\backtest_readonly\quantstudio.db"
def conn(p):
    return duckdb.connect(p, read_only=True)
try:
    cm = conn(MAIN)
    print("main: opened")
except Exception as e:
    cm = None
    print("main: BLOCKED ->", str(e)[:120])
cc = conn(COPY)
print("copy: opened")
for name, c in (("main", cm), ("copy", cc)):
    if c is None: continue
    r = c.execute("select max(time), count(*) from stock_daily").fetchone()
    print("  %s stock_daily max=%s rows=%s" % (name, r[0], r[1]))
    r2 = c.execute("select max(time) from index_daily where code='000300'").fetchone()
    print("  %s index_daily 000300 max=%s" % (name, r2[0]))
print()
# front-anchor comparison
if cm is not None:
    sql = """select code, time, close, close_front from stock_daily
             where code in ('600519','000001','601318') and time in (1751299200000, 1756339200000, 1767225600000)
             order by code, time"""
    a = cm.execute(sql).fetchall()
    b = cc.execute(sql).fetchall()
    same = 0
    for x, y in zip(a, b):
        d = "SAME" if (x[2] == y[2] and x[3] == y[3]) else "DIFF"
        if d == "SAME": same += 1
        print("  %-8s t=%s close %.4f/%.4f front %.6f/%.6f  %s" % (x[0], x[1], x[2], y[2], x[3], y[3], d))
    print("  identical rows: %d / %d" % (same, len(a)))
if cm is not None: cm.close()
cc.close()
