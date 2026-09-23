
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

def probe_1500(context):
    _probe(context, "15:00")

def probe_0931(context):
    _probe(context, "09:31")

def _last(df):
    try:
        if isinstance(df, dict):
            df = list(df.values())[0] if df else None
        if df is None or len(df) == 0:
            return "EMPTY"
        return str(df.index[-1])
    except Exception as e:
        return "ERR:" + str(e)[:60]

def _probe(context, tag):
    a = get_history(1, frequency="1d", field=["close"], security_list="600519.SS", fq="pre", include=False)
    b = get_history(1, frequency="1d", field=["close"], security_list="600519.SS", fq="pre", include=True)
    RESULTS.append((tag, str(context.blotter.current_dt), _last(a), _last(b)))
''')

tmp = Path(tempfile.mkdtemp(prefix="incprobe_"))
sp = tmp / "strategy.py"
sp.write_text(STRAT, encoding="utf-8")
res, outdir, eng = run_backtest(str(sp), "2025-07-01", "2025-07-02",
                                db_path=os.path.join(ROOT, "data", "quantstudio.db"),
                                match_price_mode="close", engine_profile="daily-open-close-proxy-v1")
mod = eng.strategy["initialize"].__globals__
print("RESULTS (tag, clock, include=False last index, include=True last index):")
for r in mod["RESULTS"]:
    print("   tag=%-6s clock=%s  incF_last=%-12s incT_last=%s" % r)
