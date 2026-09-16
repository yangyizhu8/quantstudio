# -*- coding: utf-8 -*-
'''A6 验收轮 · 自动化骨架（3×2 矩阵 + N=10 双轮 + 06:00 延迟 + 锁生命周期）。

只读预置：dev 的 T1/T2 落地前即可就绪，落地后 --matrix/--n10 即插即用。
全轮影子化：所有场景经 shadow_env 构造环境，安全闸不通过即中止。

用法:
  python acceptance_runner.py --dry-run              # 环境自检（零副作用）
  python acceptance_runner.py --list                 # 列场景与判据
  python acceptance_runner.py --matrix               # 3×2 矩阵
  python acceptance_runner.py --n10 --round final    # N=10 双轮（baseline|final）
  python acceptance_runner.py --delay-0600           # 06:00 轮启动延迟（只读生产日志）
  python acceptance_runner.py --lock-lifecycle       # 锁文件生命周期对照
  python acceptance_runner.py --all --round final    # 全量
'''
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shadow_env import (  # noqa: E402
    ARTIFACT_DIR, EVIDENCE_DIR, PY, ROOT, SHADOW_CONFIG, SHADOW_DB,
    assert_shadow_paths, lock_held, probe_rw, prod_db_untouched,
    start_bg, wait_pid_exit,
)

N_TARGET = 10        # N=10：连续任务启动全成功
PROBE_DUR = 12.0     # 每格探测时长（秒）
PROBE_IV = 0.4       # 采样间隔
GUI_STATES = ["idle", "browse", "pull"]
DAEMON_STATES = ["idle", "collecting"]
FENCE = chr(96) * 3  # markdown 围栏（避免源码中出现三反引号）

# 归因判据 —— 与 dev T1 四锚同字面（口径 32eadc7 钉死；2026-09-16 依 dev 实现校准）。
# dev 真实输出（writers.py:500-513）：
#   形态 A（有候选）：  持有者  : pid=… name=… started=…（另有 N 个候选）
#                                cmdline=…            <-- **在续行**（正则须容忍换行）
#   形态 B（无候选）：  持有者  : 未能归因（psutil 不可用或无匹配候选进程）  <-- **合法如实分支**
# **命中判据（总调度校准）**："归因未命中" = "持有者**行**缺失"，**不要求 pid= 存在**——
#   形态 B 同样含「持有者」锚，属如实报告，**不得判为未命中**（否则终轮误报）；
#   仅当「持有者」锚完全缺失时才判未命中（真判据缺失）。故本表以「持有者」为**唯一判据锚**，
#   其余三锚与 pid= 仅作诊断信息记录（不参与命中判定）。
ATTRIB_ANCHOR_HOLDER = "持有者"          # 唯一判据锚
ATTRIB_ANCHORS_DIAGNOSTIC = ["db_path", "轨迹", "原始文本"]  # 诊断锚（不入判据）
ATTRIB_UNABLE_MARKERS = ["未能归因", "无法归因", "no holder", "not attributed"]
# 兼容保留（他源/旧文本形态，不删除；仅用于诊断输出）
ATTRIB_PATTERNS = [r"psutil", r"holder", r"open_files"]


def _tail(path, n=400):
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except Exception:
        return ""


def attribution_verdict(text):
    """四项记录之三：持有者归因判定（三态，口径见文件头注释）。

    返回 (hit, kind)：
      hit  = 「持有者」锚是否出现（**唯一判据**；"未能归因"形态 B 同样满足）
      kind = "resolved"（形态 A：给出 pid=/name=/started=）
             | "unable"（形态 B：如实报未能归因 —— 合法分支，非失败）
             | "anchor_only"（有锚但两形态特征均不匹配，需人工看）
             | "absent"（**锚缺失 = 真未命中**）
    re.DOTALL：容忍 dev 的 cmdline= **续行**（writers.py:509）与多行文本形态。
    """
    t = text or ""
    if ATTRIB_ANCHOR_HOLDER not in t:
        return False, "absent"
    low = t.lower()
    if any(m.lower() in low for m in ATTRIB_UNABLE_MARKERS):
        return True, "unable"
    if re.search(r"pid[=: ]\s*\d+", low, re.DOTALL):
        return True, "resolved"
    return True, "anchor_only"


def attribution_hit(text):
    """向后兼容：仅返回是否命中（= 「持有者」锚是否出现）。"""
    return attribution_verdict(text)[0]


def silent_empty_check(text):
    """四项记录之四：静默空数据检查（A4 修复后应出现降级提示文案）。"""
    hinted = "采集中" in (text or "")
    return {"hint_present": hinted, "verdict": "有提示" if hinted else "无提示(静默空)"}


