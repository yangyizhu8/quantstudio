"""
ResidentCollector — 常驻采集进程（Layer 1 模块⑤，Phase 2 核心）

职责：7×24 常驻运行，循环执行 collector_tasks.json 里的任务：
    拉取(adapter) → 对齐(aligner) → 校验(validator) → 入库(writer) → 水位推进
失败数据自动进 Quarantine；任务失败不推进水位，下周期重试。

特性：
- 指数退避重试（来自 adapter）
- filelock 单实例锁（防多进程并发写）
- 优雅退出（SIGINT/SIGTERM 完成当前批次后退出）
- 批次审计（BatchAudit 记录每批次的 raw/aligned/validated/written 数量）
- 健康检查（数据新鲜度告警）

部署：
    前台调试：python -m quantstudio.pipeline.daemon
    Windows 服务：scripts/install_windows_service.bat（NSSM）
    Linux 服务：scripts/install_linux_service.sh（systemd）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import sqlite3
from filelock import FileLock

from .aligner import FieldAligner
from .quarantine import Quarantine
from .validator import PreIngestValidator
from .writers import create_writer
from .sources import create_adapter

logger = logging.getLogger(__name__)

# 项目根
ROOT = Path(__file__).resolve().parent.parent.parent
from quantstudio._paths import db_path, quarantine_db_path, DATA_ROOT


class BatchAudit:
    """批次审计：记录每个采集批次的结果（SQLite）"""

    def __init__(self, db_path: str | Path):
        import sqlite3
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS batch_audit (
                    batch_id TEXT PRIMARY KEY,
                    task_name TEXT, source TEXT, table_name TEXT, freq TEXT,
                    rows_raw INTEGER, rows_aligned INTEGER,
                    rows_passed INTEGER, rows_rejected INTEGER,
                    rows_written INTEGER, rows_fixed INTEGER, status TEXT,
                    error TEXT, started_at TEXT, finished_at TEXT
                )""")
            # 兼容迁移：为旧表补 rows_new / rows_updated 列（审计准确性）
            cols = {r[1] for r in conn.execute("PRAGMA table_info(batch_audit)").fetchall()}
            if "rows_new" not in cols:
                conn.execute("ALTER TABLE batch_audit ADD COLUMN rows_new INTEGER DEFAULT 0")
            if "rows_updated" not in cols:
                conn.execute("ALTER TABLE batch_audit ADD COLUMN rows_updated INTEGER DEFAULT 0")
            if "rows_fixed" not in cols:
                conn.execute("ALTER TABLE batch_audit ADD COLUMN rows_fixed INTEGER")
            conn.commit()

    def record(self, batch_id: str, task_name: str, source: str, table: str, freq: str,
               rows_raw: int, rows_aligned: int, rows_passed: int, rows_rejected: int,
               rows_written: int, status: str, error: Optional[str] = None,
               started_at: str = None, finished_at: str = None,
               rows_new: int = 0, rows_updated: int = 0,
               rows_fixed: Optional[int] = None):
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO batch_audit "
                "(batch_id, task_name, source, table_name, freq, "
                " rows_raw, rows_aligned, rows_passed, rows_rejected, "
                " rows_written, rows_new, rows_updated, rows_fixed, status, error, started_at, finished_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [batch_id, task_name, source, table, freq, rows_raw, rows_aligned,
                 rows_passed, rows_rejected, rows_written, rows_new, rows_updated,
                 rows_fixed, status, error,
                 started_at or datetime.now().isoformat(),
                 finished_at or datetime.now().isoformat()])
            conn.commit()

    def recent(self, limit: int = 20) -> pd.DataFrame:
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                f"SELECT * FROM batch_audit ORDER BY finished_at DESC LIMIT {limit}", conn)


