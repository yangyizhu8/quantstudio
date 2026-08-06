"""B-5 unit tests: discovery-baseline CAS, cutover state and aux isolation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.qfq_aux_router import AuxDbRouter, AuxRouteError
from quantstudio.pipeline.qfq_cutover import (
    CutoverCASFailed, CutoverError, create_cutover, cutover_status,
    resolve_runtime_identity, transition_cutover,
)
from quantstudio.pipeline.qfq_discovery_baseline import (
    BaselineIdentity, DiscoveryBaselineError, audit_pending_slots,
    commit_pending_slot, establish_discovery_baseline,
    logical_key_stock_dividend, reserve_pending_slot,
)
from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema


def _conn():
    c = duckdb.connect(":memory:")
    init_duckdb_schema(c)
    return c


def _create(c, cut="cut_b5", status="baseline_building"):
    create_cutover(c, cutover_id=cut, price_source="mcp",
                   source_generation="mcp-gen1", schema_version="reanchor-2.1",
                   baseline_version="baseline-v1", aux_db_path="aux.db")
    if status != "planned":
        transition_cutover(c, cutover_id=cut, new_status="prepared")
    if status in ("baseline_building", "baseline_validated"):
        transition_cutover(c, cutover_id=cut, new_status="baseline_building")
    if status == "baseline_validated":
        transition_cutover(c, cutover_id=cut, new_status="baseline_validated")


def _row(ph="A"):
    return ("600000", 1700000000000, 1690000000000, 1680000000000,
            1670000000000, 1.0, 0.9, 0.8, 0.1, 0.2, 0.3, 0.4, "实施")


def _hash(row):
    return "hash-" + str(row[5])


def test_baseline_bootstrap_only_baseline_building():
    c = _conn(); _create(c)
    ident = BaselineIdentity("cut_b5", "mcp", "mcp-gen1")
    assert establish_discovery_baseline(c, identity=ident, rows=[_row()], payload_hash=_hash) == 1
    key = logical_key_stock_dividend("600000", 1700000000000)
    assert c.execute("SELECT applied_payload_hash FROM qfq_discovery_baseline WHERE cutover_id=? AND event_logical_key=?",
                     [ident.cutover_id, key]).fetchone()[0] == "hash-1.0"
    transition_cutover(c, cutover_id="cut_b5", new_status="baseline_validated")
    with pytest.raises(DiscoveryBaselineError):
        establish_discovery_baseline(c, identity=ident, rows=[_row()], payload_hash=_hash)


def test_reserve_new_event_and_commit_returning_even_rowcount_minus_one():
    c = _conn(); _create(c)
    ident = BaselineIdentity("cut_b5", "mcp", "mcp-gen1")
    key = logical_key_stock_dividend("600000", 1700000000000)
    assert reserve_pending_slot(c, identity=ident, event_logical_key=key,
                                trigger_id="trig1", payload_hash="ph1") is True
    c.execute("INSERT INTO qfq_trigger_queue (trigger_id,asset_type,code,trigger_type,detection_source,"
              "effective_date,payload_hash,status,trigger_id_version,price_source,source_generation,cutover_id,created_at,updated_at) "
              "VALUES ('trig1','STOCK','600000','stock_dividend','stock_dividend',1,'ph1','pending',2,'mcp','mcp-gen1','cut_b5',NOW(),NOW())")
    cur = c.execute("UPDATE qfq_trigger_queue SET updated_at=NOW() WHERE trigger_id='trig1'")
    assert cur.rowcount == -1
    assert commit_pending_slot(c, identity=ident, event_logical_key=key,
                               trigger_id="trig1", payload_hash="ph1") == "committed"
    assert commit_pending_slot(c, identity=ident, event_logical_key=key,
                               trigger_id="trig1", payload_hash="ph1") == "idempotent"


def test_single_pending_serializes_newer_payload():
    c = _conn(); _create(c)
    ident = BaselineIdentity("cut_b5", "mcp", "mcp-gen1")
    key = logical_key_stock_dividend("600000", 1700000000000)
    assert reserve_pending_slot(c, identity=ident, event_logical_key=key,
                                trigger_id="trig_b", payload_hash="B")
    assert not reserve_pending_slot(c, identity=ident, event_logical_key=key,
                                    trigger_id="trig_c", payload_hash="C")


def test_pending_slot_audit_detects_orphan():
    c = _conn(); _create(c)
    ident = BaselineIdentity("cut_b5", "mcp", "mcp-gen1")
    assert reserve_pending_slot(c, identity=ident, event_logical_key="stock_dividend|x|1",
                                trigger_id="missing", payload_hash="ph")
    report = audit_pending_slots(c, identity=ident)
    assert report["orphan_pending"] == 1
    assert report["passed"] is False


def test_cutover_state_machine_and_runtime_identity():
    c = _conn(); _create(c, status="baseline_validated")
    cfg = QFQOrchestratorConfig.from_dict({"enabled": True, "price_source": "mcp",
                                           "source_generation": "mcp-gen1",
                                           "cutover_id": "cut_b5"})
    ident = resolve_runtime_identity(c, cfg, allow_prepared=True)
    assert ident == {"price_source": "mcp", "source_generation": "mcp-gen1",
                     "cutover_id": "cut_b5"}
    with pytest.raises(CutoverError):
        transition_cutover(c, cutover_id="cut_b5", new_status="prepared")
    with pytest.raises(CutoverCASFailed):
        transition_cutover(c, cutover_id="cut_b5", new_status="active",
                           expected_status="planned")


def test_mcp_without_cutover_fails_closed_when_generation_is_explicit():
    c = _conn()
    cfg = QFQOrchestratorConfig.from_dict({"enabled": True, "price_source": "mcp",
                                           "source_generation": "mcp-gen1",
                                           "cutover_id": "cut_missing"})
    with pytest.raises(CutoverError):
        resolve_runtime_identity(c, cfg, allow_prepared=True)


def test_aux_router_isolates_generation_and_requires_explicit_init(tmp_path):
    cfg = tmp_path / "qfq_aux_paths.json"
    cfg.write_text(json.dumps({"default": "legacy.db", "generations": {
        "xtquant-legacy": "legacy.db", "mcp-gen1": "mcp.db"}}), encoding="utf-8")
    router = AuxDbRouter(config_path=cfg)
    assert router.path_for("xtquant-legacy").name == "legacy.db"
    assert router.path_for("mcp-gen1").name == "mcp.db"
    with pytest.raises(AuxRouteError):
        router.path_for("mcp-gen1", require_exists=True)
    route = router.initialize_explicit(source_generation="mcp-gen1", cutover_id="cut_b5")
    assert route.exists and route.path.name == "mcp.db"
    with sqlite3.connect(route.path) as s:
        assert s.execute("SELECT name FROM sqlite_master WHERE name='qfq_factor_observation'").fetchone()
    with pytest.raises(AuxRouteError):
        router.path_for("mcp-gen2")


def test_dynamic_runtime_routes_aux_and_claims_only_current_generation(tmp_path):
    main = tmp_path / "quantstudio.db"
    legacy = tmp_path / "legacy.db"
    mcp_aux = tmp_path / "mcp_aux.db"
    c = duckdb.connect(str(main))
    init_duckdb_schema(c)
    _create(c, cut="cut_dynamic", status="baseline_validated")
    from quantstudio.pipeline.qfq_aux_router import AuxDbRouter
    AuxDbRouter(routes={"mcp-gen1": mcp_aux}).initialize_explicit(
        source_generation="mcp-gen1", cutover_id="cut_dynamic")
    # Freeze the route in the cutover row; runtime must not use the caller's legacy path.
    c.execute("UPDATE qfq_source_cutover SET aux_db_path=? WHERE cutover_id=?",
              [str(mcp_aux), "cut_dynamic"])
    cfg = QFQOrchestratorConfig.from_dict({
        "enabled": True, "require_bootstrap": False, "price_source": "mcp",
        "generation_mode": "dynamic", "source_generation": "mcp-gen1",
        "cutover_id": "cut_dynamic",
    })
    from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator
    orch = QFQResidentOrchestrator(cfg, main_db=str(main), aux_db=str(legacy))
    ident = orch.prepare_runtime(c, require_aux=True)
    assert ident == {"price_source": "mcp", "source_generation": "mcp-gen1",
                     "cutover_id": "cut_dynamic"}
    assert Path(orch.aux_db).resolve() == mcp_aux.resolve()
    now = "2026-08-06 12:00:00"
    c.execute("INSERT INTO qfq_trigger_queue "
              "(trigger_id,asset_type,code,trigger_type,detection_source,effective_date,"
              "payload_hash,status,trigger_id_version,price_source,source_generation,cutover_id,created_at,updated_at) "
              "VALUES ('legacy','STOCK','600000','stock_dividend','stock_dividend',1,'l','pending',1,'xtquant','xtquant-legacy','legacy-xtquant-pre-cutover',?,?)",
              [now, now])
    c.execute("INSERT INTO qfq_trigger_queue "
              "(trigger_id,asset_type,code,trigger_type,detection_source,effective_date,"
              "payload_hash,status,trigger_id_version,price_source,source_generation,cutover_id,created_at,updated_at) "
              "VALUES ('mcp','STOCK','600000','stock_dividend','stock_dividend',1,'m','pending',2,'mcp','mcp-gen1','cut_dynamic',?,?)",
              [now, now])
    units = orch._claim_and_merge(c, cycle_id="cycle", run_id="run", as_of_ms=2)
    assert [x["triggers"] for x in units] == [["mcp"]]
    c.close()


def test_plain_transition_cannot_bypass_active_pointer():
    c = _conn(); _create(c, cut="cut_active", status="baseline_validated")
    with pytest.raises(CutoverError):
        transition_cutover(c, cutover_id="cut_active", new_status="active",
                           expected_status="baseline_validated")


def test_legacy_capture_schema_is_tolerated_only_for_legacy_identity():
    from quantstudio.pipeline.qfq_fresh_capture import (
        CAPTURE_ACTION_RECOLLECT_OK, CaptureContentConflict, resolve_fresh_capture,
    )
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE qfq_reanchor_event(status VARCHAR, minute_ratio_plan VARCHAR)")
    c.execute("""CREATE TABLE qfq_fresh_capture(
        capture_id VARCHAR PRIMARY KEY, asset_type VARCHAR, code VARCHAR, source VARCHAR,
        daily_range_start BIGINT, daily_range_end BIGINT, minute_range_start BIGINT,
        minute_range_end BIGINT, daily_sha256 VARCHAR, minute_sha256 VARCHAR,
        metadata_sha256 VARCHAR, status VARCHAR)""")
    c.execute("INSERT INTO qfq_fresh_capture VALUES ('cap','STOCK','600000','xtquant',1,2,3,4,'d','m','meta','captured')")
    assert resolve_fresh_capture(
        c, capture_id="cap", asset_type="STOCK", code="600000", source="xtquant",
        daily_range_start=1, daily_range_end=2, minute_range_start=3, minute_range_end=4,
        daily_sha256="d", minute_sha256="m", metadata_sha256="meta",
    ) == CAPTURE_ACTION_RECOLLECT_OK
    with pytest.raises(CaptureContentConflict):
        resolve_fresh_capture(
            c, capture_id="cap", asset_type="STOCK", code="600000", source="mcp",
            source_generation="mcp-gen1", cutover_id="cut_dynamic",
            daily_range_start=1, daily_range_end=2, minute_range_start=3, minute_range_end=4,
            daily_sha256="d", minute_sha256="m", metadata_sha256="meta",
        )
    c.close()
