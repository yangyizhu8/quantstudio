"""B-5 cutover identity and state-machine helpers.

All functions are local/staging primitives.  Nothing here performs a production
migration or activates a cutover automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from quantstudio.pipeline.qfq_schema_contracts import (
    CUTOVER_LEGACY_XTQUANT_PRE_CUTOVER, GENERATION_LEGACY_XTQUANT,
    pre_cutover_qfq_identity,
)

BJ_TZ = timezone(timedelta(hours=8))
CUTOVER_STATUSES = {
    "planned", "prepared", "baseline_building", "baseline_validated",
    "active", "failed", "rolled_back", "superseded",
}
_ALLOWED = {
    "planned": {"prepared", "failed"},
    "prepared": {"baseline_building", "failed"},
    "baseline_building": {"baseline_validated", "failed"},
    "baseline_validated": {"active", "failed"},
    "active": {"superseded", "failed"},
    "failed": {"prepared", "rolled_back"},
    "rolled_back": set(),
    "superseded": set(),
}


class CutoverError(RuntimeError):
    pass


class CutoverCASFailed(CutoverError):
    pass


@dataclass(frozen=True)
class RuntimeIdentity:
    price_source: str
    source_generation: str
    cutover_id: str

    def as_dict(self) -> dict:
        return {"price_source": self.price_source,
                "source_generation": self.source_generation,
                "cutover_id": self.cutover_id}


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def read_active_cutover(conn, price_source: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT a.price_source, a.cutover_id, a.activated_at, "
        "c.source_generation, c.status, c.aux_db_path, c.schema_version, "
        "c.baseline_version, c.config_hash, c.evidence_path "
        "FROM qfq_active_cutover a JOIN qfq_source_cutover c "
        "ON c.cutover_id=a.cutover_id WHERE a.price_source=?",
        [price_source]).fetchone()
    if row is None:
        return None
    keys = ["price_source", "cutover_id", "activated_at", "source_generation",
            "status", "aux_db_path", "schema_version", "baseline_version",
            "config_hash", "evidence_path"]
    data = dict(zip(keys, row))
    if data["status"] != "active":
        raise CutoverError(f"active pointer 指向非 active cutover: {data!r}")
    return data


def resolve_runtime_identity(conn, cfg, *, require_active: bool = False,
                             allow_prepared: bool = False) -> dict:
    """Resolve the current identity without silently crossing generations."""
    ps = str(cfg.price_source)
    active = read_active_cutover(conn, ps)
    if active is not None:
        if str(cfg.source_generation) not in (GENERATION_LEGACY_XTQUANT,
                                                active["source_generation"]):
            raise CutoverError(
                f"配置 source_generation={cfg.source_generation!r} 与 active="
                f"{active['source_generation']!r} 不一致")
        if str(cfg.cutover_id) not in (CUTOVER_LEGACY_XTQUANT_PRE_CUTOVER,
                                        active["cutover_id"]):
            raise CutoverError(
                f"配置 cutover_id={cfg.cutover_id!r} 与 active={active['cutover_id']!r} 不一致")
        return {"price_source": ps, "source_generation": active["source_generation"],
                "cutover_id": active["cutover_id"]}
    if require_active and ps == "mcp":
        raise CutoverError(f"price_source=mcp 没有 active cutover，fail-closed: {ps}")
    # B-5 baseline_building/staging may use an explicitly named planned cutover.
    if ps == "mcp" and str(cfg.source_generation) != GENERATION_LEGACY_XTQUANT:
        row = conn.execute(
            "SELECT price_source, source_generation, status FROM qfq_source_cutover "
            "WHERE cutover_id=?", [cfg.cutover_id]).fetchone()
        if row is None or row[0] != ps or row[1] != cfg.source_generation:
            raise CutoverError(
                f"MCP 配置未找到匹配的 staging cutover: {cfg.cutover_id!r}")
        if row[2] not in ({"planned", "prepared", "baseline_building", "baseline_validated"}
                          if allow_prepared else {"baseline_building", "baseline_validated"}):
            raise CutoverError(f"staging cutover 状态不允许运行: {row[2]!r}")
        return {"price_source": ps, "source_generation": cfg.source_generation,
                "cutover_id": cfg.cutover_id}
    return pre_cutover_qfq_identity(ps)


def runtime_cutover_record(conn, identity: dict) -> dict:
    """Return and validate the immutable cutover routing record for an identity."""
    row = conn.execute(
        "SELECT price_source, source_generation, status, aux_db_path, schema_version, "
        "baseline_version, evidence_path FROM qfq_source_cutover WHERE cutover_id=?",
        [identity["cutover_id"]]).fetchone()
    if row is None:
        raise CutoverError(f"cutover not found: {identity['cutover_id']!r}")
    keys = ["price_source", "source_generation", "status", "aux_db_path",
            "schema_version", "baseline_version", "evidence_path"]
    data = dict(zip(keys, row))
    if data["price_source"] != identity["price_source"] or \
            data["source_generation"] != identity["source_generation"]:
        raise CutoverError(f"cutover identity mismatch: expected={identity!r}, row={data!r}")
    return data


def create_cutover(conn, *, cutover_id: str, price_source: str,
                   source_generation: str, schema_version: str,
                   baseline_version: str, aux_db_path: Optional[str] = None,
                   config_hash: Optional[str] = None,
                   evidence_path: Optional[str] = None) -> dict:
    if not cutover_id or not source_generation:
        raise CutoverError("cutover_id/source_generation 不能为空")
    now = _now_ts()
    conn.execute(
        "INSERT INTO qfq_source_cutover "
        "(cutover_id, price_source, source_generation, cutover_time, "
        "price_snapshot_version, factor_snapshot_version, baseline_version, "
        "schema_version, config_hash, aux_db_path, status, evidence_path, "
        "created_at, updated_at) VALUES (?,?,?,?,NULL,NULL,?,?,?,?,?,?,?,?)",
        [cutover_id, price_source, source_generation, now, baseline_version,
         schema_version, config_hash, aux_db_path, "planned", evidence_path,
         now, now],
    )
    return cutover_status(conn, cutover_id)


def transition_cutover(conn, *, cutover_id: str, new_status: str,
                       expected_status: Optional[str] = None) -> dict:
    if new_status not in CUTOVER_STATUSES:
        raise CutoverError(f"非法 cutover status: {new_status!r}")
    row = conn.execute("SELECT status FROM qfq_source_cutover WHERE cutover_id=?",
                       [cutover_id]).fetchone()
    if row is None:
        raise CutoverError(f"cutover 不存在: {cutover_id}")
    old = row[0]
    if expected_status is not None and old != expected_status:
        raise CutoverCASFailed(f"cutover={cutover_id} status={old}, expected={expected_status}")
    if new_status == "active":
        raise CutoverError(
            "plain status transition cannot activate a cutover; use activate_cutover "
            "with active-pointer CAS and the B-6 user gate")
    if new_status not in _ALLOWED.get(old, set()):
        raise CutoverError(f"非法状态转移 {old} -> {new_status}")
    now = _now_ts()
    result = conn.execute(
        "UPDATE qfq_source_cutover SET status=?, updated_at=? "
        "WHERE cutover_id=? AND status=? RETURNING cutover_id",
        [new_status, now, cutover_id, old]).fetchone()
    if result is None:
        raise CutoverCASFailed("cutover 状态 CAS 失败")
    return cutover_status(conn, cutover_id)


def activate_cutover(conn, *, price_source: str, new_cutover_id: str,
                     expected_old: Optional[str]) -> dict:
    """Explicit activation primitive; B-5 does not call this automatically."""
    row = conn.execute("SELECT cutover_id, status, source_generation, price_source "
                       "FROM qfq_source_cutover WHERE cutover_id=?",
                       [new_cutover_id]).fetchone()
    if row is None or row[1] != "baseline_validated" or row[3] != price_source:
        raise CutoverCASFailed("新 cutover 必须是匹配 price_source 的 baseline_validated")
    cur = conn.execute("SELECT cutover_id FROM qfq_active_cutover WHERE price_source=?",
                       [price_source]).fetchone()
    current = cur[0] if cur else None
    if current != expected_old:
        raise CutoverCASFailed(f"active={current!r} != expected_old={expected_old!r}")
    conn.execute("BEGIN TRANSACTION")
    try:
        if current is not None:
            deleted = conn.execute(
                "DELETE FROM qfq_active_cutover WHERE price_source=? AND cutover_id=? "
                "RETURNING cutover_id", [price_source, current]).fetchone()
            if deleted is None:
                raise CutoverCASFailed("旧 active 指针删除 CAS 失败")
            conn.execute("UPDATE qfq_source_cutover SET status='superseded', updated_at=? "
                         "WHERE cutover_id=?", [_now_ts(), current])
        conn.execute("INSERT INTO qfq_active_cutover VALUES (?,?,?)",
                     [price_source, new_cutover_id, _now_ts()])
        conn.execute("UPDATE qfq_source_cutover SET status='active', updated_at=? "
                     "WHERE cutover_id=?", [_now_ts(), new_cutover_id])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cutover_status(conn, new_cutover_id)


def cutover_status(conn, cutover_id: str) -> dict:
    row = conn.execute("SELECT * FROM qfq_source_cutover WHERE cutover_id=?",
                       [cutover_id]).fetchone()
    if row is None:
        raise CutoverError(f"cutover 不存在: {cutover_id}")
    cols = [r[0] for r in conn.execute("DESCRIBE qfq_source_cutover").fetchall()]
    return dict(zip(cols, row))