class ResidentCollector:
    """常驻采集进程主类

    使用：
        collector = ResidentCollector.from_configs(
            ROOT/"config/data_config.json",
            ROOT/"config/sources_config.json",
            ROOT/"config/collector_tasks.json",
            ROOT/"config/alignment_rules.json")
        collector.run_forever()
    """

    # —— QFQ 常驻编排器字段（类级缺省，兼容绕过 __init__ 的构造路径，
    # 如测试中 ResidentCollector.__new__() 手动设属性；缺省 None →
    # _qfq_config() 返回 enabled=False 安全默认 → 旧路径 writer.advance_watermark，
    # 行为逐位不变（紧急回退开关）。真实 __init__ 仍会显式赋同样的值。 ——
    _qfq_cfg_obj = None      # QFQOrchestratorConfig（惰性加载）
    _qfq_orch = None         # QFQResidentOrchestrator（惰性构造）
    _qfq_cycle_id = None     # 当前活跃协调周期

    def __init__(self, data_cfg: Dict, sources_cfg: Dict, tasks_cfg: Dict,
                 aligner: FieldAligner, validator: PreIngestValidator,
                 writer, quarantine: Quarantine, batch_audit: BatchAudit):
        self.data_cfg = data_cfg
        self.sources_cfg = sources_cfg
        self.tasks_cfg = tasks_cfg
        self.aligner = aligner
        self.validator = validator
        self.writer = writer
        self.quarantine = quarantine
        self.batch_audit = batch_audit

        self._running = True
        self._adapters: Dict[str, object] = {}  # source → adapter 实例（复用连接）

        # —— QFQ 常驻编排器（resident orchestrator v2）——
        # enabled=false（默认）时以下三个字段全程保持 None/未用，
        # 水位推进走旧路径 writer.advance_watermark，行为逐位不变（紧急回退开关）。
        self._qfq_cfg_obj = None      # QFQOrchestratorConfig（惰性加载）
        self._qfq_orch = None         # QFQResidentOrchestrator（惰性构造）
        self._qfq_cycle_id: Optional[str] = None  # 当前活跃协调周期

    def close(self):
        """v3：释放所有持连接子组件（主要是 writer._shared_conn）。

        daemon 每轮采集后、GUI 手动操作后都必须调用，确保空闲期不持有 DuckDB
        读写连接，避免跨进程读库冲突。writer.close() 已存在（writers.py），
        对其它组件做防御性 try/except（aligner/quarantine/validator 当前不持有
        长连接，但接口预留）。
        """
        for comp_attr in ("writer", "aligner", "quarantine", "validator"):
            comp = getattr(self, comp_attr, None)
            if comp is not None and hasattr(comp, "close"):
                try:
                    comp.close()
                except Exception as e:
                    logger.debug(f"[ResidentCollector.close] {comp_attr}.close() 异常: {e}")
        # 关闭已缓存的 adapter 连接（如 xtquant/baostock 的 session）
        for adapter in self._adapters.values():
            if hasattr(adapter, "close"):
                try:
                    adapter.close()
                except Exception:
                    pass
        self._adapters.clear()

    @classmethod
    def from_configs(cls, data_cfg_path, sources_cfg_path, tasks_cfg_path, align_rules_path):
        data_cfg = _load_json(data_cfg_path)
        sources_cfg = _load_json(sources_cfg_path)
        tasks_cfg = _load_json(tasks_cfg_path)

        aligner = FieldAligner.from_config(align_rules_path)
        # 隔离区防膨胀配置（从 data_config 读取，默认收紧：7天归档/10万上限/1000速率告警）
        q_cfg = data_cfg.get("quarantine", {})
        quarantine = Quarantine(
            q_cfg.get("path", str(quarantine_db_path())),
            max_rows=q_cfg.get("max_rows", 100_000),
            retention_days=q_cfg.get("retention_days", 7),
            rate_alert_threshold=q_cfg.get("rate_alert_threshold", 1000))
        validator = PreIngestValidator.from_config(align_rules_path, quarantine)
        writer = create_writer(data_cfg)
        # 将 Canonical 库路径注入 aligner，支撑 pctChg 垃圾值 DB 兜底推导（前一日 close_front）
        aligner.db_path = getattr(writer, "db_path", None)
        # 注入 writer 的持久连接 provider，避免 aligner DB 兜底开 read_only 与 write 并发冲突
        aligner.shared_conn_provider = getattr(writer, "shared_conn", None)
        batch_audit = BatchAudit(data_cfg.get("batch_audit_path",
                                               str(db_path("batch_audit.db"))))
        # 启动时配置校验（fail-fast：错误配置阻止启动，防调试残留/缺 PIT 门禁等）
        from .config_lint import assert_configs_ok
        # config_lint 只需 schemas 部分（FieldAligner 已加载并暴露）
        pseudo_align_rules = {"schemas": aligner.schemas}
        assert_configs_ok(data_cfg, sources_cfg, tasks_cfg, pseudo_align_rules)
        return cls(data_cfg, sources_cfg, tasks_cfg, aligner, validator,
                   writer, quarantine, batch_audit)

    # ---------------- QFQ 协调周期（resident orchestrator v2）----------------
    def _qfq_config(self):
        """惰性加载 qfq_orchestrator 配置块（缺失/未启用 → enabled=False 安全默认）。

        from_configs 启动时 config_lint 已 fail-fast 校验过该块，这里不会因
        非法配置在任务中途才抛错。
        """
        if self._qfq_cfg_obj is None:
            from .qfq_orchestrator_types import QFQOrchestratorConfig
            block = self.tasks_cfg.get("qfq_orchestrator", {}) or {}
            self._qfq_cfg_obj = QFQOrchestratorConfig.from_dict(dict(block))
        return self._qfq_cfg_obj

    def qfq_enabled(self) -> bool:
        return bool(self._qfq_config().enabled)

    def _qfq_orchestrator(self):
        """惰性构造编排器（xtquant fetcher 惰性连接；calendar 走主库交易日历）。"""
        if self._qfq_orch is None:
            from .qfq_resident_orchestrator import QFQResidentOrchestrator
            from .qfq_fresh_capture import XtquantFreshFetcher, McpFreshFetcher
            from .qfq_calendar import CalendarService
            cfg = self._qfq_config()
            # P2-4：fresh fetcher 按 price_source 决定（mcp 作传输通道，上游权威 xtquant）
            if cfg.price_source == "mcp":
                mcp_cfg = (self.sources_cfg.get("sources", {}).get("mcp")
                           or self.sources_cfg.get("mcp", {}))
                fetcher = McpFreshFetcher(mcp_cfg=mcp_cfg)   # 惰性：首次取数才建 MCP 连接
                logger.info("[qfq] 编排器使用 McpFreshFetcher（price_source=mcp）")
            else:
                fetcher = XtquantFreshFetcher()   # 惰性：首次取数才 import/连 xtquant
            self._qfq_orch = QFQResidentOrchestrator(
                cfg, main_db=str(self.writer.db_path),
                fetcher=fetcher,
                calendar=CalendarService(main_db=self.writer.db_path),
                watermark_advancer=self.writer.advance_watermark)
        return self._qfq_orch

    def qfq_begin_cycle(self) -> Optional[str]:
        """开启 QFQ 协调周期（daemon 每轮增量任务开始前调用）。

        enabled=false → 返回 None（旧路径，逐位不变）。
        enabled=true  → 幂等建 schema + supersede 崩溃残留 intent（restart 语义）
                        + 建 qfq_cycle_run，返回 cycle_id；此后四价格表水位
                        一律 defer，直到 qfq_run_post_ingest 的 gate 决定提交/保持。
        """
        if not self.qfq_enabled():
            return None
        orch = self._qfq_orchestrator()
        conn = self.writer.shared_conn()
        aux_conn = None
        try:
            if orch.aux_db:
                import sqlite3 as _sqlite3
                aux_conn = _sqlite3.connect(str(orch.aux_db))
            orch.init_schema(conn, aux_conn)
            if aux_conn is not None:
                aux_conn.commit()
        finally:
            if aux_conn is not None:
                aux_conn.close()
        self._qfq_cycle_id = orch.begin_cycle(conn)
        logger.info(f"[qfq] 协调周期开启 cycle_id={self._qfq_cycle_id}"
                    f"（四价格表水位延迟到周期结束统一提交）")
        return self._qfq_cycle_id

    def qfq_run_post_ingest(self, run_id: str):
        """增量任务全部结束后执行 post-ingest 闭环（recover→discover→claim→
        reanchor→gate→commit/hold watermarks）。disabled 或未开周期 → no-op None。"""
        if not self.qfq_enabled() or self._qfq_cycle_id is None:
            return None
        import time as _time
        orch = self._qfq_orchestrator()
        # —— 任务2.2：因子刷新（主动拉股票/ETF 因子），决定本轮检测器是否可信 ——
        detector_degraded = False
        if self._qfq_config().factor_refresh_enabled:
            detector_degraded = self._qfq_refresh_factors(orch)
        conn = self.writer.shared_conn()
        try:
            summary = orch.run_post_ingest(
                conn, cycle_id=self._qfq_cycle_id, run_id=run_id,
                as_of_ms=int(_time.time() * 1000),
                detector_degraded=detector_degraded)
        finally:
            self._qfq_cycle_id = None
        return summary

    def _qfq_refresh_factors(self, orch) -> bool:
        """任务2.2：调用 QFQFactorRefresher 主动刷股票/ETF 因子。

        返回 True 表示本轮 detector 不可信（刷新失败/异常）→ 调用方须 hold 水位。
        任何异常一律 fail-safe 降级为 degraded=True，绝不抛到上层破坏 post-ingest。
        """
        try:
            from .qfq_factor_refresh import QFQFactorRefresher
            from .qfq_maintenance import get_stock_universe, get_etf_universe
            refresher = QFQFactorRefresher(aux_db=orch.aux_db)
            adapter = self._get_adapter("tushare", None)
            main_db = str(self.writer.db_path)
            stock_universe = get_stock_universe(main_db)
            etf_universe = get_etf_universe(main_db)
            cfg = self._qfq_config()
            overlap_days = int(getattr(cfg, "factor_overlap_lookback_days", 5))
            lookback_days = 365
            rate_limiter = getattr(adapter, "rate_limiter", None)
            res = refresher.refresh(
                adapter, stock_universe, etf_universe,
                overlap_days=overlap_days, lookback_days=lookback_days,
                rate_limiter=rate_limiter)
            if res.degraded:
                logger.warning(
                    f"[qfq] 因子刷新 degraded（stock_failed={res.stock_failed}, "
                    f"etf_failed={res.etf_failed}, err={res.error}）→ 水位 hold")
                return True
            logger.info(
                f"[qfq] 因子刷新完成 stock={res.stock_refreshed}/"
                f"etf={res.etf_refreshed}（degraded=False）")
            return False
        except Exception as e:  # pragma: no cover - fail-safe 降级
            logger.warning(
                f"[qfq] 因子刷新异常 → degraded=True（水位 hold）: {e}", exc_info=True)
            return True

    def _advance_or_defer_watermark(self, source: str, table: str, freq: str,
                                    new_watermark, batch_id: str) -> None:
        """水位推进唯一入口（红线）。

        - 编排器 enabled 且 table ∈ 四价格表 且协调周期已开 → defer_watermark
          （写 qfq_watermark_intent，gate 通过才统一提交）；
        - enabled 但周期未开（如 GUI 手动跑任务）→ fail-closed **保持水位不动**
          （绝不提前推进；旧水位下轮幂等重拉，安全）；
        - disabled / 非价格表 → 旧路径 writer.advance_watermark，逐位不变。
        """
        cfg = self._qfq_config()
        if cfg.can_coordinate_watermark(table):
            if self._qfq_cycle_id is not None:
                orch = self._qfq_orchestrator()
                orch.defer_watermark(
                    self.writer.shared_conn(), cycle_id=self._qfq_cycle_id,
                    source=source, table=table, freq=freq,
                    candidate_watermark=new_watermark)
                logger.info(f"[qfq] {source}/{table}/{freq} 水位延迟提交 "
                            f"candidate={new_watermark} cycle={self._qfq_cycle_id}")
            else:
                logger.warning(
                    f"[qfq] {source}/{table}/{freq} 水位保持不动：qfq_orchestrator "
                    f"enabled 但无活跃协调周期（candidate={new_watermark} 丢弃，"
                    f"下轮 daemon 周期从旧水位幂等重拉后统一提交）")
            return
        self.writer.advance_watermark(source, table, freq, new_watermark, batch_id)

    # ---------------- 单任务执行（核心流水线，硬编码顺序不可绕过）----------------
    def _execute_task(self, task: Dict) -> bool:
        """回退调度器：按 source_priority 依次尝试候选源，首个成功（含空数据）即止。

        这是「xtquant 未来可能被取消」的弹性入口：取消/替换某权威源只需在
        sources_config.json 置 enabled=false，或把它从 task 的 source_priority 移除，
        无需改动管线逻辑（拉取→对齐→校验→入库保持不变）。
        回退链优先级：task.source_priority > task.source > 全局 default_source_priority，
        每个候选再经「已启用 + supports_task(table,freq)」过滤。
        """
        name = task["name"]
        table = task.get("table")
        freq = task.get("freq", "daily")
        chain = self._resolve_source_chain(task)
        if not chain:
            logger.error(f"[task={name}] 无可用数据源（{table}/{freq}）："
                         f"source_priority/source 中无已启用且支持该表+freq 的源")
            return False
        last_err = None
        for idx, source in enumerate(chain):
            batch_id = f"{name}_{source}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
            started_at = datetime.now().isoformat()
            try:
                ok = self._run_with_source(task, source, batch_id, started_at)
                if ok:
                    if source != chain[0]:
                        logger.warning(f"[task={name}] ⚠️ 权威源回退生效：首选 {chain[0]} 不可用，"
                                       f"改用 {source}")
                    return True
                logger.warning(f"[task={name}] 源={source} 执行返回失败，尝试下一个候选 {chain[idx+1:]}")
            except Exception as e:
                last_err = e
                logger.warning(f"[task={name}] 源={source} 执行异常: {type(e).__name__}: {e}，"
                               f"尝试下一个候选")
        logger.error(f"[task={name}] 所有候选源均失败: {chain}；last_err={last_err}")
        return False

    def execute_task(self, task: Dict, mode: Optional[str] = None,
                     run_quality_audit: bool = True) -> bool:
        """Public task entry shared by GUI, CLI and resident scheduling.

        The task is copied before the run so mode overrides cannot mutate the
        persisted/shared configuration. Every mode uses the same mandatory
        fetch -> align -> validate -> write pipeline.
        """
        if mode not in (None, "full_range", "incremental"):
            raise ValueError(f"unsupported collection mode: {mode!r}")
        run_task = dict(task)
        if mode is not None:
            run_task["mode"] = mode
        task_ok = False
        audit_ok = True
        try:
            task_ok = self._execute_task(run_task)
        finally:
            if run_quality_audit:
                audit_ok = self._run_full_quality_audit()
        return task_ok and audit_ok

    def execute_task_with_quality(self, task: Dict) -> bool:
        """Backward-compatible alias for the public task entry."""
        return self.execute_task(task, mode=task.get("mode"), run_quality_audit=True)

    def _run_with_source(self, task: Dict, source: str,
                         batch_id: str, started_at: str) -> bool:
        """执行单个源的数据采集流水线（拉取→对齐→校验→入库→水位推进）。

        由 _execute_task 回退调度器调用；source 为本次实际采用的源。
        """
        name = task["name"]
        table = task["table"]
        freq = task.get("freq", "daily")
        codes_cfg = task.get("codes")

        # P2-4 权威源守卫（复权一致性，2026-07-21 用户批准，扩展支持 MCP 传输通道）：
        # 区分 transport_source（传输通道：xtquant/tushare/mcp）与 upstream_authority
        # （上游数据权威：xtquant）。MCP 仅作传输通道，其上游权威声明在 sources_config
        # （mcp.upstream_authority，默认 xtquant）。仅当声明的上游权威命中权威集才放行，
        # 绝不简单用 source in ("xtquant","mcp") 而不核查上游权威 lineage。
        # 分钟表权威上游=xtquant（三段式复权基准敏感）；日线权威上游=xtquant/tushare。
        # 显式能力错误（缺数据停更）优于混源静默污染（不可检测的错误答案）。
        # 若需历史回填，是一次性显式运维 + 重建全表复权列，绝不作日常 fallback。
        def _declared_upstream(src: str) -> str:
            if src != "mcp":
                return src  # xtquant/tushare 自身即上游权威
            sc = self.sources_cfg.get("sources", {}).get("mcp") or self.sources_cfg.get("mcp", {})
            return sc.get("upstream_authority", "xtquant")

        MINUTE_UPSTREAM = {"xtquant"}
        DAILY_UPSTREAM = {"xtquant", "tushare"}
        if table in ("stock_minutes", "etf_minutes"):
            up = _declared_upstream(source)
            if up not in MINUTE_UPSTREAM:
                logger.error(
                    f"[task={name}] 分钟表 {table} 上游权威必须=xtquant"
                    f"（transport={source}，declared_upstream={up}，复权一致性决策 2026-07-21），"
                    f"拒绝写入以避免跨源复权基准漂移。若需历史回填请显式运维并重建全表复权列。")
                return False
        elif table in ("stock_daily", "etf_daily"):
            up = _declared_upstream(source)
            if up not in DAILY_UPSTREAM:
                logger.error(
                    f"[task={name}] 日线表 {table} 上游权威必须∈{{xtquant,tushare}}"
                    f"（transport={source}，declared_upstream={up}，复权一致性决策 2026-07-21），"
                    f"拒绝写入以避免跨源复权基准台阶。若需历史回填请显式运维并重建全表复权列。")
                return False

        # 通用权威源守卫（task 级 authoritative_source 声明，覆盖所有表类型）
        authoritative = task.get("authoritative_source")
        if authoritative and source != authoritative:
            allow_fb = task.get("allow_fallback", True)
            if not allow_fb:
                # allow_fallback=false：严格锁定，拒绝任何非权威源写入
                logger.error(
                    f"[task={name}] 任务权威源锁定为 {authoritative}"
                    f"（allow_fallback=false），拒绝用 {source} 写入。")
                return False
            else:
                # allow_fallback=true：允许回退源，但记录 warning
                logger.warning(
                    f"[task={name}] 当前源 {source} 非权威源 {authoritative}"
                    f"（allow_fallback=true，作为回退写入）。")

        logger.info(f"[{batch_id}] === START task={name} source={source} table={table}/{freq} ===")

        # 全市场（ALL）→ 逐只股票并行拉取+入库
        # baostock/akshare/xtquant: 按股票遍历（每只一次API）
        # tushare 日线 + 流通股本: 按交易日遍历（每天一次全市场），效率高 10 倍
        # ETF 日线（fund_daily）: 按 ts_code 遍历（trade_date 全市场模式有权限限制），走 per_stock
        is_all_market = codes_cfg == ["ALL"] or codes_cfg == "ALL" or codes_cfg is None
        if is_all_market and source == "tushare" and table in ("stock_daily", "stock_float_share", "stock_daily_valuation"):
            return self._execute_task_per_trade_date(task, batch_id, started_at, source)
        if is_all_market and table in ("stock_daily", "stock_minutes", "index_daily", "etf_daily", "etf_minutes", "fin_indicator", "stock_daily_valuation", "stock_dividend"):
            return self._execute_task_per_stock(task, batch_id, started_at, source)

        # 普通模式（指定 codes 或非全市场）
        rows_raw = rows_aligned = rows_passed = rows_rejected = rows_written = 0
        try:
            # 1. 确定拉取时间范围
            mode = task.get("mode", "incremental")
            last = self._get_safe_watermark(source, table, freq)
            if mode == "full_range":
                # 全量自定义范围：用 task 配置的 start_date / end_date
                start = task.get("start_date", "2018-01-01")
                end = task.get("end_date") or datetime.now().strftime("%Y-%m-%d")
                logger.info(f"[{batch_id}] FULL_RANGE: {start} → {end} (忽略水位线 {last})")
            else:
                # 增量（默认）：从水位线+1天开始
                start = self._bump_date(last) or task.get("start_date", "2020-01-01")
                end = datetime.now().strftime("%Y-%m-%d")
                logger.info(f"[{batch_id}] INCREMENTAL: {start} → {end} (last_watermark={last})")

            if self._date_range_empty(start, end):
                logger.info(f"[{batch_id}] 水位已追平，无需拉取: {start} > {end}")
                self.batch_audit.record(batch_id, name, source, table, freq,
                                        0, 0, 0, 0, 0, "empty", None, started_at)
                return True

            # 2. 拉取
            adapter = self._get_adapter(source, task)
            # stock_daily 任务：自动先拉依赖表 stock_daily_valuation
            # 范围跟随 stock_daily：start 前推 30 自然日（≈20交易日），满足回测/采集期
            # 近20日 circ_mv 回看，避免 stock_daily 最早一段 is_delisting_risk 退化为 close<1 兜底
            if table == "stock_daily":
                sdv_task = next((t for t in self.tasks_cfg.get("tasks", [])
                                 if t.get("table") == "stock_daily_valuation"), None)
                if sdv_task and sdv_task.get("enabled", True):
                    try:
                        import datetime as _dt
                        _sdv_start = (_dt.datetime.strptime(start, "%Y-%m-%d") - _dt.timedelta(days=30)).strftime("%Y-%m-%d")
                        _sdv_run = dict(sdv_task)   # 浅拷贝：不污染 tasks_cfg 原对象/磁盘配置
                        _sdv_run["mode"] = "full_range"
                        _sdv_run["start_date"] = _sdv_start
                        _sdv_run["end_date"] = end
                        logger.info(f"[{batch_id}] 自动触发依赖表 stock_daily_valuation（跟随范围 {_sdv_start}→{end}）...")
                        self._execute_task(_sdv_run)
                    except Exception as e:
                        logger.warning(f"[{batch_id}] 依赖表拉取失败: {e}")

            # stock_daily 任务：自动先拉依赖表 stock_namechange（ST 更名历史，hidden 任务）。
            # 适配 stock_daily 所有数据源（tushare/akshare/baostock/xtquant），
            # 只需首次触发全量拉取（7445 行深市简称变更），后续复用已入库数据。
            # is_st_reliable PIT 依赖 namechange，缺失时留 False 不阻断主流程。
            if table == "stock_daily":
                nc_task = next((t for t in self.tasks_cfg.get("tasks", [])
                                 if t.get("table") == "stock_namechange"), None)
                if nc_task and nc_task.get("enabled", True):
                    try:
                        _nc_cnt = self.writer.execute_read("SELECT COUNT(*) FROM stock_namechange")[0][0]
                        if _nc_cnt == 0:
                            logger.info(f"[{batch_id}] 自动触发依赖表 stock_namechange（首次全量拉取）...")
                            _nc_run = dict(nc_task)
                            _nc_run["mode"] = "full_range"
                            self._execute_task(_nc_run)
                    except Exception as e:
                        logger.warning(f"[{batch_id}] 依赖表 stock_namechange 拉取失败（is_st_reliable 留 False）: {e}")

            codes = task.get("codes")
            adapter.configure_execution(task)
            raw_df, metadata = adapter.fetch_table(table, start, end, freq=freq, codes=codes)
            rows_raw = len(raw_df)
            if rows_raw == 0:
                logger.info(f"[{batch_id}] no new data, skip")
                self.batch_audit.record(batch_id, name, source, table, freq,
                                        0, 0, 0, 0, 0, "empty", None, started_at)
                return True

            # 3. 对齐（硬编码，不可跳过）
            # 日线/分钟任务：自动拉取 adj_factor 传给 aligner 计算 8 个复权字段
            # baostock 路径：adapter 已提供 front/back（3 次 adjustflag），不需 adj_factor
            # tushare 路径：用 adj_factor 计算 front/back；ETF 用 fund_adj
            adj_factor_df = None
            if table in ("stock_daily", "stock_minutes", "etf_daily") and source == "tushare":
                # 从已拉取的 raw_df 提取 codes（兼容全市场拉取无 task.codes 的情况）
                raw_codes = codes
                if not raw_codes and len(raw_df) > 0:
                    code_col = next((c for c in ["ts_code", "code", "stock_code", "股票代码"]
                                     if c in raw_df.columns), None)
                    if code_col:
                        raw_codes = raw_df[code_col].unique().tolist()
                is_etf = (table in ("etf_daily", "etf_minutes"))
                adj_factor_df = self._fetch_adj_factor(adapter, raw_codes, start, end, is_etf=is_etf)
            elif (source == "mcp" and table in ("stock_daily", "stock_minutes", "etf_daily")
                  and "adj_factor" in raw_df.columns):
                # P2-4 §7.2-B 补齐：普通模式（指定 codes）与 per_stock 路径对称。
                # raw_df 已含 adj_factor 列（原样返回），直接提取并标准化为 aligner 期望格式，
                # 走 aligner tushare 计算路径（front=raw×adj_i/adj_latest, back=raw×adj_i/adj_earliest）。
                from .sources.mcp_adapter import normalize_mcp_adj_factor_df
                asset_type = "ETF" if table.startswith("etf") else "STOCK"
                adj_factor_df = normalize_mcp_adj_factor_df(raw_df, freq, asset_type)
                # 严格对齐 aligner._apply_qfq 期望的列名（code / time / adj_factor），
                # 避免 merge 时列名不匹配导致 KeyError（P3-2 第8个 bug 防御）。
                tk = self.aligner.schemas[table].get("time_key", "time")
                rename_map = {}
                if "time" in adj_factor_df.columns and tk != "time":
                    rename_map["time"] = tk
                if "code" not in adj_factor_df.columns and adj_factor_df.columns[0] != "code":
                    rename_map[adj_factor_df.columns[0]] = "code"
                if rename_map:
                    adj_factor_df = adj_factor_df.rename(columns=rename_map)
                if len(adj_factor_df) == 0:
                    logger.warning(f"[{batch_id}] MCP adj_factor 标准化后为空（表={table}），"
                                   f"复权字段将留 NULL")
                # P3-2 第8个 bug 根因修复：raw_df 仍含原始 adj_factor 列（MCP 原样返回），
                # 而 aligner._apply_qfq 的 merge(right=adj_factor_df 也含 adj_factor) 会产生
                # adj_factor_x/adj_factor_y 列名冲突 → merged["adj_factor"] KeyError。
                # daemon 已单独提取 adj_factor_df（不走 aligner 字段映射），此处从 raw_df 显式
                # drop 原始 adj_factor 列，确保 df(left) 不含该列，merge 不冲突。
                # 注：aligner._map_columns 用 rename（不删列），故仅删 column_map 条目不足以去列，
                # 必须在此处 drop。
                if "adj_factor" in raw_df.columns:
                    raw_df = raw_df.drop(columns=["adj_factor"]).reset_index(drop=True)
            # Step 5：stock_float_share 补算 circ_mv 需要收盘价（从 stock_daily 查）
            close_df = None
            if table == "stock_float_share":
                # 提取本次拉取涉及的 codes：
                #   - tushare 路径已赋值 raw_codes（具体 ts_code 列表）
                #   - 非 tushare 路径或 codes_cfg=['ALL']/None → 从 raw_df 提取实际 code
                # 关键：codes=['ALL'] / None / 空 都不能直接用，必须 fallback 到 raw_df
                fs_codes = None
                try:
                    fs_codes = raw_codes  # tushare 路径已赋值（具体 codes）
                except NameError:
                    fs_codes = codes  # task.codes，可能是 ['ALL']/None/具体列表
                # codes_cfg 是 ['ALL'] / None / 空 → 视为无具体 codes，必须从 raw_df 提取
                fs_codes_is_placeholder = (
                    not fs_codes
                    or (isinstance(fs_codes, list) and len(fs_codes) == 1
                        and str(fs_codes[0]).upper() in ("ALL", "NONE"))
                )
                if (fs_codes_is_placeholder or not fs_codes) and len(raw_df) > 0:
                    code_col = next((c for c in ["code", "stock_code", "ts_code", "股票代码"]
                                     if c in raw_df.columns), None)
                    if code_col:
                        fs_codes = raw_df[code_col].unique().tolist()
                close_df = self._prepare_close_df(fs_codes, start, end)
            # stock_daily 任务额外准备：namechange（从已入库读取）+ valuation（delisting_risk 兜底）
            # namechange 已由上方 auto-trigger 保证入库，此处仅从 DB 读（零网络开销）
            namechange_df = None
            valuation_df = None
            if table == "stock_daily":
                try:
                    namechange_df = self._prepare_namechange_df()
                except Exception as e:
                    logger.warning(f"[{batch_id}] namechange 准备失败: {e}")
                try:
                    valuation_df = self._prepare_valuation_df(start, end)
                except Exception as e:
                    logger.warning(f"[{batch_id}] valuation 准备失败: {e}")
            etf_daily_bounds_df = None
            if table == "etf_basic":
                etf_daily_bounds_df = self._prepare_etf_daily_bounds()
            std_df, align_meta = self.aligner.align(raw_df, table, source,
                                                     adj_factor_df=adj_factor_df,
                                                     close_df=close_df,
                                                     namechange_df=namechange_df,
                                                     valuation_df=valuation_df,
                                                     etf_daily_bounds_df=etf_daily_bounds_df,
                                                     freq=freq)
            rows_aligned = len(std_df)

            # 4. 校验（硬编码 + 失败进 Quarantine [E-2]）
            res = self.validator.validate(std_df, table, batch_id, source, expected_freq=freq)
            rows_passed = len(res.passed_df)
            rows_rejected = len(res.rejected_rows)
            accepted, reject_rate, threshold = self._failure_gate(task, rows_rejected, rows_raw)
            if not accepted or (rows_raw > 0 and rows_passed == 0):
                raise RuntimeError(
                    f"校验拒绝率超限或无可入库数据: rejected={rows_rejected}/{rows_raw} "
                    f"rate={reject_rate:.6%}, threshold={threshold:.6%}")

            # 5. 入库（幂等）
            write_new = write_updated = 0
            if rows_passed > 0:
                wr = self._stamp_and_write(res, table, batch_id, source, task=task)
                rows_written = wr
                write_new = getattr(wr, "new", 0)
                write_updated = getattr(wr, "updated", 0)

            # 6. 水位推进（仅成功）
            if task.get("dataset_kind") == "snapshot":
                new_watermark = self._snapshot_watermark(end)
            else:
                new_watermark = self._max_date(res.passed_df, table)
            if new_watermark:
                # 红线：水位推进唯一入口（qfq enabled 时四价格表延迟提交）
                self._advance_or_defer_watermark(source, table, freq, new_watermark, batch_id)

            logger.info(f"[{batch_id}] ✅ raw={rows_raw} aligned={rows_aligned} "
                        f"passed={rows_passed} rejected={rows_rejected} written={rows_written} "
                        f"(new {write_new} + upd {write_updated}) "
                        f"watermark→{new_watermark}")
            self.batch_audit.record(batch_id, name, source, table, freq,
                                    rows_raw, rows_aligned, rows_passed, rows_rejected,
                                    rows_written, "success", None, started_at,
                                    rows_new=write_new, rows_updated=write_updated, rows_fixed=res.fixed_count)
            return True

        except Exception as e:
            logger.error(f"[{batch_id}] ❌ FAILED: {e}", exc_info=True)
            self.batch_audit.record(batch_id, name, source, table, freq,
                                    rows_raw, rows_aligned, rows_passed, rows_rejected,
                                    rows_written, "failed", str(e), started_at)
            return False  # 不推进水位，下周期重试

    def _execute_task_per_trade_date(self, task: Dict, batch_id: str, started_at: str,
                                  source: str = "tushare") -> bool:
        """tushare 日线专用：按交易日遍历（每天一次全市场 API），比逐只快 10 倍。
        tushare daily(trade_date=XXX) 一次返回全市场 5000+ 只当日数据。
        source 由回退调度器传入（通常为 tushare，但支持回退到其他源）。"""
        import time as _time
        import tushare as ts
        name = task["name"]
        table = task["table"]
        freq = "daily"
        mode = task.get("mode", "incremental")
        max_workers = task.get("max_workers", 4)

        last = self._get_safe_watermark(source, table, freq)
        if mode == "full_range":
            start = task.get("start_date", "2018-01-01")
            end = task.get("end_date") or datetime.now().strftime("%Y-%m-%d")
        else:
            start = self._bump_date(last) or task.get("start_date", "2018-01-01")
            end = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"[{batch_id}] PER_DATE(tushare全市场按天): {start} → {end} (last={last})")
        if self._date_range_empty(start, end):
            logger.info(f"[{batch_id}] 水位已追平，无需拉取: {start} > {end}")
            self.batch_audit.record(batch_id, name, source, table, freq,
                                    0, 0, 0, 0, 0, "empty", None, started_at)
            return True

        adapter = self._get_adapter(source, task)
        token = adapter.token

        # Trade calendar also uses the task-level limiter/retry policy.
        pro = ts.pro_api(token, timeout=30)
        try:
            cal = adapter._retry_with_backoff(
                pro.trade_cal, exchange="SSE",
                start_date=start.replace("-", ""), end_date=end.replace("-", ""))
            trade_days = cal[cal["is_open"] == 1]["cal_date"].tolist()
        except Exception as e:
            logger.error(f"[{batch_id}] trade calendar fetch failed: {e}")
            return False

        total = len(trade_days)
        logger.info(f"[{batch_id}] {start}~{end} 共 {total} 个交易日，{max_workers} 线程并行")

        # 静默子模块日志
        for mod_name in ["quantstudio.pipeline.aligner", "quantstudio.pipeline.validator",
                         "quantstudio.pipeline.writers"]:
            logging.getLogger(mod_name).setLevel(logging.WARNING)

        total_raw = [0]
        total_passed = [0]
        total_rejected = [0]
        total_fixed = [0]
        total_written = [0]
        total_new = [0]      # 真实新增行数（审计准确性）
        total_updated = [0]  # upsert 更新行数
        fail_count = [0]
        done_count = [0]
        write_lock = __import__("threading").Lock()

        def record_validation(raw_count, res):
            with write_lock:
                total_raw[0] += int(raw_count)
                total_passed[0] += len(res.passed_df)
                total_rejected[0] += len(res.rejected_rows)
                total_fixed[0] += int(getattr(res, "fixed_count", 0))
        t0 = _time.time()


        # TD-1 修复：PER_DATE 路径所有 pro.XXX 调用必须走限流 + 重试
        # （原先裸调绕过 rate_limiter，4 线程并发易触发 429）
        def _api(api_fn, **kwargs):
            """限流 + 指数退避重试的 tushare 调用封装（替代裸 pro.XXX）"""
            return adapter._retry_with_backoff(api_fn, **kwargs)

        # === 预拉全量 adj_factor + 计算复权快照（方案A：修复 per_trade_date 复权失效）===
        # 全量模式：逐日拉取 start~end 的 adj_factor，计算 per code 的 (adj_latest, adj_earliest) 快照
        # 增量模式：从 qfq_aux.db 读上次全量保存的快照复用
        adj_latest_map = None
        adj_earliest_map = None
        if table == "stock_daily":
            from .qfq_maintenance import QFQMaintenance
            qfq = QFQMaintenance(self.db_path if hasattr(self, 'db_path') else self.writer.db_path)
            if mode == "full_range":
                # 优化：不再把 2067×全市场(约千万行) 全部拼进内存再 iterrows 落盘
                # （原实现会吃到 3GB+ 内存、且 concat+iterrows+executemany 全程无日志，易被误判为卡死）。
                # 改为「逐日归一化后立即增量写 SQLite」，最后用 SQL GROUP BY 直接从
                # SQLite 计算 adj_latest/adj_earliest 快照，内存峰值回到正常水位。
                from .aligner import normalize_code, to_ms_timestamp
                logger.info(f"[{batch_id}] 预拉全量 adj_factor（{len(trade_days)} 天，逐日增量写盘）...")
                _adj_t0 = _time.time()
                conn = sqlite3.connect(qfq.db_path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("DELETE FROM adj_factor")  # 全量重拉，先清旧
                _rows_total = 0
                for _i, day in enumerate(trade_days):
                    if _i % 200 == 0 and _i > 0:
                        _elapsed = _time.time() - _adj_t0
                        _eta = _elapsed / _i * (len(trade_days) - _i)
                        logger.info(f"[{batch_id}] adj_factor 预拉进度: {_i}/{len(trade_days)} "
                                    f"({_i*100//len(trade_days)}%) 已用{_elapsed:.0f}s 剩余~{_eta:.0f}s")
                    try:
                        adj_raw = _api(pro.adj_factor, trade_date=day)
                    except Exception:
                        continue
                    if len(adj_raw) == 0:
                        continue
                    # 当天数据归一化 → 立即落盘（增量写，避免千万行大表驻留内存）
                    _recs = []
                    for _, r in adj_raw.iterrows():
                        _c = normalize_code(str(r["ts_code"]), "tushare_to_raw")
                        _ms = to_ms_timestamp(str(r["trade_date"]))
                        if _c and _ms:
                            _recs.append((_c, int(_ms), float(r["adj_factor"])))
                    if _recs:
                        conn.executemany("INSERT OR REPLACE INTO adj_factor VALUES (?,?,?)", _recs)
                        _rows_total += len(_recs)
                conn.commit()
                # 从 SQLite 直接算快照（GROUP BY），不依赖内存 DataFrame。
                # adj_latest = 每码最大时间对应的 adj_factor；adj_earliest = 历史 min（与原 save_snapshot 语义一致）。
                _snap = pd.read_sql_query(
                    "SELECT s.code, "
                    "(SELECT adj_factor FROM adj_factor x WHERE x.code = s.code "
                    " ORDER BY time DESC LIMIT 1) AS adj_latest, "
                    "s.adj_earliest AS adj_earliest "
                    "FROM (SELECT DISTINCT code, "
                    "(SELECT adj_factor FROM adj_factor e WHERE e.code = a.code ORDER BY time ASC LIMIT 1) "
                    "AS adj_earliest FROM adj_factor a) s",
                    conn)
                conn.execute("DELETE FROM adj_factor_snapshot")
                conn.executemany(
                    "INSERT OR REPLACE INTO adj_factor_snapshot VALUES (?,?,?,?)",
                    [(row["code"], float(row["adj_latest"]), float(row["adj_earliest"]), end)
                     for _, row in _snap.iterrows()])
                conn.commit()
                conn.close()
                adj_latest_map, adj_earliest_map = qfq.load_snapshot()
                logger.info(f"[{batch_id}] adj_factor 快照已构建: {len(adj_latest_map) if adj_latest_map else 0} 只股票（入库 {_rows_total} 行）")
            else:
                # 增量模式：读上次全量快照
                adj_latest_map, adj_earliest_map = qfq.load_snapshot()
                if adj_latest_map:
                    logger.info(f"[{batch_id}] 复用 adj_factor 快照: {len(adj_latest_map)} 只股票")
                else:
                    logger.warning(f"[{batch_id}] 无 adj_factor 快照（未跑过全量），复权可能不准确")

        # stock_daily 任务：自动先拉依赖表 stock_daily_valuation（hidden 任务，用户不可见）
        # 与 per_stock（xtquant）路径保持一致：tushare 全市场按天路径也需前置拉取 valuation，
        # 否则 stock_daily 的 is_delisting_risk 仅靠 close<1 兜底，与 xtquant 口径不一致（影响回测与 ptrade 对齐）。
        # 该表源固定为 akshare（免积分），与 stock_daily 权威源无关。
        # 范围跟随 stock_daily：start 前推 30 自然日（≈20交易日），满足回测/采集期近20日
        # circ_mv 回看，避免 stock_daily 最早一段 is_delisting_risk 退化为 close<1 兜底。
        if table == "stock_daily":
            sdv_task = next((t for t in self.tasks_cfg.get("tasks", [])
                             if t.get("table") == "stock_daily_valuation"), None)
            if sdv_task and sdv_task.get("enabled", True):
                try:
                    import datetime as _dt
                    _sdv_start = (_dt.datetime.strptime(start, "%Y-%m-%d") - _dt.timedelta(days=30)).strftime("%Y-%m-%d")
                    _sdv_run = dict(sdv_task)   # 浅拷贝：不污染 tasks_cfg 原对象/磁盘配置
                    _sdv_run["mode"] = "full_range"
                    _sdv_run["start_date"] = _sdv_start
                    _sdv_run["end_date"] = end
                    logger.info(f"[{batch_id}] 自动触发依赖表 {sdv_task.get('table')}（跟随范围 {_sdv_start}→{end}）...")
                    self._execute_task(_sdv_run)
                except Exception as e:
                    logger.warning(f"[{batch_id}] 依赖表 stock_daily_valuation 拉取失败（is_delisting_risk 仅靠 close<1）: {e}")

        # stock_daily 任务：自动先拉依赖表 stock_namechange（ST 更名历史，hidden 任务）。
        # 适配 stock_daily 所有数据源，只需首次触发全量拉取，后续复用已入库数据。
        if table == "stock_daily":
            nc_task = next((t for t in self.tasks_cfg.get("tasks", [])
                             if t.get("table") == "stock_namechange"), None)
            if nc_task and nc_task.get("enabled", True):
                try:
                    _nc_cnt = self.writer.execute_read("SELECT COUNT(*) FROM stock_namechange")[0][0]
                    if _nc_cnt == 0:
                        logger.info(f"[{batch_id}] 自动触发依赖表 stock_namechange（首次全量拉取）...")
                        _nc_run = dict(nc_task)
                        _nc_run["mode"] = "full_range"
                        self._execute_task(_nc_run)
                except Exception as e:
                    logger.warning(f"[{batch_id}] 依赖表 stock_namechange 拉取失败（is_st_reliable 留 False）: {e}")

        # stock_daily 任务额外准备：namechange（从已入库读取）+ valuation（delisting_risk 兜底）
        # namechange 已由上方 auto-trigger 保证入库，此处仅从 DB 读（零网络开销）
        namechange_df = None
        valuation_df = None
        if table == "stock_daily":
            # 1. namechange：从已入库 stock_namechange 读（auto-trigger 已保证入库）
            try:
                namechange_df = self._prepare_namechange_df()
            except Exception as e:
                logger.warning(f"[{batch_id}] namechange 准备失败（is_st_reliable 将留 False）: {e}")
                namechange_df = None
            # 2. valuation：从已入库 stock_daily_valuation 读（要求该表先入库）
            try:
                valuation_df = self._prepare_valuation_df(start, end)
            except Exception as e:
                logger.warning(f"[{batch_id}] valuation 准备失败（is_delisting_risk 仅靠 close<1 兜底）: {e}")
                valuation_df = None

        adapter.configure_execution(task)
        def process_one_day(trade_day):
            """单日全市场：拉取→对齐→校验→入库"""
            try:
                # stock_float_share 表：直接从 daily_basic 提取流通股本+市值
                if table == "stock_float_share":
                    basic_df = _api(pro.daily_basic, trade_date=trade_day,
                        fields="ts_code,trade_date,circ_mv,total_mv,free_share,total_share,turnover_rate")
                    if len(basic_df) == 0:
                        return 0
                    # 单位转换：万元→元, 万股→股
                    basic_df["circ_mv"] = basic_df["circ_mv"] * 10000
                    basic_df["total_mv"] = basic_df["total_mv"] * 10000
                    basic_df["free_share"] = basic_df["free_share"] * 10000
                    basic_df["total_share"] = basic_df["total_share"] * 10000
                    std_df, _ = self.aligner.align(basic_df, table, source, freq=freq)
                    res = self.validator.validate(std_df, table, batch_id, source, expected_freq=freq)
                    record_validation(len(std_df), res)
                    if len(res.passed_df) > 0:
                        with write_lock:
                            wr = self._stamp_and_write(res, table, batch_id, source)
                            total_new[0] += getattr(wr, "new", 0)
                            total_updated[0] += getattr(wr, "updated", 0)
                            return wr
                    return 0

                # stock_daily_valuation 表：从 daily_basic 提取每日估值+流通股本（circ_mv/free_share/pe/pb/turnover）
                if table == "stock_daily_valuation":
                    basic_df = _api(pro.daily_basic, trade_date=trade_day,
                        fields="ts_code,trade_date,circ_mv,total_mv,free_share,pe_ttm,pb,turnover_rate")
                    if len(basic_df) == 0:
                        return 0
                    # 单位转换：万元→元（circ_mv/total_mv），万股→股（free_share）
                    basic_df["circ_mv"] = basic_df["circ_mv"] * 10000
                    basic_df["total_mv"] = basic_df["total_mv"] * 10000
                    if "free_share" in basic_df.columns:
                        basic_df["free_share"] = basic_df["free_share"] * 10000
                    std_df, _ = self.aligner.align(basic_df, table, source, freq=freq)
                    res = self.validator.validate(std_df, table, batch_id, source, expected_freq=freq)
                    record_validation(len(std_df), res)
                    if len(res.passed_df) > 0:
                        with write_lock:
                            wr = self._stamp_and_write(res, table, batch_id, source)
                            total_new[0] += getattr(wr, "new", 0)
                            total_updated[0] += getattr(wr, "updated", 0)
                            return wr
                    return 0

                # stock_daily 表：daily + daily_basic merge + isST + adj_factor
                day_str = f"{trade_day[:4]}-{trade_day[4:6]}-{trade_day[6:8]}"
                raw_df = _api(pro.daily, trade_date=trade_day)
                if len(raw_df) == 0:
                    return 0
                try:
                    basic_df = _api(pro.daily_basic, trade_date=trade_day)
                    if len(basic_df) > 0:
                        keep = ["ts_code","trade_date","turnover_rate","pe_ttm","pb","ps_ttm"]
                        basic_df = basic_df[[c for c in keep if c in basic_df.columns]]
                        raw_df = raw_df.merge(basic_df, on=["ts_code","trade_date"], how="left")
                except Exception:
                    pass
                # 补充 isST 列（tushare daily/daily_basic 不含 isST，需从 ST 列表判断）
                # ST 列表缓存（回测期间不变，只拉一次）
                # TD-2 修复：st_codes 是裸码 {600000}，ts_code 是 600000.SH，需 split 后比较
                if "isST" not in raw_df.columns:
                    if not hasattr(self, '_st_codes_cache'):
                        try:
                            self._st_codes_cache = set(adapter.get_st_codes())
                            logger.info(f"ST 列表缓存: {len(self._st_codes_cache)} 只")
                        except Exception:
                            self._st_codes_cache = set()
                    st_codes = self._st_codes_cache
                    raw_df["isST"] = raw_df["ts_code"].apply(
                        lambda c: 1 if str(c).split(".")[0] in st_codes else 0)
                # 拉当日全市场 adj_factor
                adj_df = None
                try:
                    adj_raw = _api(pro.adj_factor, trade_date=trade_day)
                    if len(adj_raw) > 0:
                        from .aligner import normalize_code, to_ms_timestamp
                        adj_df = pd.DataFrame({
                            "code": [normalize_code(str(c), "tushare_to_raw") for c in adj_raw["ts_code"]],
                            "time": [to_ms_timestamp(str(d)) for d in adj_raw["trade_date"]],
                            "adj_factor": adj_raw["adj_factor"].astype(float),
                        })
                except Exception:
                    pass
                # 传入快照 map（如果有），让 aligner 用全量快照替代批次内 groupby
                # namechange_df + valuation_df 用于推导 is_st_reliable + is_delisting_risk
                std_df, _ = self.aligner.align(raw_df, table, source, adj_factor_df=adj_df,
                                               adj_latest_map=adj_latest_map, adj_earliest_map=adj_earliest_map,
                                               namechange_df=namechange_df, valuation_df=valuation_df,
                                               freq=freq)
                res = self.validator.validate(std_df, table, batch_id, source, expected_freq=freq)
                record_validation(len(std_df), res)
                if len(res.passed_df) > 0:
                    with write_lock:
                        wr = self._stamp_and_write(res, table, batch_id, source)
                        total_new[0] += getattr(wr, "new", 0)
                        total_updated[0] += getattr(wr, "updated", 0)
                        return wr
                return 0
            except Exception as e:
                fail_count[0] += 1
                if fail_count[0] <= 5:
                    logger.warning(f"[{batch_id}] {trade_day} 失败: {e}")
                return 0

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for i, day in enumerate(trade_days):
                if not self._running:
                    break
                futures[pool.submit(process_one_day, day)] = i

            for f in as_completed(futures):
                total_written[0] += f.result()
                done_count[0] += 1
                if done_count[0] % 20 == 0 or done_count[0] == total:
                    elapsed = _time.time() - t0
                    speed = done_count[0] / elapsed if elapsed > 0 else 0
                    eta = (total - done_count[0]) / speed if speed > 0 else 0
                    logger.info(f"[{batch_id}] 进度: {done_count[0]}/{total} ({done_count[0]*100//total}%) "
                                f"已入库 {total_written[0]} 行, 失败 {fail_count[0]}, "
                                f"已用 {elapsed:.0f}s, 剩余 {eta:.0f}s")

        fetch_ok, failure_rate, threshold = self._failure_gate(task, fail_count[0], done_count[0], source=source)
        reject_ok, reject_rate, _ = self._failure_gate(task, total_rejected[0], total_raw[0], source=source, is_reject=True)
        has_usable_result = total_written[0] > 0 or (mode == "incremental" and fail_count[0] == 0)
        task_ok = fetch_ok and reject_ok and has_usable_result

        # 允许失败率内视为本轮完成并推进到 end；超阈值不推进，下轮从旧水位重试。
        if task_ok:
            # W2-0.8 缺陷 B：full_range 成功后执行 authority reconciliation（清除既有
            # 非 authoritative 历史行 + watermark），使 authority-locked 表单源收敛。
            # 失败则降级为 batch failed，不推进水位。
            recon = self._authority_reconcile(task, source, table, batch_id)
            if recon["enabled"] and recon["ran"] and not recon["ok"]:
                task_ok = False
                logger.error(
                    f"[{batch_id}] authority_reconciliation failed: {recon['reason']}; "
                    f"batch 降级 failed，不推进水位")
        if task_ok:
            self._advance_actual_watermark(source, table, freq, batch_id)

        elapsed = _time.time() - t0
        level = logger.info if task_ok else logger.error
        level(f"[{batch_id}] {'✅' if task_ok else '❌'} PER_DATE 完成: {done_count[0]}天, "
              f"入库{total_written[0]}行 (new {total_new[0]} + upd {total_updated[0]}), "
              f"拉取失败{fail_count[0]}, 拉取失败率={failure_rate:.6%}, "
              f"拒绝{total_rejected[0]}行, 拒绝率={reject_rate:.6%} "
              f"(阈值={threshold:.6%}), 耗时{elapsed:.0f}s")
        audit_status = "success" if task_ok else "failed"
        error = None if task_ok else (
            f"failed={fail_count[0]}/{done_count[0]} rate={failure_rate:.8f} "
            f"rejected={total_rejected[0]}/{total_raw[0]} reject_rate={reject_rate:.8f} "
            f"threshold={threshold:.8f} written={total_written[0]}")
        self.batch_audit.record(batch_id, name, source, table, freq,
                                total_raw[0], total_raw[0], total_passed[0], total_rejected[0],
                                total_written[0], audit_status, error,
                                started_at, rows_new=total_new[0], rows_updated=total_updated[0], rows_fixed=total_fixed[0])
        return task_ok

    def _execute_task_per_stock(self, task: Dict, batch_id: str, started_at: str,
                                 source: str = "tushare") -> bool:
        """全市场逐只股票并行拉取+入库（多线程加速，线程安全写入）。
        每只股票独立：拉取→对齐→校验→入库。中断后已入库的不丢。
        source 由回退调度器传入。"""
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
        import threading
        name = task["name"]
        table = task["table"]
        freq = task.get("freq", "daily")
        mode = task.get("mode", "incremental")
        max_workers = task.get("max_workers", 4)  # 默认4线程，可配

        # 确定日期范围
        last = self._get_safe_watermark(source, table, freq)
        if mode == "full_range":
            start = task.get("start_date", "2018-01-01")
            end = task.get("end_date") or datetime.now().strftime("%Y-%m-%d")
        else:
            start = self._bump_date(last) or task.get("start_date", "2018-01-01")
            end = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"[{batch_id}] PER_STOCK(并行{max_workers}线程): {start} → {end} (last={last})")
        if self._date_range_empty(start, end):
            logger.info(f"[{batch_id}] 水位已追平，无需拉取: {start} > {end}")
            self.batch_audit.record(batch_id, name, source, table, freq,
                                    0, 0, 0, 0, 0, "empty", None, started_at)
            return True

        adapter = self._get_adapter(source, task)

        # 获取全市场代码列表（按表类型选择正确的代码获取方法）
        if table in ("etf_daily", "etf_minutes") and hasattr(adapter, "get_etf_codes"):
            all_codes = adapter.get_etf_codes()
        elif table == "index_daily" and hasattr(adapter, "get_index_daily_universe"):
            # F5：正式动态指数宇宙 = 普通指数 + SW2021 L1 申万行业指数
            all_codes = adapter.get_index_daily_universe()
        elif table == "index_daily" and hasattr(adapter, "get_index_codes"):
            all_codes = adapter.get_index_codes()
        elif hasattr(adapter, "get_all_stock_codes"):
            all_codes = adapter.get_all_stock_codes()
        else:
            logger.error(f"[{batch_id}] {source} 不支持全市场获取")
            return False

        total = len(all_codes)
        logger.info(f"[{batch_id}] 全市场 {total} 只，{max_workers} 线程并行拉取")

        # 静默子模块日志（避免刷屏）
        for mod_name in ["quantstudio.pipeline.aligner", "quantstudio.pipeline.validator",
                         "quantstudio.pipeline.writers", "quantstudio.pipeline.sources.baostock_adapter"]:
            logging.getLogger(mod_name).setLevel(logging.WARNING)

        # stock_daily 任务：自动先拉依赖表 stock_daily_valuation（hidden 任务，用户不可见）
        # 全量拉取时，若依赖表无对应日期的数据，自动触发全量拉取；增量时按水位线自动触发
        # 范围跟随 stock_daily：start 前推 30 自然日（≈20交易日），满足回测/采集期近20日
        # circ_mv 回看，避免 stock_daily 最早一段 is_delisting_risk 退化为 close<1 兜底。
        if table == "stock_daily":
            sdv_task = next((t for t in self.tasks_cfg.get("tasks", [])
                             if t.get("table") == "stock_daily_valuation"), None)
            if sdv_task and sdv_task.get("enabled", True):
                try:
                    import datetime as _dt
                    _sdv_start = (_dt.datetime.strptime(start, "%Y-%m-%d") - _dt.timedelta(days=30)).strftime("%Y-%m-%d")
                    _sdv_run = dict(sdv_task)   # 浅拷贝：不污染 tasks_cfg 原对象/磁盘配置
                    _sdv_run["mode"] = "full_range"
                    _sdv_run["start_date"] = _sdv_start
                    _sdv_run["end_date"] = end
                    logger.info(f"[{batch_id}] 自动触发依赖表 {sdv_task.get('table')}（跟随范围 {_sdv_start}→{end}）...")
                    self._execute_task(_sdv_run)
                except Exception as e:
                    logger.warning(f"[{batch_id}] 依赖表 stock_daily_valuation 拉取失败（is_delisting_risk 仅靠 close<1）: {e}")

        # stock_daily 任务：自动先拉依赖表 stock_namechange（ST 更名历史，hidden 任务）。
        # 适配 stock_daily 所有数据源，只需首次触发全量拉取，后续复用已入库数据。
        if table == "stock_daily":
            nc_task = next((t for t in self.tasks_cfg.get("tasks", [])
                             if t.get("table") == "stock_namechange"), None)
            if nc_task and nc_task.get("enabled", True):
                try:
                    _nc_cnt = self.writer.execute_read("SELECT COUNT(*) FROM stock_namechange")[0][0]
                    if _nc_cnt == 0:
                        logger.info(f"[{batch_id}] 自动触发依赖表 stock_namechange（首次全量拉取）...")
                        _nc_run = dict(nc_task)
                        _nc_run["mode"] = "full_range"
                        self._execute_task(_nc_run)
                except Exception as e:
                    logger.warning(f"[{batch_id}] 依赖表 stock_namechange 拉取失败（is_st_reliable 留 False）: {e}")

        # stock_daily 任务额外准备：namechange（从已入库读取）+ valuation（与 per_trade_date 路径同款注入）
        namechange_df = None
        valuation_df = None
        if table == "stock_daily":
            try:
                namechange_df = self._prepare_namechange_df()
            except Exception as e:
                logger.warning(f"[{batch_id}] namechange 准备失败: {e}")
            try:
                valuation_df = self._prepare_valuation_df(start, end)
            except Exception as e:
                logger.warning(f"[{batch_id}] valuation 准备失败: {e}")

        # 线程安全：写库用一个锁（DuckDB 单连接不支持并发写）
        adapter.configure_execution(task)
        write_lock = threading.Lock()
        total_raw = [0]
        total_passed = [0]
        total_rejected = [0]
        total_fixed = [0]
        total_written = [0]  # 用 list 包装以便线程内修改
        total_new = [0]      # 真实新增行数（审计准确性）
        total_updated = [0]  # upsert 更新行数
        fail_count = [0]
        done_count = [0]
        t0 = _time.time()

        def process_one(code):
            """单只股票：拉取→对齐→校验→入库（线程安全写入）"""
            try:
                raw_df, _ = adapter.fetch_table(table, start, end, freq=freq, codes=[code])
                if len(raw_df) == 0:
                    return 0
                # tushare 需要拉复权因子计算复权价；baostock 直通（adapter 已提供）
                # MCP（P2-4 §7.2-B）：raw_df 已含 adj_factor 列（原样返回），直接提取并
                # 标准化为 aligner 期望格式（SH/SZ 后缀、UTC→Asia/Shanghai、交易日连接、
                # 去重、清洗），走 aligner tushare 计算路径
                # （_apply_qfq: front=raw×adj_i/adj_latest, back=raw×adj_i/adj_earliest）。
                # ETF 用 fund_adj 口径（asset_type=ETF），股票用 adj_factor。
                adj_factor_df = None
                if source == "tushare" and table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
                    adj_factor_df = self._fetch_adj_factor(adapter, [code], start, end,
                                                           is_etf=(table in ("etf_daily", "etf_minutes")))
                elif source == "mcp" and "adj_factor" in raw_df.columns \
                        and table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
                    from .sources.mcp_adapter import normalize_mcp_adj_factor_df
                    asset_type = "ETF" if table.startswith("etf") else "STOCK"
                    adj_factor_df = normalize_mcp_adj_factor_df(raw_df, freq, asset_type)
                    if len(adj_factor_df) == 0:
                        logger.warning(f"[{code}] MCP adj_factor 标准化后为空（表={table}），"
                                       f"复权字段将留 NULL")
                std_df, _ = self.aligner.align(raw_df, table, source, adj_factor_df=adj_factor_df,
                                               namechange_df=namechange_df, valuation_df=valuation_df,
                                               freq=freq)
                res = self.validator.validate(std_df, table, batch_id, source, expected_freq=freq)
                with write_lock:
                    total_raw[0] += len(std_df)
                    total_passed[0] += len(res.passed_df)
                    total_rejected[0] += len(res.rejected_rows)
                    total_fixed[0] += int(getattr(res, "fixed_count", 0))
                if len(res.passed_df) > 0:
                    with write_lock:  # 线程安全写库
                        n = self._stamp_and_write(res, table, batch_id, source)
                        total_new[0] += getattr(n, "new", 0)
                        total_updated[0] += getattr(n, "updated", 0)
                        return n
                return 0
            except Exception as e:
                fail_count[0] += 1
                if fail_count[0] <= 5:
                    logger.warning(f"[{batch_id}] {code} 失败: {e}")
                return 0

        # 并行拉取，但分批提交（每批 max_workers 只，控制并发）
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for i, code in enumerate(all_codes):
                if not self._running:
                    break
                futures[pool.submit(process_one, code)] = i
            # 注：提交阶段不打进度日志（submit 是瞬时的，done_count≈0 无意义，
            # 真实进度在下方 as_completed 循环里按完成数打印）

            # 等待全部完成 + 统计
            # 分钟数据单只约 17 秒、8 线程 → 旧"每 50 只"打一次 ≈ 106 秒无输出，
            # 看起来像卡住。改用完成数(约每 2% / 至少 10 只) + 时间兜底(每 30 秒) 双触发：
            # 即使单只慢，也至少每 30 秒打一行心跳，避免误判卡死。
            log_step = max(10, total // 50)  # 约 2% 一次，至少 10 只
            last_progress_ts = _time.time()
            pending = set(futures)
            while pending and self._running:
                done, pending = wait(pending, timeout=30.0, return_when=FIRST_COMPLETED)
                for f in done:
                    total_written[0] += f.result()
                    done_count[0] += 1
                now = _time.time()
                # 触发条件：完成数跨过 log_step 整数倍 / 全部完成 / 距上次打点超 30 秒
                time_heartbeat = now - last_progress_ts >= 30.0
                count_step = done_count[0] % log_step == 0
                if done_count[0] == total or count_step or time_heartbeat:
                    elapsed = now - t0
                    speed = done_count[0] / elapsed if elapsed > 0 else 0
                    # speed=0（尚无完成）时 ETA 显示 "--" 避免误导为"即将完成"
                    eta_str = f"{(total - done_count[0]) / speed:.0f}s" if speed > 0 else "--"
                    tag = "心跳" if (time_heartbeat and not count_step) else "进度"
                    logger.info(f"[{batch_id}] {tag}: {done_count[0]}/{total} "
                                f"({done_count[0]*100//total}%) "
                                f"已入库 {total_written[0]} 行, 失败 {fail_count[0]}, "
                                f"已用 {elapsed:.0f}s, 剩余 {eta_str}")
                    last_progress_ts = now

        fetch_ok, failure_rate, threshold = self._failure_gate(task, fail_count[0], done_count[0], source=source)
        reject_ok, reject_rate, _ = self._failure_gate(task, total_rejected[0], total_raw[0], source=source, is_reject=True)
        has_usable_result = total_written[0] > 0 or (mode == "incremental" and fail_count[0] == 0)
        task_ok = fetch_ok and reject_ok and has_usable_result

        # 允许失败率内视为本轮完成并推进到 end；超阈值不推进，下轮从旧水位重试。
        if task_ok:
            # W2-0.8 缺陷 B：full_range 成功后执行 authority reconciliation（清除既有
            # 非 authoritative 历史行 + watermark），使 authority-locked 表单源收敛。
            # 失败则降级为 batch failed，不推进水位。
            recon = self._authority_reconcile(task, source, table, batch_id)
            if recon["enabled"] and recon["ran"] and not recon["ok"]:
                task_ok = False
                logger.error(
                    f"[{batch_id}] authority_reconciliation failed: {recon['reason']}; "
                    f"batch 降级 failed，不推进水位")
        if task_ok:
            self._advance_actual_watermark(source, table, freq, batch_id)

        elapsed = _time.time() - t0
        level = logger.info if task_ok else logger.error
        level(f"[{batch_id}] {'✅' if task_ok else '❌'} PER_STOCK 完成: "
              f"{done_count[0]}只({max_workers}线程), 入库{total_written[0]}行 "
              f"(new {total_new[0]} + upd {total_updated[0]}), 拉取失败{fail_count[0]}, "
              f"拉取失败率={failure_rate:.6%}, 拒绝{total_rejected[0]}行, "
              f"拒绝率={reject_rate:.6%} (阈值={threshold:.6%}), 耗时{elapsed:.0f}s")
        audit_status = "success" if task_ok else "failed"
        error = None if task_ok else (
            f"failed={fail_count[0]}/{done_count[0]} rate={failure_rate:.8f} "
            f"rejected={total_rejected[0]}/{total_raw[0]} reject_rate={reject_rate:.8f} "
            f"threshold={threshold:.8f} written={total_written[0]}")
        self.batch_audit.record(batch_id, name, source, table, freq,
                                total_raw[0], total_raw[0], total_passed[0], total_rejected[0],
                                total_written[0], audit_status, error,
                                started_at, rows_new=total_new[0], rows_updated=total_updated[0], rows_fixed=total_fixed[0])
        return task_ok

    # ---------------- 事件循环（统一每日调度）----------------
    def run_forever(self, max_iterations: Optional[int] = None):
        """DEPRECATED v3：旧版常驻循环，长期持有 DuckDB _shared_conn，与 GUI 跨进程
        读库冲突。新常驻模式由 ``DaemonLifecycle``（daemon_lifecycle.py）负责轻量调度，
        空闲期不持有 collector/连接。此方法保留作回退，**新代码不应调用**。

        7×24 常驻循环（旧语义）：
        - incremental 任务：每天 daemon_schedule.daily_time 自动执行一轮
        - full_range 任务：不参与自动循环，仅 run_once / GUI 手动触发
        """
        lock_path = DATA_ROOT / ".collector.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(lock_path), timeout=5)

        # 优雅退出信号：仅在主解释器主线程注册。
        # 原因：signal.signal() 只能在主线程调用；GUI 通过 DaemonWorker(QThread) 跑
        # run_forever 时当前线程非主线程，注册会抛
        # ValueError: signal only works in main thread of the main interpreter。
        # GUI 场景的优雅退出由 DaemonWorker.stop() 设 self._running=False 实现，
        # 不依赖 signal handler；CLI 独立进程场景仍在主线程，signal 注册有效（Ctrl+C 退出）。
        def _stop(signum, frame):
            logger.info(f"[Daemon] received signal {signum}, graceful shutdown...")
            self._running = False
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, _stop)
            try:
                signal.signal(signal.SIGTERM, _stop)
            except (AttributeError, ValueError, OSError):
                pass
        else:
            logger.info("[Daemon] 非主线程运行（如 GUI DaemonWorker），跳过 signal 注册；"
                        "退出由 stop() 触发 self._running=False。")

        # 读调度配置
        sched_cfg = self.tasks_cfg.get("daemon_schedule", {})
        daily_time = sched_cfg.get("daily_time", "17:00")
        check_interval = sched_cfg.get("check_interval_sec", 300)
        logger.info(f"[Daemon] 启动。增量任务每天 {daily_time} 自动执行，"
                    f"检查间隔 {check_interval}s。full_range 任务需手动触发。")

        with lock:
            last_run_date = ""  # 记录上次自动执行的日期（防止同一天重复）
            while self._running:
                now = datetime.now()
                today_str = now.strftime("%Y-%m-%d")
                now_hm = now.strftime("%H:%M")
                # 到达 daily_time 且今天还没执行过 → 执行所有 incremental 任务
                if now_hm >= daily_time and last_run_date != today_str:
                    logger.info(f"[Daemon] === 每日定时执行 {daily_time} 开始（{today_str}）===")
                    self._run_incremental_cycle()
                    last_run_date = today_str
                    logger.info(f"[Daemon] === 每日定时执行完成 ===")
                # 健康检查
                self._health_check()
                # 睡眠等待下次检查
                if max_iterations is not None:
                    break
                self._interruptible_sleep(check_interval)

        logger.info("[Daemon] ResidentCollector 已停止")

    def run_once(self, task_name: Optional[str] = None,
                 mode: str = "incremental",
                 quality_audit: str = "full"):
        """Run one task or all enabled tasks with explicit range semantics.

        Returns a dict result (W2-0.8 缺陷 D/E 修复):
            {"task_found": bool, "task_ok": bool, "audit_run": bool, "audit_ok": bool}

        `quality_audit`:
            "full" (default, production-safe) — run the full-DB quality audit after
              the task and include its result in the returned aggregate. This
              preserves the existing resident/GUI semantic.
            "none" — skip the full-DB audit. Used by staging phase_run_task for
              staged loads (each target table loaded separately; the final unified
              audit is run by staging Phase 6). A not-yet-loaded sibling target
              table must NOT cause a staged task to appear failed.
        """
        if mode not in ("full_range", "incremental"):
            raise ValueError(f"unsupported collection mode: {mode!r}")
        if quality_audit not in ("full", "none"):
            raise ValueError(f"unsupported quality_audit mode: {quality_audit!r}")
        tasks = self.tasks_cfg.get("tasks", [])
        task_found = False
        task_ok = True
        audit_ok = True
        audit_run = False
        try:
            for task in tasks:
                if task_name and task["name"] != task_name:
                    continue
                if not task_name and not task.get("enabled", True):
                    logger.info(f"[Daemon] skip disabled task: {task['name']}")
                    continue
                task_found = True
                ok = self.execute_task(task, mode=mode, run_quality_audit=False)
                if not ok:
                    task_ok = False
            if task_name and not task_found:
                logger.error(f"[Daemon] task not found: {task_name}")
                task_ok = False
        finally:
            if quality_audit == "full":
                audit_run = True
                audit_ok = self._run_full_quality_audit()
        return {
            "task_found": task_found,
            "task_ok": task_ok,
            "audit_run": audit_run,
            "audit_ok": audit_ok,
        }

    def _run_incremental_cycle(self):
        """执行一轮常驻增量任务；无论成功、失败或中断，结束后必跑质量审计。"""
        try:
            for task in self.tasks_cfg.get("tasks", []):
                if not self._running:
                    break
                if not task.get("enabled", True):
                    continue
                # Legacy configs may still persist mode=full_range. Resident
                # scheduling never runs those tasks; GUI/CLI can invoke them
                # explicitly through the same execute_task() entry.
                if task.get("mode", "incremental") != "incremental":
                    continue
                try:
                    self.execute_task(task, mode="incremental", run_quality_audit=False)
                except Exception as e:
                    logger.error(f"[Daemon] {task['name']} 执行失败: {e}", exc_info=True)
        finally:
            self._run_full_quality_audit()

    def _authority_reconcile(self, task: Dict, source: str, table: str,
                              batch_id: str) -> Dict:
        """W2-0.9 缺陷 B：通用 authority reconciliation（完整契约 + 原子事务）。

        在 full_range 成功写入后，清除目标表中**既有的非权威/NULL data_source 行**，
        使 authority-locked 表收敛到单一权威源。这是纯 upsert 无法做到的（upsert 只触及
        本次有数据的 code，旧 NULL/akshare 历史行残留会导致 SourceTraceability/
        AuthoritySourceViolation 审计失败）。

        配置契约（严格）：
            authority_reconciliation:
              enabled: true
              mode: purge_non_authoritative      # 仅支持此值
              scope: full_range_only             # 仅支持此值
              cleanup_source_watermark: true|false
        未声明该键的旧任务行为不变。enabled=true 但 mode/scope 为未知值 → fail-fast（ok=False，
        不执行 DELETE），绝不在忽略未知配置后执行删除。

        真实触发条件（全部满足才执行 DELETE）：
          - authority_reconciliation.enabled == true
          - mode == "purge_non_authoritative" 且 scope == "full_range_only"
          - task.authoritative_source 非空
          - task.allow_fallback == false
          - task.mode == "full_range"（runtime）
          - 本轮 actual source == authoritative_source
        cleanup_source_watermark=False 时只删数据行，不删任何 watermark。

        事务：表数据 DELETE、可选 watermark DELETE、后置 source 集合校验位于同一事务
        （BEGIN/COMMIT/ROLLBACK）。任一步失败 → ROLLBACK，不留半删除状态。
        后置结果必须为 source 集合恰好等于 {authoritative}；full_range 有可用写入结果
        却得到空表时不静默 PASS。

        返回 dict：{enabled, ran, ok, reason, mode, scope, cleanup_source_watermark,
                   authoritative_source, actual_source, rows_purged, watermarks_purged,
                   source_set_after}
        失败时 ok=False，调用方将 batch 标记 failed 且不推进水位。
        """
        import re as _re
        result = {"enabled": False, "ran": False, "ok": True, "reason": "",
                  "mode": None, "scope": None, "cleanup_source_watermark": True,
                  "authoritative_source": None, "actual_source": source,
                  "rows_purged": 0, "watermarks_purged": 0, "source_set_after": []}
        recon_cfg = task.get("authority_reconciliation") or {}
        if not recon_cfg.get("enabled", False):
            return result
        result["enabled"] = True

        # 严格契约校验：mode/scope 必须是受支持值，否则 fail-fast（不执行 DELETE）
        rec_mode = recon_cfg.get("mode")
        rec_scope = recon_cfg.get("scope")
        cleanup_wm = recon_cfg.get("cleanup_source_watermark", True)
        result["mode"] = rec_mode
        result["scope"] = rec_scope
        result["cleanup_source_watermark"] = cleanup_wm
        if rec_mode != "purge_non_authoritative":
            result["ok"] = False
            result["reason"] = f"unsupported authority_reconciliation.mode={rec_mode!r}"
            logger.error(f"[{batch_id}] authority_reconciliation BLOCKED: {result['reason']}")
            return result
        if rec_scope != "full_range_only":
            result["ok"] = False
            result["reason"] = f"unsupported authority_reconciliation.scope={rec_scope!r}"
            logger.error(f"[{batch_id}] authority_reconciliation BLOCKED: {result['reason']}")
            return result

        # 触发条件
        authoritative = task.get("authoritative_source")
        result["authoritative_source"] = authoritative
        allow_fb = task.get("allow_fallback", True)
        rt_mode = task.get("mode", "incremental")
        if not authoritative:
            result["reason"] = "authoritative_source not set"
            return result
        if allow_fb:
            result["reason"] = "allow_fallback=true (not strict)"
            return result
        if rt_mode != "full_range":
            result["reason"] = f"runtime mode={rt_mode} (reconciliation is full_range-only)"
            return result
        if source != authoritative:
            result["reason"] = (f"actual source {source!r} != authoritative "
                                f"{authoritative!r}")
            return result

        # 标识符校验：table 必须是可信 schema 标识符（字母/数字/下划线），防 SQL 注入
        if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            result["ok"] = False
            result["reason"] = f"invalid table identifier: {table!r}"
            logger.error(f"[{batch_id}] authority_reconciliation BLOCKED: {result['reason']}")
            return result

        # 触发条件全部满足：执行 purge（原子事务）
        # NOTE: shared_conn() 内部 acquire/release _conn_lock 并返回持久 read_write 连接。
        # 不要外层再套 `with self.writer._conn_lock`（非重入锁会自死锁）。once 模式单表
        # 单线程，在 shared conn 上操作无需再加锁。
        result["ran"] = True
        conn = self.writer.shared_conn()
        try:
            conn.execute("BEGIN")
            rows_before = conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            # NULL-safe: IS DISTINCT FROM treats NULL != authoritative correctly
            conn.execute(
                f"DELETE FROM {table} "
                f"WHERE data_source IS DISTINCT FROM ?", [authoritative])
            rows_after = conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            result["rows_purged"] = rows_before - rows_after
            wm_before = 0
            wm_after = 0
            if cleanup_wm:
                wm_before = conn.execute(
                    "SELECT COUNT(*) FROM source_watermark WHERE table_name = ?",
                    [table]).fetchone()[0]
                conn.execute(
                    "DELETE FROM source_watermark "
                    "WHERE table_name = ? AND source IS DISTINCT FROM ?",
                    [table, authoritative])
                wm_after = conn.execute(
                    "SELECT COUNT(*) FROM source_watermark WHERE table_name = ?",
                    [table]).fetchone()[0]
                result["watermarks_purged"] = wm_before - wm_after
            # 后置校验：source 集合必须恰好为 {authoritative}（不能是空表——
            # full_range 有可用写入结果却得到空表说明回填未生效，不得静默 PASS）
            remaining = conn.execute(
                f"SELECT DISTINCT data_source FROM {table}").fetchall()
            remaining_sources = {r[0] for r in remaining}
            result["source_set_after"] = sorted(s for s in remaining_sources if s is not None)
            if remaining_sources != {authoritative}:
                # 回滚整个事务，不留半删除状态
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                result["ok"] = False
                if not remaining_sources:
                    result["reason"] = (f"post-reconcile {table} is empty — full_range "
                                        f"produced no usable authoritative rows")
                else:
                    result["reason"] = (f"post-reconcile source set {remaining_sources} "
                                        f"!= {{{authoritative}}}")
                logger.error(f"[{batch_id}] authority_reconciliation FAILED "
                             f"(rolled back): {result['reason']}")
                return result
            conn.execute("COMMIT")
            logger.info(
                f"[{batch_id}] authority_reconciliation: purged "
                f"{result['rows_purged']} non-{authoritative} rows + "
                f"{result['watermarks_purged']} watermarks from {table} "
                f"(source_set_after={result['source_set_after']})")
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            result["ok"] = False
            result["reason"] = f"reconciliation error: {e}"
            logger.error(f"[{batch_id}] authority_reconciliation FAILED "
                         f"(rolled back): {e}", exc_info=True)
        return result

    def _run_full_quality_audit(self):
        """采集完成后执行 Canonical 全库质量审计。"""
        hc = self.tasks_cfg.get("health_check", {})
        if not hc.get("full_quality_after_run", True):
            return True
        try:
            from .quality_audit import DataQualityAuditor
            # 传入 writer 的持久 read_write 连接，避免 quality_audit 自开 read_only
            # 与 writer 的 read_write 并发触发「different configuration」冲突。
            # 此处采集已完成（_run_full_quality_audit 在 execute_task 末尾调用），
            # 无并发 write，独占 shared_conn 安全。
            authority_rules = build_authority_rules(self.tasks_cfg)
            # QFQ 专项门控：仅编排器 enabled 时启用（None → 完全跳过，disabled
            # 行为逐位不变，不会因历史 qfq 表残留数据新增审计失败）。
            qfq_block = self.tasks_cfg.get("qfq_orchestrator", {}) or {}
            qfq_thresholds = (dict(qfq_block.get("quality_thresholds", {}) or {})
                              if qfq_block.get("enabled") else None)
            report = DataQualityAuditor(
                self.writer.db_path, self.aligner.schemas,
                batch_audit_path=self.batch_audit.db_path,
                quarantine_path=self.quarantine.db_path,
                authority_rules=authority_rules,
                shared_conn=self.writer.shared_conn(),
                qfq_thresholds=qfq_thresholds).run()
            errors = [issue for issue in report.issues if issue.severity == "error"]
            warnings = [issue for issue in report.issues if issue.severity == "warning"]
            if errors:
                logger.error(f"[QualityAudit] {len(errors)} 类错误，{len(warnings)} 类警告；"
                             f"checks={report.checks_run}")
                for issue in errors[:20]:
                    logger.error(f"[QualityAudit] {issue.table}/{issue.check}: "
                                 f"count={issue.count} {issue.detail}")
            elif warnings:
                logger.warning(f"[QualityAudit] 0 类错误，{len(warnings)} 类警告；"
                               f"checks={report.checks_run}")
            else:
                logger.info(f"[QualityAudit] 全部通过；checks={report.checks_run}")
            return not errors
        except Exception as e:
            logger.error(f"[QualityAudit] 全库审计执行失败: {e}", exc_info=True)
            return False

    # ---------------- 私有 ----------------
    def _get_adapter(self, source: str, task: Optional[Dict] = None):
        if source not in self._adapters:
            src_cfg = self.sources_cfg.get("sources", {}).get(source, {})
            if not src_cfg.get("enabled", False):
                raise RuntimeError(f"数据源 {source} 未启用（sources_config.json）")
            # 替换 ${ENV_VAR} 占位符为环境变量值
            cfg = {}
            for k, v in src_cfg.items():
                if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                    env_key = v[2:-1]
                    cfg[k] = os.environ.get(env_key, "")
                else:
                    cfg[k] = v
            cfg["name"] = source
            self._adapters[source] = create_adapter(source, cfg)
        adapter = self._adapters[source]
        if task is not None:
            adapter.configure_execution(task)
        return adapter

    # ---------------- 数据源回退链 ----------------
    def resolve_source_chain(self, task: Dict) -> List[str]:
        """Return the exact enabled runtime fallback chain for a task."""
        return self._resolve_source_chain(dict(task))

    def _resolve_source_chain(self, task: Dict) -> List[str]:
        """解析该 task 的候选源有序列表（去重、保留优先级、仅含已启用且支持该表+freq 的源）。

        优先级来源：
            1. task.source_priority（显式声明的回退顺序，预留替代权威源）
            2. task.source（兼容旧配置，作为兜底首选）
            3. 全局 sources_config.default_source_priority
        过滤：仅保留 sources_config 中 enabled=true 且 adapter.supports_task(table,freq) 为 True 的源。
        适配器实例化失败（如 xtquant 未安装）的源直接跳过。
        """
        table = task.get("table")
        freq = task.get("freq", "daily")
        authoritative = task.get("authoritative_source")
        allow_fallback = task.get("allow_fallback", True)

        # 权威源锁定：当 allow_fallback=false 且 authoritative_source 已设置时，
        # 仅使用该权威源（不追加全局 default_source_priority，不启用任何回退链）。
        if authoritative and not allow_fallback:
            chain: List[str] = [authoritative]
        else:
            chain: List[str] = list(task.get("source_priority") or [])
            s = task.get("source")
            if s and s not in chain:
                chain.append(s)
            global_pri = self.sources_cfg.get("default_source_priority") or []
            for g in global_pri:
                if g not in chain:
                    chain.append(g)

        out: List[str] = []
        for src in chain:
            cfg = self.sources_cfg.get("sources", {}).get(src)
            if not cfg or not cfg.get("enabled", False):
                logger.debug(f"[SourceChain] 源 '{src}' 未启用，跳过")
                continue
            try:
                adapter = self._get_adapter(src, task)
                ok, reason = adapter.supports_task(table, freq)
            except Exception as e:
                logger.debug(f"[SourceChain] 源 '{src}' 适配器不可用: {e}")
                continue
            if not ok:
                logger.debug(f"[SourceChain] 源 '{src}' 不支持 {table}/{freq}: {reason}")
                continue
            out.append(src)
        if len(out) < len(chain):
            skipped = [c for c in chain if c not in out]
            logger.info(f"[SourceChain] task={task.get('name')} 候选链 {chain} → 可用 {out}"
                        f"（跳过未启用/不支持: {skipped}）")
        # 警告：如果声明了 authoritative_source 但未出现在可用源链中
        if authoritative and authoritative not in out:
            logger.warning(
                f"[SourceChain] task={task.get('name')} authoritative_source='{authoritative}' "
                f"不在可用源链 {out} 中（源可能未启用或不支持 {table}/{freq}）")
        return out

    def _get_safe_watermark(self, source: str, table: str, freq: str) -> Optional[str]:
        """读取水位，并在其领先于 Canonical 实际数据时回退到表内最大日期。"""
        stored = self.writer.get_last_date(source, table, freq)
        if not stored:
            return None
        schema = self.aligner.schemas.get(table, {})
        time_col = schema.get("time_key")
        if not time_col:
            return stored
        try:
            # 用 writer 持久 read_write 连接（避免 read_only 与 write 并发配置冲突）
            columns = {row[0] for row in self.writer.execute_read(f'DESCRIBE "{table}"')}
            where = []
            params = []
            if "data_source" in columns:
                where.append("data_source=?")
                params.append(source)
            if "freq" in columns:
                where.append("freq=?")
                params.append(freq)
            clause = " WHERE " + " AND ".join(where) if where else ""
            rows = self.writer.execute_read(
                f'SELECT MAX("{time_col}") FROM "{table}"{clause}', params or None)
            actual = rows[0][0] if rows else None
            if actual is not None and int(stored) > int(actual):
                logger.warning(f"[Watermark] {source}/{table}/{freq} 水位领先实际数据："
                               f"stored={stored}, actual={actual}，本轮从实际最大日期续拉")
                return str(int(actual))
        except Exception as e:
            logger.warning(f"[Watermark] 校验 {source}/{table}/{freq} 失败，沿用存储水位: {e}")
        return stored

    def _advance_actual_watermark(self, source: str, table: str, freq: str,
                                  batch_id: str) -> Optional[int]:
        """按 Canonical 中本次源的实际最大业务日期推进水位。"""
        schema = self.aligner.schemas.get(table, {})
        time_col = schema.get("time_key")
        if not time_col:
            return None
        try:
            # 用 writer 持久 read_write 连接（避免 read_only 与 write 并发配置冲突）
            columns = {row[0] for row in self.writer.execute_read(f'DESCRIBE "{table}"')}
            where = []
            params = []
            if "data_source" in columns:
                where.append("data_source=?")
                params.append(source)
            if "freq" in columns:
                where.append("freq=?")
                params.append(freq)
            clause = " WHERE " + " AND ".join(where) if where else ""
            rows = self.writer.execute_read(
                f'SELECT MAX("{time_col}") FROM "{table}"{clause}', params or None)
            actual = rows[0][0] if rows else None
            if actual is not None:
                # 红线：水位推进唯一入口（qfq enabled 时四价格表延迟提交）
                self._advance_or_defer_watermark(source, table, freq, int(actual), batch_id)
                return int(actual)
        except Exception as e:
            logger.error(f"[Watermark] 推进 {source}/{table}/{freq} 失败: {e}", exc_info=True)
        return None

    def _stamp_and_write(self, res, table: str, batch_id: str, source: str,
                         task: Optional[Dict] = None):
        """Stamp provenance, then write only changed rows for snapshot datasets."""
        task = task or {}
        df = res.passed_df
        if df is not None and len(df) > 0:
            df = df.copy()
        if df is not None and len(df) > 0 and "data_source" in self.writer._table_columns(table):
            df["data_source"] = task.get("data_source_label", source)
        if (df is not None and len(df) > 0
                and task.get("dataset_kind") == "snapshot"
                and task.get("skip_unchanged", False)):
            df = self._filter_unchanged_snapshot_rows(
                df, table, exclude=task.get("change_compare_exclude", ["update_time"]))
        result = self.writer.write(df, table, batch_id)
        if table == "index_constituents" and df is not None and len(df) > 0:
            # F3 修订：成分批次写入后立即打 snapshot_meta 完整性契约
            # （expected_count/status 在打点确定，不依赖未来数据）。
            from quantstudio.pipeline.index_constituents_meta import refresh_snapshot_meta
            with self.writer._conn_lock:
                conn = self.writer._conn()
                try:
                    refresh_snapshot_meta(
                        conn, index_codes=sorted(df["index_code"].astype(str).unique()))
                finally:
                    conn.close()
        return result

    def _filter_unchanged_snapshot_rows(self, df: pd.DataFrame, table: str,
                                        exclude=None) -> pd.DataFrame:
        """Keep new/changed snapshot rows; never delete locally retained history."""
        schema = self.aligner.schemas.get(table, {})
        pk = [c for c in schema.get("primary_key", []) if c in df.columns]
        if not pk:
            return df
        exclude = set(exclude or [])
        compare_cols = [c for c in df.columns if c not in set(pk) | exclude]
        if not compare_cols:
            return df
        try:
            selected = pk + compare_cols
            quoted = ", ".join('"' + c.replace('"', '""') + '"' for c in selected)
            existing = self.writer.read_df(f'SELECT {quoted} FROM "{table}"')
        except Exception as exc:
            logger.warning(f"[SnapshotDiff] {table} existing-read failed; falling back to full-batch upsert: {exc}")
            return df
        if existing is None or existing.empty:
            return df

        incoming = df.set_index(pk, drop=False)
        current = existing.drop_duplicates(pk, keep="last").set_index(pk, drop=False)
        aligned = current.reindex(incoming.index)
        changed = pd.Series(~incoming.index.isin(current.index), index=incoming.index, dtype=bool)
        for col in compare_cols:
            equal = incoming[col].eq(aligned[col]) | (incoming[col].isna() & aligned[col].isna())
            changed |= ~equal.fillna(False)
        out = incoming.loc[changed.to_numpy()].reset_index(drop=True)
        logger.info(f"[SnapshotDiff] {table}: validated={len(df)} changed={len(out)} "
                    f"unchanged={len(df)-len(out)} exclude={sorted(exclude)}")
        return out[df.columns]

    @staticmethod
    def _snapshot_watermark(end: str) -> str:
        """Store snapshot success date as Asia/Shanghai midnight milliseconds."""
        stamp = pd.Timestamp(str(end))
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("Asia/Shanghai")
        else:
            stamp = stamp.tz_convert("Asia/Shanghai")
        return str(int(stamp.normalize().timestamp() * 1000))

    def _fetch_adj_factor(self, adapter, codes: Optional[list], start: str, end: str,
                          is_etf: bool = False):
        """拉取复权因子供 aligner 计算 8 个复权字段。
        仅 tushare 支持 adj_factor/fund_adj API；其他源返回 None（复权字段留 NULL，不影响主流程）。
        复权因子存 SQLite（qfq_aux.db），避免重复拉取。is_etf=True 时用 fund_adj 接口。"""
        from .qfq_maintenance import QFQMaintenance, resolve_ts_codes
        from .aligner import normalize_code, to_ms_timestamp
        qfq = QFQMaintenance(self.writer.db_path)
        # codes 可能是裸码或带后缀；统一用 resolve_ts_codes 解析为 Tushare ts_code
        # （元数据优先，资产类型感知前缀 fallback）。修复：market_of_code 对 ETF
        # 裸码（5/1 开头）会误判 BJ，改用 resolve_ts_codes 保证 daemon 与
        # QFQFactorRefresher 后缀推导一致。
        raw_codes = [str(c).strip() for c in (codes or [])]
        if raw_codes:
            asset_type = "ETF" if is_etf else "STOCK"
            tushare_codes = resolve_ts_codes(
                raw_codes, asset_type=asset_type, main_db=str(self.writer.db_path))
        else:
            tushare_codes = []
        # 转裸码列表（用于 SQL 查询过滤）
        bare_codes = [normalize_code(c, "tushare_to_raw") for c in tushare_codes]
        try:
            qfq.fetch_adj_factor(adapter, tushare_codes, start, end, is_etf=is_etf)
            # 从 qfq_aux.db 读回当前 code 的数据（而非全表，性能优化）
            import sqlite3
            import pandas as pd
            codes_in = "','".join(bare_codes)
            with sqlite3.connect(qfq.db_path, timeout=30) as conn:
                conn.execute("PRAGMA busy_timeout=30000")
                adj_df = pd.read_sql_query(
                    f"SELECT * FROM adj_factor WHERE code IN ('{codes_in}')", conn)
            if len(adj_df) == 0:
                return None
            logger.debug(f"[QFQ] adj_factor 准备就绪: {len(adj_df)} 行")
            return adj_df
        except Exception as e:
            logger.warning(f"[QFQ] adj_factor 拉取失败（复权字段将留 NULL）: {e}")
            return None

    def _prepare_close_df(self, codes: Optional[list], start: str, end: str) -> Optional["pd.DataFrame"]:
        """为 stock_float_share 补算 circ_mv 准备收盘价 DataFrame（列 code/time/close）。

        从 DuckDB stock_daily 查询 codes 在 start~end 范围的收盘价。
        aligner 的 _derive_market_value 用 merge_asof 按 end_date 取最近交易日 close。
        返回 None 表示无行情数据（补算跳过）。
        """
        try:
            import pandas as pd
            import duckdb
            from .aligner import normalize_code, to_ms_timestamp
            db_path = self.writer.db_path
            # 过滤 None/空 + 去重（codes 可能含 None 导致 join 失败）
            bare_codes = list({normalize_code(str(c), "tushare_to_raw")
                               for c in (codes or []) if c is not None and str(c).strip()})
            bare_codes = [c for c in bare_codes if c and c != "None"]
            if not bare_codes:
                return None
            start_ms = to_ms_timestamp(start)
            end_ms = to_ms_timestamp(end) + 86_400_000  # 含 end 当天
            # 用 writer 持久 read_write 连接（避免 read_only 与 write 并发配置冲突）
            df = self.writer.read_df("""
                SELECT code, time, close FROM stock_daily
                WHERE code IN (SELECT unnest(?))
                  AND time >= ? AND time <= ?
            """, [bare_codes, start_ms, end_ms])
            if len(df) == 0:
                logger.debug(f"[Daemon] _prepare_close_df: stock_daily 无 {start}~{end} 行情（补算将跳过）")
                return None
            logger.debug(f"[Daemon] _prepare_close_df: {len(df)} 行 close（{len(bare_codes)} codes）")
            return df
        except Exception as e:
            logger.warning(f"[Daemon] _prepare_close_df 失败（市值补算将跳过）: {e}")
            return None

    def _prepare_namechange_df(self) -> Optional["pd.DataFrame"]:
        """为 stock_daily 推导 is_st_reliable 读取已入库简称变更历史。

        stock_namechange 已作为 stock_daily 前置依赖自动触发（全量入库），
        此处仅从 DuckDB 读已入库数据，零网络开销。
        若 DB 为空（auto-trigger 失败或首次未触发），返回 None → is_st_reliable 留 False。
        """
        try:
            # 用 writer 持久 read_write 连接（避免 read_only 与 write 并发配置冲突）
            try:
                df = self.writer.read_df(
                    "SELECT code, change_date, status_after FROM stock_namechange")
                if len(df) > 0:
                    logger.info(f"[Daemon] namechange 复用已入库数据: {len(df)} 行")
                    return df
            except Exception:
                pass  # 表不存在或空
            logger.debug("[Daemon] namechange DB 为空，is_st_reliable 将留 False")
            return None
        except Exception as e:
            logger.warning(f"[Daemon] _prepare_namechange_df 失败: {e}")
            return None

    def _prepare_etf_daily_bounds(self) -> pd.DataFrame:
        """Read first/last etf_daily bars used to fill missing reference dates."""
        try:
            return self.writer.read_df("""
                SELECT code, MIN(time) AS first_bar_ms, MAX(time) AS last_bar_ms
                FROM etf_daily
                GROUP BY code
            """)
        except Exception as exc:
            logger.warning(f"[Daemon] etf_daily bounds unavailable; keeping Tushare null dates: {exc}")
            return pd.DataFrame(columns=["code", "first_bar_ms", "last_bar_ms"])

    def _prepare_valuation_df(self, start: str, end: str) -> Optional["pd.DataFrame"]:
        """为 stock_daily 推导 is_delisting_risk + 估值字段补全 准备每日估值 DataFrame。

        从已入库 stock_daily_valuation 读近 20 日数据（依赖：stock_daily_valuation 任务先入库）。
        - circ_mv：用于 is_delisting_risk（近 20 日 MIN(circ_mv) < 5e8）
        - pe_ttm/pb/turnover_rate：用于 aligner 补 stock_daily 的 peTTM/pbMRQ/turn 列
          （xtquant 不提供这些估值字段，tushare 时代来自 daily_basic；切 xtquant 后由本表 PIT JOIN 补全，
           保持数据适配层 duckdb_data_access.py 读 stock_daily 这些列时拿到非 NULL 值，避免回测层歧义）。
        返回 None 表示无数据（aligner 退化为仅靠 close<1 兜底 + 估值列留 NULL）。

        ⚠️ 依赖顺序：stock_daily_valuation 任务必须在 stock_daily 之前执行（collector_tasks.json 顺序）。
        """
        try:
            from .aligner import to_ms_timestamp
            db_path = self.writer.db_path
            start_ms = to_ms_timestamp(start) - 20 * 86_400_000   # 多取 20 日做窗口
            end_ms = to_ms_timestamp(end) + 86_400_000
            # 用 writer 持久 read_write 连接（避免 read_only 与 write 并发配置冲突）
            df = self.writer.read_df("""
                SELECT code, time, circ_mv, pe_ttm, pb, turnover_rate
                FROM stock_daily_valuation
                WHERE time >= ? AND time <= ?
            """, [start_ms, end_ms])
            if len(df) == 0:
                logger.warning(f"[Daemon] stock_daily_valuation 无 {start}~{end} 数据"
                               "（is_delisting_risk 仅靠 close<1 兜底，peTTM/pb/turn 留 NULL）")
                return None
            logger.info(f"[Daemon] valuation 准备: {len(df)} 行（用于 delisting_risk PIT + 估值字段补全）")
            return df
        except Exception as e:
            logger.warning(f"[Daemon] _prepare_valuation_df 失败: {e}")
            return None

    @staticmethod
    def _bump_date(last: Optional[str]) -> Optional[str]:
        """把水位推进一天，并返回适配器统一接受的 YYYY-MM-DD。

        source_watermark.last_date 使用毫秒时间戳；旧实现直接返回毫秒数，
        随后被各适配器按日期字符串截断，导致增量请求日期失真。
        同时兼容历史遗留的 YYYY-MM-DD / YYYYMMDD 水位。
        """
        if not last:
            return None
        try:
            value = str(last).strip()
            if value.isdigit() and int(value) > 10**11:
                current = pd.to_datetime(int(value), unit="ms", utc=True).tz_convert("Asia/Shanghai")
            else:
                current = pd.Timestamp(value)
                if current.tzinfo is None:
                    current = current.tz_localize("Asia/Shanghai")
                else:
                    current = current.tz_convert("Asia/Shanghai")
            return (current + timedelta(days=1)).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OverflowError):
            return None

    def _failure_gate(self, task: Dict, failed: int, attempted: int, source: str = None, is_reject: bool = False):
        """统一分片任务失败率门禁；默认允许失败比例不超过 0.01%。

        PR6b-1 修复（2026-07-23）：拒绝率（is_reject=True）对 xtquant source
        放宽到 1%。原因：xtquant back 复权是逐 tick 累积，同根 K 线 OHLC
        因子有 2-4% 微差（算法固有，非数据错误），validator 已用源感知阈值
        （xtquant back 5%），但仍有一小部分边缘 case 超过 5% 被隔离（~0.5-0.6%）。
        daemon 的 0.01% 拒绝率阈值把这些正常隔离当任务失败，触发无意义的
        源切换链 + 不推进 watermark（导致下次全量重拉）。

        拉取失败率（is_reject=False）保持 0.01% 严格——那是真正的拉取失败，
        不是数据质量过滤。
        """
        cfg = self.tasks_cfg.get("quality_gate", {})
        threshold = float(task.get("max_failure_rate", cfg.get("max_failure_rate", 0.0001)))
        # 拒绝率对 xtquant 放宽（AdjustmentFactorConsistency 算法固有微差）
        if is_reject and source == "xtquant":
            threshold = max(threshold, float(cfg.get("max_reject_rate_xtquant", 0.01)))
        threshold = max(0.0, threshold)
        rate = (failed / attempted) if attempted > 0 else 0.0
        return rate <= threshold, rate, threshold

    @staticmethod
    def _date_range_empty(start: str, end: str) -> bool:
        """日期范围已追平时直接跳过，避免向适配器传入 start > end。"""
        try:
            return pd.Timestamp(start) > pd.Timestamp(end)
        except (ValueError, TypeError):
            return False

    def _max_date(self, df: pd.DataFrame, table: str) -> Optional[str]:
        """按 schema.time_key 计算本批最大业务日期，避免表名硬编码漂移。"""
        schema = self.aligner.schemas.get(table, {})
        time_col = schema.get("time_key") or "time"
        if time_col not in df.columns or len(df) == 0:
            return None
        max_val = df[time_col].max()
        try:
            return str(int(max_val))
        except (ValueError, TypeError):
            return None

    def _interruptible_sleep(self, sec: int):
        """可被 _running=False 中断的 sleep（每 5s 检查一次）"""
        for _ in range(sec // 5):
            if not self._running:
                break
            time.sleep(5)

    def _health_check(self):
        """健康检查：数据新鲜度。

        阈值按"缺 1 个完整交易日才算 stale"原则，支持三级 fallback：
            table 级 > freq 级 > 全局默认。
        配置示例（collector_tasks.json::health_check）::

            "stale_alert_hours": 30,                # 全局默认（日线：覆盖过夜 + daemon 17:00 调度窗口）
            "stale_alert_hours_by_freq": {
                "1min": 20                          # 分钟：15:00 收盘 → 次日 11:00 才报
            },
            "stale_alert_hours_by_table": {
                "fin_indicator": 2200,              # 季报：~90 天
                "balance_statement": 2200,
                "income_statement": 2200,
                "cashflow_statement": 2200,
                "stock_float_share": 720            # 流通股本：~30 天
            }

        缺省默认值 30h（向后兼容：旧 config 只有 stale_alert_hours=6 时仍按 6 取，
        不改变既有行为；新 config 显式提高到 30h 才启用分级阈值）。
        """
        hc = self.tasks_cfg.get("health_check", {})
        if not hc:
            return
        global_default = hc.get("stale_alert_hours", 30)
        by_freq = hc.get("stale_alert_hours_by_freq", {}) or {}
        by_table = hc.get("stale_alert_hours_by_table", {}) or {}
        for task in self.tasks_cfg.get("tasks", []):
            chain = self._resolve_source_chain(task)
            source = chain[0] if chain else task.get("source")
            if not source:
                continue
            table = task["table"]
            freq = task.get("freq", "daily")
            # 三级 fallback：table 级 > freq 级 > 全局默认
            if table in by_table:
                stale_hours = by_table[table]
            elif freq in by_freq:
                stale_hours = by_freq[freq]
            else:
                stale_hours = global_default
            last = self._get_safe_watermark(source, table, freq)
            if last:
                try:
                    last_dt = (pd.Timestamp(int(last), unit="ms", tz="UTC")
                               .tz_convert("Asia/Shanghai").tz_localize(None).to_pydatetime())
                except (TypeError, ValueError, OverflowError):
                    logger.warning(f"[HealthCheck] {source}/{table}/{freq} 非法水位: {last}")
                    continue
                age_h = (datetime.now() - last_dt).total_seconds() / 3600
                if age_h > stale_hours:
                    logger.warning(f"[HealthCheck] {source}/{table}/{freq} 数据过期 "
                                   f"(last={last}, age={age_h:.1f}h > {stale_hours}h)")


def _load_json(path) -> Dict:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_env(value):
    """把 ${ENV_VAR} 占位符替换为环境变量"""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def build_authority_rules(tasks_cfg: Dict) -> Dict:
    """从 collector_tasks 构建 authority_rules，供 quality audit 和 staging 使用。

    Returns: {table_name: {"authoritative_source": str, "allow_fallback": bool}}
    """
    rules = {}
    for task in tasks_cfg.get("tasks", []):
        if not isinstance(task, dict):
            continue
        auth = task.get("authoritative_source")
        if auth:
            rules[task["table"]] = {
                "authoritative_source": auth,
                "allow_fallback": task.get("allow_fallback", True),
            }
    return rules


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------
def _get_git_commit() -> str:
    """获取当前 git commit hash（启动日志打印，便于精确验收）。失败返回 'unknown'。"""
    try:
        import subprocess
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="QuantStudio 常驻采集进程")
    parser.add_argument("--mode", choices=["forever", "once"], default="forever",
                        help="forever=7×24常驻 / once=单次执行")
    parser.add_argument("--task", default=None, help="once 模式下指定任务名")
    parser.add_argument("--pull-mode", choices=["full_range", "incremental"],
                        default="incremental",
                        help="once mode range: full_range or incremental")
    parser.add_argument("--max-iter", type=int, default=None,
                        help="forever 模式下最大迭代次数（测试用）")
    parser.add_argument("--config-dir", default=str(ROOT / "config"),
                        help="配置目录")
    parser.add_argument("--instance-token", default=None,
                        help="v3: GUI 启动传入的实例 token（用于身份校验）。"
                             "CLI 手动启动可不传，daemon 自行生成。")
    parser.add_argument("--runtime-manifest", default=None, type=str,
                        help="Absolute path to write runtime manifest JSON after collector init (atomic write)")
    parser.add_argument("--runtime-nonce", default=None, type=str,
                        help="Nonce UUID to embed in runtime manifest for replay protection")
    # W2-0.8 缺陷 E：显式控制单任务后的全库审计。默认 full（生产/常驻语义不变）；
    # staging 分阶段装载用 none，避免尚未回填的兄弟目标表导致当前任务假失败。
    parser.add_argument("--quality-audit", choices=["full", "none"], default="full",
                        help="once 模式下任务后的全库质量审计：full(默认)|none")
    args = parser.parse_args()

    # v3 日志：TimedRotatingFileHandler（午夜轮转，保留 14 天）+ 控制台
    from logging.handlers import TimedRotatingFileHandler
    from quantstudio._paths import DATA_ROOT
    log_dir = DATA_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        log_dir / "daemon.log", when="midnight", backupCount=14, encoding="utf-8")
    file_handler.suffix = "%Y-%m-%d"
    stream_handler = logging.StreamHandler()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[stream_handler, file_handler])
    # Review FIX-5：GUI 启动场景（stdout/stderr 重定向到 bootstrap_{token}.log）下，
    # logging 初始化后移除 StreamHandler，避免 bootstrap 文件持续接收整份日志副本
    # 且无轮转能力。CLI 手动启动场景保留 StreamHandler（输出到终端）。
    # bootstrap 文件只捕获 basicConfig 之前的 import/启动崩溃，符合其设计目的。
    if args.instance_token and sys.platform == "win32":
        # GUI 启动：stdout 是 bootstrap 文件 fd
        if not sys.stdout.isatty():
            logging.getLogger().removeHandler(stream_handler)
            logger.info("[CLI] GUI 启动场景：已移除 StreamHandler，"
                        "后续日志仅写 daemon.log（bootstrap 文件停止增长）")

    cdir = Path(args.config_dir)

    if args.mode == "once":
        # once 模式：collector_run.lock 内 from_configs + run_once + close（不写 daemon status）
        from .daemon_lifecycle import CollectorRunLock
        logger.info(f"[CLI] 单次执行模式 task={args.task or 'ALL'}")
        try:
            with CollectorRunLock(timeout=30):  # once 模式给较长等锁时间
                collector = ResidentCollector.from_configs(
                    cdir / "data_config.json",
                    cdir / "sources_config.json",
                    cdir / "collector_tasks.json",
                    cdir / "alignment_rules.json")
                # Runtime manifest: atomic write after real component construction
                if args.runtime_manifest:
                    import os as _os
                    from datetime import datetime as _dt
                    try:
                        from quantstudio._paths import DATA_ROOT as _DATA_ROOT
                        from quantstudio.pipeline.daemon_lifecycle import (
                            collector_run_lock_path as _crlock,
                            daemon_lock_path as _dlock,
                            daemon_status_path as _dstatus,
                        )
                        _imported_data_root = str(_DATA_ROOT.resolve())
                        _collector_lock = str(_crlock().resolve())
                        _daemon_lock = str(_dlock().resolve())
                        _daemon_status = str(_dstatus().resolve())
                    except Exception:
                        _imported_data_root = ""
                        _collector_lock = ""
                        _daemon_lock = ""
                        _daemon_status = ""
                    _manifest = {
                        "format_version": "1.0",
                        "task": args.task,
                        "pid": _os.getpid(),
                        "nonce": args.runtime_nonce,
                        "created_at": _dt.now().isoformat(),
                        "QUANTSTUDIO_DATA_ROOT": _os.environ.get("QUANTSTUDIO_DATA_ROOT", ""),
                        "imported_DATA_ROOT": _imported_data_root,
                        "writer_db_path": str(collector.writer.db_path) if hasattr(collector, 'writer') else "",
                        "batch_audit_db_path": str(collector.batch_audit.db_path) if hasattr(collector, 'batch_audit') else "",
                        "quarantine_db_path": str(collector.quarantine.db_path) if hasattr(collector, 'quarantine') else "",
                        "daemon_log_path": str((_DATA_ROOT / "logs" / "daemon.log").resolve()) if _imported_data_root else "",
                        "collector_lock_path": _collector_lock,
                        "daemon_lock_path": _daemon_lock,
                        "daemon_status_path": _daemon_status,
                        "config_dir": args.config_dir or "",
                        "manifest_path": args.runtime_manifest,
                        "python_executable": sys.executable,
                    }
                    # Pre-write validation: all paths must be under DATA_ROOT
                    _staging_root_str = _os.environ.get("QUANTSTUDIO_DATA_ROOT", "")
                    if _staging_root_str:
                        _sr = Path(_staging_root_str).resolve()
                        for _k in ("writer_db_path", "batch_audit_db_path", "quarantine_db_path",
                                    "daemon_log_path", "collector_lock_path", "daemon_lock_path", "daemon_status_path"):
                            _v = _manifest.get(_k, "")
                            if _v:
                                try:
                                    Path(_v).resolve().relative_to(_sr)
                                except ValueError:
                                    logger.error(f"Runtime manifest BLOCK: {_k}={_v} not under staging root {_sr}")
                                    return 1
                    _tmp = args.runtime_manifest + ".tmp"
                    import json as _json
                    with open(_tmp, 'w', encoding='utf-8') as _f:
                        _json.dump(_manifest, _f, indent=2, ensure_ascii=False)
                    _os.replace(_tmp, args.runtime_manifest)
                    logger.info(f"Runtime manifest written: {args.runtime_manifest}")
                try:
                    result = collector.run_once(
                        task_name=args.task, mode=args.pull_mode,
                        quality_audit=args.quality_audit)
                finally:
                    collector.close()
            # W2-0.8 缺陷 D：CLI 退出码必须反映任务+审计结果，不能总返回 0。
            # task 不存在 / task failed / 启用了 audit 且 audit failed → exit 1。
            if not result["task_found"]:
                logger.error(f"[CLI] task not found: {args.task}")
                sys.exit(1)
            if not result["task_ok"]:
                logger.error(f"[CLI] task failed (see batch_audit ledger)")
                sys.exit(1)
            if result["audit_run"] and not result["audit_ok"]:
                logger.error(f"[CLI] quality audit failed")
                sys.exit(1)
            logger.info("[CLI] once 完成（task + audit 全部通过）")
        except Exception as e:
            if "collector_run" in str(e).lower() or "timeout" in str(e).lower():
                logger.error(f"[CLI] collector_run.lock 获取失败（daemon 或 GUI 正在采集）: {e}")
                sys.exit(1)
            else:
                raise
    else:
        # forever 模式：v3 走 DaemonLifecycle 轻量调度（不调用旧 run_forever）
        from .daemon_lifecycle import DaemonLifecycle
        # Review FIX-6：启动日志打印 git commit hash，便于精确验收
        git_commit = _get_git_commit()
        logger.info(f"[CLI] 常驻模式（v3 DaemonLifecycle）max_iter={args.max_iter} "
                    f"token={'provided' if args.instance_token else 'auto-generated'} "
                    f"git_commit={git_commit}")
        lifecycle = DaemonLifecycle(
            config_dir=cdir,
            instance_token=args.instance_token,
            max_iterations=args.max_iter)
        if not lifecycle.acquire_instance_lock():
            logger.error("[CLI] 另一个 daemon 已在运行，退出")
            sys.exit(1)
        try:
            # Review FIX-4：publish_status 可能因 alive_other/denied 抛 RuntimeError
            lifecycle.publish_status()
            lifecycle.run_forever()
        except RuntimeError as e:
            # publish_status 拒绝启动（alive_other / AccessDenied）
            logger.error(f"[CLI] 启动被拒绝: {e}")
            # 不清 status（不是自己的），仅释放 .daemon.lock
            lifecycle.release_instance_lock()
            sys.exit(1)
        except Exception:
            # 未预期异常：释放 .daemon.lock + 清自己的 status（如已发布）
            logger.exception("[CLI] daemon 异常退出")
            try:
                lifecycle.clear_own_status()
            except Exception:
                pass
            lifecycle.release_instance_lock()
            sys.exit(1)
        finally:
            # 正常退出路径：clear_own_status 已在 run_forever() 末尾调用；
            # 此处作为崩溃兜底（run_forever 抛未捕获异常时仍保证 status 清理）。
            try:
                lifecycle.clear_own_status()
            except Exception:
                pass
            lifecycle.release_instance_lock()


if __name__ == "__main__":
    main()
