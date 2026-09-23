
import os, sys, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, ROOT)
os.environ["QUANTSTUDIO_DATA_ROOT"] = os.path.join(ROOT, "data")
import duckdb, pandas as pd
con = duckdb.connect(os.path.join(ROOT, "data", "quantstudio.db"), read_only=True)
def d(ms):
    return (datetime.datetime(1970,1,1)+datetime.timedelta(milliseconds=int(ms))+datetime.timedelta(hours=8)).strftime("%Y-%m-%d") if ms else None
print("stock_basic.list_date samples:")
for c in ("600519","000001","301618","688501","920130"):
    r = con.execute("select code, name, list_date, list_status from stock_basic where code=?", [c]).fetchone()
    print("  ", c, r, "->", d(r[2]) if r and r[2] else None)
print()
print("stock_daily first bar:")
for c in ("600519","301618"):
    r = con.execute("select min(time) from stock_daily where code=?", [c]).fetchone()
    print("  ", c, "->", d(r[0]))
con.close()
from pathlib import Path
from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider
p = DuckDBReferenceDataProvider(Path(os.path.join(ROOT, "data", "quantstudio.db")))
for c in ("600519","301618","688501"):
    info = p.get_security_info(c)
    print("  get_security_info(%s) ->" % c, {k: str(v) for k, v in (info or {}).items()})
