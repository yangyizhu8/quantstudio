# -*- coding: utf-8 -*-
"""未决项 5 取证（只读）：`qfq_trigger_queue` 中 factor_new 通道的覆盖度。

判定目标：情形 A（锚前移，新更大 time）是否已由既有 factor_new 通道留痕？
  - 若 factor_new trigger 有量且有近期留痕 → 情形 A 已覆盖 ⇒ T3 收敛为只管情形 B
  - 若为 0 / 无近期留痕 → 情形 A 未覆盖 ⇒ 注入点需直接构造 outbox 告警

守卫（WAL 巡检同款）：① 先探测 daemon 状态（status 文件 + psutil 存活）；
② 只读打开 + 查询在 daemon 线程执行，**超时即放弃**（防自卡死，CASE-007 教训）。
"""
import json
import sys
import threading
import time
from pathlib import Path

import psutil

DB = "data/quantstudio.db"
STATUS = Path("data/daemon_status.json")
TIMEOUT_S = 90


def log(*a):
    print(*a, flush=True)


def daemon_state():
    if not STATUS.exists():
        return False, "无 status 文件（视为未运行）"
    try:
        s = json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception as e:
        return True, f"status 不可解析（保守视为运行中）: {e}"
    pid = s.get("pid")
    alive = bool(pid) and psutil.pid_exists(int(pid))
    running = str(s.get("status", "")).lower() in ("running", "stopping", "stop_requested") and alive
    return running, f"status={s.get('status')} pid={pid} alive={alive}"


res = {}


def query():
    import duckdb

    con = duckdb.connect(DB, read_only=True)
    try:
        res["cols"] = [r[0] for r in con.execute("DESCRIBE qfq_trigger_queue").fetchall()]
        res["total"] = con.execute("SELECT count(*) FROM qfq_trigger_queue").fetchone()[0]
        res["by_type"] = con.execute(
            "SELECT trigger_type, count(*) FROM qfq_trigger_queue GROUP BY 1 ORDER BY 2 DESC").fetchall()
        res["fn_total"] = con.execute(
            "SELECT count(*) FROM qfq_trigger_queue WHERE trigger_type='factor_new'").fetchone()[0]
        res["fn_dates"] = con.execute(
            "SELECT min(effective_date), max(effective_date) FROM qfq_trigger_queue "
            "WHERE trigger_type='factor_new'").fetchone()
        res["fn_sample"] = con.execute(
            "SELECT trigger_id, asset_type, code, detection_source, effective_date, factor_old, factor_new "
            "FROM qfq_trigger_queue WHERE trigger_type='factor_new' "
            "ORDER BY effective_date DESC NULLS LAST LIMIT 8").fetchall()
        res["by_source"] = con.execute(
            "SELECT detection_source, count(*) FROM qfq_trigger_queue GROUP BY 1 ORDER BY 2 DESC LIMIT 10").fetchall()
        try:
            res["cursor"] = con.execute(
                "SELECT detector_name, asset_type, last_as_of, last_run_id, status FROM qfq_observation_cursor").fetchall()
        except Exception as e:
            res["cursor"] = f"查询失败: {type(e).__name__}: {e}"
    finally:
        con.close()


running, why = daemon_state()
log(f"[guard] daemon: {why} → {'**在运行**（仍尝试，超时即放弃）' if running else '未运行（空闲窗，安全）'}")

t0 = time.time()
t = threading.Thread(target=query, daemon=True, name="t3-probe")
t.start()
t.join(TIMEOUT_S)
if t.is_alive():
    log(f"[abort] {TIMEOUT_S}s 未完成 → 放弃（不自卡死；线程为 daemon，不阻塞退出）")
    sys.exit(2)

log(f"[ok] 查询完成，耗时 {time.time()-t0:.2f}s")
log(f"[1] qfq_trigger_queue 列: {res.get('cols')}")
log(f"[2] 总行数: {res.get('total')}")
log(f"[3] 按 trigger_type 分布: {res.get('by_type')}")
log(f"[4] factor_new 行数: {res.get('fn_total')}｜effective_date 区间: {res.get('fn_dates')}")
log(f"[5] factor_new 样例（最近 8 条）:")
for row in res.get("fn_sample") or []:
    log("    ", row)
log(f"[6] 按 detection_source 分布（前 10）: {res.get('by_source')}")
log(f"[7] qfq_observation_cursor: {res.get('cursor')}")
