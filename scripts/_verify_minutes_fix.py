# -*- coding: utf-8 -*-
import duckdb
import datetime

DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB)

def dt(ms):
    return (datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc)
            + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")

# 1) 600519 6/15 15:00 应还原为 1271.1
for q in [
    "SELECT code, time, open, high, low, close FROM stock_minutes WHERE code='600519' AND time=1781506800000",
    "SELECT code, time, close FROM stock_minutes WHERE code='600519' AND time=1781571600000",
    "SELECT code, time, close FROM etf_minutes WHERE code='510500' AND time=1778144400000",
]:
    r = con.execute(q).fetchone()
    print("CHECK:", r, "->", dt(r[1]) if r else None)

# 2) 全市场收盘bar与日线不一致统计（还原后应大幅下降）
for tbl, daily in (("stock_minutes", "stock_daily"), ("etf_minutes", "etf_daily")):
    n_bad = con.execute(f"""
        WITH lastbar AS (
            SELECT code, time, close,
                   ((time + 28800000) // 86400000) AS day,
                   ROW_NUMBER() OVER (PARTITION BY code, ((time + 28800000) // 86400000) ORDER BY time DESC) AS rk
            FROM {tbl}
        )
        SELECT COUNT(*) FROM lastbar l
        JOIN {daily} d ON l.code = d.code AND ((d.time + 28800000) // 86400000) = l.day
        WHERE l.rk = 1 AND abs(l.close / d.close - 1) > 0.001
    """).fetchone()[0]
    print(f"[{tbl}] 收盘bar与日线不一致(>0.1%) 剩余: {n_bad}")

# 3) 600519 6/15 全窗口 15:00 一致性明细
rows = con.execute("""
    WITH lastbar AS (
        SELECT code, time, close,
               ((time + 28800000) // 86400000) AS day,
               ROW_NUMBER() OVER (PARTITION BY code, ((time + 28800000) // 86400000) ORDER BY time DESC) AS rk
        FROM stock_minutes WHERE code='600519'
    )
    SELECT l.time, l.close, d.close AS daily
    FROM lastbar l JOIN stock_daily d ON d.code=l.code AND ((d.time+28800000)//86400000)=l.day
    WHERE l.rk=1 ORDER BY l.time
""").fetchall()
for r in rows:
    print("600519", dt(r[0]), "minute=", r[1], "daily=", r[2],
          "dev=%.4f%%" % ((r[1]/r[2]-1)*100))
con.close()