def drive_gui(state):
    """按 GUI 态启动驱动（全轮影子化）。"""
    if state == "idle":
        p = start_bg([PY, "main_gui.py"], "gui_idle")
        time.sleep(18)   # 等 GUI 初始化
        return {"proc": p, "log": Path(getattr(p, "dsh_log", ""))}
    if state == "browse":
        p = start_bg([PY, str(Path(__file__).resolve().parent.parent / "browse_sim.py")], "gui_browse")
        time.sleep(2)
        return {"proc": p, "log": Path(getattr(p, "dsh_log", ""))}
    if state == "pull":
        # dev 的 T2 落地后：此处应切换为 GUI 委托通道（--pull-driver delegated）
        return {"proc": None, "log": None, "driver": "once-direct"}
    raise ValueError(state)


def drive_daemon(state):
    """按 daemon 态启动（影子库真实拉取）。"""
    if state == "idle":
        return {"proc": None, "log": None}
    if state == "collecting":
        p = start_bg([PY, "-m", "quantstudio.pipeline.daemon", "--mode", "once",
                      "--task", "mcp_etf_basic", "--pull-mode", "incremental",
                      "--config-dir", str(SHADOW_CONFIG), "--quality-audit", "none"],
                     "daemon_collect")
        time.sleep(4)
        return {"proc": p, "log": Path(getattr(p, "dsh_log", ""))}
    raise ValueError(state)


def cleanup(handles):
    for h in handles:
        p = (h or {}).get("proc")
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()


def run_cell(gui_state, daemon_state):
    """执行一个矩阵格：驱动两侧 + 探测 + 四项记录。"""
    label = "gui=%s|daemon=%s" % (gui_state, daemon_state)
    print("\n[cell] " + label, flush=True)
    handles = []
    handles.append(drive_gui(gui_state))
    handles.append(drive_daemon(daemon_state))
    probe = probe_rw(SHADOW_DB, PROBE_DUR, PROBE_IV, label)
    logs = "\n".join(_tail(h["log"]) for h in handles if h.get("log"))
    blob = logs + " " + " ".join(probe.get("sample_errors", []))
    _v = attribution_verdict(blob)
    cell = {
        "cell": label, "gui_state": gui_state, "daemon_state": daemon_state,
        "lock_held_during": lock_held(), "probe": probe,
        "record_1_success_rate_pct": round(100.0 - (probe.get("fail_rate_pct") or 0.0), 1),
        "record_2_error_samples": probe.get("sample_errors", [])[:3],
        "record_3_attribution_hit": _v[0], "record_3_holder_kind": _v[1],
        "record_3_diagnostic_anchors": [a for a in ATTRIB_ANCHORS_DIAGNOSTIC if a in blob],
        "record_4_silent_empty": silent_empty_check(logs),
    }
    cleanup(handles)
    print("   -> 成功率 %.1f%% | 归因命中=%s | %s" % (
        cell["record_1_success_rate_pct"], cell["record_3_attribution_hit"],
        cell["record_4_silent_empty"]["verdict"]), flush=True)
    return cell


def run_matrix():
    return [run_cell(g, d) for g in GUI_STATES for d in DAEMON_STATES]


def run_n10(round_name):
    """N=10：连续 10 次任务启动全成功（baseline 直连 once；final 可切委托通道）。"""
    results = []
    for i in range(N_TARGET):
        t0 = time.time()
        p = start_bg([PY, "-m", "quantstudio.pipeline.daemon", "--mode", "once",
                      "--task", "mcp_etf_basic", "--pull-mode", "incremental",
                      "--config-dir", str(SHADOW_CONFIG), "--quality-audit", "none"],
                     "n10_%s_%02d" % (round_name, i + 1))
        rc = wait_pid_exit(p, 180)
        log = _tail(Path(getattr(p, "dsh_log", "")))
        ok = ("once 完成" in log) or ("水位已追平" in log)
        results.append({"i": i + 1, "exit": rc, "ok": ok,
                        "secs": round(time.time() - t0, 1), "log_tail": log[-300:]})
        print("   N=%02d ok=%s exit=%s %.1fs" % (i + 1, ok, rc, results[-1]["secs"]), flush=True)
    succ = sum(1 for r in results if r["ok"])
    return {"round": round_name, "n": N_TARGET, "success": succ,
            "pass": succ == N_TARGET, "items": results}


def run_delay_0600():
    """06:00 轮启动延迟实测（只读生产日志，不影子）。"""
    log = ROOT / "data" / "logs" / "daemon.log"
    if not log.exists():
        return {"available": False, "reason": "生产 daemon.log 不存在"}
    txt = _tail(log, 4000)
    rows = [l for l in txt.splitlines()
            if "06:00" in l or "retry" in l.lower() or "退避" in l or "重试" in l]
    return {"available": True, "log": str(log), "matched_lines": rows[-40:],
            "note": "口径：06:00 轮启动->首个任务 START 间隔；重试轨迹计数"}


