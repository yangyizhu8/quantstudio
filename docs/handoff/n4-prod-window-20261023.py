# -*- coding: utf-8 -*-
"""N4 生产窗 ④⑤：A11/A12 真实启停演练 + 拉起 daemon + 双验证

依据：docs/evidence/fp5-22-migration-window-20261023.md（17250b2, 2026-10-23 21:55）
  · 阶段④（客户专属总运维）：N4 A11-13 真实启停演练；"启"= 在 2.2 库上拉起
    ⇒ 同步构成 A35 的 daemon 可启动实证
  · 阶段⑤：拉起 daemon + 双验证（2.2 库可启动 / 采集与水位链路正常）

序列（一次窗内完成）：
  ① 前置：daemon 停 + status 陈旧(stale) + 库 complete_2_2
  ② A11：GUI 同款启动（start_daemon_subprocess，带 token/bootstrap）→ 真实 poll 自愈
  ③ A12：优雅停 → 真实 poll×3 去抖 → stopped
  ④ ⑤ 最终拉起 + 双验证（status 新建 / daemon.log 无 schema 拒绝 / 水位表可读）

用法：python docs/handoff/n4-prod-window-20261023.py
      python docs/handoff/n4-prod-window-20261023.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quantstudio.gui import daemon_process as dp          # noqa: E402
from quantstudio.gui.tabs import task_tab as tt         # noqa: E402

CFG = ROOT / "config" / "profiles" / "mcp_only"
results = []


def ck(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)[:220]))
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
    fs._daemon_state, fs._daemon_token, fs._daemon_proc = state, None, None
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


def wait_running(timeout=90):
    for _ in range(timeout * 2):
        time.sleep(0.5)
        st = dp.read_daemon_status() or {}
        if dp.is_daemon_running():
            return st
    return None


def wait_stopped(timeout=120):
    for _ in range(timeout * 2):
        time.sleep(0.5)
        if not dp.is_daemon_running():
            return True
    return False


def launch_via_gui_path():
    """GUI 同款启动（带 token + bootstrap），返回 (token, proc)。"""
    return dp.start_daemon_subprocess(CFG)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=== ① 前置 ===")
    st0 = dp.read_daemon_status() or {}
    run0 = dp.is_daemon_running()
    print("  status:", {k: st0.get(k) for k in ("pid", "status", "started_at")})
    print("  is_daemon_running:", run0, "| pids:", dp.find_running_daemon_pids())
    if args.dry_run:
        print("  --dry-run 结束")
        return 0
    ck("① daemon 处于停止态", not run0, f"running={run0}")

    # 库状态（只读）
    try:
        import duckdb
        con = duckdb.connect(str(ROOT / "data" / "quantstudio.db"), read_only=True)
        ver = con.execute("SELECT schema_version FROM qfq_bootstrap_run "
                          "WHERE schema_version LIKE 'reanchor-2.%' ORDER BY schema_version DESC LIMIT 1").fetchone()
        n_wm = con.execute("SELECT COUNT(*) FROM source_watermark").fetchone()[0]
        cols = [r[1] for r in con.execute("PRAGMA table_info('source_watermark')").fetchall()]
        con.close()
        ck("① 库为 2.2（bootstrap 版本行）", bool(ver) and ver[0] == "reanchor-2.2", ver)
        ck("① source_watermark 含 last_run_id（9→含新增列）", "last_run_id" in cols, f"cols={len(cols)}")
        print("  source_watermark 行数 =", n_wm)
    except Exception as e:
        ck("① 库只读核对", False, f"{type(e).__name__}: {e}")

    # ---------------- ② A11：GUI 路径启动 → poll 自愈 ----------------
    print("\n=== ② A11 真实启动 + poll 自愈 ===")
    tok = None
    try:
        tok, proc = launch_via_gui_path()
        ck("② GUI 路径启动返回 token/进程", bool(tok) and proc is not None,
           f"token={str(tok)[:8]} pid={getattr(proc, 'pid', None)}")
        st1 = wait_running()
        ck("② 生产 status 变为 running", st1 is not None, st1)
        tab = make_fake_tab("stopped")
        t0 = time.time()
        tab._on_daemon_poll()
        el = time.time() - t0
        ck("A11 生产真实自愈 running", tab._daemon_state == "running", tab._daemon_state)
        ck("A11 按钮文案=运行中", "运行中" in getattr(tab.daemon_btn, "text", ""), tab.daemon_btn.text)
        ck("A11 单次 poll ≤5s", el <= 5.0, f"{el:.2f}s")
        ck("A11 status 新建 pid≠旧 69832", (st1 or {}).get("pid") != 69832, (st1 or {}).get("pid"))
        # bootstrap（FP-2 侧证：GUI 路径每 token 一个文件）
        bs = ROOT / "data" / "logs" / f"daemon_bootstrap_{tok}.log"
        print("  bootstrap:", bs.name, "exists=", bs.exists(),
              "size=", bs.stat().st_size if bs.exists() else "-")
    except Exception as e:
        ck("② 启动链路", False, f"{type(e).__name__}: {e}")
        raise

    # ---------------- ③ A12：优雅停 → 三次去抖 ----------------
    print("\n=== ③ A12 优雅停 + poll×3 去抖 ===")
    tab2 = make_fake_tab("running")
    st2 = dp.read_daemon_status() or {}
    tab2._daemon_token = st2.get("instance_token")
    ok_req = dp.request_graceful_stop(timeout_check_token=tab2._daemon_token)
    ck("③ 优雅停请求已写", ok_req, f"token={str(tab2._daemon_token)[:8]}")
    gone = wait_stopped()
    ck("③ daemon 确证退出", gone, f"pids={dp.find_running_daemon_pids()}")
    tab2._on_daemon_poll(); s1 = tab2._daemon_state
    tab2._on_daemon_poll(); s2 = tab2._daemon_state
    tab2._on_daemon_poll(); s3 = tab2._daemon_state
    ck("A12 去抖 1/2 次仍 running", s1 == "running" and s2 == "running", f"{s1}/{s2}")
    ck("A12 第 3 次判 stopped", s3 == "stopped", s3)
    ck("A12 按钮文案=已停止", "已停止" in getattr(tab2.daemon_btn, "text", ""), tab2.daemon_btn.text)

    # ---------------- ④⑤ 最终拉起 + 双验证 ----------------
    print("\n=== ④⑤ 最终拉起 + 双验证 ===")
    tok2, proc2 = launch_via_gui_path()
    st3 = wait_running()
    ck("⑤ 最终拉起成功（status running）", st3 is not None, st3)
    ck("⑤ 新 pid 与演练前不同", (st3 or {}).get("pid") not in (None, 69832), (st3 or {}).get("pid"))

    # 日志侧：无 schema 拒绝 / 无启动 ERROR
    log = (ROOT / "data" / "logs" / "daemon.log").read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
    tail = "\n".join(log)
    ck("⑤ daemon.log 无 schema 拒绝启动", "partial_or_mixed" not in tail and "MigrationRequired" not in tail)
    ck("⑤ daemon.log 无「另一个 daemon 已在运行」", "另一个 daemon 已在运行" not in tail)
    ck("⑤ 出现常驻模式启动行（本次）",
       any("常驻模式（v3 DaemonLifecycle）" in l for l in log[-20:]), log[-4:])

    # 水位链路：daemon 起来后库仍可只读查
    try:
        import duckdb
        con = duckdb.connect(str(ROOT / "data" / "quantstudio.db"), read_only=True)
        n_wm = con.execute("SELECT COUNT(*) FROM source_watermark").fetchone()[0]
        nonnull = con.execute("SELECT COUNT(*) FROM source_watermark WHERE last_run_id IS NOT NULL").fetchone()[0]
        con.close()
        ck("⑤ 水位表可读（采集链路可用）", n_wm > 0, f"rows={n_wm} last_run_id 非空={nonnull}")
    except Exception as e:
        ck("⑤ 水位表可读", False, f"{type(e).__name__}: {e}")

    print("\n=== 结果 ===")
    for n, ok, d in results:
        print("  [%s] %s | %s" % ("PASS" if ok else "FAIL", n, d))
    npass = sum(1 for _, o, _ in results if o)
    print("  汇总: %d/%d PASS" % (npass, len(results)))
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
