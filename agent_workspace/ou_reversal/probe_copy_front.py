
import os, duckdb
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
COPY = r"D:\miniQMT策略实盘\QuantStudio\agent_workspace\backtest_readonly\quantstudio.db"
c = duckdb.connect(COPY, read_only=True)
# 与主库早前实测值对照（主库 2025-01-02 600519: close=1488.0 front=1401.7967454286804）
rows = c.execute("""select code, time, close, close_front from stock_daily
                    where code='600519' and time in (1735747200000, 1751299200000) order by time""").fetchall()
print("copy 600519 rows:")
for r in rows:
    print("   t=%s close=%s front=%s" % (r[1], r[2], r[3]))
print()
print("主库早前实测（本会话 probe_front_basis 输出）：")
print("   t=1735747200000 close=1488.0 front=1401.7967454286804")
print("   t=1751299200000 close=?  front=? (未取)")
print()
r = c.execute("select max(time), count(*) from stock_daily").fetchone()
print("copy stock_daily max=%s rows=%s" % (r[0], r[1]))
c.close()
