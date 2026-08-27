"""QFQ 复权因子修订检测 + 审计 schema [PR2 Commit 2]

**核心语义（务必区分）**：
- revision（修订）≠ 同一 code 不同 factor_time 之间的 LAG 时间序列变化。
- revision = 同一逻辑键 ``(asset_type, code, factor_time)`` 在不同审计观察批次中，
  ``factor_value`` 发生 > epsilon 的变化。
- 例：同一 510050 / 2026-06-20 的因子先观察到 1.0000，数据刷新后变成 1.0005 → revision。
  而 2026-06-20=1.0 → 2026-07-10=1.1 是不同日期的正常因子变化，**不是** revision。
- 现有 ``scripts/audit_qfq_staleness.py::select_etf_candidates_from_adj_factor`` 的
  changed/stable/no_record（LAG 时间序列分类）保持不变，**不写入** revision 表。

**分层（PR2 Commit 2 硬要求）**：
1. 纯检测函数 ``detect_revisions()``（无副作用，可独立测试）。
2. ``RevisionStore``（schema init / load baseline / persist run+events+baseline，单事务）。
3. CLI 集成在 audit 脚本（默认 dry-run，仅 ``--persist-revision-audit`` 写审计辅助表）。

**写边界**：本模块只写 qfq_aux.db 的 ``qfq_revision_*`` 审计辅助表；不修改 adj_factor /
adj_factor_snapshot / stock_daily / etf_daily / Canonical front-back 字段 / source_watermark；
不是 repair/write-back。默认不初始化 schema、不写 qfq_aux.db，仅显式持久化入口才写。

**范围（ETF-only）**：asset_type 固定 "ETF"。不做 stock_dividend / 股票 adj_factor /
删除缺失检测 / ingestion hook / repair / 自动修改 Canonical。
"""
from __future__ import annotations

from quantstudio.pipeline.snapshot_lock import locked_connect  # 3A 写锁收口

import logging
import math
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BJ_TZ = timezone(timedelta(hours=8))

# audit schema 版本（初始值，写入 qfq_revision_run.schema_version）
REVISION_SCHEMA_VERSION = "1.0"
# asset_type 范围（当前仅 ETF）
ASSET_TYPE_ETF = "ETF"
_ALLOWED_ASSET_TYPES = frozenset({ASSET_TYPE_ETF})


class RevisionInputError(ValueError):
    """纯检测输入校验失败（epsilon/code/factor_time/factor_value/重复键）。"""


# ---------------------------------------------------------------------------
# 输入校验（binding §6）
# ---------------------------------------------------------------------------

def _finite_or_none(x) -> Optional[float]:
    """转 float；None/NaN/Inf → None（调用方据语义拒绝或置 NULL）。"""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _validate_epsilon(epsilon: float) -> float:
    """epsilon 必须有限且 >= 0。"""
    if epsilon is None:
        raise RevisionInputError("epsilon 不能为 None")
    try:
        e = float(epsilon)
    except (TypeError, ValueError):
        raise RevisionInputError(f"epsilon 非数值: {epsilon!r}")
    if math.isnan(e) or math.isinf(e):
        raise RevisionInputError(f"epsilon 必须有限: {epsilon!r}")
    if e < 0:
        raise RevisionInputError(f"epsilon 必须 >= 0: {epsilon!r}")
    return e


def _bare_code(code) -> str:
    """归一化为裸码（复用项目权威 security_code_rules.bare_code）。

    binding §2 修正（review）：security_code_rules.bare_code(None) 会把 None 字符串化成
    "NONE"（bare_code 内部 str(code).strip().upper().split(".")[0]），旧实现只检查归一化结果
    是否为空，导致 code=None 被当作合法 logical code "NONE" 持久化。必须在调用 bare_code 前
    显式拒绝 None / 空串 / 纯空白；同时拒绝归一化后的字面 "NONE"（None 误入的典型产物）。
    """
    if code is None:
        raise RevisionInputError("code 不能为 None")
    if not isinstance(code, str):
        # 非字符串先转串再校验空白（数字 code 等）
        s = str(code)
    else:
        s = code
    if s is None or s.strip() == "":
        raise RevisionInputError(f"code 为空或纯空白: {code!r}")
    from quantstudio.backtest.libs.security_code_rules import bare_code
    bc = bare_code(code)
    if not bc or bc.strip() == "":
        raise RevisionInputError(f"code 为空或归一化后为空: {code!r}")
    if bc.upper() == "NONE":
        # bare_code(None) == "NONE"；此处兜底拒绝（防 None 误入经其它路径到达）
        raise RevisionInputError(f"code 归一化为 'NONE'（疑似 None 输入）: {code!r}")
    return bc


