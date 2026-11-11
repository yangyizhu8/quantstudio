#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""G9 停窗守候器（丙案自动触发）—— 等 daemon 释放锁即自动跑成因分布诊断。

依据（节点2 2026-11-11 裁）：
  「按『A4/M1 停窗开始』自动触发照准：节点1 停机信号即跑 --json-out
    docs/evidence/g9-cause-20261111.json（1-2min·不需另行协调时点）」

触发判据（客观代理，优于任何人工信号）：
  生产库 `duckdb.connect(read_only=True)` **成功** ⇒ DuckDB 写锁已释放 ⇒ 停窗已开始。
  （daemon --mode forever 全程排他持锁，故「能只读打开」等价于「daemon 已停」。）

稳健性设计：
  1. **二次确认**：连续 2 次成功（间隔 CONFIRM_GAP 秒）才执行——避开「daemon 刚关即重启」竞态。
  2. **幂等**：目标 JSON 已存在且非空 ⇒ 直接退出（不重跑、不覆盖既有读数）。
  3. **超时**：最长守候 MAX_WAIT_S（默认 14h），超时退出并打印原因（不静默）。
  4. **异常可诊断**：每次失败原因按类型归类计数，每 N 次打印一次进度。
  5. **纯只读**：只调用 g9_cause_distribution 的诊断逻辑，不写库。

用法：
  python scripts/g9_watch_and_run.py                       # 默认目标/输出
  python scripts/g9_watch_and_run.py --json-out <path> --max-wait-hours 14
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "data" / "quantstudio.db"
DEFAULT_OUT = REPO / "docs" / "evidence" / "g9-cause-20261111.json"

POLL_GAP = 20          # 轮询间隔（秒）
CONFIRM_GAP = 5        # 二次确认间隔（秒）
PROGRESS_EVERY = 15    # 每 N 次轮询打印一次进度


def _try_open(db: Path):
    """试只读打开；成功返回连接，失败返回 (None, 原因)。"""
    try:
        import duckdb
    except ImportError:
        return None, "no-duckdb"
    try:
        return duckdb.connect(str(db), read_only=True), None
    except Exception as e:
        msg = str(e)
        if "另一个程序正在使用此文件" in msg or "already open" in msg.lower():
            return None, "locked"
        if "No such file" in msg or "does not exist" in msg:
            return None, "missing"
        return None, f"other:{type(e).__name__}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--json-out", default=str(DEFAULT_OUT))
    ap.add_argument("--max-wait-hours", type=float, default=14.0)
    ap.add_argument("--sla-hours", type=int, default=72)
    ap.add_argument("--stale-hours", type=int, default=24)
    ap.add_argument("--skip-if-exists", action="store_true", default=True)
    a = ap.parse_args(argv)

    db = Path(a.db)
    out = Path(a.json_out)
    t_start = time.time()
    deadline = t_start + a.max_wait_hours * 3600

    def log(msg):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

    log("=" * 74)
    log("G9 停窗守候器启动（丙案自动触发）")
    log(f"  库      = {db}")
    log(f"  输出    = {out}")
    log(f"  最长守候 = {a.max_wait_hours} h")
    log(f"  触发判据 = duckdb.connect(read_only=True) 连续 2 次成功")
    log("=" * 74)

    # 幂等：已有非空结果则不重跑
    if a.skip_if_exists and out.exists() and out.stat().st_size > 200:
        log(f"目标已存在（{out.stat().st_size} B）⇒ 幂等退出，不重跑")
        return 0

    if not db.exists():
        log(f"FATAL: 库不存在 {db}")
        return 2

    reasons: dict[str, int] = {}
    poll = 0
    while time.time() < deadline:
        poll += 1
        con, why = _try_open(db)
        if con is not None:
            con.close()
            log(f"  第 {poll} 次探测：**只读打开成功** ⇒ 锁已释放，进入二次确认…")
            time.sleep(CONFIRM_GAP)
            con2, why2 = _try_open(db)
            if con2 is not None:
                con2.close()
                log("  二次确认成功 ⇒ **停窗已确定，开始执行诊断**")
                break
            log(f"  二次确认失败（{why2}）⇒ 视为竞态（daemon 已重启），继续守候")
            reasons[why2 or "unknown"] = reasons.get(why2 or "unknown", 0) + 1
            continue
        reasons[why or "unknown"] = reasons.get(why or "unknown", 0) + 1
        if poll % PROGRESS_EVERY == 0:
            elapsed = (time.time() - t_start) / 60
            log(f"  第 {poll} 次探测：锁未释放（{why}）　已守候 {elapsed:.1f} min　失败归类={reasons}")
        if why == "missing":
            log("FATAL: 库文件消失，中止")
            return 3
        time.sleep(POLL_GAP)
    else:
        log(f"超时：守候 {a.max_wait_hours} h 未见锁释放 ⇒ 退出（未产出读数）")
        log(f"失败原因归类 = {reasons}")
        return 4

    # ---- 执行诊断（复用单一内核）----
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import g9_cause_distribution as g9
    except Exception as e:
        log(f"FATAL: 无法导入 g9_cause_distribution：{type(e).__name__}: {e}")
        return 5

    log("---- 开始执行 g9_cause_distribution ----")
    t0 = time.time()
    try:
        rc = g9.main(["--db", str(db), "--json-out", str(out),
                      "--sla-hours", str(a.sla_hours), "--stale-hours", str(a.stale_hours)])
    except Exception as e:
        log(f"FATAL: 诊断执行异常 {type(e).__name__}: {e}")
        return 6
    dur = time.time() - t0
    log(f"---- 执行结束 rc={rc} 耗时 {dur:.1f}s ----")

    if rc == 0 and out.exists():
        log(f"✓ 读数已落盘：{out}（{out.stat().st_size} B）")
        log(f"  总耗时 = {(time.time()-t_start)/60:.1f} min（含守候）")
        try:
            j = json.loads(out.read_text(encoding="utf-8"))
            log(f"  verdict = {json.dumps(j.get('verdict'), ensure_ascii=False)}")
            log(f"  sla_verdict = {j.get('sla_verdict')}")
        except Exception:
            pass
        return 0
    log(f"✗ 诊断未产出（rc={rc}）；守候期失败归类 = {reasons}")
    return rc or 7


if __name__ == "__main__":
    sys.exit(main())
