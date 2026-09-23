
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)
def show(label, sql, params=None):
    try:
        for r in con.execute(sql, params or []).fetchall()[:40]:
            print("  ", label, r)
    except Exception as e:
        print("  ", label, "ERR:", str(e)[:300])

print("== is_st_reliable_source distribution 2025-2026 ==")
show("", """select is_st_reliable_source, is_st_reliable, count(*) from stock_daily
            where time >= 1735660800000 group by 1,2 order by 3 desc""")
print()
print("== is_delisting_risk coverage ==")
show("", """select is_delisting_risk, count(*) from stock_daily where time >= 1735660800000 group by 1""")
print()
print("== amount / volume non-null in window ==")
show("", """select count(*) tot, sum(case when amount>0 then 1 else 0 end) amt_pos,
           sum(case when volume>0 then 1 else 0 end) vol_pos, sum(case when suspendFlag=1 then 1 else 0 end) susp
           from stock_daily where time >= 1735660800000 and time <= 1788192000000""")
print()
print("== index_daily 000300 in window ==")
show("", """select count(*) rows, count(distinct time) days, min(time), max(time) from index_daily
           where code='000300' and time >= 1735660800000 and time <= 1788192000000""")
print()
# warm-up: 2025-07-01 universe members, bars in 2025-01-02..2025-06-30
snap = con.execute("select max(time) from index_constituents_snapshot_meta where index_code='000300' and status='complete' and time <= 1751328000000").fetchone()[0]
print("== warm-up check: snapshot <= 2025-07-01 is", snap, "==")
show("", """select count(*) members, sum(case when n>=61 then 1 else 0 end) ge61,
           sum(case when n>=40 then 1 else 0 end) ge40, min(n), max(n) from (
   select code, count(*) n from stock_daily
   where code in (select code from index_constituents where index_code='000300' and time=?)
     and time >= 1735660800000 and time < 1751328000000 and close_front is not null
   group by code) t""", [snap])
print()
print("== full-history bars available before window start for those members ==")
show("", """select count(*) members, sum(case when n>=120 then 1 else 0 end) ge120, min(n), max(n) from (
   select code, count(*) n from stock_daily
   where code in (select code from index_constituents where index_code='000300' and time=?)
     and time < 1735660800000 and close_front is not null
   group by code) t""", [snap])
con.close()
