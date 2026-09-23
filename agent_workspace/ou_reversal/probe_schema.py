
import os, json, duckdb
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)

def q(sql):
    try:
        return con.execute(sql).fetchall()
    except Exception as e:
        return [["ERR", str(e)[:300]]]

tables = [r[0] for r in q("select table_name from information_schema.tables order by 1")]
print("TABLES(%d): %s" % (len(tables), ", ".join(tables)))
print()
for t in ("stock_daily", "index_daily", "index_constituents_snapshot",
          "index_constituents_snapshot_meta", "stock_basic", "etf_basic",
          "trade_calendar", "stock_status", "stock_delist", "industry_membership"):
    if t not in tables:
        print("== %s : ABSENT" % t); continue
    cols = q("select column_name, data_type from information_schema.columns where table_name='%s' order by ordinal_position" % t)
    print("== %s cols: %s" % (t, ", ".join("%s:%s" % (c[0], c[1]) for c in cols)))
con.close()
