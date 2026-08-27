
import time, subprocess, sys, os, json, io
from datetime import datetime, timezone, timedelta
from pathlib import Path

BJ = timezone(timedelta(hours=8))
ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
LOG = ROOT / "output" / "golden_baseline" / "final_snapshot_orchestrator.log"
GUARD_LOG = ROOT / "data" / "snapshots" / "guard_refused.log"

def log(msg):
    ts = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# O1 修正：显式等到【周六】23:30（不是任意天 23:30）
now = datetime.now(BJ)
target = now.replace(hour=23, minute=30, second=0, microsecond=0)
# 如果今天不是周六，或已过 23:30，等下一个周六
while target.weekday() != 5 or now >= target:
    target += timedelta(days=1)
    target = target.replace(hour=23, minute=30, second=0, microsecond=0)

wait = (target - now).total_seconds()
log(f"waiting {wait:.0f}s until Saturday 23:30 ({target.strftime('%Y-%m-%d %A')})")
time.sleep(wait)

# 启动断言：确认是周六
assert datetime.now(BJ).weekday() == 5, f"O1 assert: not Saturday ({datetime.now(BJ).strftime('%A')})"

log("=== FINAL SNAPSHOT ORCHESTRATOR START ===")

# create（guard 由 governance_snapshot.py 内部检查）
os.chdir(str(ROOT))
rc = subprocess.call([sys.executable, "scripts/governance_snapshot.py", "create",
                      "--source-task", "final-snapshot-pre-baseline"])
log(f"create exited rc={rc}")

if rc == 0:
    idx = json.loads(io.open(ROOT / "data/snapshots/index.json", encoding="utf-8").read())
    latest = idx["snapshots"][-1]["snapshot_id"]
    log(f"SNAP created: {latest}")
    log(f"=== ORCHESTRATOR DONE (pending verify) ===")
else:
    log(f"=== ORCHESTRATOR FAILED rc={rc} ===")
    # O2：引用 guard 日志方便诊断
    if GUARD_LOG.exists():
        tail = io.open(GUARD_LOG, encoding="utf-8").read().strip().split("\n")
        log(f"guard_refused.log tail: {tail[-3:]}")
    else:
        log(f"guard_refused.log not found (rc={rc} may be disk/consistency error)")

sys.exit(rc)
