
import os, sys, datetime
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, ROOT)
os.environ["QUANTSTUDIO_DATA_ROOT"] = os.path.join(ROOT, "data")
from pathlib import Path
import pandas as pd
from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.providers.duckdb_provider import _end_ms

DB = os.path.join(ROOT, "data", "quantstudio.db")
acc = DuckDBDataAccess(Path(DB))

def ds(ms):
    return pd.Timestamp(int(ms), unit="ms").tz_localize("UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d")

tests = ["2024-12-31", "2025-01-02", "2025-03-15", "2025-06-29", "2025-06-30", "2025-07-01",
         "2025-08-31", "2025-10-08", "2026-08-31", "2021-04-01", "2021-03-29"]
print("%-12s %-6s %-12s %s" % ("query_date", "n", "snapshot", "leak_future?"))
for t in tests:
    df = acc.query_index_constituents("000300", _end_ms(t))
    if df.empty:
        print("%-12s %-6s %-12s" % (t, 0, "NONE"))
        continue
    snap = int(df["time"].iloc[0])
    snap_s = ds(snap)
    leak = "YES" if snap_s > t else "no"
    print("%-12s %-6s %-12s %s" % (t, len(df), snap_s, leak))
print()
# history-union check: same date twice deterministic; different dates differ
a = acc.query_index_constituents("000300", _end_ms("2025-01-02"))["code"].tolist()
b = acc.query_index_constituents("000300", _end_ms("2025-07-01"))["code"].tolist()
c = acc.query_index_constituents("000300", _end_ms("2025-01-02"))["code"].tolist()
print("deterministic:", a == c, "| differs_across_dates:", a != b, "| n_a=%d n_b=%d" % (len(a), len(b)))
# gap enumeration: for every trading day in 2025-01..2025-07 report distinct snapshot used
days = pd.date_range("2025-01-02", "2025-07-04", freq="B").strftime("%Y-%m-%d").tolist()
used = {}
for t in days:
    df = acc.query_index_constituents("000300", _end_ms(t))
    k = ds(int(df["time"].iloc[0])) if not df.empty else "NONE"
    used.setdefault(k, []).append(t)
print()
print("snapshot used per trading day (2025-01-02..2025-07-04):")
for k, v in sorted(used.items()):
    print("   %s -> %d days [%s .. %s]" % (k, len(v), v[0], v[-1]))
