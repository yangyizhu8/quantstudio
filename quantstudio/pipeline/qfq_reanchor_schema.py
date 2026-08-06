"""QFQ 重锚（re-anchor）子系统 —— DDL 初始化模块（第一批基础设施）

本模块是 QFQ 重锚子系统全部**新表**的唯一 DDL 真相源。它与既有
``qfq_maintenance.py`` / ``qfq_revision.py`` 完全独立、不改动它们任何行为：

- ``qfq_maintenance.QFQMaintenance`` 继续管理 ``qfq_aux.db`` 的
  ``adj_factor`` / ``qfq_jump_audit`` / ``adj_factor_snapshot``（旧路径原样保留）。
- ``qfq_revision.RevisionStore`` 继续管理 ETF-only 的
  ``qfq_revision_run/observation/event``（既有 CI 门禁原样运行）。
- 本模块新增的 ``qfq_factor_observation``（版本化、保留旧行，PK 含 revision_no）
  是股票+ETF 生产 revision 门禁的权威表，与上述 ETF-only 三表**并存不合并**。

表位置分层（设计 v3 §3.2 / v5 §1.5）：
- **DuckDB 主库**（``data/quantstudio.db``，与 ``source_watermark`` / 价格表同库）：
  ``qfq_anchor_state`` / ``qfq_reanchor_event`` / ``qfq_pending_backfill`` /
  ``qfq_bootstrap_run`` / ``qfq_bootstrap_item`` / ``trade_calendar``。
  价格修正 + 状态更新 + 欠账 + 水位推进必须同一 DuckDB 事务，故这些表须与
  价格表同库（原子性硬要求）。
- **SQLite 辅助库**（``data/qfq_aux.db``，与 adj_factor / revision 辅助表同库）：
  ``qfq_factor_observation`` / ``qfq_factor_revision_alert`` /
  ``qfq_deep_audit_cursor`` / ``qfq_deep_audit_item``。
  纯检测辅助数据（量大、不参与价格原子事务）；revision 判定**结果**
  （anchor status='blocked_revision'）才写 DuckDB（独立短事务）。

统一口径（贯穿全子系统）：
- ``code`` = canonical 裸码（无市场后缀）。
- 时间列（``factor_time`` / ``factor_date`` / ``range_*`` / ``cal_date`` / ``*_ex_date``）
  = epoch-ms BIGINT，与 canonical ``time`` 口径一致。
- ``rows_*`` 行数列一律 BIGINT（正式库分钟 ≈7,025 万行量级）。
- SQLite 侧 ``*_at`` 时间戳为 ISO 字符串；DuckDB 侧为 TIMESTAMP。

DDL 版本对照：v3 §5.1/5.2/3.2/4.3 基线 + v4 §1.1/2.2/3.1/3.2/7（event_type、
pending_backfill、bootstrap_item、revision_alert、deep_audit_cursor）+
v5 §1.4/3.2/5.1/5.2（trade_calendar、deep_audit_item、occurrence_count/
cycle_business_date/first_seen_at/last_seen_at、last_stale_probe_*/probe_fail_count）。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from quantstudio._paths import db_path

# QFQ 辅助库文件名（与 qfq_maintenance.py 口径一致）
QFQ_AUX_DB_NAME = "qfq_aux.db"

SCHEMA_VERSION = "reanchor-2.1"  # v2.4 B-3：从 2.0 升级，触发世代隔离 schema 变更（trigger_id_version 列 + 世代列 + 新表）

# 任务6.3：detector 基线版本（bootstrap 建基线时的版本标识；变更需重做 bootstrap）
DETECTOR_BASELINE_VERSION = "v1"

# —— 资产类型白名单（与 qfq_observation / writers 共用，单一真相源）——
QFQ_ASSET_TYPES = frozenset({"STOCK", "ETF"})

# —— 四张价格表白名单（pending backfill 的 table_name 必须属于此集合）——
PRICE_TABLES = frozenset({
    "stock_daily", "stock_minutes", "etf_daily", "etf_minutes",
})

# —— pending backfill 合法状态集合（含 dead_letter，编排器死信）——
BACKFILL_STATUS = frozenset({
    "pending", "resolved", "in_progress", "blocked", "retryable_failed",
    "dead_letter",
})

# —— cycle run 阶段 / 状态（phase 与 status 同词汇）——
CYCLE_PHASES = frozenset({
    "started", "recovering", "observing", "fetching", "applying",
    "gating", "finalized", "interrupted", "failed",
})
CYCLE_STATUS = frozenset(CYCLE_PHASES)

# —— trigger queue 合法状态集合（v2.4 B-3：加 superseded 用于历史 xtquant trigger 退役）——
TRIGGER_STATUS = frozenset({
    "scheduled", "pending", "in_progress", "committed",
    "retryable_failed", "blocked", "dead_letter",
    "superseded",  # v2.4：legacy xtquant trigger 退役终态（可审计，不进 MCP gate）
})

# —— watermark intent 合法状态集合 ——
WATERMARK_INTENT_STATUS = frozenset({
    "pending", "held", "committed", "superseded",
})

# —— fresh capture 合法状态集合 ——
FRESH_CAPTURE_STATUS = frozenset({
    "captured", "applied", "failed",
})

# —— observation cursor 合法状态集合 ——
OBSERVATION_CURSOR_STATUS = frozenset({
    "ok", "failed",
})

# —— trigger type 合法集合 ——
TRIGGER_TYPES = frozenset({
    "stock_dividend", "stock_adj_factor", "etf_fund_adj",
    "factor_revision", "factor_new", "bootstrap",
})

# —— asset_type ↔ 价格表 关联白名单（pending backfill 关联契约，阻断 3）——
ASSET_TABLE_MAP: Dict[str, frozenset] = {
    "STOCK": frozenset({"stock_daily", "stock_minutes"}),
    "ETF": frozenset({"etf_daily", "etf_minutes"}),
}

# —— epoch-ms 合理区间（与 qfq_observation 共用，单一真相源）——
# 2000-01-01 ~ 2100-01-01 Asia/Shanghai；落到此区间外的时刻视为非法（未来/历史异常）。
_MIN_EPOCH_MS = 946_684_800_000   # 2000-01-01 00:00:00 +08
_MAX_EPOCH_MS = 4_102_444_800_000  # 2100-01-01 00:00:00 +08


def _validate_epoch_ms(ms) -> int:
    """校验 epoch-ms（时间列统一口径）必须有效整数且落入 [2000, 2100] 合理区间。

    拒绝：None / 非整数 / 区间外。返回 int。供 pending backfill 的 range_start/end
    与 observation 的 factor_time 共用（阻断 2/3：非法时刻会污染欠账队列与审计语义）。
    """
    try:
        v = int(ms)
    except (TypeError, ValueError):
        raise ValueError(f"epoch-ms 非法（非整数）: {ms!r}")
    if v < _MIN_EPOCH_MS or v > _MAX_EPOCH_MS:
        raise ValueError(
            f"epoch-ms 超出合理区间[{_MIN_EPOCH_MS},{_MAX_EPOCH_MS}]（2000~2100）: {v!r}")
    return v


def _normalize_asset_type(asset_type: str) -> str:
    """归一化 asset_type → ``STOCK`` / ``ETF``；其它值抛 ValueError。

    接受大小写不敏感输入（"stock"/"Stock"/"STOCK" → "STOCK"），拒绝任何非
    STOCK/ETF 的取值（"bond"/"index"/"NONE"/None 等）。
    """
    if asset_type is None:
        raise ValueError("asset_type 不能为 None")
    t = str(asset_type).strip().upper()
    if t not in QFQ_ASSET_TYPES:
        raise ValueError(f"非法 asset_type: {asset_type!r}（仅允许 {sorted(QFQ_ASSET_TYPES)}）")
    return t


def _normalize_code(code: str) -> str:
    """归一化 code → canonical 裸 6 位码；拒绝非法码。

    拒绝：None / 空串 / "NONE"（大小写不敏感）/ 非 6 位纯数字（含市场后缀如
    "sh600000"、位数不符的 "60000" / "6000000"）。
    """
    if code is None:
        raise ValueError("code 不能为 None")
    c = str(code).strip()
    if c.upper() == "NONE" or c == "":
        raise ValueError(f"非法 code（空/NONE）: {code!r}")
    if not (len(c) == 6 and c.isdigit()):
        raise ValueError(f"非法 code（非裸 6 位纯数字）: {code!r}")
    return c


def aux_db_path(main_db: Optional[str | Path] = None) -> Path:
    """由主库路径推导 qfq_aux.db 路径（与 qfq_maintenance.QFQMaintenance 同规则）。

    规则（阻断 3 修复）：
    - 若主库文件名本身已是 ``qfq_aux.db`` → 原样返回（调用方显式指定了辅助库名）。
    - 其它任何主库路径（含自定义 DuckDB 名如 ``custom.duckdb`` / ``research.db`` /
      ``qs_test.db``，以及默认 ``quantstudio.db``）→ 一律派生到**同目录**的
      ``qfq_aux.db``。不再按文件名特判。
    - 若调用方确实想用其它辅助库文件名，应通过 ``init_all_from_paths(aux_db=...)``
      显式传入，不能靠主库文件名隐式复用。
    """
    p = Path(main_db) if main_db is not None else db_path()
    if p.name == QFQ_AUX_DB_NAME:
        return p
    return p.parent / QFQ_AUX_DB_NAME


# ---------------------------------------------------------------------------
# DuckDB 主库 DDL（价格原子事务同库）
# ---------------------------------------------------------------------------

DDL_DUCKDB: Dict[str, str] = {
    # ===========================================================================
    # v2.4 B-3a：完整 2.1 schema（世代隔离）。本字典是 QFQ 重锚主库 DDL 的真相源，
    # 必须与 qfq_schema_contracts.TARGET_QFQ_2_1_FINGERPRINT 逐字一致（机械测试保证）。
    # 4 张需 v2 swap 的表在新空库直接采用最终 2.1 PK（不走 swap；swap 是 B-3b 存量迁移）。
    # 所有 B-3 新列在 target 均 NOT NULL 无业务 DEFAULT（新空库无存量行；存量回填属 B-3b）。
    # ===========================================================================
    # ---- anchor 状态：+ source_generation，PK 扩 4 列 ----
    "qfq_anchor_state": """
        CREATE TABLE IF NOT EXISTS qfq_anchor_state (
            asset_type                  VARCHAR   NOT NULL,
            code                        VARCHAR   NOT NULL,
            price_source                VARCHAR   NOT NULL,
            source_generation           VARCHAR   NOT NULL,
            detection_source            VARCHAR,
            detection_anchor_factor     DOUBLE,
            factor_date                 BIGINT,
            anchor_version              BIGINT,
            status                      VARCHAR   NOT NULL,
            locked_detection_factor     DOUBLE,
            locked_price_anchor_version BIGINT,
            last_ex_date                BIGINT,
            last_event_id               VARCHAR,
            retry_count                 INTEGER   DEFAULT 0,
            last_stale_probe_at         TIMESTAMP,
            last_stale_probe_error      VARCHAR,
            probe_fail_count            INTEGER   DEFAULT 0,
            updated_at                  TIMESTAMP NOT NULL,
            PRIMARY KEY (asset_type, code, price_source, source_generation)
        )""",
    # ---- 重锚事件：+ source_generation/cutover_id ----
    "qfq_reanchor_event": """
        CREATE TABLE IF NOT EXISTS qfq_reanchor_event (
            event_id            VARCHAR NOT NULL,
            event_type          VARCHAR NOT NULL,
            asset_type          VARCHAR NOT NULL,
            code                VARCHAR,
            price_source        VARCHAR,
            source_generation   VARCHAR NOT NULL,
            cutover_id          VARCHAR NOT NULL,
            detection_source    VARCHAR,
            old_factor          DOUBLE,
            new_factor          DOUBLE,
            old_factor_date     BIGINT,
            new_factor_date     BIGINT,
            daily_method        VARCHAR,
            minute_ratio_plan   VARCHAR,
            xtquant_price_ratio DOUBLE,
            ratio_dispersion    DOUBLE,
            ratio_cluster_count INTEGER,
            golden_check        VARCHAR,
            status              VARCHAR NOT NULL,
            block_reason        VARCHAR,
            error               VARCHAR,
            precheck_summary    VARCHAR,
            postcheck_summary   VARCHAR,
            rows_detail         VARCHAR,
            rows_stock_daily    BIGINT,
            rows_stock_minutes  BIGINT,
            rows_etf_daily      BIGINT,
            rows_etf_minutes    BIGINT,
            collection_mode     VARCHAR,
            trigger_surface     VARCHAR,
            bootstrap_run_id    VARCHAR,
            cycle_business_date BIGINT,
            occurrence_count    BIGINT DEFAULT 1,
            started_at          TIMESTAMP,
            finished_at         TIMESTAMP,
            created_at          TIMESTAMP NOT NULL,
            first_seen_at       TIMESTAMP NOT NULL,
            last_seen_at        TIMESTAMP NOT NULL,
            PRIMARY KEY (event_id)
        )""",
    # ---- 被过滤证券精确欠账：+ price_source/source_generation，PK 扩 8 列 ----
    "qfq_pending_backfill": """
        CREATE TABLE IF NOT EXISTS qfq_pending_backfill (
            asset_type      VARCHAR NOT NULL,
            code            VARCHAR NOT NULL,
            table_name      VARCHAR NOT NULL,
            freq            VARCHAR NOT NULL,
            range_start     BIGINT  NOT NULL,
            range_end       BIGINT  NOT NULL,
            price_source    VARCHAR NOT NULL,
            source_generation VARCHAR NOT NULL,
            reason          VARCHAR NOT NULL,
            anchor_version  BIGINT,
            status          VARCHAR NOT NULL,
            attempt_count   INTEGER DEFAULT 0,
            last_error      VARCHAR,
            trigger_id      VARCHAR,
            last_event_id   VARCHAR,
            next_retry_at   TIMESTAMP,
            claimed_by      VARCHAR,
            claimed_at      TIMESTAMP,
            dead_letter_at  TIMESTAMP,
            created_at      TIMESTAMP NOT NULL,
            updated_at      TIMESTAMP NOT NULL,
            resolved_at     TIMESTAMP,
            PRIMARY KEY (asset_type, code, table_name, freq, range_start, range_end,
                         price_source, source_generation)
        )""",
    # ---- bootstrap 运行：+ price_source/source_generation/cutover_id ----
    "qfq_bootstrap_run": """
        CREATE TABLE IF NOT EXISTS qfq_bootstrap_run (
            bootstrap_run_id  VARCHAR NOT NULL,
            asset_type        VARCHAR,
            params            VARCHAR,
            resume_cursor     VARCHAR,
            total_count       BIGINT,
            completed_count   BIGINT,
            blocked_count     BIGINT,
            failed_count      BIGINT,
            status            VARCHAR,
            schema_version    VARCHAR,
            config_hash       VARCHAR,
            baseline_version  VARCHAR,
            price_source      VARCHAR NOT NULL,
            source_generation VARCHAR NOT NULL,
            cutover_id        VARCHAR NOT NULL,
            started_at        TIMESTAMP,
            updated_at        TIMESTAMP,
            PRIMARY KEY (bootstrap_run_id)
        )""",
    # ---- bootstrap 证券级状态机（不变）----
    "qfq_bootstrap_item": """
        CREATE TABLE IF NOT EXISTS qfq_bootstrap_item (
            bootstrap_run_id VARCHAR NOT NULL,
            asset_type       VARCHAR NOT NULL,
            code             VARCHAR NOT NULL,
            status           VARCHAR NOT NULL,
            attempt_count    INTEGER DEFAULT 0,
            block_reason     VARCHAR,
            last_error       VARCHAR,
            started_at       TIMESTAMP,
            finished_at      TIMESTAMP,
            updated_at       TIMESTAMP NOT NULL,
            PRIMARY KEY (bootstrap_run_id, asset_type, code)
        )""",
    # ---- 交易日历持久缓存（B-3 不涉及；P1-3：列顺序=正式库实际 introspection 顺序）----
    "trade_calendar": """
        CREATE TABLE IF NOT EXISTS trade_calendar (
            cal_date      BIGINT  NOT NULL,
            is_open       BOOLEAN NOT NULL,
            source        VARCHAR,
            updated_at    TIMESTAMP,
            exchange      VARCHAR,
            pretrade_date BIGINT,
            PRIMARY KEY (cal_date)
        )""",
    # ---- 编排器每轮运行：+ price_source/source_generation/cutover_id ----
    "qfq_cycle_run": """
        CREATE TABLE IF NOT EXISTS qfq_cycle_run (
            cycle_id          VARCHAR   NOT NULL,
            business_date     BIGINT,
            trigger_surface   VARCHAR,
            config_hash       VARCHAR,
            schema_hash       VARCHAR,
            phase             VARCHAR   NOT NULL,
            discovered_count  BIGINT    DEFAULT 0,
            executed_count    BIGINT    DEFAULT 0,
            success_count     BIGINT    DEFAULT 0,
            failed_count      BIGINT    DEFAULT 0,
            pending_count     BIGINT    DEFAULT 0,
            status            VARCHAR   NOT NULL,
            started_at        TIMESTAMP NOT NULL,
            finished_at       TIMESTAMP,
            error             VARCHAR,
            detector_degraded BOOLEAN   DEFAULT 0,
            price_source      VARCHAR   NOT NULL,
            source_generation VARCHAR   NOT NULL,
            cutover_id        VARCHAR   NOT NULL,
            updated_at        TIMESTAMP NOT NULL,
            PRIMARY KEY (cycle_id)
        )""",
    # ---- 持久化 trigger 队列：+ 6 列（trigger_id_version/price_source/source_generation/cutover_id/retired_at/retire_reason）----
    "qfq_trigger_queue": """
        CREATE TABLE IF NOT EXISTS qfq_trigger_queue (
            trigger_id        VARCHAR   NOT NULL,
            asset_type        VARCHAR   NOT NULL,
            code              VARCHAR   NOT NULL,
            trigger_type      VARCHAR   NOT NULL,
            detection_source  VARCHAR   NOT NULL,
            source_key        VARCHAR,
            effective_date    BIGINT,
            payload_hash      VARCHAR,
            factor_old        DOUBLE,
            factor_new        DOUBLE,
            factor_revision   BIGINT,
            status            VARCHAR   NOT NULL,
            attempt_count     INTEGER   DEFAULT 0,
            next_retry_at     TIMESTAMP,
            claimed_by        VARCHAR,
            claimed_at        TIMESTAMP,
            last_event_id     VARCHAR,
            last_error        VARCHAR,
            dead_letter_at    TIMESTAMP,
            trigger_id_version INTEGER  NOT NULL,
            price_source      VARCHAR   NOT NULL,
            source_generation VARCHAR   NOT NULL,
            cutover_id        VARCHAR   NOT NULL,
            retired_at        TIMESTAMP,
            retire_reason     VARCHAR,
            created_at        TIMESTAMP NOT NULL,
            updated_at        TIMESTAMP NOT NULL,
            completed_at      TIMESTAMP,
            PRIMARY KEY (trigger_id)
        )""",
    # ---- 四价格表延迟水位：+ source_generation/cutover_id，PK 扩 6 列 ----
    "qfq_watermark_intent": """
        CREATE TABLE IF NOT EXISTS qfq_watermark_intent (
            cycle_id            VARCHAR   NOT NULL,
            source              VARCHAR   NOT NULL,
            table_name          VARCHAR   NOT NULL,
            freq                VARCHAR   NOT NULL,
            source_generation   VARCHAR   NOT NULL,
            cutover_id          VARCHAR   NOT NULL,
            old_watermark       VARCHAR,
            candidate_watermark VARCHAR,
            status              VARCHAR   NOT NULL,
            hold_reason         VARCHAR,
            committed_at        TIMESTAMP,
            PRIMARY KEY (cycle_id, source, table_name, freq, source_generation, cutover_id)
        )""",
    # ---- fresh 采集证据固化：+ source_generation/cutover_id ----
    "qfq_fresh_capture": """
        CREATE TABLE IF NOT EXISTS qfq_fresh_capture (
            capture_id          VARCHAR   NOT NULL,
            asset_type          VARCHAR   NOT NULL,
            code                VARCHAR   NOT NULL,
            source              VARCHAR,
            daily_range_start   BIGINT,
            daily_range_end     BIGINT,
            minute_range_start  BIGINT,
            minute_range_end    BIGINT,
            daily_row_count     BIGINT,
            minute_row_count    BIGINT,
            daily_min_time      BIGINT,
            daily_max_time      BIGINT,
            minute_min_time     BIGINT,
            minute_max_time     BIGINT,
            daily_sha256        VARCHAR,
            minute_sha256       VARCHAR,
            metadata_sha256     VARCHAR,
            download_trace      VARCHAR,
            status              VARCHAR,
            source_generation   VARCHAR   NOT NULL,
            cutover_id          VARCHAR   NOT NULL,
            created_at          TIMESTAMP NOT NULL,
            updated_at          TIMESTAMP NOT NULL,
            PRIMARY KEY (capture_id)
        )""",
    # ---- 检测游标：+ price_source/source_generation，PK 扩 4 列 ----
    "qfq_observation_cursor": """
        CREATE TABLE IF NOT EXISTS qfq_observation_cursor (
            detector_name     VARCHAR   NOT NULL,
            asset_type        VARCHAR   NOT NULL,
            price_source      VARCHAR   NOT NULL,
            source_generation VARCHAR   NOT NULL,
            cursor_as_of      BIGINT,
            last_run_id       VARCHAR,
            scan_range_start  BIGINT,
            scan_range_end    BIGINT,
            status            VARCHAR,
            last_error        VARCHAR,
            updated_at        TIMESTAMP NOT NULL,
            PRIMARY KEY (detector_name, asset_type, price_source, source_generation)
        )""",
    # ============================ B-3 新表 ============================
    # ---- discovery baseline ledger（§3.2.3）：generation-specific 防洪水基线 ----
    "qfq_discovery_baseline": """
        CREATE TABLE IF NOT EXISTS qfq_discovery_baseline (
            cutover_id           VARCHAR NOT NULL,
            price_source         VARCHAR NOT NULL,
            source_generation    VARCHAR NOT NULL,
            event_logical_key    VARCHAR NOT NULL,
            applied_payload_hash VARCHAR,
            pending_trigger_id   VARCHAR,
            pending_payload_hash VARCHAR,
            last_trigger_id      VARCHAR,
            applied_at           TIMESTAMP,
            baselined_at         TIMESTAMP NOT NULL,
            updated_at           TIMESTAMP NOT NULL,
            PRIMARY KEY (cutover_id, event_logical_key)
        )""",
    # ---- source cutover 状态机主表（§3.4）----
    "qfq_source_cutover": """
        CREATE TABLE IF NOT EXISTS qfq_source_cutover (
            cutover_id              VARCHAR NOT NULL,
            price_source            VARCHAR NOT NULL,
            source_generation       VARCHAR NOT NULL,
            cutover_time            TIMESTAMP NOT NULL,
            price_snapshot_version  VARCHAR,
            factor_snapshot_version VARCHAR,
            baseline_version        VARCHAR NOT NULL,
            schema_version          VARCHAR NOT NULL,
            config_hash             VARCHAR,
            aux_db_path             VARCHAR,
            status                  VARCHAR NOT NULL,
            evidence_path           VARCHAR,
            created_at              TIMESTAMP NOT NULL,
            updated_at              TIMESTAMP NOT NULL,
            PRIMARY KEY (cutover_id)
        )""",
    # ---- active cutover 指针表（§3.4：独立表替代 partial unique）----
    "qfq_active_cutover": """
        CREATE TABLE IF NOT EXISTS qfq_active_cutover (
            price_source  VARCHAR NOT NULL,
            cutover_id    VARCHAR NOT NULL,
            activated_at  TIMESTAMP NOT NULL,
            PRIMARY KEY (price_source),
            FOREIGN KEY (cutover_id) REFERENCES qfq_source_cutover(cutover_id)
        )""",
    # ---- 单活 cycle lease（§4.3：CAS 防并发 cycle）----
    "qfq_cycle_lease": """
        CREATE TABLE IF NOT EXISTS qfq_cycle_lease (
            price_source       VARCHAR NOT NULL,
            source_generation  VARCHAR NOT NULL,
            cycle_id           VARCHAR NOT NULL,
            owner_pid          BIGINT NOT NULL,
            owner_cmdline_hash VARCHAR NOT NULL,
            acquired_at        TIMESTAMP NOT NULL,
            expires_at         TIMESTAMP NOT NULL,
            PRIMARY KEY (price_source, source_generation)
        )""",
}


# ---------------------------------------------------------------------------
# SQLite 辅助库 DDL（qfq_aux.db，检测辅助 / outbox / 深审队列）
# ---------------------------------------------------------------------------

DDL_SQLITE: Dict[str, str] = {
    # ---- 版本化因子观察（v3 §3.2）：同键值变化 +1 新行、旧行保留（PK 含 revision_no）----
    "qfq_factor_observation": """
        CREATE TABLE IF NOT EXISTS qfq_factor_observation (
            asset_type        TEXT    NOT NULL,
            code              TEXT    NOT NULL,
            factor_time       INTEGER NOT NULL,
            factor_value      REAL    NOT NULL,
            revision_no       INTEGER NOT NULL,
            first_seen_run_id TEXT    NOT NULL,
            last_seen_run_id  TEXT    NOT NULL,
            first_seen_at     TEXT    NOT NULL,
            last_seen_at      TEXT    NOT NULL,
            PRIMARY KEY (asset_type, code, factor_time, revision_no)
        )""",
    # ---- revision alert outbox（v4 §3.1）：与 observation 同 SQLite 事务写入 ----
    "qfq_factor_revision_alert": """
        CREATE TABLE IF NOT EXISTS qfq_factor_revision_alert (
            alert_id          TEXT PRIMARY KEY,
            asset_type        TEXT    NOT NULL,
            code              TEXT    NOT NULL,
            factor_time       INTEGER NOT NULL,
            revision_no       INTEGER NOT NULL,
            status            TEXT    NOT NULL,
            first_seen_run_id TEXT    NOT NULL,
            created_at        TEXT    NOT NULL,
            acknowledged_at   TEXT
        )""",
    # ---- deep audit 环形游标（v4 §3.2b）----
    "qfq_deep_audit_cursor": """
        CREATE TABLE IF NOT EXISTS qfq_deep_audit_cursor (
            asset_type  TEXT PRIMARY KEY,
            cursor_code TEXT    NOT NULL,
            round_no    INTEGER NOT NULL,
            updated_at  TEXT    NOT NULL
        )""",
    # ---- deep audit 失败重试队列（v5 §3.2）----
    "qfq_deep_audit_item": """
        CREATE TABLE IF NOT EXISTS qfq_deep_audit_item (
            asset_type    TEXT    NOT NULL,
            code          TEXT    NOT NULL,
            status        TEXT    NOT NULL,
            round_no      INTEGER NOT NULL,
            attempt_count INTEGER DEFAULT 0,
            last_error    TEXT,
            updated_at    TEXT    NOT NULL,
            PRIMARY KEY (asset_type, code, round_no)
        )""",
    # ---- 股票复权因子表（仅股票，裸码口径；结构与 fund_adj 完全一致）----
    "adj_factor": """
        CREATE TABLE IF NOT EXISTS adj_factor (
            code TEXT, time INTEGER, adj_factor REAL,
            PRIMARY KEY (code, time)
        )""",
    # ---- ETF/基金复权因子表（仅 ETF，裸码口径；与 adj_factor 结构完全一致）----
    "fund_adj": """
        CREATE TABLE IF NOT EXISTS fund_adj (
            code TEXT, time INTEGER, adj_factor REAL,
            PRIMARY KEY (code, time)
        )""",
}


# ---------------------------------------------------------------------------
# 列清单（供列迁移 helper 使用；顺序与 DDL 一致）
# ---------------------------------------------------------------------------

DUCKDB_COLS: Dict[str, List[str]] = {
    "qfq_anchor_state": [
        "asset_type", "code", "price_source", "source_generation",
        "detection_source", "detection_anchor_factor", "factor_date",
        "anchor_version", "status", "locked_detection_factor",
        "locked_price_anchor_version", "last_ex_date", "last_event_id",
        "retry_count", "last_stale_probe_at", "last_stale_probe_error",
        "probe_fail_count", "updated_at",
    ],
    "qfq_reanchor_event": [
        "event_id", "event_type", "asset_type", "code", "price_source",
        "source_generation", "cutover_id", "detection_source",
        "old_factor", "new_factor", "old_factor_date", "new_factor_date",
        "daily_method", "minute_ratio_plan", "xtquant_price_ratio",
        "ratio_dispersion", "ratio_cluster_count", "golden_check", "status",
        "block_reason", "error", "precheck_summary", "postcheck_summary",
        "rows_detail", "rows_stock_daily", "rows_stock_minutes", "rows_etf_daily",
        "rows_etf_minutes", "collection_mode", "trigger_surface", "bootstrap_run_id",
        "cycle_business_date", "occurrence_count", "started_at", "finished_at",
        "created_at", "first_seen_at", "last_seen_at",
    ],
    "qfq_pending_backfill": [
        "asset_type", "code", "table_name", "freq", "range_start", "range_end",
        "price_source", "source_generation", "reason", "anchor_version", "status",
        "attempt_count", "last_error", "trigger_id", "last_event_id",
        "next_retry_at", "claimed_by", "claimed_at", "dead_letter_at",
        "created_at", "updated_at", "resolved_at",
    ],
    "qfq_cycle_run": [
        "cycle_id", "business_date", "trigger_surface", "config_hash", "schema_hash",
        "phase", "discovered_count", "executed_count", "success_count",
        "failed_count", "pending_count", "status", "started_at", "finished_at",
        "error", "detector_degraded", "price_source", "source_generation",
        "cutover_id", "updated_at",
    ],
    "qfq_trigger_queue": [
        "trigger_id", "asset_type", "code", "trigger_type", "detection_source",
        "source_key", "effective_date", "payload_hash", "factor_old", "factor_new",
        "factor_revision", "status", "attempt_count", "next_retry_at", "claimed_by",
        "claimed_at", "last_event_id", "last_error", "dead_letter_at",
        "trigger_id_version", "price_source", "source_generation", "cutover_id",
        "retired_at", "retire_reason",
        "created_at", "updated_at", "completed_at",
    ],
    "qfq_watermark_intent": [
        "cycle_id", "source", "table_name", "freq", "source_generation",
        "cutover_id", "old_watermark", "candidate_watermark", "status",
        "hold_reason", "committed_at",
    ],
    "qfq_fresh_capture": [
        "capture_id", "asset_type", "code", "source", "daily_range_start",
        "daily_range_end", "minute_range_start", "minute_range_end",
        "daily_row_count", "minute_row_count", "daily_min_time", "daily_max_time",
        "minute_min_time", "minute_max_time", "daily_sha256", "minute_sha256",
        "metadata_sha256", "download_trace", "status", "source_generation",
        "cutover_id", "created_at", "updated_at",
    ],
    "qfq_observation_cursor": [
        "detector_name", "asset_type", "price_source", "source_generation",
        "cursor_as_of", "last_run_id", "scan_range_start", "scan_range_end",
        "status", "last_error", "updated_at",
    ],
    "qfq_bootstrap_run": [
        "bootstrap_run_id", "asset_type", "params", "resume_cursor", "total_count",
        "completed_count", "blocked_count", "failed_count", "status",
        "schema_version", "config_hash", "baseline_version",
        "price_source", "source_generation", "cutover_id",
        "started_at", "updated_at",
    ],
    "qfq_bootstrap_item": [
        "bootstrap_run_id", "asset_type", "code", "status", "attempt_count",
        "block_reason", "last_error", "started_at", "finished_at", "updated_at",
    ],
    "trade_calendar": ["cal_date", "is_open", "source", "updated_at",
                       "exchange", "pretrade_date"],
    # ---- B-3 新表 ----
    "qfq_discovery_baseline": [
        "cutover_id", "price_source", "source_generation", "event_logical_key",
        "applied_payload_hash", "pending_trigger_id", "pending_payload_hash",
        "last_trigger_id", "applied_at", "baselined_at", "updated_at",
    ],
    "qfq_source_cutover": [
        "cutover_id", "price_source", "source_generation", "cutover_time",
        "price_snapshot_version", "factor_snapshot_version", "baseline_version",
        "schema_version", "config_hash", "aux_db_path", "status",
        "evidence_path", "created_at", "updated_at",
    ],
    "qfq_active_cutover": ["price_source", "cutover_id", "activated_at"],
    "qfq_cycle_lease": [
        "price_source", "source_generation", "cycle_id", "owner_pid",
        "owner_cmdline_hash", "acquired_at", "expires_at",
    ],
}


# ---------------------------------------------------------------------------
# 初始化入口
# ---------------------------------------------------------------------------

def init_duckdb_schema(conn) -> "SchemaStatus":  # noqa: F821 (SchemaStatus via lazy import)
    """在给定 DuckDB 连接上创建全部 QFQ 重锚主库表（v2.4 B-3a 完整状态机）。

    状态分支（B-3R §10 + 审核冻结 §五）：

    | 物理状态 | 行为 |
    |---|---|
    | ``EMPTY_OR_NEW`` | 代码侧 DDL==target 时创建完整 2.1 + source_watermark，回读校验 |
    | ``COMPLETE_2_1`` | **严格只读 no-op**：仅 ``verify_fingerprint`` 物理契约校验，0 DDL/ALTER/DML/migrate |
    | ``COMPLETE_2_0`` / ``PARTIAL_OR_MIXED`` / ``UNKNOWN`` | 写前 fail-fast（``QfqSchemaMigrationRequired``） |

    daemon / CLI / orchestrator / 测试的幂等性改由**物理契约逐字校验成功**保证，
    不再依赖重复执行 ``CREATE TABLE IF NOT EXISTS``。

    **``_migrate_duckdb_columns`` 不再被普通 init 调用**（COMPLETE_2_1 与空库路径均不调；
    2.0/partial/unknown 由 assert_init_allowed 写前拦截）。该函数保留为受控内部兼容工具
    （见其 docstring），不承担 2.0→2.1 migration（由 B-3b migration runner 显式处理）。

    事务感知：本函数**不 commit / 不开新事务**。空库建表走 CREATE TABLE IF NOT EXISTS
    （DuckDB autocommit 立即生效）；调用方决定事务边界。

    返回探测到的 SchemaStatus（COMPLETE_2_1 路径返回 COMPLETE_2_1；空库建表后返回 COMPLETE_2_1）。
    """
    # 延迟 import 避免循环（qfq_schema_status 反向 import 本模块的常量/契约）
    from quantstudio.pipeline.qfq_schema_status import (
        assert_init_allowed, SchemaStatus, assert_code_ddl_matches_target_2_1)
    from quantstudio.pipeline.qfq_schema_contracts import (
        TARGET_MAIN_DB_2_1_FINGERPRINT, verify_fingerprint)

    # 代码侧预检：DDL_DUCKDB + source_watermark DDL 必须与 TARGET 指纹逐字一致。
    # 若不一致（开发中间态），即便空库也写前 fail-fast（禁止用旧 DDL 建库）。
    assert_code_ddl_matches_target_2_1()

    status = assert_init_allowed(conn)  # 只读门禁；2.0/partial/unknown 写前 fail-fast
    if status is SchemaStatus.COMPLETE_2_1:
        # 严格只读 no-op：仅物理契约校验，0 写操作
        if not verify_fingerprint(conn, TARGET_MAIN_DB_2_1_FINGERPRINT,
                                  reject_extra=True):
            raise RuntimeError(
                "[qfq_schema] COMPLETE_2_1 探测通过但物理契约回读校验失败（不应发生）")
        return SchemaStatus.COMPLETE_2_1

    # 仅 EMPTY_OR_NEW 走建表（assert_init_allowed 已保证 status 为 EMPTY_OR_NEW）
    for ddl in DDL_DUCKDB.values():
        conn.execute(ddl)
    # source_watermark 由 writers.py 框架 schema 建立（共享 DDL，单一真相源）；
    # 但 QFQ init 路径也建一次以保证空库完整（CREATE TABLE IF NOT EXISTS 幂等）。
    from quantstudio.pipeline.qfq_schema_contracts import SOURCE_WATERMARK_2_1_DDL
    conn.execute(SOURCE_WATERMARK_2_1_DDL)
    # 建表后回读校验（物理契约逐字一致）
    if not verify_fingerprint(conn, TARGET_MAIN_DB_2_1_FINGERPRINT,
                              reject_extra=True, strict_order=True):
        raise RuntimeError(
            "[qfq_schema] 空库建表后物理契约回读校验失败（DDL 与 target 指纹不一致）")
    return SchemaStatus.COMPLETE_2_1


def init_sqlite_schema(conn: sqlite3.Connection) -> None:
    """在给定 SQLite 连接上创建全部 QFQ 辅助库表（幂等）。不 commit（调用方负责）。"""
    for ddl in DDL_SQLITE.values():
        conn.execute(ddl)


def _migrate_duckdb_columns(conn, best_effort: bool = False) -> None:
    """存量 DuckDB 表缺列时自动 ALTER TABLE ADD COLUMN（幂等，仿 writers._migrate_add_columns）。

    **v2.4 B-3a 处置**：本函数**不再被 ``init_duckdb_schema`` 调用**（COMPLETE_2_1 路径
    严格只读 no-op、空库路径用 CREATE TABLE IF NOT EXISTS 建全、2.0/partial/unknown 由
    ``assert_init_allowed`` 写前 fail-fast）。它保留为**受控内部兼容工具**，供测试或
    显式开发路径直接调用（B-3R §4：通用补列工具，无非 B-3 的特殊兼容用途）。

    **不承担 2.0→2.1 migration**：它只做 nullable ADD COLUMN（无 NOT NULL/DEFAULT/PK swap/
    回填/原子升级/中断恢复/版本一致性验证）。2.0→2.1 升级由 B-3b migration runner 显式处理。

    可靠性（阻断 5）：默认 **fail-fast**——DESCRIBE 失败或 ALTER 失败都抛异常。
    仅在显式 ``best_effort=True`` 时降级为 warning（可选兼容，不推荐生产路径）。
    """
    for table, ddl in DDL_DUCKDB.items():
        actual = {r[0] for r in conn.execute(f"DESCRIBE {table}").fetchall()}
        for col in DUCKDB_COLS.get(table, []):
            if col not in actual:
                col_type = _infer_col_type(ddl, col)
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                    logger.info(f"[qfq_schema] 迁移: {table}.ADD {col} {col_type}")
                except Exception as e:
                    if best_effort:
                        logger.warning(f"[qfq_schema] 迁移失败(已降级忽略) {table}.{col}: {e}")
                    else:
                        raise RuntimeError(
                            f"[qfq_schema] 迁移失败 {table}.{col}（{col_type}）: {e}") from e


def _split_top_level(s: str) -> List[str]:
    """按顶层逗号切分（括号内部的逗号不切，用于解析 PRIMARY KEY (a, b, c)）。"""
    parts: List[str] = []
    depth = 0
    cur: List[str] = []
    for ch in s:
        if ch == '(':
            depth += 1
            cur.append(ch)
        elif ch == ')':
            depth -= 1
            cur.append(ch)
        elif ch == ',' and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _parse_ddl_contract(ddl: str) -> Dict:
    """从 DDL 文本解析单表契约：{columns:{name:TYPE}, not_null:[...], pk:[...]}。

    - 列类型取声明字面量（大写规范化）；
    - ``NOT NULL`` 列入 not_null；
    - 主键列：表级 ``PRIMARY KEY (...)`` 按顺序入 pk；行内 ``name TYPE PRIMARY KEY`` 入 pk。
    这是契约的**权威源**（单一真相源）；正确性关键状态表必须与实际建表结果逐字一致。
    """
    m = re.search(r"\((.+)\)", ddl, re.DOTALL)
    inner = m.group(1)
    columns: Dict[str, str] = {}
    not_null: List[str] = []
    pk: List[str] = []
    for p in _split_top_level(inner):
        p = p.strip()
        up = " " + p.upper().replace("\n", " ").strip() + " "
        if up.strip().startswith("PRIMARY KEY"):
            pkm = re.search(r"\(([^)]*)\)", p)
            if pkm:
                pk = [x.strip() for x in pkm.group(1).split(",")]
            continue
        cm = re.match(r"([A-Za-z_][\w]*)\s+([A-Za-z]+(?:\s*\([^)]*\))?)", p)
        if not cm:
            continue
        name = cm.group(1)
        typ = cm.group(2).replace(" ", "").upper()
        columns[name] = typ
        if " NOT NULL " in up or up.strip().endswith(" NOT NULL"):
            not_null.append(name)
        if re.search(r"\bPRIMARY\s+KEY\b", up):  # 行内主键（如 alert_id TEXT PRIMARY KEY）
            pk.append(name)
    return {"columns": columns, "not_null": not_null, "pk": pk}


# 正确性关键状态表的完整契约。
# v2.4 B-3a：来源固定 = qfq_schema_contracts.TARGET_QFQ_2_1_FINGERPRINT 的确定性投影
# （project_legacy_contract_shape）。**不再**运行时从当前 DDL 文本解析——消除"当前
# DDL/contract 自动等同 2.1"的隐患。DDL 与 target 指纹的一致性由
# assert_code_ddl_matches_target_2_1 机械门禁保证（init 第一条 DDL 前触发）。
from quantstudio.pipeline.qfq_schema_contracts import (  # noqa: E402
    project_legacy_contract_shape, TARGET_QFQ_2_1_FINGERPRINT)
SCHEMA_CONTRACT_DUCKDB: Dict[str, Dict] = project_legacy_contract_shape(
    TARGET_QFQ_2_1_FINGERPRINT)
SCHEMA_CONTRACT_SQLITE: Dict[str, Dict] = {
    t: _parse_ddl_contract(d) for t, d in DDL_SQLITE.items()}


def _verify_duckdb_contract(conn) -> None:
    """回读校验 DuckDB 主库全部 QFQ 表的完整契约（阻断 1）：

    - 必需列存在；
    - 列类型与契约一致；
        - NOT NULL 列确实不可空；
        - 主键列**顺序与契约一致**（DuckDB 1.5 通过 duckdb_constraints() 暴露
          constraint_column_names 验证顺序；极旧版本降级为集合校验；顺序在 DDL 中锁定）。

    任一不符即抛 RuntimeError，禁止 ``init_all_from_paths`` 报告“初始化完成”。
    注意：自动 ALTER ADD COLUMN 只能补列，无法修复 PK/nullability 契约；存量开发表若
    主键或 nullability 不符，必须显式迁移，不能无证据自动重建关键状态表。
    """
    for table, spec in SCHEMA_CONTRACT_DUCKDB.items():
        rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        actual: Dict[str, Dict] = {
            r[1]: {"type": str(r[2]).upper(), "notnull": bool(r[3]), "pk": bool(r[5])}
            for r in rows
        }
        for col, exp_type in spec["columns"].items():
            if col not in actual:
                raise RuntimeError(
                    f"[qfq_schema] {table}.{col} 缺失（契约校验失败）；初始化不完整")
            if actual[col]["type"] != exp_type:
                raise RuntimeError(
                    f"[qfq_schema] {table}.{col} 类型不符：期望 {exp_type}，"
                    f"实际 {actual[col]['type']}（契约校验失败）")
        for col in spec["not_null"]:
            if not actual.get(col, {}).get("notnull"):
                raise RuntimeError(
                    f"[qfq_schema] {table}.{col} 必须 NOT NULL（契约校验失败）")
        # 主键顺序校验（契约问题 1）：DuckDB 1.5 通过 duckdb_constraints() 暴露
        # constraint_column_names（有序列名），可验证**顺序**，不能只验集合。仅极旧版本
        # 无该函数时降级为 pragma 布尔标记的集合校验。
        pk_rows = conn.execute(
            "SELECT constraint_column_names FROM duckdb_constraints() "
            "WHERE table_name=? AND constraint_type='PRIMARY KEY'",
            [table]).fetchall()
        if pk_rows:
            actual_pk = list(pk_rows[0][0])  # 有序列名
        else:
            actual_pk = [c for c, info in actual.items() if info["pk"]]  # 降级：集合
        if actual_pk != spec["pk"]:
            raise RuntimeError(
                f"[qfq_schema] {table} 主键不符（顺序/列）：期望 {spec['pk']}，"
                f"实际 {actual_pk}（契约校验失败）")


def _verify_sqlite_contract(conn) -> None:
    """回读校验 SQLite 辅助库全部 QFQ 表的完整契约（阻断 1）：

    列存在 / 类型 / NOT NULL / 主键**顺序与列**均校验（SQLite 暴露 1-based PK 顺序）。
    任一不符即抛 RuntimeError。
    """
    for table, spec in SCHEMA_CONTRACT_SQLITE.items():
        rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        actual: Dict[str, Dict] = {
            r[1]: {"type": str(r[2]).upper(), "notnull": bool(r[3]), "pk": int(r[5] or 0)}
            for r in rows
        }
        for col, exp_type in spec["columns"].items():
            if col not in actual:
                raise RuntimeError(
                    f"[qfq_schema] {table}.{col} 缺失（契约校验失败）；初始化不完整")
            if actual[col]["type"] != exp_type:
                raise RuntimeError(
                    f"[qfq_schema] {table}.{col} 类型不符：期望 {exp_type}，"
                    f"实际 {actual[col]['type']}（契约校验失败）")
        for col in spec["not_null"]:
            if not actual.get(col, {}).get("notnull"):
                raise RuntimeError(
                    f"[qfq_schema] {table}.{col} 必须 NOT NULL（契约校验失败）")
        actual_pk = [c for c in actual if actual[c]["pk"] > 0]
        actual_pk.sort(key=lambda c: actual[c]["pk"])  # 按 1-based PK 顺序还原
        if actual_pk != spec["pk"]:
            raise RuntimeError(
                f"[qfq_schema] {table} 主键不符（顺序/列）：期望 {spec['pk']}，"
                f"实际 {actual_pk}（契约校验失败）")


def _infer_col_type(ddl: str, col: str) -> str:
    """从 DDL 文本解析某列类型（粗解析，够用；仿 writers._infer_col_type，含 BOOLEAN/REAL）。"""
    m = re.search(
        rf"\b{re.escape(col)}\s+(BIGINT|INTEGER|DOUBLE|VARCHAR|BOOLEAN|TIMESTAMP|REAL|TEXT)",
        ddl, re.IGNORECASE)
    return m.group(1).upper() if m else "VARCHAR"


def init_all_from_paths(main_db: Optional[str | Path] = None,
                        aux_db: Optional[str | Path] = None) -> Dict[str, str]:
    """便捷入口：按路径打开自有连接创建全部表并 commit。

    Args:
        main_db: DuckDB 主库路径（None → _paths.db_path()）。
        aux_db:  SQLite 辅助库路径（None → 由 main_db 推导 qfq_aux.db）。
    Returns:
        {"duckdb": <path>, "sqlite": <path>} 实际使用路径。
    """
    import duckdb

    main_path = Path(main_db) if main_db is not None else db_path()
    aux_path = Path(aux_db) if aux_db is not None else aux_db_path(main_path)
    # 可靠性（阻断 3）：主库与辅助库不能解析到同一文件，否则会异构数据库写同一文件。
    if main_path.resolve() == aux_path.resolve():
        raise ValueError(
            f"[qfq_schema] 主库与辅助库路径不可相同: {main_path} == {aux_path}；"
            f"如需自定义辅助库文件名请显式传入 aux_db=")
    aux_path.parent.mkdir(parents=True, exist_ok=True)

    dconn = duckdb.connect(str(main_path))
    try:
        init_duckdb_schema(dconn)          # 含 CREATE + 缺列 ALTER（fail-fast）
        _verify_duckdb_contract(dconn)     # 回读校验完整契约（列/类型/NOT NULL/PK）
    finally:
        dconn.close()

    sconn = sqlite3.connect(str(aux_path), timeout=30)
    try:
        sconn.execute("PRAGMA journal_mode=WAL")
        sconn.execute("PRAGMA busy_timeout=30000")
        init_sqlite_schema(sconn)
        sconn.commit()
        _verify_sqlite_contract(sconn)     # 回读校验完整契约（含 PK 顺序）
    finally:
        sconn.close()

    logger.info(f"[qfq_schema] 初始化完成 duckdb={main_path} sqlite={aux_path}")
    return {"duckdb": str(main_path), "sqlite": str(aux_path)}


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    result = init_all_from_paths()
    print(f"QFQ reanchor schema initialized: {result}")
