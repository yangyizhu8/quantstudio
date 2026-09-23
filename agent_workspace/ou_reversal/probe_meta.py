
import json, os, duckdb
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
p = r"D:\miniQMT策略实盘\QuantStudio\skills\quantstudio-strategy-compiler\references\ptrade-api-signatures.json"
d = json.load(open(p, encoding="utf-8"))
print("logger_methods:", json.dumps(d.get("logger_methods"), ensure_ascii=False)[:800])
print()
print("runtime_module_imports:", json.dumps(d.get("runtime_module_imports"), ensure_ascii=False)[:800])
print()
print("portable_rules:", json.dumps(d.get("portable_rules"), ensure_ascii=False)[:1500])
print()
con = duckdb.connect(r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db", read_only=True)
r = con.execute("select count(*), count(distinct time), min(time), max(time) from index_daily where code='000300' and time >= 1735660800000 and time <= 1788192000000").fetchone()
print("index_daily 000300 in window: rows=%s days=%s min=%s max=%s" % r)
r2 = con.execute("select count(*) from trade_calendar where is_open and cal_date >= 1735660800000 and cal_date <= 1788192000000").fetchone()
print("calendar open days in window:", r2[0])
r3 = con.execute("select count(distinct time) from index_daily where code='000300' and time >= 1751328000000 and time <= 1788192000000").fetchone()
print("index_daily 000300 days 2025-07-01..2026-09-01:", r3[0])
con.close()