def run_lock_lifecycle():
    """锁文件生命周期对照（preserve_lock_file 行为变更单列）。"""
    code = ("import os, sys, tempfile, json\n"
            "from filelock import FileLock\n"
            "out = {}\n"
            "for val in (False, True):\n"
            "    p = os.path.join(tempfile.gettempdir(), 'll_%s.lock' % val)\n"
            "    if os.path.exists(p): os.remove(p)\n"
            "    lk = FileLock(p, timeout=0, preserve_lock_file=val)\n"
            "    lk.acquire(); lk.release()\n"
            "    out['preserve_%s' % val] = os.path.exists(p)\n"
            "print(json.dumps(out))\n")
    p = subprocess.run([PY, "-c", code], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8")
    cur = json.loads(p.stdout.strip().splitlines()[-1]) if (p.stdout or "").strip() else {}
    ok = bool(cur.get("preserve_True"))
    return {"baseline_default_delete": cur.get("preserve_False"),
            "with_preserve_lock_file": cur.get("preserve_True"),
            "verdict": "preserve_lock_file=True 生效（锁文件保留）" if ok else "未生效(需查)",
            "posix_note": "POSIX 删锁文件=新 inode=双持有者陷阱；平台用例带 skipif（dev T6）"}


def write_report(payload):
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    out = EVIDENCE_DIR / ("gui-daemon-lock-acceptance-%s.md" % day)
    lines = ["# GUI×daemon 锁冲突 · A6 验收轮报告（%s）" % day, "",
             "- 生成：acceptance_runner.py（全轮影子化）", ""]
    for key, title in (("env", "环境安全闸"), ("prod_db", "生产零接触核对")):
        if key in payload:
            lines += ["## %s" % title, "", FENCE + "json",
                      json.dumps(payload[key], ensure_ascii=False, indent=1), FENCE, ""]
    if "matrix" in payload:
        lines += ["## 3×2 矩阵（每格四项记录）", "",
                  "| 格 | 成功率% | 报错样本 | 归因命中 | 静默空检查 |", "|---|---|---|---|---|"]
        for c in payload["matrix"]:
            err = (c["record_2_error_samples"][0][:60] + "…") if c["record_2_error_samples"] else "—"
            lines.append("| %s | %.1f | %s | %s | %s |" % (
                c["cell"], c["record_1_success_rate_pct"], err,
                "Y" if c["record_3_attribution_hit"] else "N",
                c["record_4_silent_empty"]["verdict"]))
        lines.append("")
    for key, title in (("n10", "N=10 双轮"), ("delay_0600", "06:00 轮启动延迟实测"),
                       ("lock_lifecycle", "锁文件生命周期（行为变更单列）")):
        if key in payload:
            lines += ["## %s" % title, "", FENCE + "json",
                      json.dumps(payload[key], ensure_ascii=False, indent=1)[:4000], FENCE, ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description="A6 验收轮自动化（全轮影子化）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--n10", action="store_true")
    ap.add_argument("--round", choices=["baseline", "final"], default="final")
    ap.add_argument("--delay-0600", action="store_true")
    ap.add_argument("--lock-lifecycle", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.list:
        print("3×2 矩阵（每格四项记录）:")
        for g in GUI_STATES:
            for d in DAEMON_STATES:
                print("  gui=%-7s x daemon=%-11s" % (g, d))
        print("")
        print("独立项: N=10 双轮(--round baseline|final) / 06:00 延迟(只读生产日志) / 锁生命周期")
        return 0
    print("=== 安全闸：影子路径校验 ===")
    paths = assert_shadow_paths()
    print(json.dumps(paths, ensure_ascii=False, indent=1))
    print("")
    print("=== 生产零接触核对 ===")
    prod = prod_db_untouched()
    print(json.dumps(prod, ensure_ascii=False, indent=1))
    if args.dry_run:
        print("")
        print("[dry-run] 影子化与安全闸通过；未执行任何场景（零副作用）。")
        return 0
    payload = {"env": paths, "prod_db": prod, "generated_at": datetime.now().isoformat()}
    if args.matrix or args.all:
        payload["matrix"] = run_matrix()
    if args.n10 or args.all:
        payload["n10"] = run_n10(args.round)
    if args.delay_0600 or args.all:
        payload["delay_0600"] = run_delay_0600()
    if args.lock_lifecycle or args.all:
        payload["lock_lifecycle"] = run_lock_lifecycle()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "acceptance_payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    out = write_report(payload)
    print("")
    print("报告: " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())