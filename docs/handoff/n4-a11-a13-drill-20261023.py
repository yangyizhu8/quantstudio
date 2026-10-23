# -*- coding: utf-8 -*-
"""N4 补验演练 v2：A11 / A12 / A13 —— **隔离环境真实进程版**（不占生产窗口）

与 v1 的区别（v1 需在生产 root 停/启 daemon，占窗口）：
  本版用临时 `QUANTSTUDIO_DATA_ROOT` 起**真实 daemon 子进程**，
  主进程只读生产 status（陈旧也只读、不写），
  ⇒ 可在**任何时刻**执行，不必等受控窗，也不必停生产 daemon。

覆盖：
  A11 stopped 态探测**外部启动**（且 status 陈旧）⇒ 自愈为 running
      —— v1 实测暴露的缺陷：第二源命中后若仍走 `_sync_daemon_state()`（只看 status）
         会被判回 stopped。已修，本用例即其回归验证。
  A12 running 态 daemon 消失 ⇒ 连续 3 次去抖后 stopped（真实生产代码路径）
  A13 stopped 态轮询 60s 的进程 CPU 增量 < 0.5%

用法：python docs/handoff/n4-a11-a13-drill-20261023.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

from quantstudio.gui import daemon_process as dp  # noqa: E402
from quantstudio.gui.tabs import task_tab as tt  # noqa: E402

PY = sys.executable
CFG = "config/profiles/mcp_only"
results = []


def ck(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)[:200]))
    print("  [%s] %s | %s" % ("PASS" if ok else "FAIL", name, detail))


class FakeTimer:
    def __init__(self, active=True, interval=5000):
        self._active, self._interval = active, interval

    def isActive(self): return self._active            # noqa: N802
    def setInterval(self, v): self._interval = v       # noqa: N802
    def interval(self): return self._interval
    def start(self): self._active = True
    def stop(self): self._active = False


def make_fake_tab(state="stopped"):
    fs = types.SimpleNamespace()
    fs._daemon_state = state
    fs._daemon_token = None
    fs._daemon_proc = None
    fs._daemon_poll_timer = FakeTimer()
    fs._daemon_death_streak = 0
    txt = {"v": ""}
    fs._set_status_text = lambda m: txt.__setitem__("v", m)
    fs.daemon_btn = types.SimpleNamespace(
        setText=lambda t: setattr(fs.daemon_btn, "text", t),
        setEnabled=lambda b: setattr(fs.daemon_btn, "enabled", b), text="", enabled=True)
    fs._sync_daemon_state = types.MethodType(tt.TaskTab._sync_daemon_state, fs)
    fs._update_daemon_btn = types.MethodType(tt.TaskTab._update_daemon_btn, fs)
    fs._on_daemon_poll = types.MethodType(tt.TaskTab._on_daemon_poll, fs)
    fs._text = txt
    return fs


def start_isolated_daemon(tmp: Path):
    """在临时 data_root 起真实 daemon（不触碰生产库/status）。"""
    env = {**os.environ, "QUANTSTUDIO_DATA_ROOT": str(tmp), "PYTHONIOENCODING": "utf-8"}
    flags = (0x00000008 | 0x00000200) if sys.platform == "win32" else 0
    p = subprocess.Popen(
        [PY, "-m", "quantstudio.pipeline.daemon", "--mode", "forever",
         "--config-dir", CFG],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags)
    for _ in range(80):
        time.sleep(0.5)
        if any(d["pid"] == p.pid for d in dp.find_running_daemon_pids()):
            return p
    return None


def kill(p):
    if p is None:
        return
    try:
        psutil.Process(p.pid).kill()
    except Exception:
        pass
    try:
        p.wait(timeout=15)
    except Exception:
        pass


def main():
    print("=== 0 现场（生产只读）===")
    st = dp.read_daemon_status() or {}
    print("  生产 status:", {k: st.get(k) for k in ("pid", "status", "started_at")})
    print("  生产 is_daemon_running():", dp.is_daemon_running())
    print("  生产 find_running_daemon_pids():", dp.find_running_daemon_pids())

    # ---------------- A12：running 态 + daemon 不存在 ⇒ 3 次去抖后 stopped ----------------
    print("\n=== A12 running→stopped（真实去抖，三次 poll）===")
    tab = make_fake_tab("running")
    tab._daemon_token = st.get("instance_token")
    if dp.is_daemon_running():
        ck("A12 前置：生产 daemon 仍在跑", False, "本用例需 daemon 已停；请先确认（不改动生产）")
    else:
        tab._on_daemon_poll()
        ck("A12 去抖第 1 次仍 running", tab._daemon_state == "running", tab._daemon_state)
        tab._on_daemon_poll()
        ck("A12 去抖第 2 次仍 running", tab._daemon_state == "running", tab._daemon_state)
        tab._on_daemon_poll()
        ck("A12 第 3 次判 stopped", tab._daemon_state == "stopped", tab._daemon_state)
        ck("A12 按钮文案=已停止", "已停止" in getattr(tab.daemon_btn, "text", ""), tab.daemon_btn.text)
        ck("A12 stopped 后定时器仍激活", tab._daemon_poll_timer.isActive() is True)
        ck("A12 stopped 间隔=5000", tab._daemon_poll_timer.interval() == 5000, tab._daemon_poll_timer.interval())

    # ---------------- A11：status 不可用 + 真实外部进程在跑 ⇒ 自愈 ----------------
    print("\n=== A11 stopped 态探测外部启动（status 不可用）===")
    tmp = Path(tempfile.mkdtemp(prefix="n4_drill_"))
    proc = None
    try:
        proc = start_isolated_daemon(tmp)
        ck("A11 前置：隔离 root 真实 daemon 拉起", proc is not None, f"pid={getattr(proc, 'pid', None)}")
        ck("A11 前置：枚举能看见它", any(d["pid"] == getattr(proc, "pid", -1) for d in dp.find_running_daemon_pids()),
           dp.find_running_daemon_pids())
        if proc is not None:
            tab2 = make_fake_tab("stopped")
            orig = tt.is_daemon_running
            tt.is_daemon_running = lambda: False        # 模拟 status 陈旧/不可用
            try:
                t0 = time.time()
                tab2._on_daemon_poll()
                elapsed = time.time() - t0
            finally:
                tt.is_daemon_running = orig
            ck("A11 自愈为 running", tab2._daemon_state == "running", tab2._daemon_state)
            ck("A11 按钮文案=运行中", "运行中" in getattr(tab2.daemon_btn, "text", ""), tab2.daemon_btn.text)
            ck("A11 状态栏提示含『外部启动』", "外部启动" in tab2._text["v"], tab2._text["v"])
            ck("A11 单次 poll 内完成（≤5s）", elapsed <= 5.0, f"{elapsed:.2f}s")
    finally:
        kill(proc)

    # ---------------- A13：stopped 态 60s CPU ----------------
    print("\n=== A13 stopped 态 60s CPU 增量 ===")
    tab3 = make_fake_tab("stopped")
    pr = psutil.Process()
    c0 = sum(pr.cpu_times()[:2]); w0 = time.time(); ticks = 0
    while time.time() - w0 < 60:
        tab3._on_daemon_poll()
        ticks += 1
        time.sleep(5)
    c1 = sum(pr.cpu_times()[:2])
    pct = (c1 - c0) / max(1e-6, time.time() - w0) * 100
    ck("A13 stopped 态 CPU <0.5%", pct < 0.5, f"{pct:.3f}%（{ticks} 次 poll / 60s）")

    print("\n=== 演练结果 ===")
    for n, ok, d in results:
        print("  [%s] %s | %s" % ("PASS" if ok else "FAIL", n, d))
    npass = sum(1 for _, o, _ in results if o)
    print("  汇总: %d/%d PASS" % (npass, len(results)))
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
