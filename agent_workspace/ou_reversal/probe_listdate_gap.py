
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
con = duckdb.connect(r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db", read_only=True)
sql = """
with fb as (select code, min(time) t0 from stock_daily group by code)
select count(*) n,
       sum(case when b.list_date = fb.t0 then 1 else 0 end) exact_same,
       sum(case when fb.t0 > b.list_date then 1 else 0 end) later,
       max(case when fb.t0 > b.list_date then (fb.t0 - b.list_date)/86400000.0 else 0 end) max_gap_days,
       sum(case when fb.t0 > b.list_date and (fb.t0 - b.list_date)/86400000.0 > 10 then 1 else 0 end) gt10
from stock_basic b join fb on fb.code = b.code
where b.list_date >= 1514764800000
"""
print("listed-after-2018 stocks: n / same-day / first-bar-later / max gap days / >10d:", con.execute(sql).fetchone())
sql2 = """
with fb as (select code, min(time) t0 from stock_daily group by code)
select b.code, b.name, b.list_date, fb.t0, (fb.t0 - b.list_date)/86400000.0 gap
from stock_basic b join fb on fb.code = b.code
where b.list_date >= 1514764800000 and fb.t0 > b.list_date
order by gap desc limit 5
"""
def d(ms): return (datetime.datetime(1970,1,1)+datetime.timedelta(milliseconds=int(ms))+datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
for r in con.execute(sql2).fetchall():
    print("  %s %s list=%s first_bar=%s gap=%sd" % (r[0], r[1], d(r[2]), d(r[3]), r[4]))
con.close()
