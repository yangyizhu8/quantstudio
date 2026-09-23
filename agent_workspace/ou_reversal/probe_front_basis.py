
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
con = duckdb.connect(r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db", read_only=True)
def bj(ms):
    return (datetime.datetime(1970,1,1) + datetime.timedelta(milliseconds=int(ms)) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

print("== 600519 close vs close_front vs ratio (ex-date check) ==")
rows = con.execute("""select time, close, preClose, close_front, close_front_ratio from stock_daily
                      where code='600519' and time >= 1735660800000 order by time""").fetchall()
prev = None
nex = 0
for t, c, pc, cf, rt in rows:
    diff = None if cf is None else round(cf - c, 4)
    if abs(diff or 0) > 1e-6:
        nex += 1
    if prev is None or abs((cf - c) if cf else 0) > 1e-6:
        print("  %s close=%s preClose=%s front=%s ratio=%s diff=%s" % (bj(t), c, pc, cf, rt, diff))
    prev = (t, c, pc, cf, rt)
print("  rows=%d, rows where front != close: %d" % (len(rows), nex))
print()
print("== ratio jump test: ratio(t)/ratio(t-1) vs close(t-1)/preClose(t) ==")
bad = 0
checked = 0
for i in range(1, len(rows)):
    t, c, pc, cf, rt = rows[i]
    pt, pc_, ppc, pcf, prt = rows[i-1]
    if rt is None or prt is None or not ppc:
        continue
    checked += 1
    lhs = rt / prt
    rhs = pc_ / pc if pc else None
    if rhs is None or abs(lhs - rhs) > 1e-6:
        bad += 1
        if bad <= 5:
            print("  MISMATCH %s lhs=%.8f rhs=%.8f" % (bj(t), lhs, rhs))
print("  checked=%d mismatches=%d" % (checked, bad))
print()
print("== 000858 (五粮液) front vs raw sample ==")
for r in con.execute("""select time, close, close_front, close_front_ratio from stock_daily
                        where code='000858' and time >= 1735660800000 order by time limit 5""").fetchall():
    print("  ", bj(r[0]), r[1], r[2], r[3])
con.close()
