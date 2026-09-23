import os, re
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
def load(p):
    raw = open(p, 'rb').read()
    return raw.decode('utf-16', errors='replace') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else raw.decode('utf-8', errors='replace')
r5 = load(os.path.join(ROOT, 'agent_workspace', 'ou_reversal_csi300_10', 'r5_run1.log'))
after = load(os.path.join(ROOT, 'agent_workspace', 'ou_reversal', 'f2a_verify.log'))
def stats(txt, label):
    lines = [l for l in txt.split(chr(10)) if 'QS_FILL_AUDIT' in l]
    dl = sum(1 for l in lines if 'delta_below_one_lot' in l)
    br = sum(int(m.group(1)) for m in re.finditer(r'buy_rejected=(\d+)', txt))
    z  = len(re.findall(r'QS_ZERO_ORDER', txt))
    print('%s: QS_FILL_AUDIT行=%d  含delta_below_one_lot的行=%d  buy_rejected合计=%d  QS_ZERO_ORDER=%d' % (label, len(lines), dl, br, z))
stats(r5, 'BEFORE F2-A (R5 run1, 287日)')
stats(after, 'AFTER  F2-A (2个月验证)')
print()
print('样例（AFTER，含 delta_below_one_lot 的审计行）:')
for l in after.split(chr(10)):
    if 'QS_FILL_AUDIT' in l and 'delta_below_one_lot' in l:
        print('   ', l.strip()[:260]); break