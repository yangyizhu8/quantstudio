
import os, re, math, statistics
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
log = os.path.join(ROOT, "agent_workspace", "ou_reversal", "rerun_2m.log")
raw = open(log, "rb").read()
txt = raw.decode("utf-16", errors="replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", errors="replace")
reb, port = {}, {}
for m in re.finditer(r"QS_REBALANCE_AUDIT rebalance_id=(\S+) date=(\S+) selected=(\d+) tradable=(\d+) sell_submitted=(\d+) buy_submitted=(\d+)", txt):
    reb[m.group(1)] = dict(date=m.group(2), selected=int(m.group(3)), tradable=int(m.group(4)), sell=int(m.group(5)), buy=int(m.group(6)))
for m in re.finditer(r"QS_PORTFOLIO_AUDIT rebalance_id=(\S+) date=(\S+) positions=(\d+) cash_ratio=([\d.]+) gross_exposure=([\d.]+)", txt):
    port[m.group(1)] = dict(date=m.group(2), positions=int(m.group(3)), cash=float(m.group(4)), gross=float(m.group(5)))
print("rebalance lines:", len(reb), "| portfolio lines:", len(port), "| liquidation lines:", len(re.findall(r"QS_LIQUIDATION", txt)))
TARGET, FILL, MINEXP, MAXCASH = 10, 0.8, 0.8, 0.2
fails = []
for rid, r in reb.items():
    p = port.get(rid)
    if not p:
        fails.append((rid, "no QS_PORTFOLIO_AUDIT")); continue
    expected = min(TARGET, r["tradable"]); required = math.ceil(expected * FILL)
    if r["selected"] < expected: fails.append((rid, "selected %d < expected %d" % (r["selected"], expected)))
    if p["positions"] < required: fails.append((rid, "positions %d < required %d" % (p["positions"], required)))
    if p["gross"] < MINEXP: fails.append((rid, "gross %.4f < %.2f" % (p["gross"], MINEXP)))
    if p["cash"] > MAXCASH: fails.append((rid, "cash %.4f > %.2f" % (p["cash"], MAXCASH)))
print("invariant FAILS:", len(fails))
for rid, msg in fails[:10]: print("   ", rid, msg)
if port:
    v = list(port.values())
    print("positions: min=%d max=%d mean=%.2f" % (min(x["positions"] for x in v), max(x["positions"] for x in v), statistics.mean(x["positions"] for x in v)))
    print("gross    : min=%.4f max=%.4f mean=%.4f" % (min(x["gross"] for x in v), max(x["gross"] for x in v), statistics.mean(x["gross"] for x in v)))
    print("cash     : min=%.4f max=%.4f mean=%.4f" % (min(x["cash"] for x in v), max(x["cash"] for x in v), statistics.mean(x["cash"] for x in v)))
    print("selected==10: %d / %d" % (sum(1 for r in reb.values() if r["selected"] == 10), len(reb)))
