"""QFQ 常驻增量事件驱动闭环 —— 共享类型、状态枚举与配置（resident orchestrator v2）

本模块是新增编排层（qfq_event_discovery / qfq_fresh_capture / qfq_resident_orchestrator /
qfq_orchestrator_cli）与既有 ``qfq_reanchor_engine`` / ``qfq_reanchor_schema`` 的**共享契约**：

- 状态枚举与 ``qfq_reanchor_schema`` 的合法状态集合（BACKFILL_STATUS / TRIGGER_STATUS /
  WATERMARK_INTENT_STATUS / CYCLE_PHASES / FRESH_CAPTURE_STATUS / OBSERVATION_CURSOR_STATUS）
  **逐字对齐**，是单一真相源的 Python 侧投影；
- 确定性 trigger_id / payload_hash 生成器（幂等、可重放）；
- ``QFQOrchestratorConfig``：从 ``collector_tasks.json`` 的 ``qfq_orchestrator`` 块加载，
  带默认值 + **fail-fast** 校验（价格源非 xtquant / 1min 未配置 / watermark policy 非法
  直接抛 ``QFQConfigError``）。

本模块不依赖 DuckDB/SQLite 连接，可独立单测。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# 状态集合直接复用 schema 单一真相源（避免两份清单漂移）
from quantstudio.pipeline.qfq_reanchor_schema import (  # noqa: F401
    BACKFILL_STATUS,
    TRIGGER_STATUS,
    WATERMARK_INTENT_STATUS,
    CYCLE_PHASES,
    CYCLE_STATUS,
    FRESH_CAPTURE_STATUS,
    OBSERVATION_CURSOR_STATUS,
    TRIGGER_TYPES,
    PRICE_TABLES,
    QFQ_ASSET_TYPES,
)


class QFQConfigError(Exception):
    """配置非法（config_lint / 加载时 fail-fast）。"""


# ---------------------------------------------------------------------------
# 状态枚举（str enum，与 schema 合法集合逐字一致）
# ---------------------------------------------------------------------------

class TriggerType(str, Enum):
    STOCK_DIVIDEND = "stock_dividend"
    STOCK_ADJ_FACTOR = "stock_adj_factor"
    ETF_FUND_ADJ = "etf_fund_adj"
    FACTOR_REVISION = "factor_revision"
    FACTOR_NEW = "factor_new"
    BOOTSTRAP = "bootstrap"


class TriggerStatus(str, Enum):
    SCHEDULED = "scheduled"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMMITTED = "committed"
    RETRYABLE_FAILED = "retryable_failed"
    BLOCKED = "blocked"
    DEAD_LETTER = "dead_letter"


class CyclePhase(str, Enum):
    STARTED = "started"
    RECOVERING = "recovering"
    OBSERVING = "observing"
    FETCHING = "fetching"
    APPLYING = "applying"
    GATING = "gating"
    FINALIZED = "finalized"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class WatermarkIntentStatus(str, Enum):
    PENDING = "pending"
    HELD = "held"
    COMMITTED = "committed"
    SUPERSEDED = "superseded"


class FreshCaptureStatus(str, Enum):
    CAPTURED = "captured"
    APPLIED = "applied"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# 确定性 ID / hash 生成器（幂等、可重放）
# ---------------------------------------------------------------------------

def trigger_id_of(asset_type: str, code: str, effective_date_ms: int,
                  detection_source: str, payload_hash: str) -> str:
    """确定性 trigger_id = sha1(asset_type|code|effective_date|detector|payload_hash)。

    相同语义事件永远生成同一 ID → 天然去重 + 崩溃可重放（崩溃后下一轮识别已存在 trigger）。
    """
    raw = f"{asset_type}|{code}|{int(effective_date_ms)}|{detection_source}|{payload_hash}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def payload_hash_of(fields: Sequence) -> str:
    """对影响语义的字段做确定性 sha256（字段须可 JSON 序列化或已归一为 str）。

    用于 stock_dividend 整行语义、adj_factor / fund_adj 因子值对、factor revision。
    """
    norm = []
    for f in fields:
        if f is None:
            norm.append("∅")
        elif isinstance(f, float):
            norm.append(repr(f))          # repr 保证跨进程稳定
        elif isinstance(f, (list, tuple)):
            norm.append("[" + ",".join(str(x) for x in f) + "]")
        else:
            norm.append(str(f))
    blob = "|".join(norm).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def capture_id_of(asset_type: str, code: str, run_id: str) -> str:
    """确定性 capture_id（同券同轮一次采集）。"""
    raw = f"{asset_type}|{code}|{run_id}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def event_id_of(trigger_id: str, attempt: int, capture_id: str) -> str:
    """引擎 event_id 基于 trigger/work + attempt + capture，崩溃可重放。"""
    raw = f"{trigger_id}|{int(attempt)}|{capture_id}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 数据记录（DB ↔ 对象映射）
# ---------------------------------------------------------------------------

@dataclass
class TriggerRecord:
    trigger_id: str
    asset_type: str
    code: str
    trigger_type: str
    detection_source: str
    source_key: Optional[str] = None
    effective_date: Optional[int] = None
    payload_hash: Optional[str] = None
    factor_old: Optional[float] = None
    factor_new: Optional[float] = None
    factor_revision: Optional[int] = None
    status: str = "pending"
    attempt_count: int = 0
    next_retry_at: Optional[str] = None
    claimed_by: Optional[str] = None
    claimed_at: Optional[str] = None
    last_event_id: Optional[str] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    # 非持久字段：本轮合并进来的其它相关事件日期（仅用于 fresh 拉取 ex_dates_ms）
    merged_effective_dates: List[int] = field(default_factory=list)

    COLS = [
        "trigger_id", "asset_type", "code", "trigger_type", "detection_source",
        "source_key", "effective_date", "payload_hash", "factor_old", "factor_new",
        "factor_revision", "status", "attempt_count", "next_retry_at", "claimed_by",
        "claimed_at", "last_event_id", "last_error", "created_at", "updated_at",
        "completed_at",
    ]

    def as_insert_params(self):
        return [
            getattr(self, c) for c in self.COLS
        ]

    @classmethod
    def from_row(cls, row) -> "TriggerRecord":
        cols = cls.COLS
        d = dict(zip(cols, row))
        return cls(**d)


@dataclass
class CycleRun:
    cycle_id: str
    business_date: Optional[int]
    trigger_surface: Optional[str]
    config_hash: Optional[str]
    schema_hash: Optional[str]
    phase: str
    discovered_count: int = 0
    executed_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    pending_count: int = 0
    status: str = "started"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class FreshCaptureRecord:
    capture_id: str
    asset_type: str
    code: str
    source: Optional[str] = None
    daily_range_start: Optional[int] = None
    daily_range_end: Optional[int] = None
    minute_range_start: Optional[int] = None
    minute_range_end: Optional[int] = None
    daily_row_count: Optional[int] = None
    minute_row_count: Optional[int] = None
    daily_min_time: Optional[int] = None
    daily_max_time: Optional[int] = None
    minute_min_time: Optional[int] = None
    minute_max_time: Optional[int] = None
    daily_sha256: Optional[str] = None
    minute_sha256: Optional[str] = None
    metadata_sha256: Optional[str] = None
    download_trace: Optional[str] = None
    status: str = "captured"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class WatermarkIntent:
    cycle_id: str
    source: str
    table_name: str
    freq: str
    old_watermark: Optional[str]
    candidate_watermark: Optional[str]
    status: str = "pending"
    hold_reason: Optional[str] = None
    committed_at: Optional[str] = None


@dataclass
class ReanchorOutcome:
    trigger_id: str
    asset_type: str
    code: str
    status: str          # committed / blocked / rolled_back / failed
    event_id: Optional[str] = None
    error: Optional[str] = None
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# 配置（collector_tasks.json 的 qfq_orchestrator 块）
# ---------------------------------------------------------------------------

# 缺省配置（enabled=False，安全默认；显式开启才进入协调周期）
DEFAULT_ORCHESTRATOR_CFG: Dict = {
    "enabled": False,
    "factor_refresh_enabled": False,
    "require_bootstrap": True,
    "price_source": "xtquant",
    "stock_factor_detector": "tushare_adj_factor",
    "etf_factor_detector": "tushare_fund_adj",
    "freqs": ["1min"],
    "factor_overlap_lookback_days": 30,
    "fresh_fetch_workers": 4,
    "claim_lease_sec": 600,
    "retry_max": 5,
    "retry_backoff_sec": [60, 120, 240, 480, 960],
    "dead_letter_policy": {"max_attempts": 8},
    "watermark_policy": "hold_until_consistent",
    "quality_thresholds": {
        "tick_error_abs": 1e-9,
        "tick_error_rel": 1e-6,
        "front_exact_match": True,
        "raw_unchanged": True,
        "row_conservation": True,
        "coverage_min": 0.0,
        "pending_sla_hours": 72,
        "dead_letter_max": 0,
    },
    "bootstrap": {"batch_size": 50, "max_parallel_securities": 8},
}


@dataclass
class QFQOrchestratorConfig:
    enabled: bool = False
    factor_refresh_enabled: bool = False  # 任务2.2：主动因子刷新（默认关闭，独立 opt-in）
    require_bootstrap: bool = True
    price_source: str = "xtquant"
    stock_factor_detector: str = "tushare_adj_factor"
    etf_factor_detector: str = "tushare_fund_adj"
    freqs: List[str] = field(default_factory=lambda: ["1min"])
    factor_overlap_lookback_days: int = 30
    fresh_fetch_workers: int = 4
    claim_lease_sec: int = 600
    retry_max: int = 5
    retry_backoff_sec: List[int] = field(default_factory=lambda: [60, 120, 240, 480, 960])
    dead_letter_max_attempts: int = 8
    watermark_policy: str = "hold_until_consistent"
    quality_thresholds: Dict = field(default_factory=dict)
    bootstrap_batch_size: int = 50
    bootstrap_max_parallel: int = 8
    raw: Dict = field(default_factory=dict)

    # —— 四张协调价格表（启用编排器时其水位延迟提交）——
    COORDINATED_PRICE_TABLES = frozenset(PRICE_TABLES)

    @classmethod
    def load(cls, config_dir: Optional[str | Path] = None,
             raw: Optional[Dict] = None) -> "QFQOrchestratorConfig":
        """从 ``collector_tasks.json`` 的 ``qfq_orchestrator`` 块加载；缺省合并。

        Args:
            config_dir: 含 collector_tasks.json 的目录；None 表示仅用 raw/缺省。
            raw: 直接传入的配置 dict（优先于文件）；便于测试与 CLI 注入。
        """
        cfg = dict(DEFAULT_ORCHESTRATOR_CFG)
        if config_dir is not None:
            p = Path(config_dir) / "collector_tasks.json"
            if p.exists():
                try:
                    full = json.loads(p.read_text(encoding="utf-8"))
                    file_cfg = full.get("qfq_orchestrator", {})
                    cfg.update(file_cfg)
                except Exception as e:  # pragma: no cover
                    raise QFQConfigError(f"读取 collector_tasks.json 失败: {e}") from e
        if raw:
            cfg.update(raw)
        return cls.from_dict(cfg)

    @classmethod
    def from_dict(cls, cfg: Dict) -> "QFQOrchestratorConfig":
        dl = cfg.get("dead_letter_policy", {}) or {}
        bs = cfg.get("bootstrap", {}) or {}
        qt = cfg.get("quality_thresholds", {}) or {}
        obj = cls(
            enabled=bool(cfg.get("enabled", False)),
            factor_refresh_enabled=bool(cfg.get("factor_refresh_enabled", False)),
            require_bootstrap=bool(cfg.get("require_bootstrap", True)),
            price_source=str(cfg.get("price_source", "xtquant")),
            stock_factor_detector=str(cfg.get("stock_factor_detector", "tushare_adj_factor")),
            etf_factor_detector=str(cfg.get("etf_factor_detector", "tushare_fund_adj")),
            freqs=list(cfg.get("freqs", ["1min"])),
            factor_overlap_lookback_days=int(cfg.get("factor_overlap_lookback_days", 30)),
            fresh_fetch_workers=int(cfg.get("fresh_fetch_workers", 4)),
            claim_lease_sec=int(cfg.get("claim_lease_sec", 600)),
            retry_max=int(cfg.get("retry_max", 5)),
            retry_backoff_sec=list(cfg.get("retry_backoff_sec", [60, 120, 240, 480, 960])),
            dead_letter_max_attempts=int(dl.get("max_attempts", 8)),
            watermark_policy=str(cfg.get("watermark_policy", "hold_until_consistent")),
            quality_thresholds=dict(qt),
            bootstrap_batch_size=int(bs.get("batch_size", 50)),
            bootstrap_max_parallel=int(bs.get("max_parallel_securities", 8)),
            raw=dict(cfg),
        )
        obj.validate()
        return obj

    def validate(self) -> None:
        """fail-fast 校验（config_lint 复用同一逻辑）。"""
        if self.price_source not in ("xtquant", "mcp"):
            raise QFQConfigError(
                f"qfq_orchestrator.price_source 必须为 xtquant 或 mcp（价格修正源合法源），"
                f"收到 {self.price_source!r}")
        if "1min" not in self.freqs:
            raise QFQConfigError(
                f"qfq_orchestrator.freqs 必须含 '1min'（当前正式频率），收到 {self.freqs!r}")
        for f in self.freqs:
            if f not in ("1min", "1m", "daily"):
                raise QFQConfigError(f"qfq_orchestrator.freqs 含非法频率 {f!r}")
        if self.watermark_policy not in ("hold_until_consistent",):
            raise QFQConfigError(
                f"qfq_orchestrator.watermark_policy 仅允许 'hold_until_consistent'（禁止 "
                f"advance_with_pending），收到 {self.watermark_policy!r}")
        if self.fresh_fetch_workers < 1:
            raise QFQConfigError("qfq_orchestrator.fresh_fetch_workers 必须 >= 1")
        if self.retry_max < 1:
            raise QFQConfigError("qfq_orchestrator.retry_max 必须 >= 1")
        if self.claim_lease_sec < 1:
            raise QFQConfigError("qfq_orchestrator.claim_lease_sec 必须 >= 1")

    def can_coordinate_watermark(self, table: str) -> bool:
        """该表是否在本轮协调周期内延迟提交水位（四张价格表 + 启用）。"""
        return self.enabled and table in self.COORDINATED_PRICE_TABLES
