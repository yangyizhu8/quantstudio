import subprocess, os
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
def git(*a, **kw):
    r = subprocess.run(['git'] + list(a), cwd=ROOT, capture_output=True, **kw)
    return r.returncode, r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')
git('add', '--', 'agent_workspace/ou_reversal_csi300_10')
git('add', '--', 'agent_workspace/ou_reversal')
rc, out, err = git('-c', 'core.quotepath=false', 'diff', '--staged', '--name-only')
print('staged total:', len(out.splitlines()))
rc, out, err = git('commit', '-F', 'agent_workspace/ou_reversal/commit_msg.txt')
print('commit rc:', rc)
print(out[-1200:] if out else '')
print('ERR:', err[:600] if err.strip() else '(none)')
rc, out, _ = git('log', '-1', '--format=%H%n%s')
print('HEAD:', out.strip())