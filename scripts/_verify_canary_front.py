# -*- coding: utf-8 -*-
import duckdb
import datetime

DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)

def dt(ms):
    return (datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc)
            + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

CANARY = ["000001", "000651", "000858", "002107", "300750", "600000", "600519", "601398", "601628"]
ph = ",".join("?" * len(CANARY))

# canary codes 全窗口末bar front vs 日线 front 不一致明细（QFQ 检查口径）
rows = con.execute(f"""
    WITH lastbar AS (
        SELECT code, time, close_front,
               ((time + 28800000) // 86400000) AS day,
               ROW_NUMBER() OVER (PARTITION BY code, ((time + 28800000) // 86400000) ORDER BY time DESC) AS rk
        FROM stock_minutes WHERE code IN ({ph})
    )
    SELECT l.code, l.time, l.close_front, d.close_front AS df, abs(l.close_front/d.close_front-1) AS dev
    FROM lastbar l JOIN stock_daily d ON d.code=l.code AND ((d.time+28800000)//86400000)=l.day
    WHERE l.rk=1 AND abs(l.close_front/d.close_front-1) > 0.001
    ORDER BY l.code, l.time""", CANARY).fetchall()
print(f"canary 9 codes 末bar front 不一致(>0.1%) 共 {len(rows)} 个:")
for r in rows:
    print(" ", r[0], dt(r[1]), "minute_front=", round(r[2],3), "daily_front=", round(r[3],3),
          "dev=%.3f%%" % (r[4]*100))
con.close()
