#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""G9 成因分布诊断（丙案）—— 只读，供「生产静默窗」搭车执行。

背景（节点2 2026-11-11 裁＝丙先行＋乙承载）：
  G9 两判据在生产上恒红 —— QfqDeadLetter=18,713（阈值 dead_letter_max=0）
  / QfqPendingSla≈45,945（阈值 pending_sla_hours=72）。
  丙案 = 先出「成因分布」，再据分布决定是否立排水/归档（甲案）。

用法（须在 daemon 停机/无锁窗内执行；否则被 IOException 拒绝）：
  python scripts/g9_cause_distribution.py                 # 用 data/quantstudio.db
  python scripts/g9_cause_distribution.py --db <path>     # 指定库
  python scripts/g9_cause_distribution.py --json-out docs/evidence/g9-cause-YYYYMMDD.json

设计要点：
  1. **双时间基准**（新判据「快照时刻重算对照」）：同时用真 NOW() 与 MAX(updated_at)
     作基准计算 SLA 超期数；两者差值 <1% ⇒ 真信号；近全量 ⇒ 时间基准问题。
  2. 全部 SELECT，无任何写操作；连接用 read_only=True。
  3. 码族用 SUBSTR(code,1,3) 归类（沪主板 600/601/603/605、深主板 000/001/002/003、
     创业板 300/301、科创板 688/689、北交所 8xx/4xx、其余 OTHER）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("FATAL: 需要 duckdb", file=sys.stderr)
    sys.exit(2)

SLA_H = 72       # quality_audit.py:732 pending_sla_hours 默认
STALE_H = 24     # quality_audit.py:733 stale_in_progress_hours 默认
CODE_FAMILY_SQL = """
CASE
  WHEN code LIKE '600%' OR code LIKE '601%' OR code LIKE '603%' OR code LIKE '605%' THEN 'SH_MAIN'
  WHEN code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%' THEN 'SZ_MAIN'
  WHEN code LIKE '300%' OR code LIKE '301%' THEN 'SZ_GEM'
  WHEN code LIKE '688%' OR code LIKE '689%' THEN 'SH_STAR'
  WHEN code LIKE '8%'   OR code LIKE '4%'   THEN 'BJ'
  ELSE 'OTHER'
END
"""


def q(con, sql, params=None):
    return con.execute(sql, params or []).fetchall()


