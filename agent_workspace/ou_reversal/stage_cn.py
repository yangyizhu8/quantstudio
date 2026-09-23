import subprocess, os
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
target = os.path.join(ROOT, 'quantstudio', 'backtest', 'strategies', '沪深300均值回归超跌反弹.py')
print('exists:', os.path.exists(target))
r = subprocess.run(['git', 'add', '--', target], cwd=ROOT, capture_output=True)
print('add rc:', r.returncode, r.stdout.decode('utf-8', 'replace')[:200], r.stderr.decode('utf-8', 'replace')[:300])
r2 = subprocess.run(['git', 'diff', '--staged', '--name-only'], cwd=ROOT, capture_output=True)
names = r2.stdout.decode('utf-8', 'replace').splitlines()
print('staged files:', len(names))
hit = [n for n in names if '均值回归' in n]
print('strategy file staged:', hit)
bad = [n for n in names if 'backtest_readonly' in n or '__pycache__' in n or n.endswith('.log') or 'fall_reversal' in n]
print('forbidden staged:', bad)