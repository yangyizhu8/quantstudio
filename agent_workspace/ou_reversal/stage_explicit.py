import subprocess, os
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
files = [
  'AGENTS.md',
  'quantstudio/backtest/strategies/沪深300均值回归超跌反弹.py',
  'quantstudio/strategy_compiler/design_metadata.py',
  'quantstudio/backtest/ptrade_api.py',
  'tests/test_design_metadata.py',
  'tests/test_fill_audit_noop_counter.py',
  'skills/quantstudio-strategy-compiler/SKILL.md',
  'skills/quantstudio-strategy-compiler/references/no-lookahead-rules.md',
  'docs/design-metadata-open-match-price-design.md',
  'docs/qs-fill-audit-delta-below-one-lot-design.md',
  'docs/evidence/f3a-design-metadata-open-acceptance-20260923.md',
  'docs/evidence/f2a-fill-audit-noop-counter-acceptance-20260923.md',
]
missing = [f for f in files if not os.path.exists(os.path.join(ROOT, f.replace('/', os.sep)))]
print('missing on disk:', missing)
r = subprocess.run(['git', 'add', '--'] + files, cwd=ROOT, capture_output=True)
print('add rc:', r.returncode)
err = r.stderr.decode('utf-8', 'replace')
print('stderr:', err[:500] if err.strip() else '(none)')
r2 = subprocess.run(['git', '-c', 'core.quotepath=false', 'diff', '--staged', '--name-only'], cwd=ROOT, capture_output=True)
names = r2.stdout.decode('utf-8', 'replace').splitlines()
print()
print('staged total:', len(names))
for f in files:
    print('  %-70s %s' % (f, 'YES' if f in names else '*** NO ***'))