def dist(con, label, sql, params=None, limit=None):
    """执行分组统计并打印。sql 须返回 (key, cnt)。"""
    rows = q(con, sql, params)
    print(f"\n--- {label} ---")
    if not rows:
        print("  （无数据）")
        return {}
    shown = rows[:limit] if limit else rows
    total = sum(r[1] for r in rows)
    for k, c in shown:
        pct = (c / total * 100) if total else 0.0
        print(f"  {str(k):<34s} {c:>8,}  {pct:5.1f}%")
    if limit and len(rows) > limit:
        print(f"  …（共 {len(rows)} 组，仅显示前 {limit}）")
    print(f"  {'合计':<34s} {total:>8,}")
    return {str(k): c for k, c in rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--sla-hours", type=int, default=SLA_H)
    ap.add_argument("--stale-hours", type=int, default=STALE_H)
    a = ap.parse_args(argv)

    db = Path(a.db)
    if not db.exists():
        print(f"FATAL: 库不存在 {db}", file=sys.stderr)
        return 2
    try:
        con = duckdb.connect(str(db), read_only=True)
    except Exception as e:
        print(f"FATAL: 只读打开失败（daemon 持锁？）: {type(e).__name__}: {e}", file=sys.stderr)
        return 3

    rep = {"db": str(db), "sla_hours": a.sla_hours}
    try:
        exists = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='main' AND table_name='qfq_trigger_queue'").fetchone()[0]
        if not exists:
            print("FATAL: qfq_trigger_queue 不存在", file=sys.stderr)
            return 4

        print("=" * 78)
        print("G9 成因分布诊断（丙案）")
        print("=" * 78)
        print(f"库 = {db}")
        print(f"打开 = read_only ✓")

        # ---- 0. 总体 + 双时间基准 ----
        total = q(con, "SELECT COUNT(*) FROM qfq_trigger_queue")[0][0]
        now_db, max_upd, max_created = q(
            con, "SELECT NOW(), MAX(updated_at), MAX(created_at) FROM qfq_trigger_queue")[0]
        rep.update(total=total, now_db=str(now_db),
                   max_updated_at=str(max_upd), max_created_at=str(max_created))
        print(f"\n总行数 = {total:,}")
        print(f"DB NOW()          = {now_db}")
        print(f"MAX(updated_at)   = {max_upd}")
        print(f"MAX(created_at)   = {max_created}")

        # 双基准 SLA 对照（新判据）
        cond = "status IN ('pending','retryable_failed')"
        due = f"COALESCE(updated_at, created_at)"
        n_now = q(con, f"SELECT COUNT(*) FROM qfq_trigger_queue WHERE {cond} "
                       f"AND {due} < NOW() - INTERVAL {a.sla_hours} HOUR")[0][0]
        n_snap = q(con, f"SELECT COUNT(*) FROM qfq_trigger_queue WHERE {cond} "
                        f"AND {due} < (SELECT MAX(updated_at) FROM qfq_trigger_queue) "
                        f"- INTERVAL {a.sla_hours} HOUR")[0][0]
        diff = abs(n_now - n_snap)
        # 边界分档（2026-11-11 修：原式在 n_now==0 时误判为「时间基准可疑」）
        if n_now == 0 and n_snap == 0:
            sign = "无超期（无需判定）"
        elif n_now == 0 or n_snap == 0:
            sign = "单侧为 0（时间基准差异显著，需人工判读）"
        elif diff / n_now < 0.01:
            sign = "真信号（差值 <1%）"
        else:
            sign = "时间基准可疑（差值 ≥1%）"
        print(f"\n[双时间基准对照 · SLA={a.sla_hours}h]")
        print(f"  真 NOW() 基准   = {n_now:,}")
        print(f"  MAX(updated_at) = {n_snap:,}")
        print(f"  差值            = {diff:,}  ⇒ {sign}")
        rep.update(sla_now=n_now, sla_snapshot=n_snap, sla_verdict=sign)

        # ---- 1. status 总览 ----
        rep["by_status"] = dist(con, "status 总览",
                                "SELECT status, COUNT(*) FROM qfq_trigger_queue "
                                "GROUP BY status ORDER BY 2 DESC")

        # ---- 2. 超期 pending/retryable_failed 的成因分布 ----
        scope = f"FROM qfq_trigger_queue WHERE {cond} AND {due} < NOW() - INTERVAL {a.sla_hours} HOUR"
        print("\n" + "=" * 78)
        print(f"超期集（SLA={a.sla_hours}h）成因分布  [真 NOW() 基准]")
        print("=" * 78)
        rep["overdue_by_month"] = dist(
            con, "按月（created_at）",
            f"SELECT STRFTIME(created_at, '%Y-%m') m, COUNT(*) {scope} GROUP BY m ORDER BY m")
        rep["overdue_by_type"] = dist(
            con, "按 trigger_type",
            f"SELECT trigger_type, COUNT(*) {scope} GROUP BY 1 ORDER BY 2 DESC")
        rep["overdue_by_source"] = dist(
            con, "按 detection_source",
            f"SELECT detection_source, COUNT(*) {scope} GROUP BY 1 ORDER BY 2 DESC")
        rep["overdue_by_family"] = dist(
            con, "按码族（code 前 3 位）",
            f"SELECT {CODE_FAMILY_SQL} fam, COUNT(*) {scope} GROUP BY fam ORDER BY 2 DESC")
        rep["overdue_by_asset_type"] = dist(
            con, "按 asset_type",
            f"SELECT asset_type, COUNT(*) {scope} GROUP BY 1 ORDER BY 2 DESC")
        rep["overdue_by_attempts"] = dist(
            con, "按 attempt_count",
            f"SELECT attempt_count, COUNT(*) {scope} GROUP BY 1 ORDER BY 1")
        rep["overdue_by_last_error"] = dist(
            con, "按 last_error（TOP 12）",
            f"SELECT COALESCE(SUBSTR(last_error,1,60),'<NULL>'), COUNT(*) {scope} "
            f"GROUP BY 1 ORDER BY 2 DESC", limit=12)
        rep["overdue_by_code_top"] = dist(
            con, "按 code（TOP 15 单码）",
            f"SELECT code, COUNT(*) {scope} GROUP BY 1 ORDER BY 2 DESC", limit=15)

        # ---- 3. dead_letter 的成因分布 ----
        dscope = "FROM qfq_trigger_queue WHERE status='dead_letter'"
        print("\n" + "=" * 78)
        print("dead_letter 成因分布")
        print("=" * 78)
        rep["dl_by_month"] = dist(
            con, "按月（created_at）",
            f"SELECT STRFTIME(created_at, '%Y-%m') m, COUNT(*) {dscope} GROUP BY m ORDER BY m")
        rep["dl_by_type"] = dist(
            con, "按 trigger_type",
            f"SELECT trigger_type, COUNT(*) {dscope} GROUP BY 1 ORDER BY 2 DESC")
        rep["dl_by_source"] = dist(
            con, "按 detection_source",
            f"SELECT detection_source, COUNT(*) {dscope} GROUP BY 1 ORDER BY 2 DESC")
        rep["dl_by_family"] = dist(
            con, "按码族", f"SELECT {CODE_FAMILY_SQL} fam, COUNT(*) {dscope} GROUP BY fam ORDER BY 2 DESC")
        rep["dl_by_attempts"] = dist(
            con, "按 attempt_count",
            f"SELECT attempt_count, COUNT(*) {dscope} GROUP BY 1 ORDER BY 1")
        rep["dl_by_last_error"] = dist(
            con, "按 last_error（TOP 12）",
            f"SELECT COALESCE(SUBSTR(last_error,1,60),'<NULL>'), COUNT(*) {dscope} "
            f"GROUP BY 1 ORDER BY 2 DESC", limit=12)
        rep["dl_dead_letter_at"] = {
            "min": str(q(con, f"SELECT MIN(dead_letter_at) {dscope}")[0][0]),
            "max": str(q(con, f"SELECT MAX(dead_letter_at) {dscope}")[0][0]),
        }
        print(f"\n--- dead_letter_at 区间 ---\n  min={rep['dl_dead_letter_at']['min']}"
              f"\n  max={rep['dl_dead_letter_at']['max']}")

        # ---- 4. 时间老化分桶（判「是否还在长」）----
        print("\n" + "=" * 78)
        print("时间老化分桶（判断积压是否仍在增长）")
        print("=" * 78)
        rep["age_buckets"] = dist(
            con, "超期集按「距今天数」分桶",
            f"SELECT CASE "
            f"  WHEN DATEDIFF('day', COALESCE(updated_at,created_at), NOW()) < 7  THEN '<7d' "
            f"  WHEN DATEDIFF('day', COALESCE(updated_at,created_at), NOW()) < 30 THEN '7-30d' "
            f"  WHEN DATEDIFF('day', COALESCE(updated_at,created_at), NOW()) < 90 THEN '30-90d' "
            f"  ELSE '>=90d' END b, COUNT(*) {scope} GROUP BY b ORDER BY 1")

        # ---- 5. 三判据定版 ----
        dl = q(con, "SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='dead_letter'")[0][0]
        stale = q(con, f"SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='in_progress' "
                       f"AND (claimed_at IS NULL OR claimed_at < NOW() - INTERVAL {a.stale_hours} HOUR)")[0][0]
        rep["verdict"] = {"QfqDeadLetter": dl, "QfqPendingSla": n_now, "QfqStaleInProgress": stale}
        print("\n" + "=" * 78)
        print("三判据定版（真 NOW() 基准）")
        print("=" * 78)
        print(f"  QfqDeadLetter      = {dl:>8,}   (阈值 dead_letter_max=0 ⇒ {'error' if dl else 'pass'})")
        print(f"  QfqPendingSla      = {n_now:>8,}   (阈值 pending_sla_hours={a.sla_hours} ⇒ {'error' if n_now else 'pass'})")
        print(f"  QfqStaleInProgress = {stale:>8,}   (阈值 stale_in_progress_hours={a.stale_hours} ⇒ {'error' if stale else 'pass'})")
    finally:
        con.close()

    if a.json_out:
        p = Path(a.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已落 = {p}")
    print("\n=== 完成（全只读，未改任何数据）===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
