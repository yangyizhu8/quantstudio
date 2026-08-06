"""QFQ 常驻增量事件发现（resident orchestrator v2 —— 事件/因子发现层）

本模块只做**事件/因子发现**，不拉价格（价格由 qfq_fresh_capture 负责，源锁定 xtquant）。
它把"可能发生复权跳变"的语义事件落进 ``qfq_trigger_queue``（DuckDB 主库），供后续
重锚执行消费。全部写入幂等（确定性 trigger_id + INSERT OR IGNORE），崩溃可重放。

三类发现源：
1. ``stock_dividend`` 表（DuckDB 主库，writers 维护）—— 全表哈希扫描，防晚到修订漏检。
2. ``adj_factor`` 表（SQLite 辅助库 qfq_aux.db）—— 股票复权因子快照 → 版本化 observation。
3. ``fund_adj`` 表（SQLite 辅助库 qfq_aux.db）—— ETF 复权因子快照 → 版本化 observation。

observation 写入 ``qfq_factor_observation``（qfq_observation.ObservationStore 管理），
修订自动产生 ``qfq_factor_revision_alert`` outbox；本模块 ``consume_revision_alerts``
把 pending alert 幂等转成 DuckDB trigger。

契约依赖（单一真相源，禁止重写）：
- ``qfq_orchestrator_types``：trigger_id_of / payload_hash_of / TriggerRecord / 枚举 / QFQOrchestratorConfig
- ``qfq_observation``：ObservationStore / ObservationResult
- ``qfq_reanchor_schema``：DDL + init_duckdb_schema / init_sqlite_schema

本模块 import 不触发任何 xtquant 网络连接（lazy，仅在真正拉因子时由维护层负责）。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from quantstudio.pipeline.qfq_orchestrator_types import (
    QFQOrchestratorConfig,
    TriggerRecord,
    payload_hash_of,
    trigger_id_of,
)
from quantstudio.pipeline.qfq_observation import ObservationResult, ObservationStore
from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
# v2.4 B-1：分红 payload hash 单一真相源（中立模块，防 scan/establish 漂移 + 防循环 import）
from quantstudio.pipeline.qfq_dividend_payload import dividend_payload_hash, norm_div_val

logger = logging.getLogger(__name__)

# 北京时区（与 qfq_observation / qfq_revision 一致）
BJ_TZ = timezone(timedelta(hours=8))

# qfq_aux.db 因子快照表（与 qfq_maintenance.py 口径对齐：code 裸码，time 毫秒时间戳，
# adj_factor 因子值）。股票读 adj_factor；ETF 读 fund_adj（本模块预期存在的独立表）。
ADJ_FACTOR_TABLE = "adj_factor"
FUND_ADJ_TABLE = "fund_adj"


def _now_ts() -> str:
    """DuckDB TIMESTAMP 友好的北京墙钟字符串（无时区偏移，避免 TIMESTAMP 解析歧义）。

    统一口径：+08 墙钟，格式 ``YYYY-MM-DD HH:MM:SS``。
    """
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _norm_div_val(v):
    """兼容别名（v2.4 B-1：生产路径已改用 ``qfq_dividend_payload.norm_div_val``）。

    保留此名仅为旧测试/外部导入兼容；指向中立模块的单一实现，**不**允许生产路径
    继续使用两套实现。迁移完成后可移除。
    """
    return norm_div_val(v)


class EventDiscovery:
    """QFQ 增量事件发现器。

    构造仅持有 cfg 与可选 aux_db 路径；不持有长连接（方法均接收外部 DuckDB ``conn``
    与 ``run_id``，观察类方法内部临时开 SQLite 连接并传 ``conn`` 给 ObservationStore）。

    Args:
        cfg: QFQOrchestratorConfig（仅需读 stock_factor_detector / etf_factor_detector / freqs）。
        aux_db: qfq_aux.db 路径（None → 由 ObservationStore 推导；测试传临时库）。
    """

    def __init__(self, cfg: QFQOrchestratorConfig, aux_db: Optional[str] = None):
        self.cfg = cfg
        self.aux_db = aux_db
        self._obs_store: Optional[ObservationStore] = None

    # —— 惰性 ObservationStore（管理 qfq_aux.db 的 observation / alert）——
    @property
    def obs_store(self) -> ObservationStore:
        if self._obs_store is None:
            self._obs_store = ObservationStore(self.aux_db)
        return self._obs_store

    # ------------------------------------------------------------------
    # 辅助：检测游标 upsert（DuckDB qfq_observation_cursor）
    # ------------------------------------------------------------------
    def _upsert_cursor(self, conn, detector_name: str, asset_type: str,
                       cursor_as_of: Optional[int], run_id: str,
                       status: str = "ok") -> None:
        from quantstudio.pipeline.qfq_schema_contracts import pre_cutover_qfq_identity
        now = _now_ts()
        # v2.4 B-3a.3 P0-2：统一静态 pre-cutover identity（price_source=真实值；
        # generation/cutover 固定 legacy 哨兵，不读 cfg.source_generation/cutover_id）。
        ident = pre_cutover_qfq_identity(self.cfg.price_source)
        conn.execute(
            "INSERT OR REPLACE INTO qfq_observation_cursor "
            "(detector_name, asset_type, price_source, source_generation, "
            " cursor_as_of, last_run_id, status, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [detector_name, asset_type, ident["price_source"], ident["source_generation"],
             cursor_as_of, run_id, status, now],
        )

    # ------------------------------------------------------------------
    # 1. stock_dividend 全表哈希扫描 → trigger（DuckDB 主库）
    # ------------------------------------------------------------------
    def scan_stock_dividend(self, conn, *, as_of_ms: int, run_id: str,
                            bootstrap: bool = False,
                            codes_filter: Optional[Sequence[str]] = None) -> List[TriggerRecord]:
        """扫描 stock_dividend（div_proc='实施' 且 ex_date 非空）生成 STOCK 分红 trigger。

        - 全表扫描（约 5 万行），不依赖日期 cursor —— 防晚到修订漏检。
        - 同语义事件用确定性 trigger_id → INSERT OR IGNORE 天然去重。
        - status：ex_date > as_of_ms → 'scheduled'（只调度不执行）；否则 'pending'。
        - bootstrap=True：不插入 trigger（避免全历史洪水），仅更新 observation cursor；
          返回空列表。
        - 无论 bootstrap 与否，都更新 qfq_observation_cursor(stock_dividend, STOCK)。

        Args:
            conn: 外部 DuckDB 连接（stock_dividend 与 qfq_trigger_queue 同库）。
            as_of_ms: 当前时刻（epoch-ms），用于划分 scheduled / pending。
            run_id: 本轮运行标识。
            bootstrap: 仅建 cursor 不插 trigger。
        Returns:
            本次**新插入**的 TriggerRecord 列表（已存在的不计入）。
        """
        # 任务4：动态探测列，兼容旧/新 schema（缺列按 NULL 处理，不阻断）
        present = {str(r[0]).lower() for r in conn.execute("DESCRIBE stock_dividend").fetchall()}
        if "ex_date" not in present:
            return []
        # v2.4 B-3a.3 P0-2：统一静态 pre-cutover identity（generation/cutover 固定 legacy 哨兵）
        from quantstudio.pipeline.qfq_schema_contracts import pre_cutover_qfq_identity
        _ident = pre_cutover_qfq_identity(self.cfg.price_source)
        _wanted = ["code", "ex_date", "record_date", "ann_date", "end_date",
                   "cash_div_before_tax", "cash_div_after_tax", "cash_div",
                   "stk_div", "stk_bo_rate", "stk_co_rate", "div_rat", "div_proc"]
        _sel = []
        for _c in _wanted:
            if _c == "code":
                _sel.append("code")  # code 必然存在
            elif _c.lower() in present:
                _sel.append(_c)
            else:
                _sel.append(f"NULL AS {_c}")
        _where = "ex_date IS NOT NULL"
        if "div_proc" in present:
            _where = f"div_proc='实施' AND {_where}"
        _params: List[object] = []
        if codes_filter:
            placeholders = ", ".join(["?"] * len(codes_filter))
            _where += f" AND code IN ({placeholders})"
            _params.extend(codes_filter)
        rows = conn.execute(
            "SELECT {cols} FROM stock_dividend WHERE {where}".format(
                cols=", ".join(_sel), where=_where),
            _params,
        ).fetchall()

        new_records: List[TriggerRecord] = []
        now = _now_ts()
        max_ex_date: Optional[int] = None

        for (code, ex_date, record_date, ann_date, end_date, cash_div_before_tax,
             cash_div_after_tax, cash_div, stk_div, stk_bo_rate, stk_co_rate,
             div_rat, div_proc) in rows:
            ex_date = int(ex_date)
            if max_ex_date is None or ex_date > max_ex_date:
                max_ex_date = ex_date
            if bootstrap:
                continue  # bootstrap：跳过 INSERT，只记录 cursor

            effective_date = ex_date
            # v2.4 B-1：payload hash 经共享函数（与 establish_discovery_baseline 共用单一真相源，防漂移）
            payload_hash = dividend_payload_hash(
                code, ex_date, record_date, ann_date, end_date,
                cash_div_before_tax, cash_div_after_tax, cash_div,
                stk_div, stk_bo_rate, stk_co_rate, div_rat, div_proc)
            trigger_id = trigger_id_of(
                "STOCK", code, ex_date, "stock_dividend", payload_hash)
            status = "scheduled" if ex_date > as_of_ms else "pending"

            # 崩溃可重放：先判存在，再 INSERT OR IGNORE，仅把"本次新插入"计入返回
            existed = conn.execute(
                "SELECT 1 FROM qfq_trigger_queue WHERE trigger_id=?",
                [trigger_id]).fetchone() is not None
            conn.execute(
                "INSERT OR IGNORE INTO qfq_trigger_queue "
                "(trigger_id, asset_type, code, trigger_type, detection_source, source_key, "
                " effective_date, payload_hash, status, trigger_id_version, price_source, "
                " source_generation, cutover_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [trigger_id, "STOCK", code, "stock_dividend", "stock_dividend",
                 str(ex_date), effective_date, payload_hash, status,
                 1, _ident["price_source"], _ident["source_generation"], _ident["cutover_id"],
                 now, now])
            if not existed:
                new_records.append(TriggerRecord(
                    trigger_id=trigger_id, asset_type="STOCK", code=code,
                    trigger_type="stock_dividend", detection_source="stock_dividend",
                    source_key=str(ex_date), effective_date=effective_date,
                    payload_hash=payload_hash, status=status,
                    trigger_id_version=1,
                    price_source=_ident["price_source"],
                    source_generation=_ident["source_generation"],
                    cutover_id=_ident["cutover_id"],
                    created_at=now, updated_at=now,
                ))

        # 无论 bootstrap 与否，更新检测游标（cursor_as_of = 最大 ex_date）
        self._upsert_cursor(
            conn, "stock_dividend", "STOCK", max_ex_date, run_id, status="ok")
        return new_records

    # ------------------------------------------------------------------
    # 2 & 3. 因子快照 → 版本化 observation（SQLite qfq_aux.db）
    # ------------------------------------------------------------------
    def _observe_factors(self, conn, *, as_of_ms: int, run_id: str,
                         bootstrap: bool, asset_type: str, table: str,
                         detector_name: str,
                         codes_filter: Optional[Sequence[str]] = None) -> ObservationResult:
        """读 qfq_aux.db 的因子快照表 → record_observations；更新观测游标。

        bootstrap 时仍记录 observation（首次 revision_no=1，不产生 alert，正好建立基线）。

        Args:
            conn: 外部 DuckDB 连接（仅用于更新 qfq_observation_cursor）。
            as_of_ms: 上界时刻（epoch-ms），future factor_time 计入 future_excluded。
            run_id: 本轮运行标识。
            bootstrap: 是否基线模式（此处观察行为不变，仅语义标记）。
            asset_type: 'STOCK' / 'ETF'。
            table: 读取的因子快照表名（adj_factor / fund_adj）。
            detector_name: 游标名（stock_adj_factor / etf_fund_adj）。
        Returns:
            ObservationResult。
        """
        aux_conn = sqlite3.connect(str(self.aux_db), timeout=30)
        try:
            aux_conn.execute("PRAGMA journal_mode=WAL")
            aux_conn.execute("PRAGMA busy_timeout=30000")
            init_sqlite_schema(aux_conn)

            factor_sql = f"SELECT code, time, adj_factor FROM {table}"
            factor_params: List[object] = []
            if codes_filter:
                placeholders = ", ".join(["?"] * len(codes_filter))
                factor_sql += f" WHERE code IN ({placeholders})"
                factor_params.extend(codes_filter)
            factor_rows = aux_conn.execute(factor_sql, factor_params).fetchall()
            observations = [
                (asset_type, str(code), int(time), float(adj_factor))
                for code, time, adj_factor in factor_rows
            ]

            result = self.obs_store.record_observations(
                observations, run_id, as_of_ms=as_of_ms, conn=aux_conn)
            # 任务3：相邻 factor_time 值变化检测 → factor_new trigger（DuckDB）
            if getattr(result, "factor_new", None):
                self._emit_factor_new_triggers(conn, result.factor_new, run_id, as_of_ms)
            aux_conn.commit()
        finally:
            aux_conn.close()

        # 局部代码扫描不能推进全局检测游标，否则后续全量周期可能漏检。
        if not codes_filter:
            self._upsert_cursor(conn, detector_name, asset_type, as_of_ms, run_id,
                                status="ok")
        return result

    # ------------------------------------------------------------------
    # 3b. 相邻 factor_time 值变化 → factor_new trigger（DuckDB，幂等）
    # ------------------------------------------------------------------
    def _emit_factor_new_triggers(self, conn, factor_new_list, run_id, as_of_ms):
        """任务3：把 record_observations 收集的 factor_new 候选幂等落地为 qfq_trigger_queue。

        detection_source = tushare_adj_factor_new（股票）/ tushare_fund_adj_new（ETF）；
        trigger_type = factor_new；trigger_id 确定性；future factor_time → scheduled。
        """
        now = _now_ts()
        from quantstudio.pipeline.qfq_schema_contracts import pre_cutover_qfq_identity
        _ident = pre_cutover_qfq_identity(self.cfg.price_source)
        for fn in factor_new_list:
            ds = "tushare_fund_adj_new" if fn.asset_type == "ETF" else "tushare_adj_factor_new"
            payload_hash = payload_hash_of(
                [fn.code, fn.factor_time, fn.previous_value, fn.current_value])
            trigger_id = trigger_id_of(fn.asset_type, fn.code, fn.factor_time, ds, payload_hash)
            status = "scheduled" if fn.factor_time > as_of_ms else "pending"
            existed = conn.execute(
                "SELECT 1 FROM qfq_trigger_queue WHERE trigger_id=?",
                [trigger_id]).fetchone() is not None
            conn.execute(
                "INSERT OR IGNORE INTO qfq_trigger_queue "
                "(trigger_id, asset_type, code, trigger_type, detection_source, source_key, "
                 " effective_date, payload_hash, factor_old, factor_new, factor_revision, "
                 " status, trigger_id_version, price_source, source_generation, "
                 " cutover_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [trigger_id, fn.asset_type, fn.code, "factor_new", ds,
                 str(fn.factor_time), fn.factor_time, payload_hash,
                 fn.previous_value, fn.current_value, None, status,
                 1, _ident["price_source"], _ident["source_generation"], _ident["cutover_id"],
                 now, now])
            if not existed:
                logger.info(
                    f"[qfq_event] factor_new trigger {trigger_id} "
                    f"({fn.asset_type} {fn.code} @ {fn.factor_time})")

    def observe_stock_adj_factor(self, conn, *, as_of_ms: int, run_id: str,
                                 bootstrap: bool = False,
                                 codes_filter: Optional[Sequence[str]] = None) -> ObservationResult:
        """股票 adj_factor 快照 → 版本化 observation。"""
        return self._observe_factors(
            conn, as_of_ms=as_of_ms, run_id=run_id, bootstrap=bootstrap,
            asset_type="STOCK", table=ADJ_FACTOR_TABLE,
            detector_name="stock_adj_factor", codes_filter=codes_filter)

    def observe_etf_fund_adj(self, conn, *, as_of_ms: int, run_id: str,
                             bootstrap: bool = False,
                             codes_filter: Optional[Sequence[str]] = None) -> ObservationResult:
        """ETF fund_adj 快照 → 版本化 observation。"""
        return self._observe_factors(
            conn, as_of_ms=as_of_ms, run_id=run_id, bootstrap=bootstrap,
            asset_type="ETF", table=FUND_ADJ_TABLE,
            detector_name="etf_fund_adj", codes_filter=codes_filter)

    # ------------------------------------------------------------------
    # 4. 消费 revision alert outbox → DuckDB trigger（幂等）
    # ------------------------------------------------------------------
    def consume_revision_alerts(self, conn, *, run_id: str,
                                as_of_ms: int,
                                codes_filter: Optional[Sequence[str]] = None) -> List[TriggerRecord]:
        """把 pending revision alert 幂等转成 DuckDB trigger。

        顺序（崩溃可重放）：先幂等落 DuckDB trigger，再 acknowledge_alert（SQLite）。
        若中间崩溃未 ack，下一轮重新消费：INSERT OR IGNORE 不重复生成，然后完成 ack。

        Returns:
            本次**新插入**的 TriggerRecord 列表（已存在的 trigger 不计入）。
        """
        aux_conn = sqlite3.connect(str(self.aux_db), timeout=30)
        new_records: List[TriggerRecord] = []
        from quantstudio.pipeline.qfq_schema_contracts import pre_cutover_qfq_identity
        _ident = pre_cutover_qfq_identity(self.cfg.price_source)
        try:
            aux_conn.execute("PRAGMA journal_mode=WAL")
            aux_conn.execute("PRAGMA busy_timeout=30000")
            init_sqlite_schema(aux_conn)

            alerts = self.obs_store.list_pending_alerts(conn=aux_conn)
            allowed_codes = set(codes_filter or ())
            for alert in alerts:
                asset_type = alert["asset_type"]
                code = alert["code"]
                if allowed_codes and code not in allowed_codes:
                    continue
                factor_time = int(alert["factor_time"])
                revision_no = int(alert["revision_no"])

                # 取该 (asset_type, code, factor_time, revision_no) 的 factor_value 作 new
                new_row = aux_conn.execute(
                    "SELECT factor_value FROM qfq_factor_observation "
                    "WHERE asset_type=? AND code=? AND factor_time=? AND revision_no=?",
                    [asset_type, code, factor_time, revision_no]).fetchone()
                if new_row is None:
                    logger.warning(
                        f"[qfq_event] alert {alert['alert_id']} 缺 observation 行，跳过")
                    continue
                new_factor_value = float(new_row[0])

                # old：revision_no-1 行（若有）
                old_factor: Optional[float] = None
                if revision_no > 1:
                    old_row = aux_conn.execute(
                        "SELECT factor_value FROM qfq_factor_observation "
                        "WHERE asset_type=? AND code=? AND factor_time=? AND revision_no=?",
                        [asset_type, code, factor_time, revision_no - 1]).fetchone()
                    if old_row is not None:
                        old_factor = float(old_row[0])

                if asset_type == "ETF":
                    detection_source = "tushare_fund_adj"
                    trigger_type = "etf_fund_adj"
                else:
                    detection_source = "tushare_adj_factor"
                    trigger_type = "factor_new" if revision_no == 1 else "factor_revision"

                payload_hash = payload_hash_of([new_factor_value])
                trigger_id = trigger_id_of(
                    asset_type, code, factor_time, detection_source, payload_hash)
                now = _now_ts()

                # 先幂等落 DuckDB trigger
                existed = conn.execute(
                    "SELECT 1 FROM qfq_trigger_queue WHERE trigger_id=?",
                    [trigger_id]).fetchone() is not None
                conn.execute(
                    "INSERT OR IGNORE INTO qfq_trigger_queue "
                    "(trigger_id, asset_type, code, trigger_type, detection_source, source_key, "
                    " effective_date, payload_hash, factor_old, factor_new, factor_revision, "
                    " status, trigger_id_version, price_source, source_generation, "
                    " cutover_id, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [trigger_id, asset_type, code, trigger_type, detection_source,
                     str(factor_time), factor_time, payload_hash, old_factor,
                     new_factor_value, revision_no, "pending",
                     1, _ident["price_source"], _ident["source_generation"], _ident["cutover_id"],
                     now, now])
                if not existed:
                    new_records.append(TriggerRecord(
                        trigger_id=trigger_id, asset_type=asset_type, code=code,
                        trigger_type=trigger_type, detection_source=detection_source,
                        source_key=str(factor_time), effective_date=factor_time,
                        payload_hash=payload_hash, factor_old=old_factor,
                        factor_new=new_factor_value, factor_revision=revision_no,
                        status="pending", trigger_id_version=1,
                        price_source=_ident["price_source"],
                        source_generation=_ident["source_generation"],
                        cutover_id=_ident["cutover_id"],
                        created_at=now, updated_at=now,
                    ))

                # 再 ack（崩溃可重放：上一步已落地，ack 失败下一轮重放不重复）
                self.obs_store.acknowledge_alert(alert["alert_id"], conn=aux_conn)

            aux_conn.commit()
        finally:
            aux_conn.close()

        return new_records

    # ------------------------------------------------------------------
    # 5. 建立基线（不灌历史 trigger，只建因子观察基线 + 分红 cursor）
    # ------------------------------------------------------------------
    def establish_baseline(self, conn, *, as_of_ms: int,
                           run_id: str) -> Dict[str, int]:
        """bootstrap 建立因子观察基线 + stock_dividend cursor（不向 trigger_queue 灌历史）。

        Returns:
            {"stock_adj_factor": <new_count>, "etf_fund_adj": <new_count>,
             "stock_dividend_cursor": <max ex_date or 0>}
        """
        res_stock = self.observe_stock_adj_factor(
            conn, as_of_ms=as_of_ms, run_id=run_id, bootstrap=True)
        res_etf = self.observe_etf_fund_adj(
            conn, as_of_ms=as_of_ms, run_id=run_id, bootstrap=True)
        self.scan_stock_dividend(
            conn, as_of_ms=as_of_ms, run_id=run_id, bootstrap=True)

        row = conn.execute(
            "SELECT cursor_as_of FROM qfq_observation_cursor "
            "WHERE detector_name='stock_dividend' AND asset_type='STOCK'"
        ).fetchone()
        cursor_as_of = int(row[0]) if row and row[0] is not None else 0

        return {
            "stock_adj_factor": res_stock.new_count,
            "etf_fund_adj": res_etf.new_count,
            "stock_dividend_cursor": cursor_as_of,
        }
