
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
con = duckdb.connect(r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db", read_only=True)
# identity test: close_front(t)/close_front(t-1) == close(t)/preClose(t)
sql = """
with s as (
  select code, time, close, preClose, close_front,
         lag(close_front) over (partition by code order by time) as pf,
         lag(close) over (partition by code order by time) as pc
  from stock_daily
  where time >= 1735660800000 and close_front is not null and close is not null
    and preClose is not null and preClose > 0
)
select count(*) n,
       sum(case when abs((close_front/pf) - (close/preClose)) <= 1e-9 then 1 else 0 end) exact,
       sum(case when abs((close_front/pf) - (close/preClose)) <= 1e-6 then 1 else 0 end) e6,
       sum(case when abs((close_front/pf) - (close/preClose)) <= 1e-4 then 1 else 0 end) e4
from s where pf is not null and pf > 0
"""
print("identity check (window >=2025-01-02):", con.execute(sql).fetchone())
# sample mismatches
sql2 = """
with s as (
  select code, time, close, preClose, close_front,
         lag(close_front) over (partition by code order by time) as pf,
         lag(close) over (partition by code order by time) as pc
  from stock_daily
  where time >= 1735660800000 and close_front is not null and close is not null
    and preClose is not null and preClose > 0
)
select code, time, close, preClose, close_front, pf,
       (close_front/pf) - (close/preClose) as d
from s where pf is not null and pf > 0 and abs((close_front/pf) - (close/preClose)) > 1e-6
order by abs(d) desc limit 8
"""
for r in con.execute(sql2).fetchall():
    print("  ", r[0], (datetime.datetime(1970,1,1)+datetime.timedelta(milliseconds=int(r[1]))+datetime.timedelta(hours=8)).strftime("%Y-%m-%d"),
          "close=%s preClose=%s front=%s pf=%s delta=%.3e" % (r[2], r[3], r[4], r[5], r[6]))
# ex-date rows specifically: preClose != prev close
sql3 = """
with s as (
  select code, time, close, preClose, close_front,
         lag(close_front) over (partition by code order by time) as pf,
         lag(close) over (partition by code order by time) as pc
  from stock_daily
  where time >= 1735660800000 and close_front is not null and close is not null
    and preClose is not null and preClose > 0
)
select count(*) ex_rows,
       sum(case when abs((close_front/pf) - (close/preClose)) <= 1e-6 then 1 else 0 end) ex_ok
from s where pf is not null and pf > 0 and pc is not null and abs(preClose - pc) > 1e-6
"""
print("ex-date subset:", con.execute(sql3).fetchone())
con.close()
