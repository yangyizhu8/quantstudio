"""QFQ 重锚（rebase）常驻监控脚本（生产启用前监控框架）。

读取编排器状态表，输出每轮摘要与告警条件；支持单次快照（--once）与
持续监控（--watch <间隔秒>）。本脚本是**只读**的运维观测工具，不写任何库。

监控对象（均位于 DuckDB 主库）：
- qfq_cycle_run        每轮协调周期（triggers_found/committed/blocked/dead_letter/detector_degraded）
- qfq_trigger_queue    事件触发队列（pending/committed/blocked/dead_letter）
- qfq_reanchor_event   重锚事件（committed/blocked/failed）
- qfq_fresh_capture    fresh 采集证据（采集状态）
- qfq_watermark_intent 四价格表水位意图（committed/held）
- qfq_pending_backfill 被过滤证券欠账（pending 超期）

告警条件：
- dead_letter > 0
- committed=0 且有 pending trigger
- detector_degraded=1（最近一轮）
- pending_backfill 中有超期未解决（默认 > 7 天）

铁律：本脚本绝不修改任何数据，仅观测。生产启停由 daemon 控制，本脚本不介入。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BJ_TZ = timezone(timedelta(hours=8))
DAY_MS = 86_400_000


def _now() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _duckdb_conn(db: str):
    import duckdb
    return duckdb.connect(db, read_only=True)


def _sqlite_conn(aux: str):
    import sqlite3
    return sqlite3.connect(aux, timeout=10)


# ---------------------------------------------------------------------------
# 单轮摘要快照
# ---------------------------------------------------------------------------

def snapshot(db: str, aux: str | None, backfill_overdue_days: int = 7) -> dict:
    """读取状态表，返回一份结构化摘要。纯只读。"""
    import duckdb

    conn = _duckdb_conn(db)
    try:
        # ---- 最近一轮 cycle_run ----
        row = conn.execute(
            "SELECT cycle_id, business_date, phase, discovered_count, executed_count, "
            "success_count, failed_count, pending_count, status, detector_degraded, "
            "started_at, finished_at "
            "FROM qfq_cycle_run ORDER BY started_at DESC LIMIT 1").fetchone()
        last_cycle = None
        if row:
            last_cycle = {
                "cycle_id": row[0],
                "business_date": row[1],
                "phase": row[2],
                "triggers_found": row[3],
                "executed": row[4],
                "committed": row[5],
                "failed": row[6],
                "pending": row[7],
                "status": row[8],
                "detector_degraded": bool(row[9]),
                "started_at": str(row[10]) if row[10] is not None else None,
                "finished_at": str(row[11]) if row[11] is not None else None,
            }
        cycle_count = conn.execute("SELECT COUNT(*) FROM qfq_cycle_run").fetchone()[0]

        # ---- trigger_queue 状态分布 ----
        tq = conn.execute(
            "SELECT status, COUNT(*) FROM qfq_trigger_queue GROUP BY status").fetchall()
        trigger_status = {r[0]: r[1] for r in tq}
        dead_letter = trigger_status.get("dead_letter", 0)
        pending_triggers = trigger_status.get("pending", 0)
        committed_triggers = trigger_status.get("committed", 0)
        blocked_triggers = trigger_status.get("blocked", 0)

        # ---- reanchor_event 状态分布 ----
        re = conn.execute(
            "SELECT status, COUNT(*) FROM qfq_reanchor_event GROUP BY status").fetchall()
        reanchor_status = {r[0]: r[1] for r in re}

        # ---- fresh_capture 状态分布 ----
        fc = conn.execute(
            "SELECT status, COUNT(*) FROM qfq_fresh_capture GROUP BY status").fetchall()
        capture_status = {r[0]: r[1] for r in fc}

        # ---- 水位状态 ----
        wm = conn.execute(
            "SELECT status, COUNT(*) FROM qfq_watermark_intent GROUP BY status").fetchall()
        watermark_status = {r[0]: r[1] for r in wm}

        # ---- pending_backfill 超期（created_at 为 TIMESTAMP，按 ISO 比较）----
        overdue_cutoff_iso = (
            datetime.now(BJ_TZ) - timedelta(days=backfill_overdue_days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        pb = conn.execute(
            "SELECT COUNT(*) FROM qfq_pending_backfill "
            "WHERE status IN ('pending', 'retryable_failed', 'blocked') "
            "AND created_at < ?",
            [overdue_cutoff_iso]).fetchone()[0] if _has_table(conn, "qfq_pending_backfill") else 0
        pb_total = conn.execute(
            "SELECT COUNT(*) FROM qfq_pending_backfill").fetchone()[0] \
            if _has_table(conn, "qfq_pending_backfill") else 0
    finally:
        conn.close()

    # ---- aux（可选，因子观察/revision alert）----
    aux_info = None
    if aux and Path(aux).exists():
        ac = _sqlite_conn(aux)
        try:
            obs = ac.execute(
                "SELECT COUNT(*) FROM qfq_factor_observation").fetchone()[0]
            alert = ac.execute(
                "SELECT COUNT(*) FROM qfq_factor_revision_alert "
                "WHERE status='open'").fetchone()[0]
            aux_info = {"factor_observation_rows": obs, "open_revision_alerts": alert}
        except Exception:
            aux_info = {"error": "aux 表读取失败（可能未初始化）"}
        finally:
            ac.close()

    summary = {
        "ts": _now(),
        "db": db,
        "cycle_count": cycle_count,
        "last_cycle": last_cycle,
        "trigger_status": trigger_status,
        "dead_letter": dead_letter,
        "pending_triggers": pending_triggers,
        "committed_triggers": committed_triggers,
        "blocked_triggers": blocked_triggers,
        "reanchor_status": reanchor_status,
        "capture_status": capture_status,
        "watermark_status": watermark_status,
        "pending_backfill_total": pb_total,
        "pending_backfill_overdue": pb,
        "aux": aux_info,
    }
    summary["alarms"] = _evaluate_alarms(summary)
    return summary


def _has_table(conn, name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {name} LIMIT 1")
        return True
    except Exception:
        return False


def _evaluate_alarms(s: dict) -> list[str]:
    """返回触发的告警列表（空 = 健康）。"""
    alarms: list[str] = []
    if s["dead_letter"] > 0:
        alarms.append(f"DEAD_LETTER>0: {s['dead_letter']} 个 trigger 进入死信队列")
    if s["committed_triggers"] == 0 and s["pending_triggers"] > 0:
        alarms.append(f"COMMITTED=0 但有 {s['pending_triggers']} 个 pending trigger 未处理")
    if s["last_cycle"] and s["last_cycle"]["detector_degraded"]:
        alarms.append("DETECTOR_DEGRADED=1: 最近一轮检测器降级")
    if s["pending_backfill_overdue"] > 0:
        alarms.append(f"PENDING_BACKFILL 超期: {s['pending_backfill_overdue']} 条未解决")
    return alarms


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def _fmt_cycle(s: dict) -> str:
    lc = s["last_cycle"]
    if not lc:
        return "  [cycle] 尚无协调周期记录"
    return (f"  [cycle] {lc['cycle_id']} phase={lc['phase']} status={lc['status']} | "
            f"found={lc['triggers_found']} executed={lc['executed']} "
            f"committed={lc['committed']} failed={lc['failed']} pending={lc['pending']} "
            f"detector_degraded={lc['detector_degraded']}")


def render(s: dict, compact: bool = False) -> str:
    lines = [f"=== QFQ rebase 监控摘要 @ {s['ts']} ===",
             f"  主库: {s['db']}  (cycle 总数={s['cycle_count']})",
             _fmt_cycle(s),
             f"  [trigger] {_kv(s['trigger_status'])}  "
             f"dead_letter={s['dead_letter']} pending={s['pending_triggers']} "
             f"committed={s['committed_triggers']} blocked={s['blocked_triggers']}",
             f"  [reanchor] {_kv(s['reanchor_status'])}",
             f"  [capture] {_kv(s['capture_status'])}",
             f"  [watermark] {_kv(s['watermark_status'])}",
             f"  [backfill] total={s['pending_backfill_total']} "
             f"overdue(>7d)={s['pending_backfill_overdue']}"]
    if s["aux"]:
        lines.append(f"  [aux] {_kv({k: v for k, v in s['aux'].items()})}")
    if s["alarms"]:
        lines.append("  !!! 告警:")
        for a in s["alarms"]:
            lines.append(f"      - {a}")
    else:
        lines.append("  [OK] 无告警")
    return "\n".join(lines)


def _kv(d: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in sorted(d.items())) if d else "(空)"


def main() -> int:
    ap = argparse.ArgumentParser(description="QFQ rebase 常驻监控（只读）")
    ap.add_argument("--db", required=True, help="DuckDB 主库路径（编排器主库）")
    ap.add_argument("--aux-db", default=None, help="SQLite 辅助库路径（可选）")
    ap.add_argument("--once", action="store_true", help="单次快照后退出")
    ap.add_argument("--watch", type=int, default=0,
                    help="持续监控间隔秒（>0 时持续轮询）")
    ap.add_argument("--overdue-days", type=int, default=7,
                    help="pending_backfill 超期阈值（天，默认 7）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: 主库不存在: {args.db}", file=sys.stderr)
        return 2

    interval = args.watch if args.watch > 0 else 0
    # --once 与 --watch 互斥时：--watch 优先，否则单次
    if interval <= 0:
        s = snapshot(args.db, args.aux_db, args.overdue_days)
        if args.json:
            import json
            print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
        else:
            print(render(s))
        return 1 if s["alarms"] else 0

    # 持续监控
    print(f"[monitor] 持续监控 主库={args.db} 间隔={interval}s (Ctrl+C 退出)\n")
    try:
        while True:
            s = snapshot(args.db, args.aux_db, args.overdue_days)
            if args.json:
                import json
                print(json.dumps(s, ensure_ascii=False, default=str))
            else:
                print(render(s))
                print("-" * 70)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[monitor] 已停止")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
