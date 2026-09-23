
import os, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)
def show(label, sql):
    try:
        rows = con.execute(sql).fetchall()
        print(label, "->", rows[:6])
    except Exception as e:
        print(label, "-> ERR:", str(e)[:500])
show("count", "select count(*) from stock_daily")
show("minmax", "select min(time), max(time) from stock_daily")
show("codes", "select count(distinct code) from stock_daily")
show("days", "select count(distinct time) from stock_daily")
show("sample", "select code, time, close, preClose, suspendFlag, isST, close_front from stock_daily where code like '600519%' order by time desc limit 3")
con.close()
