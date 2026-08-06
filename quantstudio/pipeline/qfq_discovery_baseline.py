"""Generation-specific discovery baseline with two-phase CAS.

The module owns only the DuckDB baseline ledger.  Trigger construction remains
in the event-discovery layer; this module supplies the atomic reservation and
commit contracts so concurrent discoverers cannot create B/C races or let an
old trigger roll back a newer applied payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Callable, Iterable, Optional, Sequence


BJ_TZ = timezone(timedelta(hours=8))


class DiscoveryBaselineError(RuntimeError):
    """Baseline invariant or CAS violation."""


@dataclass(frozen=True)
class BaselineIdentity:
    cutover_id: str
    price_source: str
    source_generation: str


def now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def logical_key_stock_dividend(code: str, ex_date: int) -> str:
    return f"stock_dividend|{code}|{int(ex_date)}"


def _fetchone_returning(conn, sql: str, params: Sequence):
    return conn.execute(sql, list(params)).fetchone()


def establish_discovery_baseline(conn, *, identity: BaselineIdentity,
                                 rows: Iterable[Sequence],
                                 payload_hash: Callable[[Sequence], str],
                                 require_status: str = "baseline_building") -> int:
    """Build/update a baseline only while the cutover is baseline_building."""
    status = conn.execute(
        "SELECT status FROM qfq_source_cutover WHERE cutover_id=?",
        [identity.cutover_id]).fetchone()
    if status is None or status[0] != require_status:
        raise DiscoveryBaselineError(
            f"cutover={identity.cutover_id!r} 不允许覆盖 discovery baseline，"
            f"status={status[0] if status else None!r}")
    count = 0
    ts = now_ts()
    for row in rows:
        if len(row) != 13:
            raise DiscoveryBaselineError(
                f"stock_dividend baseline row 必须为 13 字段，收到 {len(row)}")
        code, ex_date = row[0], row[1]
        key = logical_key_stock_dividend(str(code), int(ex_date))
        ph = payload_hash(row)
        conn.execute(
            "INSERT INTO qfq_discovery_baseline "
            "(cutover_id, price_source, source_generation, event_logical_key, "
            " applied_payload_hash, pending_trigger_id, pending_payload_hash, "
            " last_trigger_id, applied_at, baselined_at, updated_at) "
            "VALUES (?,?,?,?,?,NULL,NULL,NULL,?,?,?) "
            "ON CONFLICT (cutover_id, event_logical_key) DO UPDATE SET "
            "price_source=excluded.price_source, "
            "source_generation=excluded.source_generation, "
            "applied_payload_hash=excluded.applied_payload_hash, "
            "updated_at=excluded.updated_at",
            [identity.cutover_id, identity.price_source, identity.source_generation,
             key, ph, ts, ts, ts],
        )
        count += 1
    return count


def reserve_pending_slot(conn, *, identity: BaselineIdentity,
                         event_logical_key: str, trigger_id: str,
                         payload_hash: str) -> bool:
    """Atomically reserve a baseline pending slot.

    Returns ``True`` only for the caller that owns the slot.  Existing applied
    payloads and an existing pending trigger both return ``False``.
    """
    ts = now_ts()
    row = _fetchone_returning(
        conn,
        "UPDATE qfq_discovery_baseline SET pending_trigger_id=?, "
        "pending_payload_hash=?, updated_at=? "
        "WHERE cutover_id=? AND event_logical_key=? "
        "AND pending_trigger_id IS NULL "
        "AND applied_payload_hash IS DISTINCT FROM ? "
        "RETURNING cutover_id, event_logical_key",
        [trigger_id, payload_hash, ts, identity.cutover_id, event_logical_key,
         payload_hash],
    )
    if row is not None:
        return True
    row = _fetchone_returning(
        conn,
        "INSERT INTO qfq_discovery_baseline "
        "(cutover_id, price_source, source_generation, event_logical_key, "
        " applied_payload_hash, pending_trigger_id, pending_payload_hash, "
        " last_trigger_id, applied_at, baselined_at, updated_at) "
        "VALUES (?,?,?,?,NULL,?,?,NULL,NULL,?,?) "
        "ON CONFLICT (cutover_id, event_logical_key) DO NOTHING "
        "RETURNING cutover_id, event_logical_key",
        [identity.cutover_id, identity.price_source, identity.source_generation,
         event_logical_key, trigger_id, payload_hash, ts, ts],
    )
    return row is not None


def assert_existing_trigger_matches_pending_slot(conn, *, identity: BaselineIdentity,
                                                  event_logical_key: str,
                                                  trigger_id: str,
                                                  payload_hash: str) -> None:
    row = conn.execute(
        "SELECT pending_trigger_id, pending_payload_hash, price_source, "
        "source_generation FROM qfq_discovery_baseline "
        "WHERE cutover_id=? AND event_logical_key=?",
        [identity.cutover_id, event_logical_key]).fetchone()
    if row is None or row[0] != trigger_id or row[1] != payload_hash \
            or row[2] != identity.price_source or row[3] != identity.source_generation:
        raise DiscoveryBaselineError(
            f"trigger={trigger_id} 与 baseline pending slot 不一致: {row!r}")


def commit_pending_slot(conn, *, identity: BaselineIdentity,
                        event_logical_key: str, trigger_id: str,
                        payload_hash: str) -> str:
    """Advance ``applied_payload_hash`` using trigger-bound CAS.

    Returns ``committed`` for a successful CAS or ``idempotent`` when the same
    payload was already applied.  An empty result with a different pending
    trigger is a hard invariant failure.
    """
    ts = now_ts()
    row = _fetchone_returning(
        conn,
        "UPDATE qfq_discovery_baseline SET applied_payload_hash=?, "
        "pending_trigger_id=NULL, pending_payload_hash=NULL, last_trigger_id=?, "
        "applied_at=?, updated_at=? "
        "WHERE cutover_id=? AND event_logical_key=? "
        "AND pending_trigger_id=? AND pending_payload_hash=? "
        "RETURNING cutover_id, event_logical_key",
        [payload_hash, trigger_id, ts, ts, identity.cutover_id, event_logical_key,
         trigger_id, payload_hash],
    )
    if row is not None:
        return "committed"
    cur = conn.execute(
        "SELECT applied_payload_hash, pending_trigger_id, pending_payload_hash "
        "FROM qfq_discovery_baseline WHERE cutover_id=? AND event_logical_key=?",
        [identity.cutover_id, event_logical_key]).fetchone()
    if cur and cur[0] == payload_hash and cur[1] is None:
        return "idempotent"
    raise DiscoveryBaselineError(
        f"baseline commit CAS 失败 key={event_logical_key!r} trigger={trigger_id!r}: {cur!r}")


def audit_pending_slots(conn, *, identity: Optional[BaselineIdentity] = None) -> dict:
    where = ""
    params = []
    if identity is not None:
        where = "WHERE b.cutover_id=? AND b.price_source=? AND b.source_generation=?"
        params = [identity.cutover_id, identity.price_source, identity.source_generation]
    orphan = conn.execute(
        "SELECT COUNT(*) FROM qfq_discovery_baseline b "
        f"{where}{' AND' if where else 'WHERE'} b.pending_trigger_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM qfq_trigger_queue t "
        "WHERE t.trigger_id=b.pending_trigger_id)", params).fetchone()[0]
    mismatch_gen = conn.execute(
        "SELECT COUNT(*) FROM qfq_discovery_baseline b "
        f"{where}{' AND' if where else 'WHERE'} b.pending_trigger_id IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM qfq_trigger_queue t WHERE t.trigger_id=b.pending_trigger_id "
        "AND (t.price_source<>b.price_source OR t.source_generation<>b.source_generation "
        "OR t.cutover_id<>b.cutover_id))", params).fetchone()[0]
    mismatch_payload = conn.execute(
        "SELECT COUNT(*) FROM qfq_discovery_baseline b "
        f"{where}{' AND' if where else 'WHERE'} b.pending_trigger_id IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM qfq_trigger_queue t WHERE t.trigger_id=b.pending_trigger_id "
        "AND t.payload_hash<>b.pending_payload_hash)", params).fetchone()[0]
    return {"orphan_pending": int(orphan), "generation_mismatch": int(mismatch_gen),
            "payload_mismatch": int(mismatch_payload),
            "passed": int(orphan) == int(mismatch_gen) == int(mismatch_payload) == 0}
