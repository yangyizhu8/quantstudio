# -*- coding: utf-8 -*-
"""G3.5 双跑串行链：两条独立进程跑全窗，各自快照三件套 + 日志哈希。"""
import subprocess, sys, os
WS = r'D:\miniQMT策略实盘\QuantStudio\agent_workspace\dividend_defense_smallcap_5d'
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
DB = r'D:\miniQMT策略实盘\QuantStudio\data\staging\r5_maincopy_20260913\quantstudio.db'
TMP = r'C:\Users\Administrator\AppData\Local\Temp\r5'
RUNS = [('G351', 'g35_run1'), ('G352', 'g35_run2')]
for tag, sub in RUNS:
    print('=== %s start ===' % tag, flush=True)
    out = os.path.join(TMP, sub)
    logp = os.path.join(TMP, tag + '.log')
    with open(logp, 'w', encoding='utf-8') as fh:
        rc = subprocess.call([sys.executable, os.path.join(WS, 'run_window.py'),
                              ROOT, out, tag, '2020-01-01', '2026-07-31', DB],
                             stdout=fh, stderr=subprocess.STDOUT)
    print('=== %s done rc=%d ===' % (tag, rc), flush=True)
print('CHAIN_COMPLETE', flush=True)