def _valid_factor_time(ft) -> int:
    """factor_time / as_of_ms / window 必须是有效 epoch-ms integer。

    合理区间：2000-01-01 ~ 2100-01-01（项目数据自 2018 起，2000 下界安全且能拒 0/1 等非法值）。
    review 阻断 4：as_of_ms=1 等明显非法 epoch-ms 必须被拒（旧下界 v<=0 放行了 1）。
    """
    try:
        v = int(ft)
    except (TypeError, ValueError):
        raise RevisionInputError(f"factor_time 非整数 epoch-ms: {ft!r}")
    if v < 946684800000 or v > 4102444800000:  # 2000-01-01 ms ~ 2100-01-01 ms
        raise RevisionInputError(f"factor_time 超出合理 epoch-ms 区间（2000~2100）: {ft!r}")
    return v


def _validate_run_inputs(asset_type: str, as_of_ms: int, epsilon: float,
                         window_start_ms: Optional[int] = None,
                         window_end_ms: Optional[int] = None):
    """共享校验：run 级输入（asset_type / as_of_ms / epsilon / window）。

    review 修正（阻断 4）：persisted path 与 record_failed_run 必须复用同一校验，
    保证 ETF-only + epoch-ms schema 契约不被 failed ledger 绕过。
    """
    if asset_type not in _ALLOWED_ASSET_TYPES:
        raise RevisionInputError(
            f"asset_type 当前仅支持 {_ALLOWED_ASSET_TYPES}: {asset_type!r}")
    _validate_epsilon(epsilon)
    _valid_factor_time(as_of_ms)
    # window 非空时校验 epoch-ms
    if window_start_ms is not None:
        _valid_factor_time(window_start_ms)
    if window_end_ms is not None:
        _valid_factor_time(window_end_ms)


def _normalize_observations(raw_obs: List[Tuple]) -> List[Tuple[str, int, float]]:
    """校验并归一化 observations → [(bare_code, factor_time_ms, factor_value)]。

    binding §6：拒绝空 code、非有限 factor_value（None/NaN/Inf）、非法 factor_time、
    重复 (code, factor_time) 键。
    """
    seen = set()
    out = []
    for entry in raw_obs:
        if len(entry) < 3:
            raise RevisionInputError(f"observation 元组至少 3 元素: {entry!r}")
        code, ft, fv = entry[0], entry[1], entry[2]
        bc = _bare_code(code)
        ftv = _valid_factor_time(ft)
        fvv = _finite_or_none(fv)
        if fvv is None:
            raise RevisionInputError(
                f"factor_value 必须是有限数，拒绝 None/NaN/Inf: code={bc} factor_time={ftv} value={fv!r}")
        key = (bc, ftv)
        if key in seen:
            raise RevisionInputError(
                f"observations 含重复 logical key (code,factor_time)={key}，拒绝静默覆盖")
        seen.add(key)
        out.append((bc, ftv, fvv))
    return out


# ---------------------------------------------------------------------------
# 纯检测函数（binding §5 层 1，无副作用）
# ---------------------------------------------------------------------------

@dataclass
class RevisionEvent:
    code: str
    factor_time: int
    previous_factor: float
    current_factor: float
    abs_delta: float
    relative_delta: Optional[float]  # previous==0 时为 None（不得除零）
    revision_no: int                 # 修订后序号（首次修订=1）


