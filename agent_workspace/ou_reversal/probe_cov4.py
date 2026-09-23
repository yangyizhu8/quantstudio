
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)
def show(label, sql):
    try:
        rows = con.execute(sql).fetchall()
        for r in rows[:30]:
            print(label, r)
    except Exception as e:
        print(label, "ERR:", str(e)[:400])
def d(ms):
    try: return datetime.datetime.utcfromtimestamp(int(ms)/1000).strftime("%Y-%m-%d")
    except Exception: return str(ms)

print("== complete 000300 snapshots ==")
rows = con.execute("select time, n_constituents from index_constituents_snapshot_meta where index_code='000300' and status='complete' order by time").fetchall()
print("  n_complete=%d" % len(rows))
print("  " + " | ".join("%s(%d)" % (d(t), n) for t, n in rows))
print()
print("== stock_daily isST non-null coverage by year ==")
show("  ", """select strftime(to_timestamp(time/1000),'%Y') y, count(*) tot,
       sum(case when isST is not null then 1 else 0 end) st_nn,
       sum(case when isST = 1 then 1 else 0 end) st_one,
       sum(case when is_st_reliable then 1 else 0 end) rel
       from stock_daily where time >= 1704067200000 group by 1 order by 1""")
print()
print("== stock_daily close_front non-null coverage by year ==")
show("  ", """select strftime(to_timestamp(time/1000),'%Y') y, count(*) tot,
       sum(case when close_front is not null then 1 else 0 end) cf_nn
       from stock_daily where time >= 1704067200000 group by 1 order by 1""")
print()
print("== stock_basic list_date coverage ==")
show("  ", "select count(*) n, sum(case when list_date is not null then 1 else 0 end) ld_nn, sum(case when list_status='L' then 1 else 0 end) listed from stock_basic")
con.close()
