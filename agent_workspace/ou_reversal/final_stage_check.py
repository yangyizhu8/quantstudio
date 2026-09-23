import subprocess, os
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
def git(*a):
    r = subprocess.run(['git'] + list(a), cwd=ROOT, capture_output=True)
    return r.returncode, r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')
rc, out, _ = git('-c', 'core.quotepath=false', 'diff', '--staged', '--stat')
lines = out.strip().splitlines()
print('staged stat tail:'); print(chr(10).join(lines[-8:]))
rc, names, _ = git('-c', 'core.quotepath=false', 'diff', '--staged', '--name-only')
ns = names.splitlines()
print()
print('staged total:', len(ns))
bad = [n for n in ns if ('backtest_readonly' in n or '__pycache__' in n or n.endswith('.log')
                        or 'fall_reversal' in n or 'candidate_quantstudio' in n
                        or n.startswith('scripts/') or n.startswith('config/'))]
print('forbidden staged:', bad if bad else '(none)')
print()
print('non-agent_workspace staged files:')
for n in ns:
    if not n.startswith('agent_workspace/'):
        print('   ', n)