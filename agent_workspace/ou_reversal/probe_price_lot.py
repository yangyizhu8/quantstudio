
import os, sys, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, ROOT)
os.environ["QUANTSTUDIO_DATA_ROOT"] = os.path.join(ROOT, "data")
from pathlib import Path
import duckdb, pandas as pd
con = duckdb.connect(os.path.join(ROOT, "data", "quantstudio.db"), read_only=True)

# per-trading-day count of CSI300 members with front close > 97 (1 lot > per_target ~9700)
sql = """
with u as (
  select m.time as s_ms, i.code as code
  from index_constituents_snapshot_meta m
  join index_constituents i on i.index_code = m.index_code and i.time = m.time
  where m.index_code = '000300' and m.status = 'complete'
),
d as (
  select code, time, close_front from stock_daily where time >= 1751299200000 and time <= 1788192000000
)
select u.s_ms, count(distinct d.code) as members,
       sum(case when d.close_front > 97.0 then 1 else 0 end) as gt97
from u join d on d.code = u.code and d.time = u.s_ms
group by 1 order by 1
"""
def bj(ms):
    return (datetime.datetime(1970,1,1)+datetime.timedelta(milliseconds=int(ms))+datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
rows = con.execute(sql).fetchall()
print("snapshot_date  members  >97元  占比")
for s, n, g in rows:
    print("  %s  %4d  %4d  %.1f%%" % (bj(s), n, g, 100.0*g/max(n,1)))
con.close()