@dataclass
class RevisionDetectionResult:
    asset_type: str
    as_of_ms: int
    epsilon: float
    baseline_available: bool          # False: 表不存在或该 asset_type 无基线
    baseline_seeded: bool             # True: 本次为首次观察（全部 new_record）
    observed_count: int               # as-of 内 observation 数
    new_count: int                    # 新键（此前无基线）
    unchanged_count: int              # 同键值 <= epsilon 一致
    revised_count: int                # 同键值变化 > epsilon
    future_excluded_count: int        # factor_time > as_of_ms 被排除数
    events: List[RevisionEvent] = field(default_factory=list)


def detect_revisions(observations: List[Tuple], previous_baseline: Optional[Dict],
                     asset_type: str, as_of_ms: int, epsilon: float,
                     baseline_available: bool = True) -> RevisionDetectionResult:
    """纯检测：对 as-of 内 observations 与 previous_baseline 做同键比较。

    binding §1：future 过滤由本函数执行（factor_time <= as_of_ms），返回 future_excluded_count。
    binding §2：previous_baseline is None 或空（表不存在/asset_type 无基线）→ baseline_available=False，
      全部为 new_record；首次 persist 后 baseline_seeded=True。
    binding §4：abs(current-baseline) <= epsilon 视为 unchanged，保留原 baseline（不更新），
      避免多次小漂移永远累计不到 revision。
    binding §6：校验全部输入；relative_delta 分母 abs(previous)，previous==0 → None。

    Args:
        observations: [(code, factor_time_ms, factor_value)]（原始，含可能 future 行）。
        previous_baseline: {(code, factor_time_ms): factor_value} 或 None。
            None 表示表不存在或该 asset_type 完全无基线（baseline_unavailable）。
            {}（空 dict）也视为无基线（首次 seed）。
        asset_type: 当前仅 "ETF"。
        as_of_ms: as-of 上界（epoch-ms）。factor_time > as_of_ms → future_excluded。
        epsilon: 变化阈值（>=0，有限）。abs(current-baseline) > epsilon → revised。
        baseline_available: 调用方据"表是否存在"传入（baseline 为 {} 但表存在仍可 seed）。
            默认 True；store 层会精确传入。

    Returns:
        RevisionDetectionResult。revised 明细在 events 列表。
    """
    if asset_type not in _ALLOWED_ASSET_TYPES:
        raise RevisionInputError(f"asset_type 当前仅支持 {_ALLOWED_ASSET_TYPES}: {asset_type!r}")
    eps = _validate_epsilon(epsilon)
    if as_of_ms is None:
        raise RevisionInputError("as_of_ms 不能为 None")
    as_of_v = _valid_factor_time(as_of_ms)

    norm_obs = _normalize_observations(observations)

    # binding §1: future 过滤 + 计数（loader 返回全部 obs，此处显式过滤）
    included = [(c, ft, fv) for (c, ft, fv) in norm_obs if ft <= as_of_v]
    future_excluded_count = len(norm_obs) - len(included)

    # binding §2: baseline 可用性。None 或空 dict → baseline_unavailable。
    # 表不存在（baseline_available=False）与表存在但该 asset_type 无基线（baseline={}）均判 False。
    has_baseline = baseline_available and bool(previous_baseline)

    result = RevisionDetectionResult(
        asset_type=asset_type, as_of_ms=as_of_v, epsilon=eps,
        baseline_available=has_baseline,
        baseline_seeded=(not has_baseline and len(included) > 0),
        observed_count=len(included), new_count=0, unchanged_count=0,
        revised_count=0, future_excluded_count=future_excluded_count,
        events=[],
    )

    if not has_baseline:
        # 无基线：全部为 new_record（revised_count=0）。revised 明细为空。
        result.new_count = len(included)
        return result

    bl = previous_baseline or {}
    events = []
    for code, ft, fv in included:
        key = (code, ft)
        if key not in bl:
            result.new_count += 1
            continue
        prev = bl[key]
        prev_f = _finite_or_none(prev)
        if prev_f is None:
            # review 加固（阻断 1）：基线值损坏（NULL/NaN/Inf 落库）必须明确失败，
            # 不能静默分类为 new 后保留损坏 baseline。抛 RevisionInputError 使 persist 整体回滚。
            raise RevisionInputError(
                f"baseline factor_value 损坏（非有限数），拒绝比较并保留损坏基线: "
                f"key={(code, ft)} value={prev!r}")
        delta = abs(fv - prev_f)
        if delta <= eps:
            result.unchanged_count += 1
            continue
        # revised（binding §6: relative_delta 分母 abs(previous)；prev==0 → None）
        rel = None if prev_f == 0 else (delta / abs(prev_f))
        events.append(RevisionEvent(
            code=code, factor_time=ft,
            previous_factor=prev_f, current_factor=fv,
            abs_delta=delta, relative_delta=rel,
            revision_no=1,  # 占位；store 层按实际基线 revision_no+1 回填
        ))
        result.revised_count += 1
    result.events = events
    return result


