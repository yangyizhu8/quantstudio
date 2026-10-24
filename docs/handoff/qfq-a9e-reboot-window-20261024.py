# -*- coding: utf-8 -*-
"""今晚重启窗：A9e 生效验证 + A9b/A7 补验入口（2026-10-24 晚）

裁（2026-10-24）：②"A9e 验收窗并入今晚重启（重启后明晨 06:00 轮不崩 = A9e 生产实证；
A9b/A7 补验同窗机会）"；①606e688 ⑤已批，与 4ddaf55 同批出仓。

设计纪律：
- **默认 dry-run**（只读快照 + 门禁，不碰 daemon）；`--execute` 才真正停/启。
- **停/启前后各留快照**（pid/status/水位/日志 offset）——窗口证据可复核。
- **隔离预验证门禁**：重启前先在内存模拟 `(None,None)` 走 capture，不通过则**中止重启**
  （避免把一个已知会崩的版本推上生产）。
- **失败自动回滚**：启动失败 → 用**旧 token** 重启原命令（与窗口前一致的形态）。
- 新 token 用 uuid4（N4 生产窗已验证换 token 合法）。

用法：
  python docs/handoff/qfq-a9e-reboot-window-20261024.py              # dry-run（默认）
  python docs/handoff/qfq-a9e-reboot-window-20261024.py --execute    # 真停/启
  python docs/handoff/qfq-a9e-reboot-window-20261024.py --check-only # 仅事后核验（不启停）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
STATUS = DATA / "daemon_status.json"
STOP_REQ = DATA / "daemon_stop.request"
LOG = DATA / "logs" / "daemon.log"

PY = sys.executable
CFG = str(ROOT / "config" / "profiles" / "mcp_only")


def _jload(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).is_running()
    except Exception:
        return False


def snapshot(tag: str) -> dict:
    st = _jload(STATUS) or {}
    snap = {
        "tag": tag,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status_json": st,
        "pid_alive": _pid_alive(st.get("pid")) if st.get("pid") else False,
        "stop_request_exists": STOP_REQ.exists(),
        "log_bytes": LOG.stat().st_size if LOG.exists() else 0,
    }
    print(f"  [{tag}] pid={st.get('pid')} status={st.get('status')} "
          f"alive={snap['pid_alive']} log={snap['log_bytes']}B ts={snap['ts']}")
    return snap


def gate_isolated_capture() -> bool:
    """门禁：内存模拟生产崩溃路径 `(None, None)`，必须不崩。"""
    try:
        import pandas as pd
        from quantstudio.pipeline.qfq_fresh_capture import FreshCapture, FreshFetcher

        class _F(FreshFetcher):
            def __init__(self):
                self.calls = []

            def fetch_none_front(self, a, c, p, s, e):
                self.calls.append(p)
                idx = pd.DatetimeIndex(["2026-10-20 09:30"])
                df = pd.DataFrame({"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5]},
                                  index=idx)
                return df, df

        cap = FreshCapture.__new__(FreshCapture)
        cap._period_for = lambda t, d: d
        f = _F()
        rec, _fd, fm = cap.capture(None, asset_type="ETF", code="160128", run_id="gate",
                                   daily_range_ms=(1788192000000, 1792598400000),
                                   minute_range_ms=(None, None), fetcher=f,
                                   source="mcp", write=False)
        ok = (f.calls == ["daily"] and rec.minute_range_start is None and len(fm) == 0)
        print(f"  [门禁] (None,None) 路径 calls={f.calls} minute_range_start="
              f"{rec.minute_range_start} → {'PASS' if ok else 'FAIL'}")
        return ok
    except Exception as e:            # 隔离门禁自身异常也视为不通过（fail-closed）
        print(f"  [门禁] FAIL 异常: {type(e).__name__}: {e}")
        return False


def stop_daemon(timeout_s: int = 90) -> bool:
    PID = (_jload(STATUS) or {}).get("pid")
    if not PID:
        print("  [停] status.json 无 pid —— 视为已停止")
        return True
    STOP_REQ.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    print(f"  [停] 已写 daemon_stop.request，等待 pid={PID} 退出（≤{timeout_s}s）")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if not _pid_alive(PID):
            print(f"  [停] 已退出（{time.time()-t0:.1f}s）")
            try:
                STOP_REQ.unlink(missing_ok=True)
            except Exception:
                pass
            return True
        time.sleep(1.0)
    print(f"  [停] **超时未退出**（{timeout_s}s）—— 中止，不改动现状")
    return False


def start_daemon(token: str | None = None) -> int | None:
    token = token or uuid.uuid4().hex
    cmd = [PY, "-m", "quantstudio.pipeline.daemon", "--mode", "forever",
           "--config-dir", CFG, "--instance-token", token]
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(cmd, cwd=str(ROOT), creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  [启] token={token[:8]}… launcher_pid={p.pid}")
    return p.pid


def wait_handshake(timeout_s: int = 90) -> dict | None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = _jload(STATUS) or {}
        if st.get("pid") and st.get("status") == "running" and _pid_alive(st["pid"]):
            print(f"  [握手] running pid={st['pid']} started_at={st.get('started_at')} "
                  f"（{time.time()-t0:.1f}s）")
            return st
        time.sleep(2.0)
    print(f"  [握手] **超时 {timeout_s}s 未变 running**")
    return None


def tail_log(n: int = 12) -> None:
    if not LOG.exists():
        return
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    for ln in lines:
        print("    " + ln[:150])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="真正停/启 daemon")
    ap.add_argument("--check-only", action="store_true", help="仅事后核验")
    a = ap.parse_args()

    print("=== 今晚重启窗（A9e 生效）===")
    pre = snapshot("前置")

    if a.check_only:
        print("\n[事后核验] 关注项：")
        print("  1) daemon.log 是否出现「分钟表结构性为空 ⇒ 按日线-only 重锚」")
        print("  2) 是否**不再**出现 `int() argument ... not 'NoneType'`")
        print("  3) qfq_reanchor_event 是否有 status=committed 且 minute_plan 含 "
              "minute_front_coverage=absent（A9b）")
        print("  4) source_watermark 水位是否前进（A7）")
        tail_log()
        return 0

    print("\n[门禁] 隔离预验证（重启前必须 PASS）")
    if not gate_isolated_capture():
        print("  ✗ 门禁未过 ⇒ **中止重启**（不把已知会崩的版本推上生产）")
        return 2

    if not a.execute:
        print("\n[dry-run] 门禁 PASS。真正执行请加 --execute。")
        print("  将执行：停(≤90s) → 启(新 token) → 握手(≤90s) → 后置快照")
        return 0

    old_token = ((_jload(STATUS) or {}).get("instance_token")
                 or (_jload(STATUS) or {}).get("token"))
    print(f"\n[执行] 旧 token（回滚用）= {str(old_token)[:8] if old_token else '未知'}…")

    if not stop_daemon():
        return 3
    snapshot("停后")

    start_daemon()
    st = wait_handshake()
    if st is None:
        print("\n[回滚] 启动未握手 ⇒ 用旧 token 重启")
        start_daemon(old_token if isinstance(old_token, str) else None)
        wait_handshake()
        snapshot("回滚后")
        tail_log(20)
        return 4

    post = snapshot("后置")
    print("\n[完成] 重启窗成功。窗口证据：")
    print(f"  pid {pre['status_json'].get('pid')} → {post['status_json'].get('pid')}")
    print(f"  log {pre['log_bytes']}B → {post['log_bytes']}B")
    print("\n[明晨 06:00 轮观察清单]")
    print("  A9e：无 `int() argument ... not 'NoneType'`；出现「分钟表结构性为空 ⇒ 日线-only 重锚」")
    print("  A9b：qfq_reanchor_event.status='committed' 且 minute_plan 含 minute_front_coverage=absent")
    print("  A7 ：source_watermark 对应表水位前进")
    print("  积压：QfqPendingSla（当前 51013）应开始下降；若集中重锚导致长时占用需记录耗时")
    tail_log()
    return 0


if __name__ == "__main__":
    sys.exit(main())
