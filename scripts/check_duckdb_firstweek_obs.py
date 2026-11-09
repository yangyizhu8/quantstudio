#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""duckdb 1.5.6 升级后首周观测（T049 每日读数采集 · spec 002-duckdb-1.5-upgrade）。

设计约束（2026-11-08 实测固化）：
- daemon 以 `--mode forever` 常驻并**全程持 DuckDB 写锁** ⇒ 本脚本**绝不打开主库**
  （Windows 文件锁层面 RO/RW 均被拒），全部读数取自：文件统 / 进程面 / 日志 / 版本门 CLI；
- 判据：上游缺陷 FATAL 关键词命中数 **须 = 0**；WAL < 256MB；版本门 PASS。

用法：
    python scripts/check_duckdb_firstweek_obs.py            # 文本读数
    python scripts/check_duckdb_firstweek_obs.py --json     # 机读
    python scripts/check_duckdb_firstweek_obs.py --evidence # 追加落盘到窗口证据 §7.4 日表
退出码：0 = 全部判据达标；1 = 有判据未达（须人工处置）；2 = 采集异常（脚本层问题）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY311 = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
FATAL_KEYWORD = "Failed to delete all rows from index"
# 首周观测窗口起点（升级窗口日 00:00）：只有此后的日志属「升级后故障面」
OBS_WINDOW_START = "2026-11-07 00:00:00"

# 占用声明语义分类（2026-11-09 总调度裁定 · 判据改进）
# 豁免仅适用「停机/接管/阶段执行」类语义；若声明文本属「在位/正常供血描述」，
# 则 daemon 缺席时不得豁免 ⇒ 判「声明与实态不符」并单列告警（避免豁免掩盖供血缺席）。
STOP_WINDOW_PATTERNS = ("停", "停机", "接管", "阶段执行", "升级", "窗口", "维护",
                        "迁移", "重建", "重启", "水位提交轮")
INPLACE_PATTERNS = ("常驻", "采集中", "运行中", "正常供血", "在跑")
EVIDENCE = os.path.join(REPO, "docs", "evidence", "duckdb-1.5-upgrade-window-evidence.md")
DAEMON_RE = re.compile(r"quantstudio\.pipeline\.daemon")
DAEMON_MODES = ("--mode forever", "--mode once")


def sh(cmd: List[str]) -> Dict[str, Any]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=REPO, timeout=180)
        return {"rc": r.returncode, "out": (r.stdout or "").strip(),
                "err": (r.stderr or "").strip()}
    except Exception as exc:  # noqa: BLE001
        return {"rc": -1, "out": "", "err": "%s: %s" % (type(exc).__name__, exc)}


def daemon_processes() -> List[Dict[str, Any]]:
    """daemon 进程面（双形态：模块形态 `-m quantstudio.pipeline.daemon` 与脚本形态 `…\\pipeline\\daemon.py`）。"""
    out: List[Dict[str, Any]] = []
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "ForEach-Object { \"$($_.ProcessId)|$($_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss'))|$($_.CommandLine -replace '\\|','/')\" }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            pid, start, cmd = line.split("|", 2)
            if not DAEMON_RE.search(cmd):
                continue
            if "--help" in cmd:          # 查询调用，非实例，排除
                continue
            mode = "once" if "--mode once" in cmd else ("forever" if "--mode forever" in cmd else "?")
            out.append({"pid": pid.strip(), "start": start.strip(), "mode": mode})
    except Exception as exc:  # noqa: BLE001
        out.append({"error": "%s: %s" % (type(exc).__name__, exc)})
    return out


def classify_declaration(occ: Dict[str, Any]) -> str:
    """声明语义分类：stop_window（停机/接管/阶段执行）｜inplace（在位/供血描述）｜unclassified。

    在位描述**优先**：声明若宣称「常驻/采集中」等，即便同时含窗口词，也按在位描述处理
    ——因为该声明断言的是"正常供血"，缺席即与声明冲突（2026-11-09 总调度裁定）。
    """
    text = " ".join(str(occ.get(k) or "") for k in ("operation", "purpose", "note"))
    if any(p in text for p in INPLACE_PATTERNS):
        return "inplace"
    if any(p in text for p in STOP_WINDOW_PATTERNS):
        return "stop_window"
    return "unclassified"


