
import os, sys, tempfile, textwrap
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, ROOT)
os.environ["QUANTSTUDIO_DATA_ROOT"] = os.path.join(ROOT, "data")
from pathlib import Path
from quantstudio.backtest.run_ptrade_strategy import run_backtest

STRAT = textwrap.dedent('''
import numpy as np
import pandas as pd
STRATEGY_ID = "probe_include_semantics"
STRATEGY_NAME = "探针"
RESULTS = []

def initialize(context):
    set_benchmark("000300.SS")
    run_daily(context, probe_1500, time="15:00")
    run_daily(context, probe_0931, time="09:31")

def probe_1500(context): _probe(context, "15:00")
def probe_0931(context): _probe(context, "09:31")

def _desc(df, tag):
    if isinstance(df, dict):
        dd = df
    else:
        dd = {"_": df}
    out = []
    for k, v in dd.items():
        try:
            out.append("key=%s len=%d idx=%s cols=%s last_row=%s" % (
                k, len(v), list(v.index)[:2], list(v.columns)[:8],
                v.iloc[-1].to_dict() if len(v) else None))
        except Exception as e:
            out.append("key=%s ERR %s" % (k, e))
    return tag + " || " + " ;; ".join(out)

def _probe(context, tag):
    a = get_history(2, frequency="1d", field=["close","trade_date"], security_list="600519.SS", fq="pre", include=False, is_dict=True)
    b = get_history(2, frequency="1d", field=["close","trade_date"], security_list="600519.SS", fq="pre", include=True, is_dict=True)
    RESULTS.append((tag, str(context.blotter.current_dt), _desc(a, "incF"), _desc(b, "incT")))
''')

tmp = Path(tempfile.mkdtemp(prefix="incprobe_"))
sp = tmp / "strategy.py"
sp.write_text(STRAT, encoding="utf-8")
res, outdir, eng = run_backtest(str(sp), "2025-07-01", "2025-07-02",
                                db_path=os.path.join(ROOT, "data", "quantstudio.db"),
                                match_price_mode="close", engine_profile="daily-open-close-proxy-v1")
mod = eng.strategy["initialize"].__globals__
for r in mod["RESULTS"]:
    print("tag=%s clock=%s" % (r[0], r[1]))
    print("   ", r[2])
    print("   ", r[3])
