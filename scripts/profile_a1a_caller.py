
"""A1a ② 逐段分解：_stamp_and_write 每次调用的写段外开销（真实载荷，影子库只读）。"""
import sys, time, traceback
from pathlib import Path
import duckdb, pandas as pd
ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
from quantstudio.pipeline.daemon import ResidentCollector

SHADOW = ROOT / "data" / "staging_shadow_20260913" / "quantstudio.db"
TABLE = "etf_minutes"; CST = 57600000
cs = duckdb.connect(str(SHADOW), read_only=True)
day = cs.execute("SELECT (time - ((time - ?) % 86400000)) d, COUNT(*) c FROM etf_minutes "
                 "GROUP BY 1 ORDER BY c DESC LIMIT 1", [CST]).fetchone()[0]
pdf = cs.execute("SELECT * FROM etf_minutes WHERE time >= ? AND time < ?",
                 [day, day + 86400000]).df()
print("PAYLOAD_ROWS =", len(pdf), " COLS =", len(pdf.columns))
cs.close()

R = {}
t0 = time.perf_counter(); _ = pdf.copy(); R["df.copy()"] = time.perf_counter() - t0
t0 = time.perf_counter(); _ = pdf["code"].astype(str); R["astype(str) 单列"] = time.perf_counter() - t0

rc = ResidentCollector.__new__(ResidentCollector)
try:
    from quantstudio.pipeline.aligner import FieldAligner
    rc.aligner = FieldAligner.from_config(ROOT / "config/profiles/mcp_only/alignment_rules.json")
    print("aligner OK; schema keys =", len(rc.aligner.schemas))
except Exception as e:
    print("aligner FAIL:", type(e).__name__, e)
rc._qfq_invariant_state = {}
try:
    import functools
    from quantstudio.pipeline.qfq_maintenance import QFQMaintenance
    print("qfq_maintenance import OK")
except Exception as e:
    print("qfq import note:", type(e).__name__)

for name, fn in [
    ("_table_columns(静态查表)", lambda: rc.writer._table_columns(TABLE) if hasattr(rc, "writer") and rc.writer else None),
]:
    try:
        t0 = time.perf_counter(); fn(); R[name] = time.perf_counter() - t0
    except Exception as e:
        print(name, "skip:", type(e).__name__, str(e)[:80])

# 静态查表单独测（不依赖实例）
from quantstudio.pipeline.writers import DuckDBWriter
t0 = time.perf_counter()
for _ in range(1000):
    DuckDBWriter._table_columns(TABLE)
R["_table_columns x1000"] = time.perf_counter() - t0

print()
print("%-34s %10s" % ("SEGMENT", "SECONDS"))
print("-" * 46)
for k, v in R.items():
    print("%-34s %10.4f" % (k, v))
print()
print("NOTE: 若分片数为 S，则 df.copy() 总成本 ≈ %.3fs x S" % R["df.copy()"])
