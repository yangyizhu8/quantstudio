
import numpy as np

STRATEGY_ID = "probe_order_diag"
STRATEGY_NAME = "下单诊断探针"

CODES = ["600519.SS", "600036.SS", "601318.SS", "600030.SS", "601166.SS",
         "000001.SZ", "000002.SZ", "000651.SZ", "000333.SZ", "002415.SZ"]

def _ensure_runtime_state():
    if not hasattr(g, "done"):
        g.done = False

def initialize(context):
    _ensure_runtime_state()
    set_benchmark("000300.SS")

def handle_data(context, data):
    _ensure_runtime_state()
    if g.done:
        return
    g.done = True
    total = float(context.portfolio.portfolio_value)
    per = total * 0.97 / len(CODES)
    log.warning("PROBE total=%.2f per=%.2f cash=%.2f" % (total, per, float(context.portfolio.cash)))
    for code in CODES:
        o = order_target_value(code, per)
        st = getattr(o, "status", "?")
        rs = getattr(o, "reason", "?")
        fa = getattr(o, "filled_amount", "?")
        log.warning("PROBE_ORDER code=%s status=%s reason=%s filled_amount=%s" % (code, st, rs, fa))
    log.warning("PROBE cash_after=%.2f positions=%d" % (float(context.portfolio.cash), len(context.portfolio.positions)))
