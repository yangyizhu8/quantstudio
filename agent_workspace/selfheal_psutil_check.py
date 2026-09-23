# -*- coding: utf-8 -*-
"""psutil 自愈路径验证（V8-(a) 配套）：在补装 psutil 后的 venv 上验证「持有者已死 → 回收」全链。

隔离：`QS_WRITE_LOCK_DIR` 指向临时目录（**绝不触碰生产锁**）。
判据：① psutil 可用且 pid_exists 生效；② 死 pid 的锁判为 V_STALE_DEAD；
      ③ try_reclaim_stale 回收成功且锁文件消失；④ 审计行落盘。
"""
import json
import os
import socket
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="qs_selfheal_")
os.environ["QS_WRITE_LOCK_DIR"] = TMP
os.environ["QS_WRITE_LOCK_AUDIT_LOG"] = os.path.join(TMP, "write_lock_reclaim.log")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, ROOT)

import psutil  # noqa: E402

from quantstudio.pipeline import snapshot_lock as sl  # noqa: E402

print(f"[1] psutil {psutil.__version__} | pid_exists(self)={psutil.pid_exists(os.getpid())} "
      f"| pid_exists(999999)={psutil.pid_exists(999999)}")
print(f"[2] 隔离目录: {TMP}（生产锁未被触碰）")

lock = sl.lock_path()
holder = {
    "v": 2,
    "pid": 999999,                       # 不存在的 pid
    "host": socket.gethostname(),
    "pid_create_time": time.time() - 100000,
    "heartbeat": time.time() - 100000,   # 心跳早已过期
    "task_id": "selfheal-check",
    "token": "deadbeef",
}
lock.parent.mkdir(parents=True, exist_ok=True)
lock.write_text(json.dumps(holder), encoding="utf-8")
print(f"[3] 造锁: {lock}（pid=999999 不存在）")

diag = sl.diagnose_holder(holder)
print(f"[4] diagnose_holder → {diag}")
print(f"[5] verdict == V_STALE_DEAD ? {diag.get('verdict') == sl.V_STALE_DEAD}")

ok = sl.try_reclaim_stale(holder, diag, "psutil-check")
print(f"[6] try_reclaim_stale → {ok}")
print(f"[7] 锁文件已移除 ? {not lock.exists()}")

audit = os.environ["QS_WRITE_LOCK_AUDIT_LOG"]
n = sum(1 for _ in open(audit, encoding="utf-8")) if os.path.exists(audit) else 0
print(f"[8] 审计日志: {audit} | 行数={n}")

passed = (
    psutil.pid_exists(os.getpid())
    and not psutil.pid_exists(999999)
    and diag.get("verdict") == sl.V_STALE_DEAD
    and ok is True
    and not lock.exists()
)
print("RESULT:", "PASS" if passed else "FAIL")
sys.exit(0 if passed else 1)
