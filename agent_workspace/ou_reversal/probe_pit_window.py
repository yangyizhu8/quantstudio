
import os, sys
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, ROOT)
os.environ["QUANTSTUDIO_DATA_ROOT"] = os.path.join(ROOT, "data")
from pathlib import Path
import pandas as pd
from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.providers.duckdb_provider import _end_ms

acc = DuckDBDataAccess(Path(os.path.join(ROOT, "data", "quantstudio.db")))
def ds(ms):
    return pd.Timestamp(int(ms), unit="ms").tz_localize("UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d")

# trading days from calendar table (open only) within window
import duckdb
con = duckdb.connect(os.path.join(ROOT, "data", "quantstudio.db"), read_only=True)
rows = con.execute("""select cal_date from trade_calendar where is_open and cal_date >= 1735660800000 and cal_date <= 1788192000000 order by cal_date""").fetchall()
days = [pd.Timestamp(int(r[0]), unit="ms").tz_localize("UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d") for r in rows]
print("trading days in window:", len(days), days[0], "..", days[-1])
used = {}
for t in days:
    df = acc.query_index_constituents("000300", _end_ms(t))
    k = ds(int(df["time"].iloc[0])) if not df.empty else "NONE"
    used.setdefault(k, []).append(t)
print()
print("%-12s %-6s %s" % ("snapshot", "days", "range"))
for k in sorted(used):
    v = used[k]
    print("%-12s %-6d %s .. %s" % (k, len(v), v[0], v[-1]))
print()
stale = sum(len(v) for k, v in used.items() if k < "2025-01-01")
print("trading days using a snapshot older than the window start:", stale)
