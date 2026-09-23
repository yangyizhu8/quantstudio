
import os, re, math, statistics
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
log = os.path.join(ROOT, "agent_workspace", "ou_reversal_csi300_10", "r5_run1.log")
raw = open(log, "rb").read()
txt = raw.decode("utf-16", errors="replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", errors="replace")
reb, port = {}, {}
for m in re.finditer(r"QS_REBALANCE_AUDIT rebalance_id=(\S+) date=(\S+) selected=(\d+) tradable=(\d+) sell_submitted=(\d+) buy_submitted=(\d+)", txt):
    reb[m.group(1)] = dict(date=m.group(2), selected=int(m.group(3)), tradable=int(m.group(4)), sell=int(m.group(5)), buy=int(m.group(6)))
for m in re.finditer(r"QS_PORTFOLIO_AUDIT rebalance_id=(\S+) date=(\S+) positions=(\d+) cash_ratio=([\d.]+) gross_exposure=([\d.]+)", txt):
    port[m.group(1)] = dict(date=m.group(2), positions=int(m.group(3)), cash=float(m.group(4)), gross=float(m.group(5)))
print("rebalance lines=%d | portfolio lines=%d | liquidation lines=%d" % (len(reb), len(port), len(re.findall(r"QS_LIQUIDATION", txt))))
print("ERROR/Traceback 行数: %d" % (len(re.findall(r"ERROR|Traceback", txt))))
print("QS_HISTORY_FAIL: %d | QS_UNIVERSE_FAIL: %d | QS_INDEX_BAR_FAIL: %d" % (
    len(re.findall(r"QS_HISTORY_FAIL", txt)), len(re.findall(r"QS_UNIVERSE_FAIL", txt)), len(re.findall(r"QS_INDEX_BAR_FAIL", txt))))
TARGET, FILL, MINEXP, MAXCASH = 10, 0.8, 0.8, 0.2
fails = []
for rid, r in reb.items():
    p = port.get(rid)
    if not p: fails.append((rid, "no QS_PORTFOLIO_AUDIT")); continue
    expected = min(TARGET, r["tradable"]); required = math.ceil(expected*FILL)
    if r["selected"] < expected: fails.append((rid, "selected %d < expected %d" % (r["selected"], expected)))
    if p["positions"] < required: fails.append((rid, "positions %d < required %d" % (p["positions"], required)))
    if p["gross"] < MINEXP: fails.append((rid, "gross %.4f < %.2f" % (p["gross"], MINEXP)))
    if p["cash"] > MAXCASH: fails.append((rid, "cash %.4f > %.2f" % (p["cash"], MAXCASH)))
print("r5_deployment_invariants FAILS: %d" % len(fails))
for rid, msg in fails[:8]: print("   ", rid, msg)
if port:
    v = list(port.values())
    print("positions: min=%d max=%d mean=%.2f" % (min(x["positions"] for x in v), max(x["positions"] for x in v), statistics.mean(x["positions"] for x in v)))
    print("gross    : min=%.4f max=%.4f mean=%.4f" % (min(x["gross"] for x in v), max(x["gross"] for x in v), statistics.mean(x["gross"] for x in v)))
    print("selected==10: %d / %d" % (sum(1 for r in reb.values() if r["selected"]==10), len(reb)))
# 对账
sub = sum(int(m.group(1)) for m in re.finditer(r"buy_submitted=(\d+)", txt))
bf = sum(int(m.group(1)) for m in re.finditer(r"buy_filled=(\d+)", txt))
br = sum(int(m.group(1)) for m in re.finditer(r"buy_rejected=(\d+)", txt))
print("对账(买): submitted=%d filled=%d rejected=%d 缺口=%d | QS_ZERO_ORDER=%d" % (sub, bf, br, sub-bf-br, len(re.findall(r"QS_ZERO_ORDER", txt))))
