
import os, json, duckdb, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
con = duckdb.connect(DB, read_only=True)
def q(sql):
    try: return con.execute(sql).fetchall()
    except Exception as e: return [["ERR", str(e)[:300]]]
def d(ms):
    return datetime.datetime.utcfromtimestamp(ms/1000).strftime("%Y-%m-%d") if ms else None

print("== index_constituents cols ==")
print(q("select column_name, data_type from information_schema.columns where table_name='index_constituents' order by ordinal_position"))
print()
print("== index_constituents index_code coverage (top 15 by rows) ==")
for r in q("select index_code, count(*) n, min(time) t0, max(time) t1, count(distinct time) nd from index_constituents group by 1 order by 2 desc limit 15"):
    print(" ", r[0], "rows=%s" % r[1], d(r[2]), "..", d(r[3]), "dates=%s" % r[4])
print()
print("== snapshot_meta for 000300 ==")
for r in q("select index_code, time, n_constituents, expected_count, status, data_source from index_constituents_snapshot_meta where index_code like '000300%' order by time"):
    print(" ", r[0], d(r[1]), "n=%s exp=%s status=%s src=%s" % (r[2], r[3], r[4], r[5]))
print()
print("== snapshot_meta all index_code summary ==")
for r in q("select index_code, count(*) nd, min(time) t0, max(time) t1, sum(case when status='ok' then 1 else 0 end) nok from index_constituents_snapshot_meta group by 1 order by 2 desc limit 15"):
    print(" ", r[0], "dates=%s" % r[1], d(r[2]), "..", d(r[3]), "status_ok=%s" % r[4])
con.close()
