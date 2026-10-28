#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T001 基线复现：常驻采集持库时，回测侧无法可靠打开主库。

复现的是本 feature 要解决的失败形态：
  1) 跨进程「一方读写 + 另一方只读」对同一 DuckDB 文件不可行 → 回测打不开库；
  2) 回测数据层连接失败时静默返回 None → 各取数方法返回空 → 「空数据静默跑完」。

两种模式：
  sim  （默认，安全）：临时目录里造一个小库，子进程持读写连接，主进程尝试只读打开并查询。
                       不启 daemon、不碰生产主库 data/quantstudio.db。
  live （需显式 --i-know-live-occupancy）：对真实主库做只读探测。注意：只读打开可能触发
                       WAL 回放（历史上有 GUI 构造期只读连接被阻塞 22.3 分钟的案例），
                       且会与运行中的采集进程争用。执行前必须按铁律 19 声明占用窗口。

用法：
    python scripts/repro_backtest_while_collecting.py --mode sim
    python scripts/repro_backtest_while_collecting.py --mode sim --out docs/evidence/.../baseline-repro.txt

退出码：sim 复现成功（即确实观察到互斥）→ 0；未观察到（环境差异）→ 2，如实报告不粉饰。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(
    ROOT, "docs", "evidence", "backtest-coexistence-20261027", "baseline-repro.txt"
)
MAIN_DB = os.path.join(ROOT, "data", "quantstudio.db")

# 子进程：持读写连接，直到父进程让它退出
_HOLDER = r'''
import sys, time, duckdb
db, hold = sys.argv[1], float(sys.argv[2])
con = duckdb.connect(db)
con.execute("CREATE TABLE IF NOT EXISTS probe(a INTEGER)")
con.execute("INSERT INTO probe VALUES (1)")
print("HOLDER_READY", flush=True)
time.sleep(hold)
con.close()
print("HOLDER_DONE", flush=True)
'''

# 静态证据：既有生产实测与数据层静默路径（不执行，仅登记出处）
STATIC_EVIDENCE = [
    (
        "生产实测（2026-10-23）",
        "docs/evidence/daemon-gui-start-failure-20261023.md:152-154",
        "daemon 运行中，任何进程（含 read_only）都无法打开："
        "`Cannot open file … 另一个程序正在使用此文件`",
    ),
    (
        "数据层静默路径",
        "quantstudio/backtest/providers/duckdb_data_access.py:183-204",
        "`_get_conn()` 只记首条 WARNING 并返回 None；各取数方法 `if conn is None` 直接返回 "
        "→ 空结果静默跑完（本 feature FR-010/011 要堵的口子）",
    ),
    (
        "官方并发模型（DuckDB 1.4 LTS）",
        "https://duckdb.org/docs/1.4/connect/concurrency",
        "仅支持「单进程读写」或「多进程只读、无写者」；跨进程一写多读不在支持之列",
    ),
]


