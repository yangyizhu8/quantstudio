"""QFQ 重锚子系统 —— 版本化因子 observation + revision alert outbox 写入协议（第一批基础设施）

实现设计 v3 §3.2（版本化 observation，比较先于覆盖，旧行保留）+ v4 §3.1（跨库漏报窗口
修复方案 A：alert 与 observation 同 SQLite 事务写入）。

与既有 ``qfq_revision.RevisionStore`` 的区别（**并存不合并**，见 v3 §3.2 末）：
- 旧 ``qfq_revision_observation``：UPDATE 原地覆盖（factor_value 与 revision_no 在同一行更新，
  旧值丢失），ETF-only，服务既有 CI 门禁。
- 新 ``qfq_factor_observation``：**保留旧行**版本化（PK 含 revision_no，同键值变化 +1 新增行），
  股票 + ETF 生产 revision 门禁权威表。本模块**不改动** qfq_revision / qfq_maintenance 任何行为。

§3.2 核心算法（每个 (asset_type, code, factor_time) 键）：

    last = SELECT factor_value, revision_no ... ORDER BY revision_no DESC LIMIT 1
    if last is None:                      INSERT revision_no = 1          # 首次观测
    elif |new - last.value| <= epsilon:   UPDATE last_seen_run_id/at      # 未变
    else:                                 INSERT revision_no = last+1      # 修订，旧行保留
                                          + INSERT OR IGNORE alert(pending) # 同事务 outbox

§3.1 outbox 协议（消除 SQLite↔DuckDB 漏报窗口）：
- 写入侧（同一 SQLite 事务）：revision_no≥2 的 INSERT 与 alert(status='pending') 原子写入。
- 消费侧（下一 cycle A2 前最先执行，见 ``list_pending_alerts`` / ``acknowledge_alert``）：
  读 pending → 写 DuckDB anchor status='blocked_revision'（独立短事务，幂等）→ ack。
  **"是否本轮新插入"不参与判定**，pending 处理完前永远重试。
  注：historical(factor_time ≤ anchor.factor_date) vs 常规(factor_time > anchor.factor_date)
  的 BLOCK 判定属消费侧（需读 DuckDB anchor.factor_date），在集成批次实现；写入侧对所有
  revision_no≥2 一律发 alert（持久信号），由消费侧据 anchor 决定是否 BLOCK。
"""
from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from quantstudio.pipeline.qfq_reanchor_schema import (
    aux_db_path,
    init_sqlite_schema,
    _normalize_asset_type,
    _normalize_code,
    _validate_epoch_ms,
    _MIN_EPOCH_MS,
    _MAX_EPOCH_MS,
)

logger = logging.getLogger(__name__)

BJ_TZ = timezone(timedelta(hours=8))  # 与 qfq_revision.py 一致（北京时区 ISO 时间戳）

# revision 判定容差（阻断 2：绝对 + 相对双分量）。
# 默认值经原则性选取（待真实 Tushare 因子样本校准，不得为通过测试自行放宽）：
#   - abs：1e-9，吸收 float 表示抖动；
#   - rel：1e-6，对大数量级因子放宽绝对判定（避免大值微小抖动误报 revision）。
# 相等判定：abs(a-b) <= max(epsilon_abs, epsilon_rel * max(|a|, |b|))。
DEFAULT_EPSILON_ABS = 1e-9
DEFAULT_EPSILON_REL = 1e-6

# factor_time 合理区间（epoch-ms，Asia/Shanghai）：2000-01-01 ~ 2100-01-01。
# 与 qfq_reanchor_schema._MIN/_MAX_EPOCH_MS 共用单一真相源（阻断 2/3 跨模块共享）。
_MIN_FACTOR_MS = _MIN_EPOCH_MS
_MAX_FACTOR_MS = _MAX_EPOCH_MS


