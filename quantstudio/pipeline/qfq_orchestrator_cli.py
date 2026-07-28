"""QFQ 常驻编排器运维 CLI（resident orchestrator v2 · Task #82）

子命令（只读 / 变更两类，变更类默认 dry-run，须 ``--execute`` 才落库）：

只读（DuckDB read_only 连接，绝不发 DDL / UPDATE）：
    status            编排器配置 + trigger 队列 / 周期 / 水位 / 死信 / bootstrap 全景
    show-pending      qfq_pending_backfill 未 resolved 明细
    show-dead-letter  dead_letter trigger 明细
    bootstrap-audit   指定 bootstrap run 的审计（total/completed/blocked/failed/remaining）

变更（写库；默认 dry-run，``--execute`` 才执行；指向正式库须 ``--allow-production``）：
    bootstrap-plan    生成 bootstrap 计划（写 qfq_bootstrap_run/item，不触发重锚）
    bootstrap-run     执行 bootstrap 批次（真实 xtquant fetcher；--max-batches 限流）
    bootstrap-resume  等价于 bootstrap-run --resume（继续既有 run 的 pending 项）
    reconcile-once    手动跑一轮 post-ingest 协调周期（发现→领取→重锚→gate→水位）
    retry-due         恢复类操作：回收 stale in_progress + retryable_failed 到期回 pending
                      + scheduled 到期晋升
    reopen            将 dead_letter trigger 重开为 pending（人工确认修复后使用）

安全铁律（内建，不可绕过的默认）：
1. 变更类命令不带 ``--execute`` 时一律 dry-run（只打印将发生什么，不写库）。
2. 目标 db 解析为正式库（quantstudio._paths.db_path()）时，变更类命令必须显式
   ``--allow-production``，否则拒绝执行——staging 演练请指向 staging 副本。
3. ``qfq_orchestrator.enabled=false`` 时 reconcile-once / bootstrap-run 拒绝执行
   （与 daemon 紧急回退开关语义一致）；可用 ``--override enabled=true`` 显式覆盖
   （仅建议用于 staging 演练）。
4. 只读命令使用 DuckDB ``read_only=True`` 连接，物理上写不进去。

用法示例：
    python -m quantstudio.pipeline.qfq_orchestrator_cli status --db data/staging_x/quantstudio.db
    python -m quantstudio.pipeline.qfq_orchestrator_cli bootstrap-plan --db ...staging... --execute
    python -m quantstudio.pipeline.qfq_orchestrator_cli bootstrap-run --db ...staging... \
        --run-id bs_xxxx --max-batches 10 --execute
    python -m quantstudio.pipeline.qfq_orchestrator_cli reconcile-once --db ...staging... --execute
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BJ_TZ = timezone(timedelta(hours=8))

#: 只读子命令集合（用 read_only 连接）
READONLY_CMDS = frozenset({"status", "show-pending", "show-dead-letter", "bootstrap-audit"})
#: 变更子命令集合（需 --execute；正式库需 --allow-production）
MUTATING_CMDS = frozenset({"bootstrap-plan", "bootstrap-run", "bootstrap-resume",
                           "reconcile-once", "retry-due", "reopen"})
#: 需要 enabled=true 才允许执行的变更命令（紧急回退开关语义）
ENABLED_REQUIRED_CMDS = frozenset({"reconcile-once", "bootstrap-run", "bootstrap-resume"})


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(datetime.now(BJ_TZ).timestamp() * 1000)


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _production_db_path() -> Path:
    from quantstudio._paths import db_path
    return Path(db_path()).resolve()


def _resolve_db(args) -> Path:
    if not args.db:
        raise SystemExit("必须显式指定 --db（不提供默认库，避免误指正式库）")
    p = Path(args.db).resolve()
    if not p.exists():
        raise SystemExit(f"数据库不存在: {p}")
    return p


def _guard_mutating(args, db: Path) -> None:
    """变更类命令的双闸门：--execute + 正式库需 --allow-production。"""
    prod = None
    try:
        prod = _production_db_path()
    except Exception:  # pragma: no cover - _paths 不可用时保守放行比较
        pass
    if prod is not None and db == prod and not args.allow_production:
        raise SystemExit(
            f"拒绝执行：目标是正式库 {db}\n"
            "变更类命令指向正式库必须显式 --allow-production。\n"
            "staging 演练请把 --db 指向带 marker+manifest 的 staging 副本。")


def _connect(db: Path, *, read_only: bool):
    import duckdb
    try:
        return duckdb.connect(str(db), read_only=read_only)
    except Exception as e:
        raise SystemExit(f"连接失败（read_only={read_only}）: {e}")


def _load_cfg(args):
    from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
    overrides: Dict = {}
    for kv in (args.override or []):
        if "=" not in kv:
            raise SystemExit(f"--override 格式须为 key=value: {kv}")
        k, v = kv.split("=", 1)
        try:
            overrides[k] = json.loads(v)  # true/false/数字/JSON 数组均可
        except json.JSONDecodeError:
            overrides[k] = v
    return QFQOrchestratorConfig.load(
        config_dir=args.config_dir, raw=overrides or None)


def _make_orchestrator(cfg, db: Path, args, *, with_fetcher: bool):
    from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator
    from quantstudio.pipeline.qfq_calendar import CalendarService
    fetcher = None
    if with_fetcher:
        from quantstudio.pipeline.qfq_fresh_capture import XtquantFreshFetcher
        fetcher = XtquantFreshFetcher()  # 惰性连接：首次取数才 import/连 xtquant
    calendar = CalendarService(main_db=db)
    return QFQResidentOrchestrator(
        cfg, main_db=str(db), aux_db=args.aux_db,
        fetcher=fetcher, calendar=calendar)


def _emit(args, payload: Dict, text_lines: List[str]) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for ln in text_lines:
            print(ln)


def _q(conn, sql: str, params=()) -> List[tuple]:
    """容错查询：表不存在（旧库/未初始化）返回空并提示，不让整个命令崩。"""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception as e:
        msg = str(e)
        if "does not exist" in msg or "Table" in msg and "not" in msg:
            logger.warning(f"[cli] 查询跳过（表可能未初始化）: {msg.splitlines()[0]}")
            return []
        raise


# ---------------------------------------------------------------------------
# 只读命令
# ---------------------------------------------------------------------------

def cmd_status(args) -> int:
    db = _resolve_db(args)
    cfg = _load_cfg(args)
    conn = _connect(db, read_only=True)
    try:
        trig = dict(_q(conn, "SELECT status, COUNT(*) FROM qfq_trigger_queue GROUP BY status"))
        cycles = _q(conn,
                    "SELECT cycle_id, phase, status, discovered_count, success_count, "
                    "failed_count, started_at, finished_at FROM qfq_cycle_run "
                    "ORDER BY started_at DESC LIMIT 5")
        intents = _q(conn,
                     "SELECT status, COUNT(*) FROM qfq_watermark_intent GROUP BY status")
        held = _q(conn,
                  "SELECT cycle_id, table_name, freq, hold_reason FROM qfq_watermark_intent "
                  "WHERE status='held' ORDER BY cycle_id DESC LIMIT 10")
        backfill = dict(_q(conn,
                           "SELECT status, COUNT(*) FROM qfq_pending_backfill GROUP BY status"))
        boots = _q(conn,
                   "SELECT bootstrap_run_id, status, total_count, completed_count, "
                   "blocked_count, failed_count FROM qfq_bootstrap_run "
                   "ORDER BY started_at DESC LIMIT 5")
        wm = _q(conn,
                "SELECT source, table_name, freq, last_date FROM source_watermark "
                "WHERE table_name IN ('stock_daily','stock_minutes','etf_daily','etf_minutes') "
                "ORDER BY table_name, freq")
        payload = {
            "db": str(db),
            "config": {"enabled": cfg.enabled, "require_bootstrap": cfg.require_bootstrap,
                       "price_source": cfg.price_source, "freqs": list(cfg.freqs),
                       "retry_max": cfg.retry_max,
                       "watermark_policy": cfg.watermark_policy},
            "trigger_queue": trig,
            "recent_cycles": [dict(zip(
                ("cycle_id", "phase", "status", "discovered", "success", "failed",
                 "started_at", "finished_at"), r)) for r in cycles],
            "watermark_intents": dict(intents),
            "held_intents": [dict(zip(("cycle_id", "table", "freq", "reason"), r))
                             for r in held],
            "pending_backfill": backfill,
            "bootstrap_runs": [dict(zip(
                ("run_id", "status", "total", "completed", "blocked", "failed"), r))
                for r in boots],
            "price_watermarks": [dict(zip(("source", "table", "freq", "last_date"), r))
                                 for r in wm],
        }
        lines = [
            f"== QFQ orchestrator status @ {db}",
            f"config: enabled={cfg.enabled} require_bootstrap={cfg.require_bootstrap} "
            f"price_source={cfg.price_source} watermark_policy={cfg.watermark_policy}",
            f"trigger_queue: {trig or '(空)'}",
            f"pending_backfill: {backfill or '(空)'}",
            f"watermark_intents: {dict(intents) or '(空)'}",
        ]
        if held:
            lines.append("held intents（未提交水位）:")
            lines += [f"  {r}" for r in held]
        lines.append("recent cycles:")
        lines += [f"  {r}" for r in cycles] or ["  (无)"]
        lines.append("bootstrap runs:")
        lines += [f"  {r}" for r in boots] or ["  (无)"]
        lines.append("price watermarks:")
        lines += [f"  {r}" for r in wm] or ["  (无)"]
        _emit(args, payload, lines)
        return 0
    finally:
        conn.close()


def cmd_show_pending(args) -> int:
    db = _resolve_db(args)
    conn = _connect(db, read_only=True)
    try:
        rows = _q(conn,
                  "SELECT asset_type, code, table_name, freq, range_start, range_end, "
                  "reason, status, attempt_count, trigger_id, last_error, updated_at "
                  "FROM qfq_pending_backfill WHERE status != 'resolved' "
                  "ORDER BY updated_at DESC LIMIT ?", [args.limit])
        cols = ("asset_type", "code", "table", "freq", "range_start", "range_end",
                "reason", "status", "attempts", "trigger_id", "last_error", "updated_at")
        payload = {"db": str(db), "count": len(rows),
                   "rows": [dict(zip(cols, r)) for r in rows]}
        lines = [f"== 未 resolved pending_backfill（{len(rows)} 条，limit={args.limit}）"]
        lines += [f"  {r}" for r in rows] or ["  (空)"]
        _emit(args, payload, lines)
        return 0
    finally:
        conn.close()


def cmd_show_dead_letter(args) -> int:
    db = _resolve_db(args)
    conn = _connect(db, read_only=True)
    try:
        rows = _q(conn,
                  "SELECT trigger_id, asset_type, code, trigger_type, attempt_count, "
                  "last_error, dead_letter_at FROM qfq_trigger_queue "
                  "WHERE status='dead_letter' ORDER BY dead_letter_at DESC LIMIT ?",
                  [args.limit])
        cols = ("trigger_id", "asset_type", "code", "trigger_type", "attempts",
                "last_error", "dead_letter_at")
        payload = {"db": str(db), "count": len(rows),
                   "rows": [dict(zip(cols, r)) for r in rows]}
        lines = [f"== dead_letter triggers（{len(rows)} 条，limit={args.limit}）"]
        lines += [f"  {r}" for r in rows] or ["  (空)"]
        _emit(args, payload, lines)
        return 0
    finally:
        conn.close()


def cmd_bootstrap_audit(args) -> int:
    db = _resolve_db(args)
    cfg = _load_cfg(args)
    conn = _connect(db, read_only=True)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        run_id = args.run_id
        if not run_id:
            row = _q(conn, "SELECT bootstrap_run_id FROM qfq_bootstrap_run "
                           "ORDER BY started_at DESC LIMIT 1")
            if not row:
                print("无 bootstrap run 记录")
                return 1
            run_id = row[0][0]
        audit = orch.bootstrap_audit(conn, run_id)
        blocked = _q(conn,
                     "SELECT asset_type, code, block_reason FROM qfq_bootstrap_item "
                     "WHERE bootstrap_run_id=? AND status='blocked' LIMIT 20", [run_id])
        audit["blocked_sample"] = [dict(zip(("asset_type", "code", "reason"), r))
                                   for r in blocked]
        lines = [f"== bootstrap audit {run_id}: {audit}"]
        _emit(args, audit, lines)
        return 0 if audit.get("clean") else 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 变更命令
# ---------------------------------------------------------------------------

def cmd_bootstrap_plan(args) -> int:
    db = _resolve_db(args)
    _guard_mutating(args, db)
    cfg = _load_cfg(args)
    if not args.execute:
        print("[dry-run] 将扫描 stock_dividend(实施) + qfq_factor_observation 生成候选，"
              "写入 qfq_bootstrap_run/qfq_bootstrap_item(pending)。加 --execute 执行。")
        return 0
    conn = _connect(db, read_only=False)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        orch.init_schema(conn)
        plan = orch.bootstrap_plan(conn, as_of_ms=_now_ms())
        payload = {"run_id": plan.run_id, "total": plan.total,
                   "sample": plan.items[:20]}
        _emit(args, payload, [
            f"bootstrap plan 生成: run_id={plan.run_id} total={plan.total}",
            f"样例(≤20): {plan.items[:20]}",
            f"下一步: bootstrap-run --run-id {plan.run_id} --execute"])
        return 0
    finally:
        conn.close()


def _run_bootstrap(args, *, resume: bool) -> int:
    db = _resolve_db(args)
    _guard_mutating(args, db)
    cfg = _load_cfg(args)
    if not cfg.enabled:
        raise SystemExit("qfq_orchestrator.enabled=false：bootstrap-run 拒绝执行"
                         "（可 --override enabled=true 显式覆盖，仅限 staging）")
    if not args.run_id:
        raise SystemExit("bootstrap-run/resume 必须指定 --run-id（由 bootstrap-plan 生成）")
    if not args.execute:
        print(f"[dry-run] 将对 run {args.run_id} 执行至多 {args.max_batches} 批 "
              f"bootstrap（batch={cfg.bootstrap_batch_size}，xtquant 真实取数）。"
              "加 --execute 执行。")
        return 0
    conn = _connect(db, read_only=False)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=True)
        orch.init_schema(conn)
        as_of = _now_ms()
        results = []
        for i in range(args.max_batches):
            r = orch.bootstrap_run(conn, run_id=args.run_id, as_of_ms=as_of,
                                   fetcher=orch.fetcher, resume=resume)
            results.append(r)
            print(f"batch {i + 1}/{args.max_batches}: {r}")
            if not r.get("remaining"):
                break
        audit = orch.bootstrap_audit(conn, args.run_id)
        _emit(args, {"batches": results, "audit": audit},
              [f"bootstrap 结束: {audit}"])
        return 0 if audit.get("clean") else 1
    finally:
        conn.close()


def cmd_bootstrap_run(args) -> int:
    return _run_bootstrap(args, resume=False)


def cmd_bootstrap_resume(args) -> int:
    return _run_bootstrap(args, resume=True)


def cmd_reconcile_once(args) -> int:
    db = _resolve_db(args)
    _guard_mutating(args, db)
    cfg = _load_cfg(args)
    if not cfg.enabled:
        raise SystemExit("qfq_orchestrator.enabled=false：reconcile-once 拒绝执行"
                         "（紧急回退开关语义；--override enabled=true 可覆盖，仅限 staging）")
    if not args.execute:
        print("[dry-run] 将执行一轮 post-ingest 协调周期："
              "recover→discover→claim→fresh(xtquant)→reanchor→gate→水位延迟提交。"
              "加 --execute 执行。")
        return 0
    conn = _connect(db, read_only=False)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=True)
        orch.init_schema(conn)
        run_id = f"cli_{uuid.uuid4().hex[:8]}"
        cycle_id = orch.begin_cycle(conn)
        summary = orch.run_post_ingest(conn, cycle_id=cycle_id, run_id=run_id,
                                       as_of_ms=_now_ms())
        payload = summary.__dict__
        _emit(args, payload, [f"reconcile-once 完成: {payload}"])
        return 0 if summary.status == "finalized" and not summary.error else 1
    finally:
        conn.close()


def cmd_retry_due(args) -> int:
    db = _resolve_db(args)
    _guard_mutating(args, db)
    cfg = _load_cfg(args)
    if not args.execute:
        print("[dry-run] 将执行：回收 lease 超时 in_progress + retryable_failed 到期回 "
              "pending + scheduled 到期晋升。加 --execute 执行。")
        return 0
    conn = _connect(db, read_only=False)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        run_id = f"cli_retry_{uuid.uuid4().hex[:8]}"
        stale = orch.recover_stale_in_progress(conn, run_id)
        due = orch.recover_pending_due(conn, run_id)
        promoted = orch.promote_scheduled_due(conn, as_of_ms=_now_ms())
        payload = {"stale_recovered": stale, "retry_due": due, "promoted": promoted}
        _emit(args, payload, [f"retry-due 完成: {payload}"])
        return 0
    finally:
        conn.close()


def cmd_reopen(args) -> int:
    db = _resolve_db(args)
    _guard_mutating(args, db)
    if not args.trigger_id:
        raise SystemExit("reopen 必须指定 --trigger-id")
    conn = _connect(db, read_only=(not args.execute))
    try:
        row = conn.execute(
            "SELECT status, attempt_count, last_error FROM qfq_trigger_queue "
            "WHERE trigger_id=?", [args.trigger_id]).fetchone()
        if not row:
            raise SystemExit(f"trigger 不存在: {args.trigger_id}")
        status, attempts, last_error = row
        if status != "dead_letter":
            raise SystemExit(f"仅允许 reopen dead_letter（当前 status={status}）")
        if not args.execute:
            print(f"[dry-run] 将把 {args.trigger_id} 从 dead_letter 重开为 pending"
                  f"（attempt_count 归零，清 dead_letter_at；原 attempts={attempts}，"
                  f"last_error={last_error}）。加 --execute 执行。")
            return 0
        conn.execute(
            "UPDATE qfq_trigger_queue SET status='pending', attempt_count=0, "
            "next_retry_at=NULL, dead_letter_at=NULL, claimed_by=NULL, claimed_at=NULL, "
            "updated_at=? WHERE trigger_id=?", [_now_ts(), args.trigger_id])
        print(f"reopen 完成: {args.trigger_id} dead_letter→pending（下轮周期将重新领取）")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qfq_orchestrator_cli",
        description="QFQ 常驻编排器运维 CLI（变更类默认 dry-run，--execute 才落库）")
    p.add_argument("--db", required=False, help="DuckDB 主库路径（必填，不提供默认值）")
    p.add_argument("--aux-db", default=None, help="SQLite 辅助库路径（默认按主库派生）")
    p.add_argument("--config-dir", default=None,
                   help="含 collector_tasks.json 的配置目录（读 qfq_orchestrator 块）")
    p.add_argument("--override", action="append", default=[],
                   help="覆盖配置项 key=value（value 按 JSON 解析），可重复")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    p.add_argument("--execute", action="store_true",
                   help="变更类命令实际执行（缺省 dry-run）")
    p.add_argument("--allow-production", action="store_true",
                   help="显式允许变更类命令指向正式库（默认拒绝）")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="全景状态（只读）")

    sp = sub.add_parser("show-pending", help="未 resolved pending_backfill（只读）")
    sp.add_argument("--limit", type=int, default=50)

    sd = sub.add_parser("show-dead-letter", help="dead_letter triggers（只读）")
    sd.add_argument("--limit", type=int, default=50)

    ba = sub.add_parser("bootstrap-audit", help="bootstrap run 审计（只读）")
    ba.add_argument("--run-id", default=None, help="缺省取最近一个 run")

    sub.add_parser("bootstrap-plan", help="生成 bootstrap 计划（写 run/item）")

    for name in ("bootstrap-run", "bootstrap-resume"):
        br = sub.add_parser(name, help="执行 bootstrap 批次（xtquant 真实取数）")
        br.add_argument("--run-id", required=False, default=None)
        br.add_argument("--max-batches", type=int, default=1,
                        help="本次最多执行批次数（每批 bootstrap_batch_size 个证券）")

    sub.add_parser("reconcile-once", help="手动执行一轮 post-ingest 协调周期")

    sub.add_parser("retry-due", help="stale/retry/scheduled 三类恢复")

    ro = sub.add_parser("reopen", help="dead_letter trigger 重开为 pending")
    ro.add_argument("--trigger-id", required=False, default=None)
    return p


DISPATCH = {
    "status": cmd_status,
    "show-pending": cmd_show_pending,
    "show-dead-letter": cmd_show_dead_letter,
    "bootstrap-audit": cmd_bootstrap_audit,
    "bootstrap-plan": cmd_bootstrap_plan,
    "bootstrap-run": cmd_bootstrap_run,
    "bootstrap-resume": cmd_bootstrap_resume,
    "reconcile-once": cmd_reconcile_once,
    "retry-due": cmd_retry_due,
    "reopen": cmd_reopen,
}


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    args = build_parser().parse_args(argv)
    return DISPATCH[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