def collect() -> Dict[str, Any]:
    now = dt.datetime.now()
    res: Dict[str, Any] = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "date": now.strftime("%Y-%m-%d")}

    # ① 库尺寸 + WAL（文件系统，不开库）
    db = os.path.join(REPO, "data", "quantstudio.db")
    try:
        st = os.stat(db)
        res["db_gb"] = round(st.st_size / (1024 ** 3), 2)
        res["db_mtime"] = dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:  # noqa: BLE001
        res["db_error"] = str(exc)
    wal = db + ".wal"
    if os.path.exists(wal):
        res["wal_mb"] = round(os.path.getsize(wal) / (1024 ** 2), 2)
    else:
        res["wal_mb"] = 0.0

    # ② daemon 进程面
    res["daemons"] = daemon_processes()

    # ③ 上游缺陷 FATAL 关键词（日志扫描，不开库）
    logdir = os.path.join(REPO, "data", "logs")
    logs: List[str] = []
    hits: List[str] = []
    qa_hits: List[str] = []
    try:
        # 只统计首周窗口内（OBS_WINDOW_START 之后修改）的日志 —— 历史日志含升级前既有故障，
        # 计入即误报（升级后故障面才是本观测的对象）
        wstart = dt.datetime.strptime(OBS_WINDOW_START, "%Y-%m-%d %H:%M:%S").timestamp()
        files = sorted((os.path.join(logdir, f) for f in os.listdir(logdir) if f.endswith(".log")),
                       key=os.path.getmtime, reverse=True)[:60]
        files = [f for f in files if os.path.getmtime(f) >= wstart]
        for f in files:
            logs.append(os.path.basename(f))
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if FATAL_KEYWORD in line:
                            hits.append("%s:%d" % (os.path.basename(f), i))
                        # 只计 CLI 级失败结论行（明细行是检测输出，不等于失败结论）
                        if "quality audit failed" in line:
                            qa_hits.append("%s:%d" % (os.path.basename(f), i))
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001
        res["log_error"] = str(exc)
    res["logs_scanned"] = len(logs)
    res["fatal_hits"] = len(hits)
    res["fatal_hit_files"] = hits[:10]
    res["qa_failed_count"] = len(qa_hits)
    res["qa_failed_files"] = qa_hits[:10]

    # ④ 版本门（判定源 CLI；不触主库）
    g = sh([PY311, "-m", "quantstudio.pipeline.duckdb_version_gate", "--check"])
    res["gate_rc"] = g["rc"]
    res["gate_out"] = g["out"].splitlines()[0] if g["out"] else g["err"][:120]

    # ⑤ daemon 首轮耗时（wrapper 心跳/日志线索）
    hb = os.path.join(logdir, "daemon_guard_heartbeat.log")
    res["heartbeat_tail"] = []
    if os.path.exists(hb):
        try:
            with open(hb, "r", encoding="utf-8", errors="replace") as fh:
                res["heartbeat_tail"] = [l.strip() for l in fh.readlines()[-3:]]
        except Exception:  # noqa: BLE001
            pass

    # ④ 占用声明（铁律 19）：声明窗内 daemon 缺席属计划内停机
    occ = {"occupied": False, "owner": None, "operation": None, "window": None}
    occ_path = os.path.join(REPO, "data", "daemon_occupancy.json")
    try:
        if os.path.exists(occ_path):
            with open(occ_path, "r", encoding="utf-8", errors="replace") as fh:
                raw = json.load(fh)
            occ = {"occupied": bool(raw.get("occupied")),
                   "owner": raw.get("owner"), "operation": raw.get("operation"),
                   "window": raw.get("window")}
            sem = classify_declaration(occ) if occ["occupied"] else None
            occ["semantics"] = sem
            if occ["occupied"]:
                w = occ.get("window") or {}
                in_win = True
                try:
                    st = dt.datetime.fromisoformat(str(w.get("start")))
                    en = dt.datetime.fromisoformat(str(w.get("end")))
                    in_win = st <= dt.datetime.now() <= en
                except Exception:
                    in_win = True   # 窗口字段缺失 ⇒ 保守视为窗内（语义分类仍生效）
                occ["in_window"] = in_win
                # 在位描述类 ⇒ 不豁免（缺席即与声明冲突）；停机窗/未分类 ⇒ 窗内豁免
                res["occupancy_excused"] = in_win and sem in ("stop_window", "unclassified")
                res["declaration_inplace"] = (sem == "inplace")
            else:
                res["occupancy_excused"] = False
                res["declaration_inplace"] = False
    except Exception as exc:  # noqa: BLE001
        res["occupancy_error"] = str(exc)
        res["occupancy_excused"] = False
    res["occupancy"] = occ

    # 判据
    checks = {
        "fatal_zero": res.get("fatal_hits", -1) == 0,
        "wal_under_256mb": res.get("wal_mb", 999) < 256,
        "gate_pass_1_5_6": res.get("gate_rc") == 0 and "PASS 1.5.6" in res.get("gate_out", ""),
        # 供血主体 = --mode forever 常驻实例（once 轮为一次性任务，会自然消失，不作存活判据）
        "daemon_alive": (any(isinstance(d, dict) and d.get("mode") == "forever" for d in res.get("daemons", []))
                         or res.get("occupancy_excused", False)),
        # 占用声明窗内缺席属计划内停机（铁律 19 声明窗）⇒ 不判供血失败
        "daemon_absence_excused": res.get("occupancy_excused", False),
        # 声明称「在位/正常供血」但实际缺席 ⇒ 声明与实态不符（单列告警，不豁免）
        "no_declaration_mismatch": not (
            res.get("declaration_inplace")
            and not any(isinstance(d, dict) and d.get("mode") == "forever" for d in res.get("daemons", []))
        ),
        "once_running": any(isinstance(d, dict) and d.get("mode") == "once" for d in res.get("daemons", [])),
        # 采集轮质量审计失败（首周内须为 0；属升级后故障面，我方只记录不修他线域）
        "no_qa_failure": res.get("qa_failed_count", -1) == 0,
    }
    res["checks"] = checks
    res["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    res["exit_code"] = 0 if all(checks.values()) else 1
    return res


def render(res: Dict[str, Any]) -> str:
    c = res.get("checks", {})
    mark = {True: "✓", False: "✗"}
    lines = [
        "=== duckdb 1.5.6 首周观测（%s）===" % res.get("ts"),
        "  daemon 实例: %s" % (", ".join("pid %s(%s,%s)" % (d.get("pid"), d.get("mode"), d.get("start"))
                                   for d in res.get("daemons", []) if isinstance(d, dict) and "pid" in d)
                               or "(无)"),
        "  版本门: %s (rc=%s)" % (res.get("gate_out"), res.get("gate_rc")),
        "  主库: %s GB  mtime=%s" % (res.get("db_gb"), res.get("db_mtime")),
        "  WAL: %s MB" % res.get("wal_mb"),
        "  FATAL 关键词 '%s': 命中 %s（扫 %s 个日志）" % (FATAL_KEYWORD, res.get("fatal_hits"), res.get("logs_scanned")),
        "  占用声明: %s" % (("声明窗内（%s %s）**语义=%s** ⇒ %s" % (
            res.get("occupancy", {}).get("owner"), res.get("occupancy", {}).get("operation"),
            res.get("occupancy", {}).get("semantics"),
            "在位描述类 ⇒ **不豁免**（缺席即与声明不符）" if res.get("declaration_inplace")
            else "停机窗类 ⇒ 缺席属计划内停机，豁免"))
            if (res.get("occupancy_excused") or res.get("declaration_inplace"))
            else "无（data/daemon_occupancy.json 无进行中声明）"),
        "  ⚠ 告警: %s" % ("**声明与实态不符**：声明称在位/正常供血，但无 forever 实例"
                            if not res.get("checks", {}).get("no_declaration_mismatch", True) else "无"),
        "  判据: FATAL=0 %s ｜ WAL<256MB %s ｜ 版本门 PASS 1.5.6 %s ｜ forever 常驻在位 %s ｜ (once 轮在跑 %s) ｜ 采集轮质量审计失败 0 %s（命中 %s）"
        % (
           mark.get(c.get("fatal_zero"), "?"), mark.get(c.get("wal_under_256mb"), "?"),
           mark.get(c.get("gate_pass_1_5_6"), "?"), mark.get(c.get("daemon_alive"), "?"),
           mark.get(c.get("once_running"), "?"), mark.get(c.get("no_qa_failure"), "?"),
           res.get("qa_failed_count")),
        "  判定: %s" % res.get("verdict"),
    ]
    if res.get("fatal_hit_files"):
        lines.append("  ⚠ 命中文件: %s" % ", ".join(res["fatal_hit_files"]))
    return "\n".join(lines)


def append_evidence(res: Dict[str, Any]) -> None:
    """把当日读数追加进窗口证据（§7.4 日表）。"""
    c = res.get("checks", {})
    row = ("| %s %s | FATAL 命中 %s（扫 %s 日志）｜ WAL %s MB ｜ 版本门 %s ｜ daemon %s ｜ 采集轮质量审计失败 %s ｜ 判据 %s ｜ **%s** |"
           % (res.get("date"), (res.get("ts") or "")[11:19],
              res.get("fatal_hits"), res.get("logs_scanned"), res.get("wal_mb"),
              res.get("gate_out"),
              ", ".join("pid %s(%s)" % (d.get("pid"), d.get("mode")) for d in res.get("daemons", []) if isinstance(d, dict) and "pid" in d) or "无",
              res.get("qa_failed_count"),
              " ".join("%s=%s" % (k, "✓" if v else "✗") for k, v in sorted(c.items())),
              res.get("verdict")))
    text = ""
    if os.path.exists(EVIDENCE):
        with open(EVIDENCE, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    if "### §7.4 首周观测日表" not in text:
        text = text.rstrip() + ("\n\n### §7.4 首周观测日表（每日一行 · 由 `scripts/check_duckdb_firstweek_obs.py --evidence` 自动追加）\n\n"
                                "| 日期 时点 | 判据 | 判定 |\n|---|---|---|\n")
    text = text.rstrip() + "\n" + row + "\n"
    with open(EVIDENCE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="duckdb 首周观测读数采集")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--evidence", action="store_true", help="追加落盘到窗口证据 §7.4 日表")
    args = ap.parse_args(argv)
    res = collect()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(render(res))
    if args.evidence:
        append_evidence(res)
        print("  已落盘 §7.4 日表: %s" % os.path.relpath(EVIDENCE, REPO))
    return int(res.get("exit_code", 2))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