# ---------------------------------------------------------------------------
# RevisionStore（binding §5 层 2/3）
# ---------------------------------------------------------------------------

class RevisionStore:
    """qfq_aux.db 的 revision schema 显式管理 + 单事务持久化。

    构造**不建表**；仅显式 init_schema() / run_persisted_audit() 才创建并写。
    binding §3：持久化入口 run_persisted_audit() 在单个 BEGIN IMMEDIATE 事务内
    完成 baseline 读取 + detect + 写 run/event/observation + completed，避免事务外陈旧结果竞态。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    # ---- schema ----
    def init_schema(self):
        """显式初始化三表（可重复执行，CREATE TABLE IF NOT EXISTS）。

        binding §9: 不声明强制外键（无 FK 引用）；factor_time/as_of_ms=epoch-ms integer；
        *_at=ISO 字符串；event UNIQUE(run_id,asset_type,code,factor_time)。
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with locked_connect(lambda: sqlite3.connect(str(self.db_path), timeout=30), "qfq_revision:conn") as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._ddl(conn)
            conn.commit()

    @staticmethod
    def _ddl(conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS qfq_revision_run (
                run_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                as_of_ms INTEGER NOT NULL,
                window_start_ms INTEGER,
                window_end_ms INTEGER,
                epsilon REAL NOT NULL,
                status TEXT NOT NULL,
                observed_count INTEGER NOT NULL,
                new_count INTEGER NOT NULL,
                unchanged_count INTEGER NOT NULL,
                revised_count INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS qfq_revision_observation (
                asset_type TEXT NOT NULL,
                code TEXT NOT NULL,
                factor_time INTEGER NOT NULL,
                factor_value REAL NOT NULL,
                revision_no INTEGER NOT NULL,
                first_seen_run_id TEXT NOT NULL,
                last_seen_run_id TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (asset_type, code, factor_time)
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS qfq_revision_event (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                code TEXT NOT NULL,
                factor_time INTEGER NOT NULL,
                previous_factor REAL NOT NULL,
                current_factor REAL NOT NULL,
                abs_delta REAL NOT NULL,
                relative_delta REAL,
                revision_no INTEGER NOT NULL,
                previous_seen_run_id TEXT,
                detected_at TEXT NOT NULL,
                UNIQUE (run_id, asset_type, code, factor_time)
            )""")

    # ---- 只读：表/基线/observations ----
    def _has_revision_schema(self, conn) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='qfq_revision_observation'"
        ).fetchone()
        return row is not None

    def load_baseline(self, asset_type: str, conn=None) -> Tuple[Optional[dict], bool]:
        """返回 (baseline_dict_or_None, table_exists)。

        binding §2: 区分"表不存在"与"表存在但该 asset_type 无基线"。
        - 表不存在 → (None, False)
        - 表存在但无该 asset_type 行 → ({}, True)  → 调用方判 baseline_unavailable（首次 seed）
        - 表存在且有行 → ({key:value}, True)
        """
        own = conn is None
        if own:
            conn = sqlite3.connect(str(self.db_path), timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
        try:
            if not self._has_revision_schema(conn):
                return None, False
            rows = conn.execute(
                "SELECT code, factor_time, factor_value FROM qfq_revision_observation "
                "WHERE asset_type=?", [asset_type]).fetchall()
            if not rows:
                return {}, True
            return {(r[0], r[1]): r[2] for r in rows}, True
        finally:
            if own:
                conn.close()

    def load_observations_from_adj_factor(self, etf_universe: List[str],
                                          as_of_end_ms: int, conn=None) -> List[Tuple]:
        """从 adj_factor 读 observations（含可能 future 行 + NULL/损坏值；交给 detector 校验）。

        binding §1: loader 返回全部匹配 universe 的 (code,factor_time,factor_value)，
        future 过滤交给 detect_revisions（产 future_excluded_count），不在 loader 静默删除。
        review 修正（阻断 1）：**不得在 loader 静默过滤 NULL factor_value**。旧实现
        `if r[2] is not None` 会把 adj_factor=NULL 的损坏源行静默删除，绕过 detector 的
        None/NaN/Inf 拒绝门禁，伪装成"没有观察"并生成 completed 空审计。修复：返回原始行
        （含 NULL），由 _normalize_observations / detect_revisions 显式校验并抛
        RevisionInputError；persist 路径因此整体失败+回滚+记 failed ledger。
        code 用裸码（adj_factor 口径即裸码）。
        """
        own = conn is None
        if own:
            conn = sqlite3.connect(str(self.db_path), timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='adj_factor'").fetchall()]
            if not tables:
                return []
            if not etf_universe:
                return []
            ph = ",".join("?" * len(etf_universe))
            rows = conn.execute(
                f"SELECT code, time, adj_factor FROM adj_factor WHERE code IN ({ph})",
                list(etf_universe)).fetchall()
            # 原样返回（含 NULL factor_value），校验交给 detector；不在此静默过滤。
            return [(r[0], int(r[1]), r[2]) for r in rows]
        finally:
            if own:
                conn.close()

    # ---- 单事务持久化（binding §3/§5/§6）----
    def run_persisted_audit(self, asset_type: str, as_of_ms: int, epsilon: float,
                            etf_universe: List[str],
                            window_start_ms: Optional[int] = None,
                            window_end_ms: Optional[int] = None,
                            run_id: Optional[str] = None,
                            observations: Optional[List[Tuple]] = None) -> Tuple[str, RevisionDetectionResult]:
        """在单个 BEGIN IMMEDIATE 事务内完成检测 + 持久化（binding §3）。

        事务内步骤：
          1. CREATE schema（IF NOT EXISTS）
          2. 拒绝已存在 run_id（completed/failed 均拒绝；binding §5/§6）
          3. 加载 baseline（事务内，无陈旧结果）
          4. 加载/确认本次 observations（若未传入则从 adj_factor 读）
          5. detect_revisions()
          6. INSERT run(status=running) + events + UPSERT observation
          7. UPDATE run status=completed
          COMMIT
        任一步失败 → ROLLBACK（baseline 不推进，不留 completed run，不留 event）。

        Args:
            observations: 可选预加载（用于测试注入）；为 None 时事务内从 adj_factor 读。
        Returns:
            (run_id, detection_result)
        """
        # 入口先校验 run 级输入（早失败，不进事务；与 record_failed_run 共享同一校验）
        _validate_run_inputs(asset_type, as_of_ms, epsilon, window_start_ms, window_end_ms)
        eps = float(epsilon)
        rid = run_id or f"r_{uuid.uuid4().hex[:12]}"
        as_of_v = int(as_of_ms)
        started_at = datetime.now(BJ_TZ).isoformat(timespec="seconds")

        _lc = locked_connect(lambda: sqlite3.connect(str(self.db_path), timeout=30), "qfq_revision:assign")  # 3A 写锁
        conn = _lc.__enter__()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            conn.execute("BEGIN IMMEDIATE")
            # 1. schema
            self._ddl(conn)
            # 2. 拒绝已存在 run_id（binding §5/§6，禁 INSERT OR REPLACE 覆盖）
            existing = conn.execute(
                "SELECT status FROM qfq_revision_run WHERE run_id=?", [rid]).fetchone()
            if existing is not None:
                raise RevisionInputError(
                    f"run_id {rid!r} 已存在（status={existing[0]}）；重试须用新 run_id，禁止覆盖")

            # 3. 事务内加载 baseline（binding §3，无陈旧结果）
            baseline, table_exists = self.load_baseline(asset_type, conn=conn)
            # 表存在但 baseline={}（首次 seed）→ previous_baseline 视为 None（baseline_unavailable）
            prev_for_detect = baseline if baseline else None

            # 4. observations
            if observations is None:
                obs = self.load_observations_from_adj_factor(etf_universe, as_of_v, conn=conn)
            else:
                obs = list(observations)

            # 5. detect（纯函数，复用）
            result = detect_revisions(
                obs, prev_for_detect, asset_type, as_of_v, eps,
                baseline_available=table_exists and bool(baseline))

            # 6. INSERT run(running) + events + UPSERT observation
            conn.execute(
                "INSERT INTO qfq_revision_run "
                "(run_id, schema_version, asset_type, as_of_ms, window_start_ms, window_end_ms, "
                " epsilon, status, observed_count, new_count, unchanged_count, revised_count, "
                " started_at, finished_at, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                [rid, REVISION_SCHEMA_VERSION, asset_type, as_of_v,
                 window_start_ms, window_end_ms, eps, "running",
                 result.observed_count, result.new_count, result.unchanged_count,
                 result.revised_count, started_at])

            detected_at = datetime.now(BJ_TZ).isoformat(timespec="seconds")
            # events + observation UPSERT（按 as-of 内归一化 obs 逐键处理）
            for code, ft, fv in [(c, ft, fv) for (c, ft, fv) in
                                 _normalize_observations(obs) if ft <= as_of_v]:
                blrow = conn.execute(
                    "SELECT revision_no, last_seen_run_id FROM qfq_revision_observation "
                    "WHERE asset_type=? AND code=? AND factor_time=?",
                    [asset_type, code, ft]).fetchone()
                prev_rev_no = blrow[0] if blrow else 0
                prev_seen_run = blrow[1] if blrow else None
                is_new = blrow is None
                # 是否 revised：纯函数已用 baseline 比较，结果在内存 result.events
                ev = next((e for e in result.events
                           if e.code == code and e.factor_time == ft), None)
                if ev is not None:
                    # revised：写 event + UPDATE observation（factor_value+revision_no+1+last_seen）
                    new_rev_no = prev_rev_no + 1
                    conn.execute(
                        "INSERT INTO qfq_revision_event "
                        "(run_id, asset_type, code, factor_time, previous_factor, current_factor, "
                        " abs_delta, relative_delta, revision_no, previous_seen_run_id, detected_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        [rid, asset_type, code, ft,
                         ev.previous_factor, ev.current_factor, ev.abs_delta,
                         ev.relative_delta, new_rev_no, prev_seen_run, detected_at])
                    conn.execute(
                        "UPDATE qfq_revision_observation SET factor_value=?, revision_no=?, "
                        " last_seen_run_id=?, last_seen_at=? "
                        "WHERE asset_type=? AND code=? AND factor_time=?",
                        [fv, new_rev_no, rid, detected_at, asset_type, code, ft])
                    ev.revision_no = new_rev_no  # 回填内存
                elif is_new:
                    # new_record：INSERT observation revision_no=0
                    conn.execute(
                        "INSERT INTO qfq_revision_observation "
                        "(asset_type, code, factor_time, factor_value, revision_no, "
                        " first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
                        "VALUES (?,?,?,?,0,?,?,?,?)",
                        [asset_type, code, ft, fv, rid, rid, detected_at, detected_at])
                else:
                    # unchanged：仅 last_seen_*，factor_value/revision_no 不动（binding §4）
                    conn.execute(
                        "UPDATE qfq_revision_observation SET last_seen_run_id=?, last_seen_at=? "
                        "WHERE asset_type=? AND code=? AND factor_time=?",
                        [rid, detected_at, asset_type, code, ft])

            # 7. completed
            conn.execute(
                "UPDATE qfq_revision_run SET status='completed', finished_at=? WHERE run_id=?",
                [datetime.now(BJ_TZ).isoformat(timespec="seconds"), rid])
            conn.commit()
            return rid, result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
            _lc.__exit__(None, None, None)  # 3A 写锁随连接释放

    def record_failed_run(self, run_id: str, asset_type: str, as_of_ms: int,
                          epsilon: float, error: str,
                          window_start_ms: Optional[int] = None,
                          window_end_ms: Optional[int] = None):
        """binding §5: 失败在独立短事务记一条 status='failed' run（无 event/observation 更新）。

        与 run_persisted_audit 分离：后者整体回滚后调用本方法落 failed 行。
        failed run 携带 error；不含 event，不推进 observation。

        review 修正（阻断 4）：必须复用与 persisted path 相同的 asset_type / as_of_ms /
        epsilon / window 校验（ETF-only + epoch-ms），不得绕过。非法输入不创建 schema、
        不写 failed ledger。
        """
        # 复用 persisted path 的全部输入校验
        _validate_run_inputs(asset_type, as_of_ms, epsilon, window_start_ms, window_end_ms)
        eps = _validate_epsilon(epsilon)  # 二次显式（_validate_run_inputs 内已校验，保持冗余清晰）
        as_of_v = _valid_factor_time(as_of_ms)
        started_at = datetime.now(BJ_TZ).isoformat(timespec="seconds")
        with locked_connect(lambda: sqlite3.connect(str(self.db_path), timeout=30), "qfq_revision:conn") as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._ddl(conn)
            existing = conn.execute(
                "SELECT status FROM qfq_revision_run WHERE run_id=?", [run_id]).fetchone()
            if existing is not None:
                # 已存在（completed/failed）→ 拒绝（binding §5/§6）
                raise RevisionInputError(
                    f"run_id {run_id!r} 已存在（status={existing[0]}）；拒绝覆盖")
            conn.execute(
                "INSERT INTO qfq_revision_run "
                "(run_id, schema_version, asset_type, as_of_ms, window_start_ms, window_end_ms, "
                " epsilon, status, observed_count, new_count, unchanged_count, revised_count, "
                " started_at, finished_at, error) "
                "VALUES (?,?,?,?,?,?,?,'failed',0,0,0,0,?,?,?)",
                [run_id, REVISION_SCHEMA_VERSION, asset_type, as_of_v,
                 window_start_ms, window_end_ms, eps, started_at,
                 datetime.now(BJ_TZ).isoformat(timespec="seconds"), error])
            conn.commit()

    # ---- 只读：dry-run 检测（报告用）----
    def dry_run_detect(self, asset_type: str, as_of_ms: int, epsilon: float,
                       etf_universe: List[str]) -> RevisionDetectionResult:
        """默认 dry-run 检测：只读，不建表、不写。

        binding §2: revision schema 不存在或 observation 表存在但该 asset_type 无基线
        → baseline_available=False，调用方据 result.baseline_available 输出 baseline_unavailable。

        review 加固（阻断 1）：损坏源数据（NULL/NaN/Inf factor_value）不再静默丢弃，
        detect_revisions 会抛 RevisionInputError（与 persisted path 一致）。调用方据需 catch。
        """
        _validate_run_inputs(asset_type, as_of_ms, epsilon)
        eps = float(epsilon)
        as_of_v = int(as_of_ms)
        if not self.db_path.exists():
            # 整库不存在 → 表不存在 → baseline_unavailable
            return RevisionDetectionResult(
                asset_type=asset_type, as_of_ms=as_of_v, epsilon=eps,
                baseline_available=False, baseline_seeded=False,
                observed_count=0, new_count=0, unchanged_count=0, revised_count=0,
                future_excluded_count=0, events=[])
        _lc = locked_connect(lambda: sqlite3.connect(str(self.db_path), timeout=30), "qfq_revision:assign")  # 3A 写锁
        conn = _lc.__enter__()
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            baseline, table_exists = self.load_baseline(asset_type, conn=conn)
            obs = self.load_observations_from_adj_factor(etf_universe, as_of_v, conn=conn)
            prev = baseline if baseline else None
            return detect_revisions(
                obs, prev, asset_type, as_of_v, eps,
                baseline_available=table_exists and bool(baseline))
        finally:
            conn.close()
            _lc.__exit__(None, None, None)  # 3A 写锁随连接释放
