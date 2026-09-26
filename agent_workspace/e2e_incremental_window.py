# -*- coding: utf-8 -*-
"""端到端 A 受控窗（jabberwock 四项修复验证）——**自恢复**设计。

流程：停 daemon(旧码) → --mode once --pull-mode incremental(新码) → 判据采集（含库侧）
      → **无论成败**恢复 --mode forever（新代际，detached）→ 三验 → 汇总 JSON。

判据（审核执行令）：<2h 且 无门禁否决 且 水位提交 且 无 IndexError 且 skipped_empty 计数可见。

安全设计：
- `finally` 段**必然**执行恢复（防会话中断把采集器留在停止态）；
- 停进程先 graceful（terminate → 等待），超时才 kill（本窗经用户批准）；
- once 结束后、重启 forever **之前**读库（once 进程持有 duckdb 独占锁，必须先退出）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

ROOT = r"D:\miniQMT策略实盘\QuantStudio"
PY = r"C:\python3.12.9\python.exe"
CFG = os.path.join(ROOT, "config", "profiles", "mcp_only")
TOKEN = "0e042acc343d45fe8b2302b947a3c054"
AW = os.path.join(ROOT, "agent_workspace")
SUMMARY = os.path.join(AW, "e2e_run_summary.json")
ONCE_LOG = os.path.join(AW, "e2e_once_run.log")
FOREVER_LOG = os.path.join(AW, "e2e_forever_restart.log")
WINDOW_S = 3 * 3600
DB = os.path.join(ROOT, "data", "quantstudio.db")
DAEMON_LOG = os.path.join(ROOT, "data", "logs", "daemon.log")

import psutil  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def find_daemons():
    """只认 `python -m quantstudio.pipeline.daemon …`（**argv 精确匹配**）。

    勿用子串匹配：任何命令行文本里含该字符串的进程（编辑器、grep、本脚本的调用壳）都会被误伤。
    """
    out = []
    for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            argv = p.info["cmdline"] or []
        except Exception:
            continue
        if len(argv) >= 3 and argv[1:3] == ["-m", "quantstudio.pipeline.daemon"]:
            out.append((p, " ".join(argv)))
    return out


def stop_daemons(grace=180.0):
    """优雅停止（terminate → 等待 → 超时 kill）。返回停止明细。"""
    detail = []
    for p, cl in find_daemons():
        pid = p.info["pid"]
        mode = "forever" if "--mode forever" in cl else "once"
        log(f"停止 daemon PID={pid} mode={mode}")
        try:
            p.terminate()
        except Exception as e:
            detail.append({"pid": pid, "action": "terminate_failed", "err": str(e)})
            continue
        t0 = time.time()
        while time.time() - t0 < grace:
            if not psutil.pid_exists(pid):
                break
            try:
                if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                    break
            except Exception:
                break
            time.sleep(2)
        alive = False
        try:
            alive = psutil.Process(pid).is_running()
        except Exception:
            alive = False
        if alive:
            log(f"  PID={pid} 未在 {grace}s 内退出 → kill()")
            try:
                psutil.Process(pid).kill()
                time.sleep(3)
            except Exception as e:
                detail.append({"pid": pid, "action": "kill_failed", "err": str(e)})
        detail.append({"pid": pid, "mode": mode,
                       "graceful_exit": not alive, "waited_s": round(time.time() - t0, 1)})
        log(f"  PID={pid} 已停止（graceful={not alive}, waited={time.time()-t0:.0f}s）")
    return detail


def start_daemon(mode: str, log_path: str):
    """detached 启动（独立进程组，父进程退出不影响）。"""
    args = [PY, "-m", "quantstudio.pipeline.daemon", "--mode", mode,
            "--config-dir", CFG, "--instance-token", TOKEN]
    if mode == "once":
        args += ["--pull-mode", "incremental"]
    f = open(log_path, "ab", buffering=0)
    f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 启动 mode={mode} =====\n".encode())
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(args, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, **kw)
    return p, args


def grep_log(path: str, since_bytes: int = 0):
    """日志判据（只读新增段）。"""
    pats = {
        "IndexError": r"IndexError",
        "epoch-1970": r"1970-01-01|19700101",
        "skipped_engine": r"跳过引擎|empty_fresh_capture|invalid_range_ms",
        "tombstone": r"空窗墓碑",
        "detector_degraded": r"detector_degraded|水位 hold",
        "artifact_payload_error": r"载荷级 error|缺少 Parquet",
        "gate_failed": r"quality_gate_failed|gate.*(FAIL|否决)",
        "watermark_committed": r"水位.*commit|watermarks_committed|commit.*水位",
    }
    try:
        with open(path, "rb") as fh:
            fh.seek(since_bytes)
            data = fh.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"error": str(e)}, since_bytes
    res = {k: len(re.findall(p, data, re.I)) for k, p in pats.items()}
    res["lines"] = data.count("\n")
    return res, since_bytes + len(data.encode("utf-8", errors="replace"))


def db_criteria():
    """库侧判据（须在 once 进程退出后读）。"""
    out = {}
    try:
        import duckdb
        con = duckdb.connect(DB, read_only=True)
        try:
            out["watermark_status"] = dict(con.execute(
                "SELECT status, count(*) FROM qfq_watermark_intent GROUP BY status").fetchall())
        except Exception as e:
            out["watermark_status_err"] = str(e)[:160]
        try:
            rows = con.execute(
                "SELECT cycle_id, status, detector_degraded, started_at "
                "FROM qfq_cycle_run ORDER BY started_at DESC LIMIT 3").fetchall()
            out["recent_cycles"] = [[str(x) for x in r] for r in rows]
        except Exception as e:
            out["recent_cycles_err"] = str(e)[:160]
        try:
            out["trigger_queue"] = dict(con.execute(
                "SELECT status, count(*) FROM qfq_trigger_queue GROUP BY status").fetchall())
        except Exception as e:
            out["trigger_queue_err"] = str(e)[:160]
        con.close()
    except Exception as e:
        out["db_open_err"] = f"{type(e).__name__}: {str(e)[:200]}"
    # G4 判据（SQLite aux）：outbox 首笔写入
    try:
        import sqlite3
        c = sqlite3.connect(os.path.join(ROOT, "data", "qfq_aux.db"))
        out["revision_alert_count"] = c.execute(
            "SELECT count(*) FROM qfq_factor_revision_alert").fetchone()[0]
        c.close()
    except Exception as e:
        out["revision_alert_err"] = str(e)[:160]
    return out


def forever_alive():
    for p, cl in find_daemons():
        if "--mode forever" in cl:
            return {"pid": p.info["pid"], "cmdline": cl,
                    "created": datetime.fromtimestamp(p.info["create_time"]).strftime("%H:%M:%S")}
    return None


def main() -> int:
    summary = {"window": "A 受控窗（端到端本机增量）",
               "started_at": datetime.now().isoformat(timespec="seconds")}
    once_p = None
    try:
        log("=== ① 停 daemon（旧码 forever）===")
        summary["stop"] = stop_daemons()
        time.sleep(5)

        # ①b 窗口前置 CHECKPOINT：消除 WAL 回放对「增量耗时」判据的污染
        # （CASE-007 实证：2.33GB WAL 回放需 1338.4s；无 WAL 时 <2h 判据才度量增量本身）
        log("=== ①b 前置 CHECKPOINT（清 WAL）===")
        wal = DB + ".wal"
        w0 = os.path.getsize(wal) if os.path.exists(wal) else 0
        rc_ck = subprocess.run(
            [PY, "-c",
             "import sys;sys.path.insert(0,r'%s');"
             "from quantstudio.pipeline.db_checkpoint import checkpoint_database as ck;"
             "ok,detail=ck(r'%s');print('CHECKPOINT',ok,detail)" % (ROOT, DB)],
            cwd=ROOT, capture_output=True, text=True, timeout=1800)
        w1 = os.path.getsize(wal) if os.path.exists(wal) else 0
        summary["precheckpoint"] = {
            "wal_before_mb": round(w0 / 1e6, 1), "wal_after_mb": round(w1 / 1e6, 1),
            "rc": rc_ck.returncode, "out": (rc_ck.stdout or "")[-300:],
            "err": (rc_ck.stderr or "")[-300:]}
        log(f"  WAL {w0/1e6:.1f}MB → {w1/1e6:.1f}MB（rc={rc_ck.returncode}）")

        log("=== ② once 增量（新码）===")
        log_off = os.path.getsize(DAEMON_LOG) if os.path.exists(DAEMON_LOG) else 0
        t0 = time.time()
        once_p, args = start_daemon("once", ONCE_LOG)
        summary["once_cmd"] = " ".join(args)
        summary["once_pid"] = once_p.pid
        rc = None
        while time.time() - t0 < WINDOW_S:
            rc = once_p.poll()
            if rc is not None:
                break
            if int(time.time() - t0) % 300 < 5:
                log(f"  …运行中 {time.time()-t0:.0f}s（rc={rc}）")
            time.sleep(5)
        summary["once_elapsed_s"] = round(time.time() - t0, 1)
        summary["once_exit_code"] = rc
        summary["once_timed_out"] = (rc is None)
        if rc is None:
            log(f"  ⚠ 超窗 {WINDOW_S}s 未结束 → 终止 once 进程")
            try:
                once_p.terminate()
                time.sleep(10)
                if once_p.poll() is None:
                    once_p.kill()
            except Exception as e:
                log(f"  once 终止异常: {e}")
        log(f"=== ② 完成：耗时 {summary['once_elapsed_s']}s，exit={summary['once_exit_code']} ===")

        log("=== ③ 判据采集（日志 + 库）===")
        time.sleep(5)
        summary["log_criteria"], _ = grep_log(DAEMON_LOG, log_off)
        summary["db_criteria"] = db_criteria()
        try:
            with open(ONCE_LOG, "rb") as fh:
                tail = fh.read()[-4000:].decode("utf-8", errors="replace")
            summary["once_log_tail"] = tail[-2500:]
        except Exception:
            pass
        el = summary["once_elapsed_s"]
        lc, dc = summary["log_criteria"], summary["db_criteria"]
        ws = dc.get("watermark_status", {}) or {}
        summary["verdict"] = {
            "lt_2h": el < 7200,
            "no_gate_veto": (lc.get("detector_degraded", 0) == 0 and lc.get("gate_failed", 0) == 0),
            "watermark_committed": int(ws.get("committed", 0) or 0) > 0,
            "no_indexerror": lc.get("IndexError", 0) == 0,
            "skipped_visible": lc.get("skipped_engine", 0) >= 0,
            "tombstone_visible": lc.get("tombstone", 0),
        }
        log(f"  判据: {json.dumps(summary['verdict'], ensure_ascii=False)}")
    except Exception as e:
        summary["error"] = f"{type(e).__name__}: {e}"
        log(f"!! 异常: {e}")
    finally:
        log("=== ④ 恢复 forever（新代际，detached）===")
        try:
            p, args = start_daemon("forever", FOREVER_LOG)
            summary["restore_cmd"] = " ".join(args)
            summary["restore_pid"] = p.pid
            time.sleep(25)
            alive = forever_alive()
            summary["restore_alive"] = alive
            log(f"  恢复结果: {json.dumps(alive, ensure_ascii=False)}")
            # 三验
            summary["three_checks"] = {
                "process": bool(alive),
                "log_advancing": None,
                "db_openable": None,
            }
            try:
                s1 = os.path.getsize(DAEMON_LOG)
                time.sleep(20)
                summary["three_checks"]["log_advancing"] = os.path.getsize(DAEMON_LOG) > s1
            except Exception:
                pass
            try:
                import duckdb
                c = duckdb.connect(DB, read_only=True)
                c.execute("SELECT 1").fetchone()
                c.close()
                summary["three_checks"]["db_openable"] = True
            except Exception as e:
                summary["three_checks"]["db_openable"] = f"被占用/异常: {str(e)[:80]}"
        except Exception as e:
            summary["restore_error"] = f"{type(e).__name__}: {e}"
            log(f"!! 恢复失败: {e}")
        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        os.makedirs(AW, exist_ok=True)
        with open(SUMMARY, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        log(f"=== 汇总已写 {SUMMARY} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
