import subprocess, os
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
r2 = subprocess.run(['git', '-c', 'core.quotepath=false', 'diff', '--staged', '--name-only'], cwd=ROOT, capture_output=True)
names = r2.stdout.decode('utf-8', 'replace').splitlines()
print('staged files:', len(names))
print('strategy file:', [n for n in names if '均值回归' in n])
print()
print('--- 关键文件是否入暂存 ---')
for k in ('AGENTS.md', 'design_metadata.py', 'ptrade_api.py', 'test_design_metadata.py', 'test_fill_audit_noop_counter.py', 'SKILL.md', 'no-lookahead-rules.md', 'design-metadata-open-match-price-design.md', 'qs-fill-audit-delta-below-one-lot-design.md', 'f3a-design', 'f2a-fill-audit'):
    hit = [n for n in names if k in n]
    print('  %-45s %s' % (k, 'YES' if hit else '*** NO ***'))