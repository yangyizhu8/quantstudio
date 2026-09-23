
import os, re
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
log = os.path.join(ROOT, "agent_workspace", "ou_reversal", "rerun_2m.log")
raw = open(log, "rb").read()
txt = raw.decode("utf-16", errors="replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", errors="replace")
sub = sum(int(m.group(1)) for m in re.finditer(r"buy_submitted=(\d+)", txt))
sells = sum(int(m.group(1)) for m in re.finditer(r"sell_submitted=(\d+)", txt))
bf = sum(int(m.group(1)) for m in re.finditer(r"buy_filled=(\d+)", txt))
sf = sum(int(m.group(1)) for m in re.finditer(r"sell_filled=(\d+)", txt))
br = sum(int(m.group(1)) for m in re.finditer(r"buy_rejected=(\d+)", txt))
sr = sum(int(m.group(1)) for m in re.finditer(r"sell_rejected=(\d+)", txt))
print("== 对账（2 个月样本）==")
print("  buy : submitted=%d  filled=%d  rejected=%d  -> 缺口=%d" % (sub, bf, br, sub - bf - br))
print("  sell: submitted=%d  filled=%d  rejected=%d  -> 缺口=%d" % (sells, sf, sr, sells - sf - sr))
print()
print("  QS_ZERO_ORDER 行数: %d" % len(re.findall(r"QS_ZERO_ORDER", txt)))
print("  reason=delta_below_one_lot 出现次数: %d" % len(re.findall(r"reason=delta_below_one_lot", txt)))
print("  reason=below_rebalance_threshold 出现次数: %d" % len(re.findall(r"reason=below_rebalance_threshold", txt)))