def _validate_as_of(ms):
    """as_of_ms 必须 None 或有效 epoch-ms（合理区间）。非法值抛 ValueError。

    阻断 2：as_of_ms=-1 等非法值不得将整批 observation 计为 future_excluded 而生成
    看似正常实则无效的空 run。
    """
    if ms is None:
        return None
    return _validate_epoch_ms(ms)


def _now_iso() -> str:
    return datetime.now(BJ_TZ).isoformat(timespec="seconds")


def _finite(x) -> bool:
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(xf)


def _validate_epsilon_pair(epsilon_abs, epsilon_rel):
    if epsilon_abs is None or not _finite(epsilon_abs) or float(epsilon_abs) < 0:
        raise ValueError(f"epsilon_abs 必须为有限非负数: {epsilon_abs!r}")
    if epsilon_rel is None or not _finite(epsilon_rel) or float(epsilon_rel) < 0:
        raise ValueError(f"epsilon_rel 必须为有限非负数: {epsilon_rel!r}")
    return float(epsilon_abs), float(epsilon_rel)


def _tol_eq(a: float, b: float, eps_abs: float, eps_rel: float) -> bool:
    """绝对+相对双分量相等判定（阻断 2）。"""
    return abs(a - b) <= max(eps_abs, eps_rel * max(abs(a), abs(b)))


def _validate_factor_value(v) -> float:
    """因子值必须 finite 且 > 0（拒绝零/负因子，零因子会造成除零）。"""
    if v is None:
        raise ValueError("factor_value 不能为 None")
    try:
        fv = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"factor_value 非数值: {v!r}")
    if not math.isfinite(fv):
        raise ValueError(f"factor_value 非有限数: {v!r}")
    if fv <= 0:
        raise ValueError(f"factor_value 必须 > 0（拒绝零/负因子，防除零）: {v!r}")
    return fv


def _validate_factor_time(ft) -> int:
    """因子时刻必须有效 epoch-ms（落入 [2000-01-01, 2100-01-01] 合理区间）。

    委托 qfq_reanchor_schema._validate_epoch_ms（单一真相源，与 pending backfill 共用）。
    """
    return _validate_epoch_ms(ft)


def alert_id_of(asset_type: str, code: str, factor_time: int, revision_no: int,
                source_generation: Optional[str] = None) -> str:
    """Deterministic alert id; generation is included when supplied by B-5."""
    if source_generation is None:
        raw = f"{asset_type}|{code}|{int(factor_time)}|{int(revision_no)}"
    else:
        raw = f"{asset_type}|{code}|{int(factor_time)}|{int(revision_no)}|{source_generation}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class RevisionRow:
    asset_type: str
    code: str
    factor_time: int
    revision_no: int
    previous_value: float
    current_value: float
    alert_id: str


@dataclass
class FactorNewRow:
    """任务3：相邻 factor_time 值变化检测命中——新 factor_time 与前一 factor_time 值不同。"""
    asset_type: str
    code: str
    factor_time: int
    previous_value: float
    current_value: float


@dataclass
class ObservationResult:
    run_id: str
    observed: int = 0
    new_count: int = 0
    unchanged_count: int = 0
    revised_count: int = 0
    future_excluded: int = 0
    revisions: List[RevisionRow] = field(default_factory=list)
    factor_new: List[FactorNewRow] = field(default_factory=list)