def _simulate(hold_sec: float = 8.0) -> dict:
    import duckdb  # 主进程也需要

    tmpdir = tempfile.mkdtemp(prefix="qs_repro_")
    db = os.path.join(tmpdir, "probe.db")
    rec: dict = {
        "mode": "sim",
        "db": db,
        "duckdb_version": duckdb.__version__,
        "python": sys.version.split()[0],
        "hold_sec": hold_sec,
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, db, str(hold_sec)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = (proc.stdout.readline() or "").strip() if proc.stdout else ""
        rec["holder_ready"] = ready == "HOLDER_READY"

        t0 = time.perf_counter()
        try:
            con = duckdb.connect(db, read_only=True)
            rec["ro_open"] = "success"
            try:
                n = con.execute("SELECT count(*) FROM probe").fetchone()[0]
                rec["rows_read"] = int(n)
            except Exception as e:  # noqa: BLE001
                rec["query_error"] = f"{type(e).__name__}: {e}"
            finally:
                try:
                    con.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            rec["ro_open"] = "fail"
            rec["error_type"] = type(e).__name__
            rec["error_text"] = str(e)
        rec["probe_elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    finally:
        try:
            proc.wait(timeout=hold_sec + 20)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr = ""
        try:
            stderr = (proc.stderr.read() or "").strip() if proc.stderr else ""
        except Exception:  # noqa: BLE001
            pass
        rec["holder_stderr_tail"] = stderr[-400:]
    return rec


def _live_probe(db_path: str) -> dict:
    import duckdb

    rec: dict = {"mode": "live", "db": db_path, "exists": os.path.exists(db_path)}
    if not rec["exists"]:
        rec["result"] = "db_missing"
        return rec
    t0 = time.perf_counter()
    try:
        con = duckdb.connect(db_path, read_only=True)
        rec["ro_open"] = "success"
        try:
            n = con.execute("SELECT 1").fetchone()
            rec["probe_rows"] = n
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        rec["ro_open"] = "fail"
        rec["error_type"] = type(e).__name__
        rec["error_text"] = str(e)
    rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return rec


def _render(rec: dict, args) -> str:
    lines = [
        "=" * 78,
        "T001 基线复现报告 — 常驻采集持库 vs 回测打开主库",
        "=" * 78,
        f"生成时间      : {datetime.now().isoformat(timespec='seconds')}",
        f"模式          : {rec.get('mode')}",
        f"Python        : {rec.get('python')}",
        f"DuckDB        : {rec.get('duckdb_version')}",
        f"探针库        : {rec.get('db')}",
        "",
        "── 主复现结论 ──",
    ]
    if rec.get("mode") == "sim":
        lines.append(f"持锁子进程就绪 : {rec.get('holder_ready')}")
        lines.append(f"只读打开结果   : {rec.get('ro_open')}")
        if rec.get("ro_open") == "fail":
            lines.append(f"错误类型       : {rec.get('error_type')}")
            lines.append(f"错误原文       : {rec.get('error_text')}")
        else:
            lines.append(f"读到的行数     : {rec.get('rows_read')}")
            if rec.get("query_error"):
                lines.append(f"查询错误       : {rec.get('query_error')}")
        lines.append(f"探测耗时(ms)   : {rec.get('probe_elapsed_ms')}")
    else:
        lines.append(f"主库存在       : {rec.get('exists')}")
        lines.append(f"只读打开结果   : {rec.get('ro_open')}")
        if rec.get("ro_open") == "fail":
            lines.append(f"错误原文       : {rec.get('error_text')}")
        lines.append(f"探测耗时(ms)   : {rec.get('elapsed_ms')}")

    lines += ["", "── 静态证据（既有实测与代码位置，非本次新测）──"]
    for name, loc, note in STATIC_EVIDENCE:
        lines.append(f"• {name}")
        lines.append(f"  出处: {loc}")
        lines.append(f"  要点: {note}")

    lines += ["", "── 失败形态判定 ──"]
    if rec.get("ro_open") == "fail":
        lines.append("形态 A：回测侧**打不开库**（显式 IO 错误）—— 当前唯一可用路径是人工停采集。")
    elif rec.get("ro_open") == "success" and rec.get("rows_read") == 0:
        lines.append("形态 B：能打开但**读到空数据**（静默跑完）—— 诚信风险，本 feature 明令禁止。")
    elif rec.get("ro_open") == "success":
        lines.append("未复现互斥（环境差异）：本次只读打开成功且读到数据——如实记录，不据此下结论。")
    else:
        lines.append("无法判定（探针未完成）。")

    lines += ["", "── 机器可读 ──", json.dumps(rec, ensure_ascii=False, indent=2), ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="T001 基线复现")
    ap.add_argument("--mode", choices=["sim", "live"], default="sim")
    ap.add_argument("--hold-sec", type=float, default=8.0)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--i-know-live-occupancy", action="store_true",
                    help="live 模式必填：确认已按铁律 19 声明占用窗口")
    args = ap.parse_args()

    if args.mode == "live" and not args.i_know_live_occupancy:
        print("拒绝执行 live 模式：需 --i-know-live-occupancy，且先声明 daemon 占用窗口（铁律 19）。",
              file=sys.stderr)
        return 3

    rec = _simulate(args.hold_sec) if args.mode == "sim" else _live_probe(MAIN_DB)
    text = _render(rec, args)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"[T001] 报告已写入 {args.out}")
    else:
        print(text)

    if args.mode == "sim":
        return 0 if rec.get("ro_open") == "fail" else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
