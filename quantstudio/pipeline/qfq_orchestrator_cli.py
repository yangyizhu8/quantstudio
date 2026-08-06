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
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BJ_TZ = timezone(timedelta(hours=8))

#: 只读子命令集合（用 read_only 连接）
READONLY_CMDS = frozenset({"status", "show-pending", "show-dead-letter", "bootstrap-audit", "cutover-status"})
#: 变更子命令集合（需 --execute；正式库需 --allow-production）
MUTATING_CMDS = frozenset({"bootstrap-plan", "bootstrap-run", "bootstrap-resume",
                           "reconcile-once", "retry-due", "reopen", "cutover-init",
                           "cutover-transition", "aux-init", "baseline-build",
                           "cutover-evidence", "cutover-activate",
                           "cutover-prep-staging", "cutover-canary"})
#: 需要 enabled=true 才允许执行的变更命令（紧急回退开关语义）
ENABLED_REQUIRED_CMDS = frozenset({"reconcile-once", "bootstrap-run", "bootstrap-resume"})


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(datetime.now(BJ_TZ).timestamp() * 1000)


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _parse_codes_filter(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    source = raw.strip()
    if not source:
        raise SystemExit("--codes 不能为空")
    path = Path(source)
    if path.suffix.lower() == ".json":
        if not path.is_file():
            raise SystemExit(f"--codes JSON 文件不存在: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"--codes JSON 文件读取失败: {path}: {exc}") from exc
        values = payload.get("codes") if isinstance(payload, dict) else payload
        if not isinstance(values, list):
            raise SystemExit("--codes JSON 必须是数组，或包含 codes 数组")
    else:
        values = source.split(",")
    codes = sorted({str(value).strip() for value in values if str(value).strip()})
    invalid = [code for code in codes if not re.fullmatch(r"\d{6}", code)]
    if invalid:
        raise SystemExit(f"--codes 含非法证券代码（必须为 6 位裸码）: {invalid}")
    if not codes:
        raise SystemExit("--codes 解析后为空，拒绝退化为全量 bootstrap")
    return codes


def _parse_admissible_codes(
        raw: Optional[str], *, config_dir: str
) -> Optional[List[tuple[str, str]]]:
    """读取 QFQ 准入名单，保留 ``by_asset`` 中的资产类型。"""
    if raw is None:
        return None
    path = Path(raw) if raw else Path(config_dir) / "qfq_rebase_admissible_securities.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"读取 --admissible 文件失败: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("by_asset"), dict):
        raise SystemExit("--admissible JSON 必须包含 by_asset 对象")
    by_asset = payload["by_asset"]
    unknown_assets = set(by_asset) - {"STOCK", "ETF"}
    if unknown_assets:
        raise SystemExit(f"--admissible 含未知资产类型: {sorted(unknown_assets)}")
    values: List[tuple[str, str]] = []
    for asset_type in ("STOCK", "ETF"):
        codes = by_asset.get(asset_type, [])
        if not isinstance(codes, list):
            raise SystemExit(f"--admissible by_asset.{asset_type} 必须是数组")
        values.extend(
            (asset_type, str(code).strip()) for code in codes if str(code).strip())
    unique = sorted(set(values))
    invalid = [code for _, code in unique if not re.fullmatch(r"\d{6}", code)]
    if invalid:
        raise SystemExit(f"--admissible 含非法证券代码（必须为 6 位裸码）: {invalid}")
    if not unique:
        raise SystemExit("--admissible 名单不能为空")
    declared = payload.get("total_admissible")
    if declared is not None and declared != len(unique):
        raise SystemExit(
            f"--admissible total_admissible={declared} 与唯一证券数={len(unique)} 不一致")
    return unique


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
        # v2.4 P0-1：fresh fetcher 经共享工厂构造（按 cfg.price_source，与 daemon 一致，防漂移）
        # CLI 不再硬编码 XtquantFreshFetcher；MCP 模式下不 import xtquant、不连 QMT
        from quantstudio.pipeline.qfq_fresh_fetcher_factory import (
            build_qfq_fresh_fetcher, load_sources_cfg)
        sources_dir = getattr(args, "sources_dir", None) or args.config_dir
        sources_cfg = load_sources_cfg(sources_dir)
        fetcher = build_qfq_fresh_fetcher(cfg, sources_cfg, str(db))
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
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        ident = orch.prepare_runtime(conn, require_aux=False)
        gp = [ident["price_source"], ident["source_generation"], ident["cutover_id"]]
        trig = dict(_q(conn, "SELECT status, COUNT(*) FROM qfq_trigger_queue "
                            "WHERE price_source=? AND source_generation=? AND cutover_id=? "
                            "GROUP BY status", gp))
        cycles = _q(conn, "SELECT cycle_id, phase, status, discovered_count, success_count, "
                          "failed_count, started_at, finished_at FROM qfq_cycle_run "
                          "WHERE price_source=? AND source_generation=? AND cutover_id=? "
                          "ORDER BY started_at DESC LIMIT 5", gp)
        intents = _q(conn, "SELECT status, COUNT(*) FROM qfq_watermark_intent "
                           "WHERE source_generation=? AND cutover_id=? GROUP BY status", gp[1:])
        held = _q(conn, "SELECT cycle_id, table_name, freq, hold_reason FROM qfq_watermark_intent "
                        "WHERE status='held' AND source_generation=? AND cutover_id=? "
                        "ORDER BY cycle_id DESC LIMIT 10", gp[1:])
        backfill = dict(_q(conn, "SELECT status, COUNT(*) FROM qfq_pending_backfill "
                                "WHERE price_source=? AND source_generation=? GROUP BY status", gp[:2]))
        boots = _q(conn, "SELECT bootstrap_run_id, status, total_count, completed_count, "
                         "blocked_count, failed_count FROM qfq_bootstrap_run "
                         "WHERE price_source=? AND source_generation=? AND cutover_id=? "
                         "ORDER BY started_at DESC LIMIT 5", gp)
        wm = _q(conn, "SELECT source, table_name, freq, last_date FROM source_watermark "
                      "WHERE table_name IN ('stock_daily','stock_minutes','etf_daily','etf_minutes') "
                      "AND source_generation=? AND cutover_id=? ORDER BY table_name, freq", gp[1:])
        payload = {
            "db": str(db), "identity": ident,
            "config": {"enabled": cfg.enabled, "require_bootstrap": cfg.require_bootstrap,
                       "price_source": cfg.price_source, "generation_mode": cfg.generation_mode,
                       "freqs": list(cfg.freqs), "retry_max": cfg.retry_max,
                       "watermark_policy": cfg.watermark_policy},
            "trigger_queue": trig,
            "recent_cycles": [dict(zip(("cycle_id", "phase", "status", "discovered",
                "success", "failed", "started_at", "finished_at"), r)) for r in cycles],
            "watermark_intents": dict(intents),
            "held_intents": [dict(zip(("cycle_id", "table", "freq", "reason"), r)) for r in held],
            "pending_backfill": backfill,
            "bootstrap_runs": [dict(zip(("run_id", "status", "total", "completed",
                "blocked", "failed"), r)) for r in boots],
            "price_watermarks": [dict(zip(("source", "table", "freq", "last_date"), r)) for r in wm],
        }
        _emit(args, payload, [f"== QFQ orchestrator status @ {db}",
                               f"config: enabled={cfg.enabled} require_bootstrap={cfg.require_bootstrap} "
                               f"price_source={cfg.price_source} generation_mode={cfg.generation_mode}",
                               f"identity: {ident}",
                               f"trigger_queue: {trig or '(empty)'}",
                               f"pending_backfill: {backfill or '(empty)'}"])
        return 0
    finally:
        conn.close()


def cmd_show_pending(args) -> int:
    db = _resolve_db(args); cfg = _load_cfg(args)
    conn = _connect(db, read_only=True)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        ident = orch.prepare_runtime(conn, require_aux=False)
        rows = _q(conn,
            "SELECT asset_type, code, table_name, freq, range_start, range_end, "
            "reason, status, attempt_count, trigger_id, last_error, updated_at "
            "FROM qfq_pending_backfill WHERE status<>'resolved' AND price_source=? "
            "AND source_generation=? ORDER BY updated_at DESC LIMIT ?",
            [ident["price_source"], ident["source_generation"], args.limit])
        cols = ("asset_type", "code", "table", "freq", "range_start", "range_end",
                "reason", "status", "attempts", "trigger_id", "last_error", "updated_at")
        payload = {"db": str(db), "identity": ident, "count": len(rows),
                   "rows": [dict(zip(cols, r)) for r in rows]}
        _emit(args, payload, [f"== 未 resolved pending_backfill（{len(rows)} 条）",
                              *([f"  {r}" for r in rows] or ["  (空)"])])
        return 0
    finally:
        conn.close()


def cmd_show_dead_letter(args) -> int:
    db = _resolve_db(args); cfg = _load_cfg(args)
    conn = _connect(db, read_only=True)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        ident = orch.prepare_runtime(conn, require_aux=False)
        rows = _q(conn,
            "SELECT trigger_id, asset_type, code, trigger_type, attempt_count, "
            "last_error, dead_letter_at FROM qfq_trigger_queue WHERE status='dead_letter' "
            "AND price_source=? AND source_generation=? AND cutover_id=? "
            "ORDER BY dead_letter_at DESC LIMIT ?",
            [ident["price_source"], ident["source_generation"], ident["cutover_id"], args.limit])
        cols = ("trigger_id", "asset_type", "code", "trigger_type", "attempts",
                "last_error", "dead_letter_at")
        payload = {"db": str(db), "identity": ident, "count": len(rows),
                   "rows": [dict(zip(cols, r)) for r in rows]}
        _emit(args, payload, [f"== dead_letter triggers（{len(rows)} 条）",
                              *([f"  {r}" for r in rows] or ["  (空)"])])
        return 0
    finally:
        conn.close()


def cmd_bootstrap_audit(args) -> int:
    db = _resolve_db(args); cfg = _load_cfg(args)
    conn = _connect(db, read_only=True)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        ident = orch.prepare_runtime(conn, require_aux=False)
        run_id = args.run_id
        if not run_id:
            row = _q(conn, "SELECT bootstrap_run_id FROM qfq_bootstrap_run "
                           "WHERE price_source=? AND source_generation=? AND cutover_id=? "
                           "ORDER BY started_at DESC LIMIT 1",
                     [ident["price_source"], ident["source_generation"], ident["cutover_id"]])
            if not row:
                print("当前世代无 bootstrap run 记录")
                return 1
            run_id = row[0][0]
        audit = orch.bootstrap_audit(conn, run_id)
        blocked = _q(conn, "SELECT asset_type, code, block_reason FROM qfq_bootstrap_item "
                           "WHERE bootstrap_run_id=? AND status='blocked' LIMIT 20", [run_id])
        audit["blocked_sample"] = [dict(zip(("asset_type", "code", "reason"), r)) for r in blocked]
        _emit(args, audit, [f"== bootstrap audit {run_id}: {audit}"])
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
    codes_filter = _parse_codes_filter(args.codes)
    admissible_codes = _parse_admissible_codes(
        args.admissible, config_dir=args.config_dir)
    scope = f"指定 {len(codes_filter)} 只证券" if codes_filter is not None else "全量候选"
    if admissible_codes is not None:
        scope += f"，准入名单 {len(admissible_codes)} 只"
    if not args.execute:
        if admissible_codes is not None:
            print(f"[dry-run] 将把准入名单全部 {len(admissible_codes)} 只证券直接写入 "
                  "qfq_bootstrap_item(pending)；名单外的 stock_dividend(实施) + "
                  "qfq_factor_observation 候选记录为 excluded。加 --execute 执行。")
        else:
            print(f"[dry-run] 将按{scope}扫描 stock_dividend(实施) + "
                  "qfq_factor_observation，并仅将 stale 候选写入 "
                  "qfq_bootstrap_item(pending)。加 --execute 执行。")
        return 0
    conn = _connect(db, read_only=False)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        orch.init_schema(conn)
        plan = orch.bootstrap_plan(
            conn, as_of_ms=_now_ms(), codes_filter=codes_filter,
            admissible_codes=admissible_codes)
        payload = {"run_id": plan.run_id, "total": plan.total,
                   "pending": len(plan.items), "excluded": plan.excluded,
                   "admissible_count": (len(admissible_codes)
                                        if admissible_codes is not None else None),
                   "codes_filter": codes_filter, "sample": plan.items[:20]}
        _emit(args, payload, [
            f"bootstrap plan 生成: run_id={plan.run_id} total={plan.total} "
            f"pending={len(plan.items)} excluded={plan.excluded} scope={scope}",
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
    codes_filter = _parse_codes_filter(args.codes)
    scope = f"指定 {len(codes_filter)} 只证券" if codes_filter is not None else "全量证券"
    if not cfg.enabled:
        raise SystemExit("qfq_orchestrator.enabled=false：reconcile-once 拒绝执行"
                         "（紧急回退开关语义；--override enabled=true 可覆盖，仅限 staging）")
    if not args.execute:
        print(f"[dry-run] 将按{scope}执行一轮 post-ingest 协调周期："
              "recover→discover→claim→fresh(xtquant)→reanchor→gate→水位延迟提交。"
              "加 --execute 执行。")
        return 0
    conn = _connect(db, read_only=False)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=True)
        orch.init_schema(conn)
        run_id = f"cli_{uuid.uuid4().hex[:8]}"
        cycle_id = orch.begin_cycle(conn)
        summary = orch.run_post_ingest(
            conn, cycle_id=cycle_id, run_id=run_id, as_of_ms=_now_ms(),
            codes_filter=codes_filter)
        payload = {**summary.__dict__, "codes_filter": codes_filter}
        _emit(args, payload, [f"reconcile-once 完成: {payload}"])
        return 0 if (
            not summary.error
            and (
                summary.status == "finalized"
                or (
                    summary.status == "finalized_held"
                    and summary.gate_report.get("scoped_mode", False)
                )
            )
        ) else 1
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
    db = _resolve_db(args); cfg = _load_cfg(args)
    _guard_mutating(args, db)
    if not args.trigger_id:
        raise SystemExit("reopen 必须指定 --trigger-id")
    conn = _connect(db, read_only=(not args.execute))
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        ident = orch.prepare_runtime(conn, require_aux=False)
        gp = [ident["price_source"], ident["source_generation"], ident["cutover_id"]]
        row = conn.execute(
            "SELECT status, attempt_count, last_error FROM qfq_trigger_queue "
            "WHERE trigger_id=? AND price_source=? AND source_generation=? AND cutover_id=?",
            [args.trigger_id, *gp]).fetchone()
        if not row:
            raise SystemExit(f"trigger 不存在或不属于当前世代: {args.trigger_id}")
        status, attempts, last_error = row
        if status != "dead_letter":
            raise SystemExit(f"仅允许 reopen dead_letter（当前 status={status}）")
        if not args.execute:
            print(f"[dry-run] 将把 {args.trigger_id} 从 dead_letter 重开为 pending；"
                  f"attempts={attempts}, last_error={last_error}")
            return 0
        changed = conn.execute(
            "UPDATE qfq_trigger_queue SET status='pending', attempt_count=0, "
            "next_retry_at=NULL, dead_letter_at=NULL, claimed_by=NULL, claimed_at=NULL, "
            "updated_at=? WHERE trigger_id=? AND price_source=? AND source_generation=? "
            "AND cutover_id=? RETURNING trigger_id", [_now_ts(), args.trigger_id, *gp]).fetchone()
        if changed is None:
            raise SystemExit("reopen CAS 失败")
        print(f"reopen 完成: {args.trigger_id} dead_letter→pending")
        return 0
    finally:
        conn.close()


def cmd_bootstrap_supersede(args) -> int:
    db = _resolve_db(args)
    _guard_mutating(args, db)
    run_ids = sorted({str(run_id).strip() for run_id in (args.run_id or [])
                      if str(run_id).strip()})
    if not run_ids:
        raise SystemExit("bootstrap-supersede 必须至少指定一个 --run-id")
    if not args.execute:
        print(f"[dry-run] 将把旧 bootstrap run 标记为 superseded: {run_ids}。"
              "加 --execute 执行。")
        return 0
    cfg = _load_cfg(args)
    conn = _connect(db, read_only=False)
    try:
        orch = _make_orchestrator(cfg, db, args, with_fetcher=False)
        result = orch.supersede_bootstrap_runs(conn, run_ids)
        payload = {"run_ids": run_ids, **result}
        _emit(args, payload, [f"bootstrap supersede 完成: {payload}"])
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def cmd_cutover_status(args) -> int:
    db = _resolve_db(args)
    conn = _connect(db, read_only=True)
    try:
        cutovers = _q(conn, "SELECT cutover_id, price_source, source_generation, status, "
                           "schema_version, baseline_version, aux_db_path, evidence_path, updated_at "
                           "FROM qfq_source_cutover ORDER BY created_at DESC")
        active = _q(conn, "SELECT price_source, cutover_id, activated_at FROM qfq_active_cutover")
        payload = {"db": str(db), "cutovers": cutovers, "active": active}
        _emit(args, payload, [f"cutovers={cutovers}", f"active={active}"])
        return 0
    finally:
        conn.close()


def cmd_cutover_init(args) -> int:
    db = _resolve_db(args); cfg = _load_cfg(args)
    if not args.execute:
        _emit(args, {"dry_run": True, "cutover_id": args.cutover_id},
              [f"[dry-run] create planned cutover {args.cutover_id}"])
        return 0
    _guard_mutating(args, db)
    conn = _connect(db, read_only=False)
    try:
        from quantstudio.pipeline.qfq_cutover import create_cutover
        if cfg.source_generation != "xtquant-legacy" and not args.aux_db:
            raise SystemExit("dynamic cutover-init requires explicit --aux-db")
        from quantstudio.pipeline.qfq_reanchor_schema import SCHEMA_VERSION, DETECTOR_BASELINE_VERSION
        row = create_cutover(conn, cutover_id=args.cutover_id,
            price_source=cfg.price_source, source_generation=cfg.source_generation,
            schema_version=SCHEMA_VERSION, baseline_version=DETECTOR_BASELINE_VERSION,
            aux_db_path=args.aux_db, evidence_path=args.evidence_path)
        _emit(args, row, [f"cutover created: {row}"])
        return 0
    finally:
        conn.close()


def cmd_cutover_transition(args) -> int:
    db = _resolve_db(args)
    if not args.execute:
        _emit(args, {"dry_run": True, "cutover_id": args.cutover_id,
                     "from": args.expected_status, "to": args.new_status},
              [f"[dry-run] {args.cutover_id}: {args.expected_status}->{args.new_status}"])
        return 0
    _guard_mutating(args, db)
    conn = _connect(db, read_only=False)
    try:
        from quantstudio.pipeline.qfq_cutover import transition_cutover
        row = transition_cutover(conn, cutover_id=args.cutover_id,
                                 expected_status=args.expected_status,
                                 new_status=args.new_status)
        _emit(args, row, [f"cutover transitioned: {row}"])
        return 0
    finally:
        conn.close()


def cmd_baseline_build(args) -> int:
    db = _resolve_db(args); cfg = _load_cfg(args)
    if not args.execute:
        _emit(args, {"dry_run": True, "cutover_id": args.cutover_id},
              [f"[dry-run] build discovery baseline for {args.cutover_id}"])
        return 0
    _guard_mutating(args, db)
    conn = _connect(db, read_only=False)
    try:
        from quantstudio.pipeline.qfq_discovery_baseline import BaselineIdentity, establish_discovery_baseline
        from quantstudio.pipeline.qfq_dividend_payload import dividend_payload_hash
        from quantstudio.pipeline.qfq_cutover import runtime_cutover_record
        ident = {"cutover_id": args.cutover_id, "price_source": cfg.price_source,
                 "source_generation": cfg.source_generation}
        runtime_cutover_record(conn, ident)
        rows = conn.execute(
            "SELECT code, ex_date, record_date, ann_date, end_date, "
            "cash_div_before_tax, cash_div_after_tax, cash_div, stk_div, stk_bo_rate, "
            "stk_co_rate, div_rat, div_proc FROM stock_dividend "
            "WHERE div_proc='实施' AND ex_date IS NOT NULL").fetchall()
        conn.execute("BEGIN TRANSACTION")
        try:
            count = establish_discovery_baseline(
                conn, identity=BaselineIdentity(**ident), rows=rows,
                payload_hash=lambda row: dividend_payload_hash(*row))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        payload = {"cutover_id": args.cutover_id, "rows": count, "identity": ident}
        _emit(args, payload, [f"baseline rows={count}"])
        return 0
    finally:
        conn.close()


def cmd_cutover_evidence(args) -> int:
    db = _resolve_db(args)
    if not args.execute:
        _emit(args, {"dry_run": True, "cutover_id": args.cutover_id,
                     "output": str(Path(args.output).resolve())},
              [f"[dry-run] freeze immutable B-6 evidence for {args.cutover_id}"])
        return 0
    _guard_mutating(args, db)
    conn = _connect(db, read_only=False)
    try:
        from quantstudio.pipeline.qfq_cutover_activation import build_cutover_evidence
        row = build_cutover_evidence(conn, cutover_id=args.cutover_id,
                                     main_db_path=db, output_path=args.output)
        _emit(args, row, [f"B-6 evidence frozen: {row['evidence_path']}"])
        return 0
    finally:
        conn.close()


def cmd_cutover_activate(args) -> int:
    db = _resolve_db(args)
    expected_old = args.expected_old or None
    # B-6 is local/staging only.  --allow-production intentionally cannot
    # override this boundary; formal activation remains separately gated.
    try:
        if db == _production_db_path():
            raise SystemExit("B-6 cutover-activate is staging-only; formal activation is not authorized")
    except SystemExit:
        raise
    if args.dry_run and args.execute:
        raise SystemExit("cutover-activate --dry-run and --execute are mutually exclusive")
    if args.dry_run or not args.execute:
        conn = _connect(db, read_only=True)
        try:
            from quantstudio.pipeline.qfq_cutover_activation import activation_dry_run
            plan = activation_dry_run(
                conn, cutover_id=args.cutover_id, price_source=args.price_source,
                expected_old=expected_old, main_db_path=db)
        finally:
            conn.close()
        _emit(args, plan, [
            f"[dry-run] staging-only activation plan for {args.cutover_id}",
            *[f"  {index + 1}. {step}" for index, step in enumerate(plan["sequence"])],
        ])
        return 0
    _guard_mutating(args, db)
    from filelock import FileLock, Timeout
    from .daemon_lifecycle import collector_run_lock_path
    run_lock = FileLock(str(collector_run_lock_path()), timeout=0)
    try:
        run_lock.acquire()
    except Timeout as exc:
        raise SystemExit("collector/daemon is running; cannot safely perform B-6 activation") from exc
    conn = None
    try:
        conn = _connect(db, read_only=False)
        from quantstudio.pipeline.qfq_cutover_activation import activate_cutover_staging
        row = activate_cutover_staging(conn, cutover_id=args.cutover_id,
                                       price_source=args.price_source,
                                       expected_old=expected_old,
                                       main_db_path=db, fault_at=args.fault_at)
        _emit(args, row, [f"B-6 staging activation complete: {row}"])
        return 0
    finally:
        if conn is not None:
            conn.close()
        run_lock.release()


def cmd_cutover_prep_staging(args) -> int:
    if not args.execute:
        payload = {"dry_run": True, "source_db": str(Path(args.source_db).resolve()),
                   "source_aux": str(Path(args.source_aux).resolve()),
                   "dest": str(Path(args.dest).resolve()), "writes_database": False}
        _emit(args, payload, [f"[dry-run] prepare staging copy at {payload['dest']}"])
        return 0
    from quantstudio.pipeline.qfq_staging_prep import prepare_staging_copy
    payload = prepare_staging_copy(
        source_db=args.source_db, source_aux=args.source_aux, dest=args.dest)
    _emit(args, payload, [f"staging copy prepared: {payload['root']}"])
    return 0


def cmd_cutover_canary(args) -> int:
    db = _resolve_db(args)
    if db == _production_db_path():
        raise SystemExit("cutover-canary is staging-only; formal DB is rejected")
    codes = _parse_codes_filter(args.codes)
    if not args.execute:
        payload = {"dry_run": True, "db": str(db),
                   "aux_db": str(Path(args.aux_db).resolve()),
                   "codes": codes, "cutover_id": args.cutover_id,
                   "expected_baseline_rows": args.expected_baseline_rows}
        _emit(args, payload, [f"[dry-run] scoped staging canary codes={codes}"])
        return 0
    from filelock import FileLock, Timeout
    from .daemon_lifecycle import collector_run_lock_path
    lock = FileLock(str(collector_run_lock_path()), timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise SystemExit("collector/daemon is running; cannot safely run staging canary") from exc
    try:
        from quantstudio.pipeline.qfq_staging_canary import (
            _write_exclusive, run_full_noop_with_timeout, run_scoped_canary)
        full_noop = run_full_noop_with_timeout(
            main_db=db, aux_db=args.aux_db, cutover_id=args.cutover_id,
            timeout_sec=args.full_noop_timeout_sec)
        payload = run_scoped_canary(
            main_db=db, aux_db=args.aux_db, codes=codes,
            cutover_id=args.cutover_id,
            expected_baseline_rows=args.expected_baseline_rows,
            output_path=None,
            recover_aborted=(args.full_noop_timeout_sec <= 0))
        payload["full_noop"] = full_noop
        _write_exclusive(Path(args.output).resolve(), payload)
        payload["output_path"] = str(Path(args.output).resolve())
    finally:
        lock.release()
    _emit(args, payload, [f"staging canary complete: status={payload['summary']['status']}"])
    return 0


def cmd_aux_init(args) -> int:
    """Explicitly initialize the isolated auxiliary DB recorded by a cutover."""
    db = _resolve_db(args); cfg = _load_cfg(args)
    if not args.execute:
        _emit(args, {"dry_run": True, "cutover_id": args.cutover_id},
              [f"[dry-run] initialize isolated aux DB for {args.cutover_id}"])
        return 0
    _guard_mutating(args, db)
    conn = _connect(db, read_only=True)
    try:
        from quantstudio.pipeline.qfq_cutover import runtime_cutover_record
        from quantstudio.pipeline.qfq_aux_router import AuxDbRouter
        ident = {"cutover_id": args.cutover_id, "price_source": cfg.price_source,
                 "source_generation": cfg.source_generation}
        record = runtime_cutover_record(conn, ident)
        if record["status"] not in ("prepared", "baseline_building"):
            raise SystemExit(
                f"aux-init requires prepared/baseline_building; current={record['status']!r}")
        if not record.get("aux_db_path"):
            raise SystemExit("cutover has no immutable aux_db_path")
        router = AuxDbRouter(main_db=db, routes={cfg.source_generation: record["aux_db_path"]})
        route = router.initialize_explicit(
            source_generation=cfg.source_generation, cutover_id=args.cutover_id)
        payload = {"cutover_id": args.cutover_id, "source_generation": cfg.source_generation,
                   "aux_db": str(route.path), "exists": route.exists}
        _emit(args, payload, [f"aux initialized: {payload}"])
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qfq_orchestrator_cli",
        description="QFQ 常驻编排器运维 CLI（变更类默认 dry-run，--execute 才落库）")
    p.add_argument("--db", required=False, help="DuckDB 主库路径（必填，不提供默认值）")
    p.add_argument("--aux-db", default=None, help="SQLite 辅助库路径（默认按主库派生）")
    p.add_argument("--config-dir", default=None,
                   help="含 collector_tasks.json 的配置目录（读 qfq_orchestrator 块）")
    p.add_argument("--sources-dir", default=None,
                   help="含 sources_config.json 的配置目录；缺省与 --config-dir 相同")
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

    bp = sub.add_parser("bootstrap-plan", help="生成 bootstrap 计划（写 run/item）")
    bp.add_argument(
        "--codes", default=None,
        help="限定 6 位裸码：逗号分隔列表，或 JSON 文件（数组/含 codes 数组）")
    bp.add_argument(
        "--admissible", nargs="?", const="", default=None,
        help=("将准入名单全部证券直接写为 pending，名单外候选记录为 excluded；"
              "不带路径时读取 config/qfq_rebase_admissible_securities.json"))

    bs = sub.add_parser("bootstrap-supersede", help="将明确废弃的旧 bootstrap run 标记为 superseded")
    bs.add_argument("--run-id", action="append", default=[],
                    help="待废弃的旧 run_id，可重复指定")

    for name in ("bootstrap-run", "bootstrap-resume"):
        br = sub.add_parser(name, help="执行 bootstrap 批次（xtquant 真实取数）")
        br.add_argument("--run-id", required=False, default=None)
        br.add_argument("--max-batches", type=int, default=1,
                        help="本次最多执行批次数（每批 bootstrap_batch_size 个证券）")

    rc = sub.add_parser("reconcile-once", help="手动执行一轮 post-ingest 协调周期")
    rc.add_argument(
        "--codes", default=None,
        help="限定 6 位裸码：逗号分隔列表，或 JSON 文件（数组/含 codes 数组）")

    sub.add_parser("retry-due", help="stale/retry/scheduled 三类恢复")

    ro = sub.add_parser("reopen", help="dead_letter trigger 重开为 pending")
    ro.add_argument("--trigger-id", required=False, default=None)
    sub.add_parser("cutover-status", help="read B-5 cutover status and active pointer")
    ci = sub.add_parser("cutover-init", help="create a planned cutover; dry-run by default")
    ci.add_argument("--cutover-id", required=True)
    ci.add_argument("--evidence-path", default=None)
    ct = sub.add_parser("cutover-transition", help="CAS transition cutover state without activation")
    ct.add_argument("--cutover-id", required=True)
    ct.add_argument("--expected-status", required=True)
    ct.add_argument("--new-status", required=True)
    ai = sub.add_parser("aux-init", help="explicitly initialize isolated generation aux DB")
    ai.add_argument("--cutover-id", required=True)
    bb = sub.add_parser("baseline-build", help="build discovery baseline during baseline_building")
    bb.add_argument("--cutover-id", required=True)
    ce = sub.add_parser("cutover-evidence", help="freeze immutable B-6 staging evidence")
    ce.add_argument("--cutover-id", required=True)
    ce.add_argument("--output", required=True)
    ca = sub.add_parser("cutover-activate", help="staging-only B-6 active-pointer CAS")
    ca.add_argument("--cutover-id", required=True)
    ca.add_argument("--price-source", default="mcp")
    ca.add_argument("--expected-old", default=None)
    ca.add_argument("--dry-run", action="store_true",
                    help="read-only activation plan; no BEGIN, writes, or evidence creation")
    ca.add_argument("--fault-at", default=None,
                    choices=["after_retirement", "after_pointer_delete", "after_pointer_insert",
                             "after_new_status", "before_commit", "after_commit_before_report"])
    ps = sub.add_parser("cutover-prep-staging", help="copy an explicit staging/hermetic source under dual locks")
    ps.add_argument("--source-db", required=True)
    ps.add_argument("--source-aux", required=True)
    ps.add_argument("--dest", required=True)
    cc = sub.add_parser("cutover-canary", help="scoped post-activation staging canary")
    cc.add_argument("--aux-db", required=True)
    cc.add_argument("--codes", default="510500,159919,000001")
    cc.add_argument("--cutover-id", default="b6_mcp_gen1_20260806")
    cc.add_argument("--expected-baseline-rows", type=int, default=2181)
    cc.add_argument("--output", required=True)
    cc.add_argument("--full-noop-timeout-sec", type=float, default=300.0,
                    help="unscoped no-op timeout; timeout triggers staging recovery")
    return p


DISPATCH = {
    "status": cmd_status,
    "show-pending": cmd_show_pending,
    "show-dead-letter": cmd_show_dead_letter,
    "bootstrap-audit": cmd_bootstrap_audit,
    "bootstrap-plan": cmd_bootstrap_plan,
    "bootstrap-supersede": cmd_bootstrap_supersede,
    "bootstrap-run": cmd_bootstrap_run,
    "bootstrap-resume": cmd_bootstrap_resume,
    "reconcile-once": cmd_reconcile_once,
    "retry-due": cmd_retry_due,
    "reopen": cmd_reopen,
    "cutover-status": cmd_cutover_status,
    "cutover-init": cmd_cutover_init,
    "cutover-transition": cmd_cutover_transition,
    "aux-init": cmd_aux_init,
    "baseline-build": cmd_baseline_build,
    "cutover-evidence": cmd_cutover_evidence,
    "cutover-activate": cmd_cutover_activate,
    "cutover-prep-staging": cmd_cutover_prep_staging,
    "cutover-canary": cmd_cutover_canary,
}


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    args = build_parser().parse_args(argv)
    return DISPATCH[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
