# -*- coding: utf-8 -*-
"""因子表 time 口径诊断：159307 因子 time 分布 vs 主库日线 time。"""
import io
import sqlite3
import sys

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ac = sqlite3.connect("data/qfq_aux.db")
ac.execute("PRAGMA query_only = ON")
rows = ac.execute(
    "SELECT time, adj_factor FROM fund_adj WHERE code='159307' "
    "AND time BETWEEN 1779750000000 AND 1780000000000 ORDER BY time").fetchall()
ac.close()
print("159307 因子（05-25~05-28 窗口）:")
for t, v in rows:
    # t 的 UTC 日期与上海日期 + 上海墙钟时刻
    import datetime as dt
    utc_d = dt.datetime.fromtimestamp(t / 1000, tz=dt.timezone.utc)
    sh_d = utc_d.astimezone(dt.timezone(dt.timedelta(hours=8)))
    print(f"  time={t} utc={utc_d.strftime('%Y-%m-%d %H:%M')} sh={sh_d.strftime('%Y-%m-%d %H:%M')} adj={v}")

con = duckdb.connect("data/quantstudio.db", read_only=True)
main_rows = con.execute(
    "SELECT time, close FROM etf_daily WHERE code='159307' "
    "AND time BETWEEN 1779750000000 AND 1780000000000 ORDER BY time").fetchall()
print("159307 主库日线（同窗口）:")
for t, c in main_rows:
    import datetime as dt
    utc_d = dt.datetime.fromtimestamp(t / 1000, tz=dt.timezone.utc)
    sh_d = utc_d.astimezone(dt.timezone(dt.timedelta(hours=8)))
    print(f"  time={t} utc={utc_d.strftime('%Y-%m-%d %H:%M')} sh={sh_d.strftime('%Y-%m-%d %H:%M')} close={c}")
con.close()
