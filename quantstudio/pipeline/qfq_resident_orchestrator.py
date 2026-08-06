"""QFQ 常驻增量事件驱动闭环 —— 完整编排器（resident orchestrator v2）

本模块是把既有 ``qfq_reanchor_engine.apply_reanchor_for_security``（单证券 fresh_staged
重锚能力）串成**生产常驻闭环**的编排层。职责（单一真相源的衔接）：

1. 每轮 ``begin_cycle`` 建立 ``qfq_cycle_run``；
2. 启动先 ``recover_stale_in_progress``（回收 lease 超时 in_progress）+ ``recover_pending_due``
   （retryable_failed 到期回到 pending）；
3. ``_discover``：股票分红扫描 + 股票/ETF 因子观察 + revision alert 消费 → 持久化
   ``qfq_trigger_queue``（确定性 trigger_id，天然去重 + 崩溃可重放）；
4. ``_claim_and_merge``：领取到期 trigger，同 (asset_type, code) 多事件合并为一次工作单元；
5. ``_reanchor_security``：**事务外** fresh 采集（xtquant 单源）→ 单证券短事务 apply
   （``apply_reanchor_for_security(model='fresh_staged')``）→ 更新 trigger 状态；
6. ``_qfq_gate``：编排级质量门控（orphan / pending SLA / dead letter / 本轮全 committed）；
7. ``_commit_or_hold_watermarks``：gate 通过才推进四价格表水位（``qfq_watermark_intent``
   提交），否则保持（hold_until_consistent）；
8. bootstrap：首次部署建立基线 + 分批重锚存量 stale 证券，completed 前 fail-closed。

硬约束（用户铁律）：
- 价格修正源**永远 xtquant**（fresh_capture 已 fail-fast 锁定）；
- 四价格表水位**延迟到协调周期结束统一提交**，未过 gate 不推进（比“先推进后修”安全）；
- 引擎 committed 后若 trigger 未更新即崩溃，下一轮通过 event_id/trigger_id 识别已提交，
  **只补写 trigger 状态，绝不重算价格或重复推进 anchor**；
- ``enabled=False`` 时本编排器整体不介入（daemon 走旧路径，逐位不变）；
- require_bootstrap=true 且无匹配版本 completed bootstrap → 本轮 fail-closed（不推进水位、
  不处理 trigger）。

本模块不持有长连接：所有方法接收调用方 DuckDB/SQLite 连接，由 daemon 管理事务边界。
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from quantstudio.pipeline.qfq_reanchor_schema import (
    init_duckdb_schema, init_sqlite_schema, aux_db_path,
    SCHEMA_VERSION, DETECTOR_BASELINE_VERSION,
    PRICE_TABLES, ASSET_TABLE_MAP,
)
from quantstudio.pipeline.qfq_orchestrator_types import (
    QFQOrchestratorConfig, TriggerRecord, TriggerStatus, CyclePhase,
    WatermarkIntentStatus, ReanchorOutcome, event_id_of, QFQConfigError,
)
from quantstudio.pipeline.qfq_event_discovery import EventDiscovery
from quantstudio.pipeline.qfq_cutover import resolve_runtime_identity, runtime_cutover_record
from quantstudio.pipeline.qfq_aux_router import AuxDbRouter
from quantstudio.pipeline.qfq_discovery_baseline import (
    audit_pending_slots, BaselineIdentity, commit_pending_slot,
    logical_key_stock_dividend,
)
from quantstudio.pipeline.qfq_fresh_capture import FreshCapture, FreshFetcher, CaptureContentConflict
from quantstudio.pipeline.qfq_reanchor_engine import ReanchorBlocked

logger = logging.getLogger(__name__)

BJ_TZ = timezone(timedelta(hours=8))

# 资产类型 → (daily 表, minute 表)
ASSET_PRICE_TABLES = {
    "STOCK": ("stock_daily", "stock_minutes"),
    "ETF": ("etf_daily", "etf_minutes"),
}


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _now_iso() -> str:
    return datetime.now(BJ_TZ).isoformat(timespec="seconds")


def _ms_to_yyyymmdd(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, BJ_TZ).strftime("%Y%m%d")


@dataclass
class CycleSummary:
    cycle_id: str
    triggers_found: int = 0
    claimed: int = 0
    committed: int = 0
    retryable_failed: int = 0
    dead_letter: int = 0
    blocked: int = 0
    watermarks_committed: int = 0
    watermarks_held: int = 0
    pending_due: int = 0
    bootstrap_required: bool = False
    status: str = "finalized"
    error: Optional[str] = None
    gate_report: Dict = field(default_factory=dict)


@dataclass
class BootstrapPlan:
    total: int = 0
    items: List[Tuple[str, str]] = field(default_factory=list)  # pending (asset_type, code)
    run_id: str = ""  # qfq_bootstrap_run 主键（CLI/运维需要，不能只留在表里）
    excluded: int = 0


class QFQResidentOrchestrator:
    """QFQ 常驻增量事件驱动闭环编排器（无状态核心，连接由调用方注入）。"""

    def __init__(self, cfg: QFQOrchestratorConfig, *,
                 main_db: Optional[str] = None,
                 aux_db: Optional[str] = None,
                 fetcher: Optional[FreshFetcher] = None,
                 calendar=None,
                 watermark_advancer: Optional[Callable] = None,
                 aux_router: Optional[AuxDbRouter] = None):
        self.cfg = cfg
        from quantstudio.pipeline.qfq_schema_contracts import pre_cutover_qfq_identity
        self._ident = pre_cutover_qfq_identity(cfg.price_source)
        if not cfg.enabled:
            logger.info("[qfq_orch] enabled=False，编排器为 no-op（daemon 走旧路径）")
        self.main_db = main_db
        self._explicit_aux_db = aux_db or (aux_db_path(main_db) if main_db else None)
        self.aux_db = self._explicit_aux_db
        self._aux_router = aux_router or AuxDbRouter(
            main_db=main_db, explicit_default=self._explicit_aux_db)
        self.fetcher = fetcher
        self.calendar = calendar
        self.watermark_advancer = watermark_advancer
        self.discovery = EventDiscovery(cfg, aux_db=self.aux_db, identity=self._ident)
    def _resolve_dynamic_identity(self, conn, *, require_aux: bool = True) -> None:
        """Resolve B-5 identity and route the generation-specific auxiliary DB."""
        if getattr(self.cfg, "generation_mode", "pre_cutover") != "dynamic":
            self.discovery.set_runtime(self._ident, aux_db=self.aux_db)
            return
        ident = resolve_runtime_identity(conn, self.cfg, allow_prepared=False)
        record = runtime_cutover_record(conn, ident)
        aux_path = record.get("aux_db_path")
        if not aux_path:
            raise RuntimeError(
                f"dynamic cutover={ident['cutover_id']!r} has no immutable aux_db_path; fail-closed")
        router = AuxDbRouter(
            main_db=self.main_db, explicit_default=self._explicit_aux_db,
            routes={ident["source_generation"]: aux_path})
        resolved = router.path_for(
            ident["source_generation"], require_exists=require_aux)
        self._ident = dict(ident)
        self._aux_router = router
        self.aux_db = str(resolved)
        self.discovery.set_runtime(self._ident, aux_db=self.aux_db)

    def prepare_runtime(self, conn, *, require_aux: bool = True) -> dict:
        self._resolve_dynamic_identity(conn, require_aux=require_aux)
        return dict(self._ident)


    def _generation_where(self, alias: str = "") -> Tuple[str, List[object]]:
        prefix = f"{alias}." if alias else ""
        return (f"{prefix}price_source=? AND {prefix}source_generation=? "
                f"AND {prefix}cutover_id=?",
                [self._ident["price_source"], self._ident["source_generation"],
                 self._ident["cutover_id"]])

    def _baseline_identity(self) -> BaselineIdentity:
        return BaselineIdentity(**self._ident)


    # ------------------------------------------------------------------
    # schema / 初始化
    # ------------------------------------------------------------------
    def init_schema(self, duck_conn, sqlite_conn=None) -> None:
        """Initialize schema without implicitly creating a dynamic-generation aux DB."""
        init_duckdb_schema(duck_conn)
        self.prepare_runtime(
            duck_conn,
            require_aux=(getattr(self.cfg, "generation_mode", "pre_cutover") == "dynamic"),
        )
        if sqlite_conn is not None:
            init_sqlite_schema(sqlite_conn)

    # ------------------------------------------------------------------
    # 周期生命周期
    # ------------------------------------------------------------------
    def begin_cycle(self, conn, *, business_date_ms: Optional[int] = None,
                    config_hash: Optional[str] = None,
                    schema_hash: Optional[str] = None) -> str:
        self._resolve_dynamic_identity(conn)
        # 崩溃/中断恢复（restart 语义）：上一周期若在 finalize 前中断，
        # qfq_watermark_intent 会残留 status='pending'。这些 intent 永远不会
        # 被旧周期提交（水位从未推进，daemon 下轮会从旧水位幂等重拉），
        # 新周期开始时统一标 superseded 清障，防止残留 pending 永久堆积。
        self.supersede_stale_intents(conn)
        cycle_id = f"cyc_{uuid.uuid4().hex[:12]}"
        now = _now_ts()
        conn.execute(
            "INSERT INTO qfq_cycle_run "
            "(cycle_id, business_date, trigger_surface, config_hash, schema_hash, "
             " phase, discovered_count, executed_count, success_count, failed_count, "
             " pending_count, status, started_at, updated_at, detector_degraded, "
             " price_source, source_generation, cutover_id) "
            "VALUES (?,?,?,?,?, 'started', 0,0,0,0,0, 'started', ?, ?, 0, ?, ?, ?)",
            [cycle_id, business_date_ms, "resident_v2", config_hash, schema_hash,
             now, now, self._ident["price_source"], self._ident["source_generation"], self._ident["cutover_id"]])
        return cycle_id

    def supersede_stale_intents(self, conn) -> int:
        rows = conn.execute(
            "SELECT wi.cycle_id, wi.source, wi.table_name, wi.freq "
            "FROM qfq_watermark_intent wi "
            "LEFT JOIN qfq_cycle_run cr ON cr.cycle_id=wi.cycle_id "
            "AND cr.price_source=? AND cr.source_generation=? AND cr.cutover_id=? "
            "WHERE wi.source_generation=? AND wi.cutover_id=? "
            "AND wi.status='pending' AND (cr.cycle_id IS NULL "
            "OR cr.status IN ('finalized','finalized_held','failed','interrupted'))",
            [self._ident["price_source"], self._ident["source_generation"], self._ident["cutover_id"],
             self._ident["source_generation"], self._ident["cutover_id"]],
        ).fetchall()
        for cyc, source, table, freq in rows:
            conn.execute(
                "UPDATE qfq_watermark_intent SET status='superseded', "
                "hold_reason='stale pending superseded by new cycle' "
                "WHERE cycle_id=? AND source=? AND table_name=? AND freq=? "
                "AND source_generation=? AND cutover_id=?",
                [cyc, source, table, freq, self._ident["source_generation"],
                 self._ident["cutover_id"]])
        return len(rows)

    def _set_cycle_phase(self, conn, cycle_id: str, phase: str) -> None:
        conn.execute(
            "UPDATE qfq_cycle_run SET phase=?, updated_at=? WHERE cycle_id=? "
            "AND price_source=? AND source_generation=? AND cutover_id=?",
            [phase, _now_ts(), cycle_id, self._ident["price_source"],
             self._ident["source_generation"], self._ident["cutover_id"]])

    def _finish_cycle(self, conn, cycle_id: str, status: str,
                      summary: CycleSummary) -> None:
        conn.execute(
            "UPDATE qfq_cycle_run SET phase=?, status=?, discovered_count=?, "
            "executed_count=?, success_count=?, failed_count=?, pending_count=?, "
            "finished_at=?, error=?, updated_at=? WHERE cycle_id=? "
            "AND price_source=? AND source_generation=? AND cutover_id=?",
            [phase_of(status), status, summary.triggers_found, summary.claimed,
             summary.committed, summary.retryable_failed + summary.dead_letter,
             summary.pending_due, _now_ts(), summary.error, _now_ts(), cycle_id,
             self._ident["price_source"], self._ident["source_generation"],
             self._ident["cutover_id"]])

    # ------------------------------------------------------------------
    # 重启恢复
    # ------------------------------------------------------------------
    def recover_stale_in_progress(self, conn, run_id: str,
                                  codes_filter: Optional[Sequence[str]] = None) -> int:
        lease = self.cfg.claim_lease_sec
        cutoff = (_now_iso_ts() - timedelta(seconds=lease)).isoformat(timespec="seconds")
        gen_sql, gen_params = self._generation_where()
        sql = ("SELECT trigger_id, attempt_count FROM qfq_trigger_queue "
               "WHERE status='in_progress' AND claimed_at IS NOT NULL AND claimed_at < ? "
               "AND " + gen_sql)
        params: List[object] = [cutoff, *gen_params]
        if codes_filter:
            sql += f" AND code IN ({', '.join(['?'] * len(codes_filter))})"
            params.extend(codes_filter)
        rows = conn.execute(sql, params).fetchall()
        for tid, attempt in rows:
            if (attempt or 0) > 0:
                now_retry = _now_iso_ts().isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE qfq_trigger_queue SET status='retryable_failed', claimed_by=NULL, "
                    "claimed_at=NULL, next_retry_at=?, updated_at=? WHERE trigger_id=? "
                    "AND price_source=? AND source_generation=? AND cutover_id=?",
                    [now_retry, _now_ts(), tid, *gen_params])
            else:
                conn.execute(
                    "UPDATE qfq_trigger_queue SET status='pending', claimed_by=NULL, "
                    "claimed_at=NULL, updated_at=? WHERE trigger_id=? "
                    "AND price_source=? AND source_generation=? AND cutover_id=?",
                    [_now_ts(), tid, *gen_params])
        return len(rows)

    def promote_scheduled_due(self, conn, *, as_of_ms: int,
                              codes_filter: Optional[Sequence[str]] = None) -> int:
        gen_sql, gen_params = self._generation_where()
        sql = ("SELECT trigger_id FROM qfq_trigger_queue WHERE status='scheduled' "
               "AND effective_date IS NOT NULL AND effective_date <= ? AND " + gen_sql)
        params: List[object] = [as_of_ms, *gen_params]
        if codes_filter:
            sql += f" AND code IN ({', '.join(['?'] * len(codes_filter))})"
            params.extend(codes_filter)
        rows = conn.execute(sql, params).fetchall()
        for (tid,) in rows:
            conn.execute(
                "UPDATE qfq_trigger_queue SET status='pending', updated_at=? "
                "WHERE trigger_id=? AND price_source=? AND source_generation=? AND cutover_id=?",
                [_now_ts(), tid, *gen_params])
        return len(rows)

    def recover_pending_due(self, conn, run_id: str,
                            codes_filter: Optional[Sequence[str]] = None) -> int:
        now_iso = _now_iso_ts().isoformat(timespec="seconds")
        gen_sql, gen_params = self._generation_where()
        sql = ("SELECT trigger_id FROM qfq_trigger_queue WHERE status='retryable_failed' "
               "AND next_retry_at IS NOT NULL AND next_retry_at <= ? AND " + gen_sql)
        params: List[object] = [now_iso, *gen_params]
        if codes_filter:
            sql += f" AND code IN ({', '.join(['?'] * len(codes_filter))})"
            params.extend(codes_filter)
        rows = conn.execute(sql, params).fetchall()
        for (tid,) in rows:
            conn.execute(
                "UPDATE qfq_trigger_queue SET status='pending', updated_at=? "
                "WHERE trigger_id=? AND price_source=? AND source_generation=? AND cutover_id=?",
                [_now_ts(), tid, *gen_params])
        return len(rows)

    # ------------------------------------------------------------------
    # 事件发现
    # ------------------------------------------------------------------
    def _discover(self, conn, *, run_id: str, as_of_ms: int,
                  codes_filter: Optional[Sequence[str]] = None) -> List[TriggerRecord]:
        new: List[TriggerRecord] = []
        disc = self.discovery
        new += disc.scan_stock_dividend(
            conn, as_of_ms=as_of_ms, run_id=run_id, codes_filter=codes_filter)
        disc.observe_stock_adj_factor(
            conn, as_of_ms=as_of_ms, run_id=run_id, codes_filter=codes_filter)
        disc.observe_etf_fund_adj(
            conn, as_of_ms=as_of_ms, run_id=run_id, codes_filter=codes_filter)
        new += disc.consume_revision_alerts(
            conn, run_id=run_id, as_of_ms=as_of_ms, codes_filter=codes_filter)
        return new

    # ------------------------------------------------------------------
    # 领取 + 合并
    # ------------------------------------------------------------------
    def _claim_and_merge(self, conn, *, cycle_id: str, run_id: str,
                         as_of_ms: int,
                         codes_filter: Optional[Sequence[str]] = None) -> List[Dict]:
        now_iso = _now_iso_ts().isoformat(timespec="seconds")
        gen_sql, gen_params = self._generation_where()
        sql = (
            "SELECT trigger_id, asset_type, code, trigger_type, source_key, effective_date, "
            "payload_hash, factor_old, factor_new, factor_revision, status, attempt_count "
            "FROM qfq_trigger_queue WHERE status='pending' AND effective_date IS NOT NULL "
            "AND effective_date <= ? AND " + gen_sql)
        params: List[object] = [as_of_ms, *gen_params]
        if codes_filter:
            sql += f" AND code IN ({', '.join(['?'] * len(codes_filter))})"
            params.extend(codes_filter)
        sql += " ORDER BY asset_type, code, trigger_id"
        rows = conn.execute(sql, params).fetchall()
        claimed_ids = [r[0] for r in rows]
        if claimed_ids:
            placeholders = ", ".join(["?"] * len(claimed_ids))
            claimed = conn.execute(
                f"UPDATE qfq_trigger_queue SET status='in_progress', claimed_by=?, "
                f"claimed_at=?, updated_at=? WHERE trigger_id IN ({placeholders}) "
                f"AND status='pending' AND price_source=? AND source_generation=? AND cutover_id=? "
                f"RETURNING trigger_id",
                [run_id, now_iso, _now_ts(), *claimed_ids, *gen_params]).fetchall()
            if {r[0] for r in claimed} != set(claimed_ids):
                raise RuntimeError("trigger claim CAS 失败；拒绝跨并发继续")
        units: Dict[Tuple[str, str], Dict] = {}
        for r in rows:
            (tid, at, code, ttype, source_key, eff, phash,
             fold, fnew, frev, st, att) = r
            u = units.setdefault((at, code), {
                "asset_type": at, "code": code, "triggers": [],
                "trigger_records": [], "effective_dates": [], "attempt": 0,
            })
            u["triggers"].append(tid)
            u["trigger_records"].append({
                "trigger_id": tid, "trigger_type": ttype, "source_key": source_key,
                "effective_date": int(eff) if eff is not None else None,
                "payload_hash": phash,
            })
            if eff is not None:
                u["effective_dates"].append(int(eff))
            u["attempt"] = max(u["attempt"], int(att or 0))
        for u in units.values():
            u["effective_dates"] = sorted(set(u["effective_dates"]))
        return list(units.values())

    # ------------------------------------------------------------------
    # 单证券重锚（核心）
    # ------------------------------------------------------------------
    def _security_range(self, conn, asset_type: str, code: str) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        daily_t, minute_t = ASSET_PRICE_TABLES[asset_type]
        d = conn.execute(
            f"SELECT MIN(time), MAX(time) FROM {daily_t} WHERE code=?", [code]).fetchone()
        m = conn.execute(
            f"SELECT MIN(time), MAX(time) FROM {minute_t} WHERE code=?", [code]).fetchone()
        daily_range = (int(d[0]) if d and d[0] is not None else 0,
                       int(d[1]) if d and d[1] is not None else 0)
        minute_range = (int(m[0]) if m and m[0] is not None else 0,
                        int(m[1]) if m and m[1] is not None else 0)
        return daily_range, minute_range

    def _already_committed(self, conn, trigger_id: str) -> Optional[str]:
        if self._ident["source_generation"] == "xtquant-legacy":
            row = conn.execute(
                "SELECT e.event_id FROM qfq_reanchor_event e "
                "JOIN qfq_trigger_queue t ON t.last_event_id=e.event_id "
                "WHERE t.trigger_id=? AND t.price_source=? AND t.source_generation=? "
                "AND t.cutover_id=? AND e.status='committed' LIMIT 1",
                [trigger_id, self._ident["price_source"], self._ident["source_generation"],
                 self._ident["cutover_id"]]).fetchone()
        else:
            row = conn.execute(
                "SELECT e.event_id FROM qfq_reanchor_event e "
                "JOIN qfq_trigger_queue t ON t.last_event_id=e.event_id "
                "WHERE t.trigger_id=? AND t.price_source=? AND t.source_generation=? "
                "AND t.cutover_id=? AND e.price_source=? AND e.source_generation=? "
                "AND e.cutover_id=? AND e.status='committed' LIMIT 1",
                [trigger_id, self._ident["price_source"], self._ident["source_generation"],
                 self._ident["cutover_id"], self._ident["price_source"],
                 self._ident["source_generation"], self._ident["cutover_id"]]).fetchone()
        return row[0] if row else None

    def _reanchor_security(self, conn, *, run_id: str, asset_type: str, code: str,
                           trigger_ids: List[str], effective_dates: List[int],
                           attempt: int, fetcher: FreshFetcher) -> ReanchorOutcome:
        """事务外 fresh 采集 + 单证券短事务 apply（fresh_authoritative_rebase）。

        显式选择 authoritative rebase 模型（precheck + postcheck + 捕获不可变契约 +
        事务回滚 + 崩溃恢复）。崩溃幂等：若 trigger 已有 committed event → 只补写状态，
        不重算/不重推进 anchor。
        """
        # 合并 trigger 取主 id（用于事件/回溯）
        primary_tid = trigger_ids[0]
        # 崩溃恢复：已提交则跳过 apply
        existing_event = self._already_committed(conn, primary_tid)
        if existing_event is not None:
            logger.info(f"[qfq_orch] {code} trigger {primary_tid} 已 committed "
                        f"(event={existing_event})，跳过重算（崩溃恢复）")
            return ReanchorOutcome(trigger_id=primary_tid, asset_type=asset_type,
                                   code=code, status="committed", event_id=existing_event,
                                   reason="crash_recovery_skip")

        daily_range, minute_range = self._security_range(conn, asset_type, code)
        cap = FreshCapture(self.cfg)
        # 编排器只负责「计算证据 + 采集 fresh」，捕获落库交由引擎的不可变契约
        # （resolve_fresh_capture → NEW 时 write_fresh_capture plain INSERT）完成，
        # 避免在编排器侧用 INSERT OR REPLACE 覆盖已提交捕获（write=False）。
        record, fresh_daily, fresh_minute = cap.capture(
            conn, asset_type=asset_type, code=code, run_id=run_id,
            daily_range_ms=daily_range, minute_range_ms=minute_range, fetcher=fetcher,
            source=self.cfg.price_source,  # P2-4：xtquant/mcp 由 price_source 驱动
            write=False)
        record.source_generation = self._ident["source_generation"]
        record.cutover_id = self._ident["cutover_id"]
        capture_id = record.capture_id
        event_id = event_id_of(primary_tid, attempt, capture_id)
        # 崩溃恢复关键：apply 前把本次 event_id 预写入全部 trigger（与引擎同事务/
        # 同连接提交）。若引擎 committed 后崩溃，下一轮 _already_committed 可据
        # last_event_id 精确识别，只补状态不重算。
        for tid in trigger_ids:
            conn.execute(
                "UPDATE qfq_trigger_queue SET last_event_id=?, updated_at=? "
                "WHERE trigger_id=? AND price_source=? AND source_generation=? AND cutover_id=?",
                [event_id, _now_ts(), tid, self._ident["price_source"],
                 self._ident["source_generation"], self._ident["cutover_id"]])
        try:
            from quantstudio.pipeline.qfq_reanchor_engine import apply_reanchor_for_security
            # R4/6A 修复：rebase 必须传该证券「全部已知除权日」，而非仅本轮领取的
            # trigger 子集。增量轮次下，同券部分 trigger 已 committed、本轮只领到新增
            # pending trigger → unit["effective_dates"] 仅含新增 ex_date → 传引擎的
            # ex_dates_ms 不全 → 局部重基（旧 ex_date 被忽略）。改为从 stock_dividend +
            # factor_observation 取该证券全量 ex_dates（与触发源一致，独立于 trigger
            # 领取状态）。仅影响 rebase 模式（ratio/fresh_staged 不走此路径）。
            ex_dates_ms = tuple(self._security_effective_dates(conn, asset_type, code))
            if not ex_dates_ms:
                ex_dates_ms = tuple(effective_dates)  # 降级：保持原 trigger 子集语义
            res = apply_reanchor_for_security(
                conn, asset_type=asset_type, code=code,
                fresh_daily=fresh_daily, calendar=self.calendar,
                freqs=("1min",),
                ex_dates_ms=ex_dates_ms,
                model="fresh_authoritative_rebase",
                model_reason="resident corporate-action/factor-change authoritative rebase",
                fresh_minutes=fresh_minute,
                price_source=self._ident["price_source"],
                source_generation=self._ident["source_generation"],
                cutover_id=self._ident["cutover_id"],
                fresh_source=self._ident["price_source"],
                fresh_capture_id=capture_id,
                fresh_metadata_sha256=record.metadata_sha256,
                event_id=event_id,
                trigger_surface="resident_v2",
                allow_partial_minute=True,
            )
            status = res.status  # committed / blocked / rolled_back / failed
            cap.mark_applied(
                conn, capture_id, source_generation=self._ident["source_generation"],
                cutover_id=self._ident["cutover_id"])
            return ReanchorOutcome(trigger_id=primary_tid, asset_type=asset_type,
                                   code=code, status=status, event_id=res.event_id,
                                   error=getattr(res, "error", None))
        except Exception as e:  # 引擎以外异常 → 记 failed / blocked
            logger.exception(f"[qfq_orch] {code} apply 异常: {e}")
            # 捕获不可变契约冲突 / 引擎明确 BLOCK（如 RECOVER_APPLIED_NO_EVENT）：
            # 映射为 blocked，绝不静默跳过、绝不推进 anchor（gate 会据此 hold 水位）。
            if isinstance(e, (ReanchorBlocked, CaptureContentConflict)):
                return ReanchorOutcome(trigger_id=primary_tid, asset_type=asset_type,
                                       code=code, status="blocked",
                                       error=f"{type(e).__name__}: {e}")
            return ReanchorOutcome(trigger_id=primary_tid, asset_type=asset_type,
                                   code=code, status="failed", error=f"{type(e).__name__}: {e}")

    def _apply_trigger_outcome(self, conn, *, run_id: str, unit: Dict,
                               outcome: ReanchorOutcome, fetcher: FreshFetcher,
                               summary: CycleSummary) -> None:
        now = _now_ts()
        now_iso = _now_iso_ts().isoformat(timespec="seconds")
        at, code = unit["asset_type"], unit["code"]
        tids = unit["triggers"]
        records = {r["trigger_id"]: r for r in unit.get("trigger_records", [])}
        tables = ASSET_TABLE_MAP.get(at, frozenset())
        gen_params = [self._ident["price_source"], self._ident["source_generation"],
                      self._ident["cutover_id"]]
        conn.execute("BEGIN TRANSACTION")
        try:
            if outcome.status == "committed":
                for tid in tids:
                    changed = conn.execute(
                        "UPDATE qfq_trigger_queue SET status='committed', last_event_id=?, "
                        "completed_at=?, updated_at=? WHERE trigger_id=? AND price_source=? "
                        "AND source_generation=? AND cutover_id=? RETURNING trigger_id",
                        [outcome.event_id, now_iso, now, tid, *gen_params]).fetchone()
                    if changed is None:
                        raise RuntimeError(f"trigger outcome 跨世代或缺失: {tid}")
                    rec = records.get(tid)
                    if (self._ident["source_generation"] != "xtquant-legacy" and rec
                            and rec.get("trigger_type") == "stock_dividend"):
                        ex_date = rec.get("effective_date") or rec.get("source_key")
                        commit_pending_slot(
                            conn, identity=self._baseline_identity(),
                            event_logical_key=logical_key_stock_dividend(code, int(ex_date)),
                            trigger_id=tid, payload_hash=rec.get("payload_hash"))
                    if self._ident["source_generation"] == "xtquant-legacy":
                        conn.execute(
                            "UPDATE qfq_pending_backfill SET status='resolved', resolved_at=?, "
                            "updated_at=? WHERE asset_type=? AND code=? AND price_source=? "
                            "AND source_generation=? AND status IN "
                            "('pending','retryable_failed','blocked','dead_letter')",
                            [now_iso, now, at, code, self._ident["price_source"],
                             self._ident["source_generation"]])
                    else:
                        conn.execute(
                            "UPDATE qfq_pending_backfill SET status='resolved', resolved_at=?, "
                            "updated_at=? WHERE trigger_id=? AND price_source=? "
                            "AND source_generation=? AND status IN "
                            "('pending','retryable_failed','blocked','dead_letter')",
                            [now_iso, now, tid, self._ident["price_source"],
                             self._ident["source_generation"]])
                summary.committed += 1
            elif outcome.status in ("blocked", "rolled_back", "failed"):
                attempt = self._bump_attempt(conn, tids)
                dead = attempt >= self.cfg.retry_max
                next_retry = None if dead else (
                    _now_iso_ts() + timedelta(seconds=self._backoff_sec(attempt))
                ).isoformat(timespec="seconds")
                for tid in tids:
                    self._enqueue_pending(
                        conn, at, code, tables, reason=outcome.status,
                        trigger_id=tid, event_id=outcome.event_id, dead_letter=dead)
                    if dead:
                        conn.execute(
                            "UPDATE qfq_trigger_queue SET status='dead_letter', last_error=?, "
                            "next_retry_at=NULL, dead_letter_at=?, updated_at=? "
                            "WHERE trigger_id=? AND price_source=? AND source_generation=? "
                            "AND cutover_id=?",
                            [outcome.error, now_iso, now, tid, *gen_params])
                    else:
                        conn.execute(
                            "UPDATE qfq_trigger_queue SET status='retryable_failed', "
                            "last_error=?, next_retry_at=?, updated_at=? WHERE trigger_id=? "
                            "AND price_source=? AND source_generation=? AND cutover_id=?",
                            [outcome.error, next_retry, now, tid, *gen_params])
                if dead:
                    summary.dead_letter += 1
                else:
                    summary.retryable_failed += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        # blocked 也记 pending_backfill 精确区间（已含在上一分支）

    def _bump_attempt(self, conn, tids: List[str]) -> int:
        placeholders = ','.join(['?'] * len(tids))
        gen_params = [self._ident["price_source"], self._ident["source_generation"],
                      self._ident["cutover_id"]]
        cur = conn.execute(
            f"SELECT MAX(attempt_count) FROM qfq_trigger_queue WHERE trigger_id IN "
            f"({placeholders}) AND price_source=? AND source_generation=? AND cutover_id=?",
            [*tids, *gen_params]).fetchone()[0] or 0
        new = int(cur) + 1
        conn.execute(
            f"UPDATE qfq_trigger_queue SET attempt_count=? WHERE trigger_id IN ({placeholders}) "
            f"AND price_source=? AND source_generation=? AND cutover_id=?",
            [new, *tids, *gen_params])
        return new

    def _backoff_sec(self, attempt: int) -> int:
        seq = self.cfg.retry_backoff_sec
        if attempt <= len(seq):
            return seq[attempt - 1]
        return seq[-1] * (2 ** (attempt - len(seq)))

    def _enqueue_pending(self, conn, asset_type: str, code: str, tables, *,
                         reason: str, trigger_id: str, event_id: Optional[str],
                         dead_letter: bool) -> List[Tuple]:
        now = _now_ts()
        now_iso = _now_iso_ts().isoformat(timespec="seconds")
        ranges = self._security_range(conn, asset_type, code)
        keys = []
        for table in tables:
            freq = "1min" if table.endswith("minutes") else "daily"
            rs, re = ranges[1] if freq == "1min" else ranges[0]
            status = "dead_letter" if dead_letter else "retryable_failed"
            dl_at = now_iso if dead_letter else None
            conn.execute(
                "INSERT INTO qfq_pending_backfill "
                "(asset_type, code, table_name, freq, range_start, range_end, price_source, "
                "source_generation, reason, status, trigger_id, last_event_id, dead_letter_at, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (asset_type, code, table_name, freq, range_start, range_end, "
                "price_source, source_generation) DO UPDATE SET reason=excluded.reason, "
                "status=excluded.status, trigger_id=excluded.trigger_id, "
                "last_event_id=excluded.last_event_id, dead_letter_at=excluded.dead_letter_at, "
                "updated_at=excluded.updated_at",
                [asset_type, code, table, freq, rs, re, self._ident["price_source"],
                 self._ident["source_generation"], reason, status, trigger_id,
                 event_id, dl_at, now, now])
            keys.append((asset_type, code, table, freq, rs, re,
                         self._ident["price_source"], self._ident["source_generation"]))
        return keys

    # ------------------------------------------------------------------
    # 质量门控（编排级）
    # ------------------------------------------------------------------
    def _qfq_gate(self, conn, cycle_id: str, claimed_units: List[Dict],
                  run_id: str,
                  codes_filter: Optional[Sequence[str]] = None) -> Tuple[bool, Dict]:
        report: Dict = {"passed": True, "reasons": [], "identity": dict(self._ident)}
        gen_sql, gen_params = self._generation_where()
        cutoff = (_now_iso_ts() - timedelta(seconds=self.cfg.claim_lease_sec)).isoformat(timespec="seconds")
        orphan_sql = ("SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='in_progress' "
                      "AND (claimed_at IS NULL OR claimed_at >= ?) AND " + gen_sql)
        orphan_params: List[object] = [cutoff, *gen_params]
        if codes_filter:
            orphan_sql += f" AND code IN ({', '.join(['?'] * len(codes_filter))})"
            orphan_params.extend(codes_filter)
        orphan = conn.execute(orphan_sql, orphan_params).fetchone()[0]
        if orphan:
            report["passed"] = False
            report["reasons"].append(f"orphan in_progress={orphan}")
        dl_sql = "SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='dead_letter' AND " + gen_sql
        dl_params: List[object] = list(gen_params)
        if codes_filter:
            dl_sql += f" AND code IN ({', '.join(['?'] * len(codes_filter))})"
            dl_params.extend(codes_filter)
        dl = conn.execute(dl_sql, dl_params).fetchone()[0]
        dl_max = int(self.cfg.quality_thresholds.get("dead_letter_max", 0))
        if dl > dl_max:
            report["passed"] = False
            report["reasons"].append(f"dead_letter={dl} 超过阈值 {dl_max}")
        for unit in claimed_units:
            for tid in unit["triggers"]:
                row = conn.execute(
                    "SELECT status FROM qfq_trigger_queue WHERE trigger_id=? "
                    "AND price_source=? AND source_generation=? AND cutover_id=?",
                    [tid, *gen_params]).fetchone()
                if row is None or row[0] != "committed":
                    report["passed"] = False
                    report["reasons"].append(
                        f"trigger {tid} 未 committed（{row[0] if row else 'missing'}）")
                    break
        wi = conn.execute(
            "SELECT COUNT(*) FROM qfq_watermark_intent WHERE cycle_id=? "
            "AND source_generation=? AND cutover_id=?",
            [cycle_id, self._ident["source_generation"], self._ident["cutover_id"]]).fetchone()[0]
        report["watermark_intents"] = int(wi)
        baseline = audit_pending_slots(conn, identity=self._baseline_identity())
        report["discovery_baseline"] = baseline
        if not baseline.get("passed", False):
            report["passed"] = False
            report["reasons"].append("discovery baseline pending-slot audit failed")
        return report["passed"], report

    # ------------------------------------------------------------------
    # 水位延迟提交 / 保持
    # ------------------------------------------------------------------
    def defer_watermark(self, conn, *, cycle_id: str, source: str, table: str,
                        freq: str, candidate_watermark) -> None:
        old = self._read_watermark(conn, source, table, freq)
        conn.execute(
            "INSERT INTO qfq_watermark_intent "
            "(cycle_id, source, table_name, freq, source_generation, cutover_id, "
            "old_watermark, candidate_watermark, status, hold_reason, committed_at) "
            "VALUES (?,?,?,?,?,?,?,?,'pending',NULL,NULL) "
            "ON CONFLICT (cycle_id, source, table_name, freq, source_generation, cutover_id) "
            "DO UPDATE SET candidate_watermark=excluded.candidate_watermark, "
            "status='pending', hold_reason=NULL, committed_at=NULL",
            [cycle_id, source, table, freq, self._ident["source_generation"],
             self._ident["cutover_id"], old, candidate_watermark])

    def _read_watermark(self, conn, source: str, table: str, freq: str):
        row = conn.execute(
            "SELECT last_date FROM source_watermark WHERE source=? AND table_name=? AND freq=? "
            "AND source_generation=? AND cutover_id=?",
            [source, table, freq, self._ident["source_generation"],
             self._ident["cutover_id"]]).fetchone()
        return row[0] if row else None

    def _commit_or_hold_watermarks(self, conn, *, cycle_id: str, passed: bool,
                                   reason: str, run_id: str,
                                   summary: CycleSummary) -> None:
        intents = conn.execute(
            "SELECT source, table_name, freq, candidate_watermark FROM qfq_watermark_intent "
            "WHERE cycle_id=? AND source_generation=? AND cutover_id=?",
            [cycle_id, self._ident["source_generation"], self._ident["cutover_id"]]).fetchall()
        for source, table, freq, cand in intents:
            if passed:
                self._advance_watermark(conn, source, table, freq, cand, run_id)
                conn.execute(
                    "UPDATE qfq_watermark_intent SET status='committed', committed_at=?, "
                    "hold_reason=NULL WHERE cycle_id=? AND source=? AND table_name=? AND freq=? "
                    "AND source_generation=? AND cutover_id=?",
                    [_now_ts(), cycle_id, source, table, freq,
                     self._ident["source_generation"], self._ident["cutover_id"]])
                summary.watermarks_committed += 1
            else:
                conn.execute(
                    "UPDATE qfq_watermark_intent SET status='held', hold_reason=? "
                    "WHERE cycle_id=? AND source=? AND table_name=? AND freq=? "
                    "AND source_generation=? AND cutover_id=?",
                    [reason, cycle_id, source, table, freq,
                     self._ident["source_generation"], self._ident["cutover_id"]])
                summary.watermarks_held += 1

    def _advance_watermark(self, conn, source: str, table: str, freq: str,
                           new_watermark, batch_id: str) -> None:
        if (self.watermark_advancer is not None
                and getattr(self.cfg, "generation_mode", "pre_cutover") != "dynamic"):
            self.watermark_advancer(source, table, freq, new_watermark, batch_id)
            return
        # 回退：直接 upsert source_watermark（与 writer.advance_watermark 同表）。
        # v2.4 B-3a：8 列显式 INSERT + pre-cutover 静态哨兵（source 保留真实值不改写）。
        gen, cutover = self._ident["source_generation"], self._ident["cutover_id"]
        conn.execute(
            "INSERT INTO source_watermark (source, table_name, freq, last_date, "
            " last_batch_id, updated_at, source_generation, cutover_id) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT (source, table_name, freq) DO UPDATE SET "
            " last_date=excluded.last_date, last_batch_id=excluded.last_batch_id, "
            " updated_at=excluded.updated_at, "
            " source_generation=excluded.source_generation, cutover_id=excluded.cutover_id",
            [source, table, freq, new_watermark, batch_id, _now_ts(), gen, cutover])

    # ------------------------------------------------------------------
    # bootstrap（首次部署）
    # ------------------------------------------------------------------
    def bootstrap_completed(self, conn) -> bool:
        """任务6.2/6.3：bootstrap 完成态 fail-closed 判定。

        必须同时满足：
        1. 存在 status='completed' 的 bootstrap_run；
        2. 版本校验：schema_version / config_hash / baseline_version 与当前一致
           （任一不匹配 → 视为未完成，需重做 bootstrap）；
        3. 证券级状态机全清：pending / in_progress / blocked / failed / dead_letter
           计数均为 0（blocked 不得解锁，failed/dead_letter 不得被当作完成）。
        任一非零或版本不匹配 → 返回 False（本轮 fail-closed，不推进水位、不处理 trigger）。
        """
        run = conn.execute(
            "SELECT bootstrap_run_id, schema_version, config_hash, baseline_version "
            "FROM qfq_bootstrap_run WHERE status='completed' "
            "AND price_source=? AND source_generation=? AND cutover_id=? "
            "ORDER BY started_at DESC LIMIT 1",
            [self._ident["price_source"], self._ident["source_generation"],
             self._ident["cutover_id"]]
        ).fetchone()
        if run is None:
            return False
        run_id, schema_v, config_h, baseline_v = run[0], run[1], run[2], run[3]

        # 任务6.3：版本校验（落库值为 NULL 时视为旧库，跳过该单项校验，避免破坏历史库）
        if schema_v is not None and schema_v != SCHEMA_VERSION:
            logger.warning(
                f"[qfq_orch] bootstrap 未完成：schema_version 不匹配 "
                f"({schema_v} != {SCHEMA_VERSION})")
            return False
        cur_cfg_h = getattr(self.cfg, "config_hash", None)
        if (config_h is not None and cur_cfg_h is not None
                and config_h != cur_cfg_h):
            logger.warning(
                f"[qfq_orch] bootstrap 未完成：config_hash 不匹配 "
                f"({config_h} != {cur_cfg_h})")
            return False
        cur_bl = getattr(self.cfg, "detector_baseline_version", DETECTOR_BASELINE_VERSION)
        if baseline_v is not None and baseline_v != cur_bl:
            logger.warning(
                f"[qfq_orch] bootstrap 未完成：baseline_version 不匹配 "
                f"({baseline_v} != {cur_bl})")
            return False

        # 任务6.2：证券级状态机全清才视为完成（blocked 不得解锁）
        counts = conn.execute(
            "SELECT status, COUNT(*) FROM qfq_bootstrap_item "
            "WHERE bootstrap_run_id=? GROUP BY status", [run_id]).fetchall()
        by_status = {r[0]: r[1] for r in counts}
        for bad in ("pending", "in_progress", "blocked", "failed", "dead_letter"):
            if by_status.get(bad, 0) > 0:
                logger.warning(
                    f"[qfq_orch] bootstrap 未完成：存在 {bad}={by_status.get(bad, 0)}")
                return False
        return True

    def _aux_query(self, sql: str, params: Sequence = ()) -> List[tuple]:
        if not self.aux_db:
            if getattr(self.cfg, "generation_mode", "pre_cutover") == "dynamic":
                raise RuntimeError("dynamic generation 未配置隔离 aux_db")
            logger.warning("[qfq_orch] aux_db 未配置，因子观察查询降级为空结果")
            return []
        try:
            aconn = self._aux_router.connect(
                source_generation=self._ident["source_generation"],
                cutover_id=self._ident["cutover_id"], read_only=True,
                require_exists=(getattr(self.cfg, "generation_mode", "pre_cutover") == "dynamic"),
            )
        except Exception:
            if getattr(self.cfg, "generation_mode", "pre_cutover") == "dynamic":
                raise
            aconn = sqlite3.connect(str(self.aux_db), timeout=30)
        try:
            return aconn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if getattr(self.cfg, "generation_mode", "pre_cutover") == "dynamic":
                raise RuntimeError(f"dynamic aux 查询失败: {exc}") from exc
            logger.warning(f"[qfq_orch] 辅助库查询失败（视为空）: {exc}")
            return []
        finally:
            aconn.close()

    def _classify_bootstrap_security(self, conn, asset_type: str, code: str) -> str:
        daily_t = ASSET_PRICE_TABLES[asset_type][0]
        if not conn.execute(f"SELECT 1 FROM {daily_t} WHERE code=? LIMIT 1", [code]).fetchone():
            return "no_price_history"
        has_div = conn.execute("SELECT 1 FROM stock_dividend WHERE code=?", [code]).fetchone()
        has_obs = self._aux_query(
            "SELECT 1 FROM qfq_factor_observation WHERE asset_type=? AND code=? LIMIT 1",
            [asset_type, code])
        if not has_div and not has_obs:
            return "unverifiable"
        committed = conn.execute(
            "SELECT 1 FROM qfq_trigger_queue WHERE asset_type=? AND code=? "
            "AND status='committed' AND trigger_type IN ('stock_dividend','factor_new') "
            "AND price_source=? AND source_generation=? AND cutover_id=? LIMIT 1",
            [asset_type, code, self._ident["price_source"],
             self._ident["source_generation"], self._ident["cutover_id"]]).fetchone()
        return "consistent" if committed else "stale"

    def bootstrap_plan(self, conn, *, as_of_ms: int,
                       codes_filter: Optional[Sequence[str]] = None,
                       admissible_codes: Optional[Sequence[Tuple[str, str]]] = None
                       ) -> BootstrapPlan:
        """构建 bootstrap 计划；准入模式直接以完整名单为工作集。"""
        candidates: List[Tuple[str, str]] = []
        codes = sorted({str(code).strip() for code in (codes_filter or []) if str(code).strip()})
        admissible = None
        if admissible_codes is not None:
            normalized_admissible = []
            for item in admissible_codes:
                if isinstance(item, str):
                    normalized_admissible.append(("STOCK", item))
                else:
                    normalized_admissible.append((item[0], item[1]))
            admissible = {
                (str(asset_type).strip().upper(), str(code).strip())
                for asset_type, code in normalized_admissible
                if str(asset_type).strip() and str(code).strip()
            }
            if not admissible:
                raise ValueError("admissible_codes 不能为空")
            invalid_assets = sorted({at for at, _ in admissible if at not in ASSET_PRICE_TABLES})
            if invalid_assets:
                raise ValueError(f"admissible_codes 含非法资产类型: {invalid_assets}")
            if codes_filter is not None:
                admissible = {(at, code) for at, code in admissible if code in codes}
                if not admissible:
                    raise ValueError("codes_filter 与 admissible_codes 无交集")
        where_sql = ""
        params: List[str] = []
        if codes_filter is not None:
            if not codes:
                raise ValueError("codes_filter 不能为空")
            placeholders = ",".join("?" for _ in codes)
            where_sql = f" AND code IN ({placeholders})"
            params = codes
        sd = conn.execute(
            "SELECT DISTINCT code FROM stock_dividend WHERE div_proc='实施'" + where_sql,
            params).fetchall()
        for (code,) in sd:
            candidates.append(("STOCK", code))
        obs_sql = "SELECT DISTINCT asset_type, code FROM qfq_factor_observation"
        if codes_filter is not None:
            obs_sql += f" WHERE code IN ({','.join('?' for _ in codes)})"
        obs = self._aux_query(obs_sql, params)
        for at, code in obs:
            candidates.append((at, code))
        candidate_set = {
            (str(at).strip().upper(), str(code).strip()) for at, code in candidates
        }
        if admissible is None:
            work_items = sorted(candidate_set)
            excluded_items: List[Tuple[str, str]] = []
        else:
            work_items = sorted(admissible)
            excluded_items = sorted(candidate_set - admissible)
        all_items = work_items + excluded_items
        run_id = f"bs_{uuid.uuid4().hex[:10]}"
        # 任务6.3：落盘版本标识，供 bootstrap_completed 做 fail-closed 校验
        _schema_v = SCHEMA_VERSION
        _config_h = getattr(self.cfg, "config_hash", None)
        _baseline_v = getattr(self.cfg, "detector_baseline_version", DETECTOR_BASELINE_VERSION)
        conn.execute(
            "INSERT INTO qfq_bootstrap_run (bootstrap_run_id, total_count, "
            " completed_count, blocked_count, failed_count, status, "
            " schema_version, config_hash, baseline_version, price_source, "
            " source_generation, cutover_id, started_at, updated_at) "
            "VALUES (?,?,0,0,0,'planned',?,?,?,?,?,?,?,?)",
            [run_id, len(all_items), _schema_v, _config_h, _baseline_v,
             self._ident["price_source"], self._ident["source_generation"], self._ident["cutover_id"],
             _now_ts(), _now_ts()])
        pending_items: List[Tuple[str, str]] = []
        classify: Dict[str, int] = {}
        for at, code in excluded_items:
            conn.execute(
                "INSERT INTO qfq_bootstrap_item (bootstrap_run_id, asset_type, code, "
                " status, block_reason, updated_at) "
                "VALUES (?,?,?,'excluded','NOT_ADMISSIBLE',?) "
                "ON CONFLICT (bootstrap_run_id, asset_type, code) DO NOTHING",
                [run_id, at, code, _now_ts()])
        for at, code in work_items:
            if admissible is None:
                cat = self._classify_bootstrap_security(conn, at, code)
                classify[cat] = classify.get(cat, 0) + 1
                if cat != "stale":
                    continue
            conn.execute(
                "INSERT INTO qfq_bootstrap_item (bootstrap_run_id, asset_type, code, "
                " status, updated_at) VALUES (?,?,?,'pending',?) "
                "ON CONFLICT (bootstrap_run_id, asset_type, code) DO NOTHING",
                [run_id, at, code, _now_ts()])
            pending_items.append((at, code))
        logger.info(
            f"[qfq_orch] bootstrap 候选分类 {classify}；"
            f"入队 pending={len(pending_items)}/{len(all_items)} "
            f"excluded={len(excluded_items)}")
        return BootstrapPlan(
            total=len(all_items), items=pending_items, run_id=run_id,
            excluded=len(excluded_items))

    def supersede_bootstrap_runs(self, conn, run_ids: Sequence[str]) -> Dict[str, int]:
        ids = sorted({str(run_id).strip() for run_id in run_ids if str(run_id).strip()})
        if not ids:
            raise ValueError("run_ids 不能为空")
        placeholders = ",".join("?" for _ in ids)
        ident = [self._ident["price_source"], self._ident["source_generation"],
                 self._ident["cutover_id"]]
        found = conn.execute(
            f"SELECT bootstrap_run_id FROM qfq_bootstrap_run WHERE bootstrap_run_id IN "
            f"({placeholders}) AND price_source=? AND source_generation=? AND cutover_id=?",
            [*ids, *ident]).fetchall()
        missing = sorted(set(ids) - {r[0] for r in found})
        if missing:
            raise ValueError(f"bootstrap run 不存在或不属于当前世代: {missing}")
        now = _now_ts()
        item_count = conn.execute(
            f"SELECT COUNT(*) FROM qfq_bootstrap_item WHERE bootstrap_run_id IN "
            f"({placeholders}) AND status='blocked'", ids).fetchone()[0]
        conn.execute(
            f"UPDATE qfq_bootstrap_item SET status='superseded', updated_at=? "
            f"WHERE bootstrap_run_id IN ({placeholders}) AND status='blocked'",
            [now, *ids])
        run_count = conn.execute(
            f"SELECT COUNT(*) FROM qfq_bootstrap_run WHERE bootstrap_run_id IN "
            f"({placeholders}) AND status<>'completed' AND price_source=? "
            f"AND source_generation=? AND cutover_id=?", [*ids, *ident]).fetchone()[0]
        conn.execute(
            f"UPDATE qfq_bootstrap_run SET status='superseded', updated_at=? "
            f"WHERE bootstrap_run_id IN ({placeholders}) AND status<>'completed' "
            f"AND price_source=? AND source_generation=? AND cutover_id=?",
            [now, *ids, *ident])
        return {"runs": int(run_count), "items": int(item_count)}

    def bootstrap_run(self, conn, *, run_id: str, as_of_ms: int,
                      fetcher: FreshFetcher, resume: bool = False) -> Dict:
        ident = [self._ident["price_source"], self._ident["source_generation"],
                 self._ident["cutover_id"]]
        owner = conn.execute(
            "SELECT 1 FROM qfq_bootstrap_run WHERE bootstrap_run_id=? AND price_source=? "
            "AND source_generation=? AND cutover_id=?", [run_id, *ident]).fetchone()
        if owner is None:
            raise ValueError(f"bootstrap run 不属于当前世代: {run_id}")
        batch = self.cfg.bootstrap_batch_size
        rows = conn.execute(
            "SELECT asset_type, code FROM qfq_bootstrap_item WHERE bootstrap_run_id=? "
            "AND status='pending' LIMIT ?", [run_id, batch]).fetchall()
        completed = blocked = failed = 0
        for at, code in rows:
            eff = self._security_effective_dates(conn, at, code)
            outcome = self._reanchor_security(
                conn, run_id=run_id, asset_type=at, code=code,
                trigger_ids=[f"bootstrap:{at}:{code}"], effective_dates=eff,
                attempt=1, fetcher=fetcher)
            if outcome.status == "committed":
                conn.execute(
                    "UPDATE qfq_bootstrap_item SET status='completed', finished_at=?, "
                    "updated_at=? WHERE bootstrap_run_id=? AND asset_type=? AND code=?",
                    [_now_ts(), _now_ts(), run_id, at, code])
                completed += 1
            elif outcome.status == "blocked":
                conn.execute(
                    "UPDATE qfq_bootstrap_item SET status='blocked', block_reason=?, updated_at=? "
                    "WHERE bootstrap_run_id=? AND asset_type=? AND code=?",
                    [outcome.error, _now_ts(), run_id, at, code])
                blocked += 1
            else:
                conn.execute(
                    "UPDATE qfq_bootstrap_item SET status='failed', last_error=?, updated_at=? "
                    "WHERE bootstrap_run_id=? AND asset_type=? AND code=?",
                    [outcome.error, _now_ts(), run_id, at, code])
                failed += 1
        conn.execute(
            "UPDATE qfq_bootstrap_run SET "
            "completed_count=(SELECT COUNT(*) FROM qfq_bootstrap_item WHERE bootstrap_run_id=? AND status='completed'), "
            "blocked_count=(SELECT COUNT(*) FROM qfq_bootstrap_item WHERE bootstrap_run_id=? AND status='blocked'), "
            "failed_count=(SELECT COUNT(*) FROM qfq_bootstrap_item WHERE bootstrap_run_id=? AND status='failed'), "
            "updated_at=? WHERE bootstrap_run_id=? AND price_source=? AND source_generation=? AND cutover_id=?",
            [run_id, run_id, run_id, _now_ts(), run_id, *ident])
        remaining = self._bootstrap_remaining(conn, run_id)
        failed_total = conn.execute(
            "SELECT COUNT(*) FROM qfq_bootstrap_item WHERE bootstrap_run_id=? AND status='failed'",
            [run_id]).fetchone()[0]
        if remaining == 0:
            conn.execute(
                "UPDATE qfq_bootstrap_run SET status=?, updated_at=? WHERE bootstrap_run_id=? "
                "AND price_source=? AND source_generation=? AND cutover_id=?",
                ["completed" if failed_total == 0 else "failed", _now_ts(), run_id, *ident])
        else:
            conn.execute(
                "UPDATE qfq_bootstrap_run SET status='running', updated_at=? WHERE bootstrap_run_id=? "
                "AND status='planned' AND price_source=? AND source_generation=? AND cutover_id=?",
                [_now_ts(), run_id, *ident])
        return {"run_id": run_id, "completed": completed, "blocked": blocked,
                "failed": failed, "remaining": remaining}

    def _bootstrap_remaining(self, conn, run_id: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM qfq_bootstrap_item WHERE bootstrap_run_id=? "
            "AND status='pending'", [run_id]).fetchone()[0]

    def _security_effective_dates(self, conn, asset_type: str, code: str) -> List[int]:
        rows = conn.execute(
            "SELECT DISTINCT ex_date FROM stock_dividend WHERE code=? AND div_proc='实施' "
            "AND ex_date IS NOT NULL", [code]).fetchall()
        dates = [int(r[0]) for r in rows]
        # 叠加因子观察时间（SQLite 辅助库）
        frows = self._aux_query(
            "SELECT DISTINCT factor_time FROM qfq_factor_observation "
            "WHERE asset_type=? AND code=?", (asset_type, code))
        dates += [int(r[0]) for r in frows]
        return sorted(set(dates))

    def bootstrap_audit(self, conn, run_id: str) -> Dict:
        row = conn.execute(
            "SELECT total_count, completed_count, blocked_count, failed_count, status "
            "FROM qfq_bootstrap_run WHERE bootstrap_run_id=? AND price_source=? "
            "AND source_generation=? AND cutover_id=?",
            [run_id, self._ident["price_source"], self._ident["source_generation"],
             self._ident["cutover_id"]]).fetchone()
        if row is None:
            raise ValueError(f"bootstrap run 不存在或不属于当前世代: {run_id}")
        remaining = self._bootstrap_remaining(conn, run_id)
        def count(status):
            return conn.execute(
                "SELECT COUNT(*) FROM qfq_bootstrap_item WHERE bootstrap_run_id=? AND status=?",
                [run_id, status]).fetchone()[0]
        dead, excluded, superseded = count("failed"), count("excluded"), count("superseded")
        return {"run_id": run_id, "total": row[0], "completed": row[1],
                "blocked": row[2], "failed": row[3], "excluded": excluded,
                "superseded": superseded, "status": row[4], "remaining": remaining,
                "dead_letter_items": dead, "clean": (remaining == 0 and dead == 0),
                "identity": dict(self._ident)}

    # ------------------------------------------------------------------
    # 主入口：post-ingest 阶段
    # ------------------------------------------------------------------
    def run_post_ingest(self, conn, *, cycle_id: str, run_id: str,
                        as_of_ms: int, fetcher: Optional[FreshFetcher] = None,
                        detector_degraded: bool = False,
                        codes_filter: Optional[Sequence[str]] = None) -> CycleSummary:
        """daemon 在普通增量任务 + 水位延迟写完后调用。

        流程：recover → discover → claim/merge → reanchor → gate → commit/hold watermarks。
        返回 CycleSummary（含 watermarks_committed/held）。

        detector_degraded: 由 daemon 调用方传入，表示本轮因子刷新失败/缺失，
            检测器不可信 → 四价格表水位强制 hold（仍跑 discover，但 gate 不通过）。
        """
        scope = tuple(dict.fromkeys(codes_filter or ())) or None
        fetcher = fetcher or self.fetcher
        self._resolve_dynamic_identity(conn, require_aux=True)
        summary = CycleSummary(cycle_id=cycle_id)
        if not self.cfg.enabled:
            summary.error = "orchestrator disabled"
            return summary
        # 记录本轮因子检测器健康度（即使后续 discover 抛错也要落库，供审计/水位决策）
        conn.execute(
            "UPDATE qfq_cycle_run SET detector_degraded=? WHERE cycle_id=? "
            "AND price_source=? AND source_generation=? AND cutover_id=?",
            [1 if detector_degraded else 0, cycle_id, self._ident["price_source"],
             self._ident["source_generation"], self._ident["cutover_id"]])
        if self.cfg.require_bootstrap and not self.bootstrap_completed(conn):
            summary.bootstrap_required = True
            summary.error = "require_bootstrap=true 且无可匹配 completed bootstrap，fail-closed"
            logger.error(f"[qfq_orch] {summary.error}（本轮不推进水位、不处理 trigger）")
            self._finish_cycle(conn, cycle_id, "failed", summary)
            return summary
        if fetcher is None:
            summary.error = "fetcher 未提供（xtquant 不可用）→ 保持水位"
            logger.error(f"[qfq_orch] {summary.error}")
            self._finish_cycle(conn, cycle_id, "failed", summary)
            return summary
        if self.calendar is None:
            summary.error = "calendar 未提供（trade_calendar 校验是引擎硬前置）→ 保持水位"
            logger.error(f"[qfq_orch] {summary.error}")
            self._finish_cycle(conn, cycle_id, "failed", summary)
            return summary
        try:
            self._set_cycle_phase(conn, cycle_id, "recovering")
            self.recover_stale_in_progress(conn, run_id, codes_filter=scope)
            self.recover_pending_due(conn, run_id, codes_filter=scope)
            self.promote_scheduled_due(
                conn, as_of_ms=as_of_ms, codes_filter=scope)

            self._set_cycle_phase(conn, cycle_id, "observing")
            new_triggers = self._discover(
                conn, run_id=run_id, as_of_ms=as_of_ms, codes_filter=scope)
            summary.triggers_found = len(new_triggers)

            self._set_cycle_phase(conn, cycle_id, "fetching")
            units = self._claim_and_merge(
                conn, cycle_id=cycle_id, run_id=run_id, as_of_ms=as_of_ms,
                codes_filter=scope)
            summary.claimed = len(units)

            self._set_cycle_phase(conn, cycle_id, "applying")
            for unit in units:
                outcome = self._reanchor_security(
                    conn, run_id=run_id, asset_type=unit["asset_type"], code=unit["code"],
                    trigger_ids=unit["triggers"], effective_dates=unit["effective_dates"],
                    attempt=int(unit.get("attempt", 0)) + 1, fetcher=fetcher)
                self._apply_trigger_outcome(conn, run_id=run_id, unit=unit,
                                            outcome=outcome, fetcher=fetcher, summary=summary)

            self._set_cycle_phase(conn, cycle_id, "gating")
            passed, report = self._qfq_gate(
                conn, cycle_id, units, run_id, codes_filter=scope)
            if scope:
                scoped_passed = passed
                passed = False
                report["scoped_codes"] = list(scope)
                report["scoped_gate_passed"] = scoped_passed
                report["passed"] = False
                report.setdefault("reasons", []).append(
                    "scoped reconcile: 全局水位强制 hold")
            if detector_degraded:
                # 因子检测器本轮不可信（刷新失败/缺失）→ 四价格表水位强制 hold：
                # 仍跑 discover，但 gate 不通过，避免基于不可信因子重锚推进水位。
                passed = False
                report.setdefault("reasons", []).append(
                    "detector_degraded: 因子刷新失败，价格水位强制 hold")
                logger.warning("[qfq_orch] detector_degraded=True → 本轮价格水位强制 hold")
            summary.gate_report = report

            self._set_cycle_phase(conn, cycle_id, "finalized")
            reason = "; ".join(report.get("reasons", [])) or "quality_gate_failed"
            self._commit_or_hold_watermarks(conn, cycle_id=cycle_id, passed=passed,
                                            reason=reason, run_id=run_id, summary=summary)
            summary.status = "finalized" if passed else "finalized_held"
            self._finish_cycle(conn, cycle_id,
                               "finalized" if passed else "finalized_held", summary)
            return summary
        except Exception as e:
            logger.exception(f"[qfq_orch] 周期异常: {e}")
            summary.error = f"{type(e).__name__}: {e}"
            summary.status = "failed"
            self._finish_cycle(conn, cycle_id, "failed", summary)
            return summary

    # 兼容别名
    def run_cycle(self, conn, **kw) -> CycleSummary:
        return self.run_post_ingest(conn, **kw)


def phase_of(status: str) -> str:
    return {
        "finalized": "finalized", "finalized_held": "finalized",
        "failed": "failed", "interrupted": "interrupted",
    }.get(status, "finalized")


def _now_iso_ts() -> datetime:
    return datetime.now(BJ_TZ)
