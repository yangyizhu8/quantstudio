import os, re
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
log = os.path.join(ROOT, 'agent_workspace', 'ou_reversal', 'f2a_verify.log')
raw = open(log, 'rb').read()
txt = raw.decode('utf-16', errors='replace') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else raw.decode('utf-8', errors='replace')
def s(pat):
    return sum(int(m.group(1)) for m in re.finditer(pat, txt))
sub = s(r'buy_submitted=(\d+)'); bf = s(r'buy_filled=(\d+)'); br = s(r'buy_rejected=(\d+)')
ssub = s(r'sell_submitted=(\d+)'); sf = s(r'sell_filled=(\d+)'); sr = s(r'sell_rejected=(\d+)')
print('== F2-A 对账（2025-07-01..2025-08-29）==')
print('  buy : submitted=%d  filled=%d  rejected=%d  -> 缺口=%d' % (sub, bf, br, sub - bf - br))
print('  sell: submitted=%d  filled=%d  rejected=%d  -> 缺口=%d' % (ssub, sf, sr, ssub - sf - sr))
print('  QS_ZERO_ORDER 行数: %d' % len(re.findall(r'QS_ZERO_ORDER', txt)))
print('  rejected_detail 含 delta_below_one_lot 的行数: %d' % len(re.findall(r'delta_below_one_lot\]', txt)))