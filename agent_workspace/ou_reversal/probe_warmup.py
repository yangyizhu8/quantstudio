
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)
def d(ms):
    try: return datetime.datetime.utcfromtimestamp(int(ms)/1000).strftime("%Y-%m-%d")
    except Exception: return str(ms)

print("== trade_calendar ==")
r = con.execute("select count(*), min(cal_date), max(cal_date), sum(case when is_open then 1 else 0 end) from trade_calendar").fetchone()
print("   rows=%s %s..%s open=%s" % (r[0], d(r[1]), d(r[2]), r[3]))
print()
# warm-up window: trading days 2024-10-01 .. 2024-12-31
for snap in ("2021-03-30", "2025-06-30"):
    t = con.execute("select time from index_constituents_snapshot_meta where index_code='000300' and status='complete' and time = (select min(time) from index_constituents_snapshot_meta where index_code='000300' and status='complete' and strftime(to_timestamp(time/1000),'%Y-%m-%d') = ?)", [snap]).fetchone()
    if not t:
        print("snapshot %s not found" % snap); continue
    st = t[0]
    codes = [x[0] for x in con.execute("select code from index_constituents where index_code='000300' and time=?", [st]).fetchall()]
    print("== universe snapshot %s : %d codes ==" % (snap, len(codes)))
    # bars in warm-up window per member
    q = """
    select count(*) as members,
           sum(case when n >= 61 then 1 else 0 end) as ge61,
           sum(case when n >= 40 then 1 else 0 end) as ge40,
           min(n) as minn, max(n) as maxn
    from (
      select code, count(*) as n from stock_daily
      where code in (select code from index_constituents where index_code='000300' and time=?)
        and time >= 1727740800000 and time <= 1735660800000
        and close is not null
      group by code
    ) t
    """
    rows = con.execute(q, [st]).fetchall()
    print("   warm-up(2024-10-01..2024-12-31) members=%s ge61=%s ge40=%s min=%s max=%s" % rows[0])
    # coverage of close_front in warm-up
    rows2 = con.execute("""
      select count(*) from stock_daily where code in (select code from index_constituents where index_code='000300' and time=?)
        and time >= 1727740800000 and time <= 1735660800000 and close_front is not null
    """, [st]).fetchone()
    print("   warm-up rows close_front non-null = %s" % rows2[0])
    # amount non-null
    rows3 = con.execute("""
      select count(*), sum(case when amount is not null and amount>0 then 1 else 0 end)
      from stock_daily where code in (select code from index_constituents where index_code='000300' and time=?)
        and time >= 1727740800000 and time <= 1735660800000
    """, [st]).fetchone()
    print("   warm-up rows=%s amount>0 = %s" % (rows3[0], rows3[1]))
    print()
con.close()
