import os, shutil
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
src = os.path.join(ROOT, 'output', 'generated_strategies', 'ou_reversal_csi300_10')
dst = os.path.join(ROOT, 'agent_workspace', 'ou_reversal_csi300_10')
for f in ('R5_BACKTEST_EVIDENCE.md', 'R2_E1_EVIDENCE_APPENDIX.md', 'R2_5_CONFIRMATION_PACKAGE.md', 'R2_AGENT_COMPONENT_PLAN.md'):
    s = os.path.join(src, f)
    if os.path.exists(s):
        shutil.copy2(s, os.path.join(dst, f)); print('copied', f)
    else:
        print('MISSING', f)
print()
for d in ('agent_workspace/ou_reversal', 'agent_workspace/ou_reversal_csi300_10'):
    p = os.path.join(ROOT, d.replace('/', os.sep))
    files = sorted(os.listdir(p))
    tot = sum(os.path.getsize(os.path.join(p, f)) for f in files if os.path.isfile(os.path.join(p, f)))
    print('%s: %d files, %.1f KB' % (d, len(files), tot/1024))
    print('   ', ', '.join(files[:40]))