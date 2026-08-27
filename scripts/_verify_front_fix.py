# -*- coding: utf-8 -*-
import duckdb
import datetime

DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)

def dt(ms):
    return (datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc)
            + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")

# 1) 600519 6/15 15:00 front 应 = 1241.71（与日线 front 一致）
for q in [
    "SELECT code, time, close, close_front, close_back FROM stock_minutes WHERE code='600519' AND time=1781506800000",
    "SELECT code, time, close, close_front FROM stock_daily WHERE code='600519' AND time=1781452800000",
]:
    r = con.execute(q).fetchone()
    print("CHECK:", r)

# 2) 全市场：分钟末bar close_front vs 日线 close_front 不一致统计（QFQ 检查口径）
for tbl, daily in (("stock_minutes", "stock_daily"), ("etf_minutes", "etf_daily")):
    n_bad = con.execute(f"""
        WITH lastbar AS (
            SELECT code, time, close_front,
                   ((time + 28800000) // 86400000) AS day,
                   ROW_NUMBER() OVER (PARTITION BY code, ((time + 28800000) // 86400000) ORDER BY time DESC) AS rk
            FROM {tbl}
        )
        SELECT COUNT(*) FROM lastbar l
        JOIN {daily} d ON l.code = d.code AND ((d.time + 28800000) // 86400000) = l.day
        WHERE l.rk = 1 AND abs(l.close_front / d.close_front - 1) > 0.001
    """).fetchone()[0]
    print(f"[{tbl}] 末bar close_front vs 日线 close_front 不一致(>0.1%) 剩余: {n_bad}")

# 3) 600519 全窗口 front 一致性明细
rows = con.execute("""
    WITH lastbar AS (
        SELECT code, time, close_front,
               ((time + 28800000) // 86400000) AS day,
               ROW_NUMBER() OVER (PARTITION BY code, ((time + 28800000) // 86400000) ORDER BY time DESC) AS rk
        FROM stock_minutes WHERE code='600519'
    )
    SELECT l.time, l.close_front, d.close_front AS daily_front
    FROM lastbar l JOIN stock_daily d ON d.code=l.code AND ((d.time+28800000)//86400000)=l.day
    WHERE l.rk=1 ORDER BY l.time
""").fetchall()
for r in rows:
    print("600519", dt(r[0]), "minute_front=", round(r[1], 4), "daily_front=", round(r[2], 4),
          "dev=%.4f%%" % ((r[1]/r[2]-1)*100))
con.close()