class ObservationStore:
    """``qfq_factor_observation`` + ``qfq_factor_revision_alert`` 的版本化写入器。

    Args:
        aux_db: qfq_aux.db 路径（None → 由 ``aux_db_path()`` 推导；测试可传临时库）。
        epsilon: revision 判定绝对容差（默认 1e-9）。
    """

    def __init__(self, aux_db: Optional[str | Path] = None,
                 epsilon_abs: float = DEFAULT_EPSILON_ABS,
                 epsilon_rel: float = DEFAULT_EPSILON_REL):
        self.aux_db = Path(aux_db) if aux_db is not None else aux_db_path()
        self.epsilon_abs, self.epsilon_rel = _validate_epsilon_pair(epsilon_abs, epsilon_rel)

    # ---- 连接（自管理时用；保证 schema 存在）----
    def __connect(self) -> sqlite3.Connection:
        """3A 硬约束（DSH 拆分审计）：内部裸工厂（含 init_sqlite_schema DDL），
        类外调用一律 AttributeError（name-mangling 私有化）——
        外部唯一合法入口是 _connect_locked() 上下文。"""
        self.aux_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.aux_db), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        init_sqlite_schema(conn)
        conn.commit()
        return conn

    def _connect_locked(self):
        """3A 写锁上下文（observation:190 拆分）：锁生命周期严格等于连接生命周期。"""
        from quantstudio.pipeline.snapshot_lock import locked_connect
        return locked_connect(self._ObservationStore__connect, "observation:own_conn")

    # ---- 同批预归一化 + 冲突检测（阻断 2）----
    def _preprocess(self, observations, as_of_ms, epsilon_abs, epsilon_rel):
        """统一归一化 + 同批去重/合并/冲突检测。

        返回 ``(to_write, future_excluded)``，其中 to_write 为归一化后待写入列表
        [(asset_type, code, factor_time, factor_value)]，每个 (asset_type, code,
        factor_time) 键至多出现一次（杜绝同一 run 内生成跨 revision_no）。

        - 同键同值或在容差内 → 合并（只处理一次）；
        - 同键超容差差异 → 抛 ValueError（整批拒绝，不写任何行）；
        - factor_time > as_of_ms → 计入 future_excluded 并跳过。
        """
        seen: Dict[Tuple[str, str, int], float] = {}
        to_write: List[Tuple[str, str, int, float]] = []
        future_excluded = 0
        for raw in observations:
            asset_type, code, factor_time, factor_value = raw
            at = _normalize_asset_type(asset_type)
            cd = _normalize_code(code)
            ft = _validate_factor_time(factor_time)
            fv = _validate_factor_value(factor_value)
            if as_of_ms is not None and ft > int(as_of_ms):
                future_excluded += 1
                continue
            key = (at, cd, ft)
            if key in seen:
                prev_fv = seen[key]
                if _tol_eq(prev_fv, fv, epsilon_abs, epsilon_rel):
                    continue  # 容差内：合并，已记录一次
                raise ValueError(
                    f"同一 run 内同键冲突（超容差）：{key} 同时出现值 "
                    f"{prev_fv} 与 {fv}；整批拒绝并回滚（source duplicate conflict）")
            seen[key] = fv
            to_write.append((at, cd, ft, fv))
        return to_write, future_excluded

    # ---- 写入侧：版本化 observation + 同事务 alert ----
    def record_observations(
        self,
        observations: Sequence[Tuple[str, str, int, float]],
        run_id: str,
        *,
        as_of_ms: Optional[int] = None,
        epsilon_abs: Optional[float] = None,
        epsilon_rel: Optional[float] = None,
        source_generation: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> ObservationResult:
        """版本化写入一批因子观测（v3 §3.2）+ revision alert outbox（v4 §3.1）。

        Args:
            observations: [(asset_type, code, factor_time_ms, factor_value)]。
            run_id: 本轮 run 标识（**必须是 str**，strip 后非空；持久化使用 strip 后值）。
            as_of_ms: 可选 as-of 上界；必须是 None 或有效 epoch-ms（合理区间）。
                factor_time > as_of_ms 的行按 binding §1 过滤（future_excluded）。
            epsilon_abs / epsilon_rel: 覆盖实例默认容差（绝对 + 相对双分量）。
                **部分覆盖语义**：仅传其一，另一分量回退到实例默认；两者都未传则用实例默认。
                （不接受“只传一个分量却丢弃实例默认”的隐式行为，避免调用方误解。）
            conn: 传入则并入调用方**外部事务**（本方法不 BEGIN / 不 commit / 不 rollback）；
                  为 None 则自开 BEGIN IMMEDIATE 事务并 commit（异常 rollback）。

        Returns:
            ObservationResult（含 revisions 明细：revision_no≥2 的新行）。

        Raises:
            ValueError: 输入非法（run_id/asset_type/code/factor_time/factor_value/as_of_ms）、
                或同一 run 内同键超容差冲突（整批拒绝，未写任何行）。
        """
        # —— run_id：必须是 str，strip 后非空（阻断 2）——
        if not isinstance(run_id, str):
            raise ValueError(f"run_id 必须为 str: {run_id!r}")
        run_id = run_id.strip()
        if not run_id:
            raise ValueError("run_id 不能为空（strip 后）")
        # —— as_of_ms：None 或有效 epoch-ms（阻断 2）——
        as_of = _validate_as_of(as_of_ms)
        # —— epsilon：部分覆盖回退实例默认；两分量最终都需有效 ——
        ea = float(epsilon_abs) if epsilon_abs is not None else self.epsilon_abs
        er = float(epsilon_rel) if epsilon_rel is not None else self.epsilon_rel
        _validate_epsilon_pair(ea, er)

        # 预归一化 + 同批冲突检测（在开启事务前完成，非法输入/冲突不写任何行）
        # 注意：必须使用本次调用覆盖后的 ea/er，而非实例默认（阻断 1：per-call 覆盖
        # 必须同时作用于批内去重与后续 DB baseline 比较）。
        to_write, future_excluded = self._preprocess(observations, as_of, ea, er)

        now = _now_iso()
        result = ObservationResult(run_id=run_id)
        result.future_excluded = future_excluded

        own = conn is None
        _lctx = self._connect_locked() if own else None  # 3A 写锁
        c = _lctx.__enter__() if own else conn
        try:
            if own:
                c.execute("BEGIN IMMEDIATE")
            # 任务3：baseline 识别——本批之前该 (asset_type,code) 是否已有观测。
            # 首次建立基线的 code 在本批内不触发 factor_new（避免历史洪水）。
            _code_set = {}
            for _at, _cd, _ft, _fv in to_write:
                _code_set[(_at, _cd)] = True
            baseline_map = {}
            for (_at, _cd) in _code_set:
                _ex = c.execute(
                    "SELECT 1 FROM qfq_factor_observation "
                    "WHERE asset_type=? AND code=? LIMIT 1",
                    [_at, _cd]).fetchone()
                baseline_map[(_at, _cd)] = (_ex is None)
            for asset_type, code, ft, fv in to_write:
                result.observed += 1
                row = c.execute(
                    "SELECT factor_value, revision_no FROM qfq_factor_observation "
                    "WHERE asset_type=? AND code=? AND factor_time=? "
                    "ORDER BY revision_no DESC LIMIT 1",
                    [asset_type, code, ft],
                ).fetchone()

                if row is None:
                    # 首次观测：revision_no = 1
                    c.execute(
                        "INSERT INTO qfq_factor_observation "
                        "(asset_type, code, factor_time, factor_value, revision_no, "
                        " first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        [asset_type, code, ft, fv, 1, run_id, run_id, now, now],
                    )
                    result.new_count += 1
                    # 任务3：相邻 factor_time 值变化检测（仅非 baseline code 触发 factor_new）
                    if not baseline_map.get((asset_type, code), False):
                        _prev = c.execute(
                            "SELECT factor_time, factor_value FROM qfq_factor_observation "
                            "WHERE asset_type=? AND code=? AND factor_time < ? "
                            "ORDER BY factor_time DESC LIMIT 1",
                            [asset_type, code, ft]).fetchone()
                        if _prev is not None:
                            _pv = float(_prev[1])
                            if not _tol_eq(_pv, fv, ea, er):
                                result.factor_new.append(FactorNewRow(
                                    asset_type=asset_type, code=code,
                                    factor_time=ft, previous_value=_pv,
                                    current_value=fv))
                    continue

                last_value, last_rev = row
                try:
                    last_value = _validate_factor_value(last_value)
                except ValueError:
                    raise ValueError(
                        f"基线 factor_value 损坏（非有限或 ≤0），拒绝比较: "
                        f"{(asset_type, code, ft)} value={last_value!r}")

                if _tol_eq(float(last_value), fv, ea, er):
                    # 未变：仅刷新最新行的 last_seen（factor_value / revision_no 不动）
                    c.execute(
                        "UPDATE qfq_factor_observation "
                        "SET last_seen_run_id=?, last_seen_at=? "
                        "WHERE asset_type=? AND code=? AND factor_time=? AND revision_no=?",
                        [run_id, now, asset_type, code, ft, last_rev],
                    )
                    result.unchanged_count += 1
                    continue

                # 修订：INSERT 新行 revision_no = last+1（旧行保留）+ 同事务 alert(pending)
                new_rev = int(last_rev) + 1
                c.execute(
                    "INSERT INTO qfq_factor_observation "
                    "(asset_type, code, factor_time, factor_value, revision_no, "
                    " first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    [asset_type, code, ft, fv, new_rev, run_id, run_id, now, now],
                )
                aid = alert_id_of(asset_type, code, ft, new_rev, source_generation)
                c.execute(
                    "INSERT OR IGNORE INTO qfq_factor_revision_alert "
                    "(alert_id, asset_type, code, factor_time, revision_no, status, "
                    " first_seen_run_id, created_at, acknowledged_at) "
                    "VALUES (?,?,?,?,?,?,?,?,NULL)",
                    [aid, asset_type, code, ft, new_rev, "pending", run_id, now],
                )
                result.revised_count += 1
                result.revisions.append(RevisionRow(
                    asset_type=asset_type, code=code, factor_time=ft,
                    revision_no=new_rev, previous_value=float(last_value),
                    current_value=fv, alert_id=aid))

            if own:
                c.commit()
        except Exception:
            if own:
                c.rollback()
            raise
        finally:
            if own:
                c.close()
                _lctx.__exit__(None, None, None)  # 3A 写锁随连接释放

        if result.revised_count:
            logger.info(
                f"[qfq_obs] run={run_id} observed={result.observed} "
                f"new={result.new_count} unchanged={result.unchanged_count} "
                f"revised={result.revised_count} (alert pending)")
        return result

    # ---- 消费侧辅助（SQLite outbox）----
    def list_pending_alerts(self, conn: Optional[sqlite3.Connection] = None) -> List[Dict]:
        """列出全部 status='pending' 的 alert（供消费侧写 DuckDB anchor 前读取）。"""
        own = conn is None
        _lctx = self._connect_locked() if own else None  # 3A 写锁
        c = _lctx.__enter__() if own else conn
        try:
            rows = c.execute(
                "SELECT alert_id, asset_type, code, factor_time, revision_no, "
                "first_seen_run_id, created_at FROM qfq_factor_revision_alert "
                "WHERE status='pending' ORDER BY created_at, alert_id"
            ).fetchall()
        finally:
            if own:
                c.close()
                _lctx.__exit__(None, None, None)  # 3A 写锁随连接释放
        cols = ["alert_id", "asset_type", "code", "factor_time", "revision_no",
                "first_seen_run_id", "created_at"]
        return [dict(zip(cols, r)) for r in rows]

    def acknowledge_alert(self, alert_id: str, *, at: Optional[str] = None,
                          conn: Optional[sqlite3.Connection] = None) -> None:
        """将 alert 标记为 acknowledged（消费侧成功写 DuckDB anchor 后调用，幂等）。"""
        ts = at or _now_iso()
        own = conn is None
        _lctx = self._connect_locked() if own else None  # 3A 写锁
        c = _lctx.__enter__() if own else conn
        try:
            c.execute(
                "UPDATE qfq_factor_revision_alert "
                "SET status='acknowledged', acknowledged_at=? WHERE alert_id=?",
                [ts, alert_id])
            if own:
                c.commit()
        finally:
            if own:
                c.close()
                _lctx.__exit__(None, None, None)  # 3A 写锁随连接释放
