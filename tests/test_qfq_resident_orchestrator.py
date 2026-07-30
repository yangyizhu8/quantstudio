"""qfq_resident_orchestrator 单元测试（fake 引擎 + FakeFreshFetcher，不依赖 xtquant / 不写正式库）。

聚焦编排层语义（引擎本体已有 batch1/batch2 测试覆盖，此处 monkeypatch 为 fake）：
- enabled=False → no-op（红线：disabled 模式保持旧行为）；
- require_bootstrap fail-closed（红线：未 bootstrap 不得处理 trigger / 推进水位）；
- scheduled → pending 到期晋升（红线：future 事件不得永久搁置）；
- stale in_progress 回收 + retryable_failed 到期领回（restart recovery）；
- e2e committed：trigger committed + pending_backfill resolved + 水位延迟提交；
- 失败退避：retryable_failed + next_retry_at + 精确欠账 + gate 不过 → 水位 held（红线：
  四价格表不得存在提前推进水位路径）；
- retry_max 耗尽 → dead_letter（含 dead_letter_at）；
- 崩溃恢复：engine committed 后崩溃 → 下一轮只补 trigger 状态，绝不重调引擎（红线）。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema
from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
from quantstudio.pipeline.qfq_fresh_capture import FakeFreshFetcher
from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator

BJ_TZ = timezone(timedelta(hours=8))


def _ms(s: str) -> int:
    """'2026-07-10' / '2026-07-10 09:30:00' → epoch-ms（+08 口径）。"""
    fmt = "%Y-%m-%d %H:%M:%S" if " " in s else "%Y-%m-%d"
    return int(datetime.strptime(s, fmt).replace(tzinfo=BJ_TZ).timestamp() * 1000)


AS_OF_MS = _ms("2026-07-28 09:00:00")
EX_PAST_MS = _ms("2026-07-10")
EX_FUTURE_MS = _ms("2026-08-15")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _make_ohlc(index_dates, prices):
    idx = pd.to_datetime(index_dates)
    rows = [{"open": o, "high": h, "low": l, "close": c} for (o, h, l, c) in prices]
    return pd.DataFrame(rows, index=idx)


NONE_DAILY = _make_ohlc(["2026-07-08", "2026-07-09", "2026-07-10"],
                        [(10, 11, 9, 10), (10.5, 11.5, 10, 11), (11, 12, 10.5, 11.5)])
FRONT_DAILY = NONE_DAILY * 0.9
NONE_MIN = _make_ohlc(["2026-07-10 09:30:00", "2026-07-10 09:31:00"],
                      [(10, 10.2, 9.9, 10.1), (10.1, 10.3, 10.0, 10.2)])
FRONT_MIN = NONE_MIN * 0.9


def _fetcher() -> FakeFreshFetcher:
    return FakeFreshFetcher({
        ("600000.SH", "1d"): (NONE_DAILY, FRONT_DAILY),
        ("600000.SH", "1m"): (NONE_MIN, FRONT_MIN),
    })


class _FakeCalendar:
    """引擎已被 monkeypatch，此对象只需非 None 通过前置校验。"""

    def is_trading_day(self, ms):  # pragma: no cover
        return True

    def prev_trading_day(self, ms):  # pragma: no cover
        return ms - 86_400_000


def _new_conn():
    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    # 最小价格表（编排器只读 MIN/MAX(time)）
    conn.execute("CREATE TABLE stock_daily (code VARCHAR, time BIGINT)")
    conn.execute("CREATE TABLE stock_minutes (code VARCHAR, time BIGINT)")
    conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
    conn.execute("CREATE TABLE etf_minutes (code VARCHAR, time BIGINT)")
    conn.execute("""
        CREATE TABLE source_watermark (
            source VARCHAR, table_name VARCHAR, freq VARCHAR,
            last_date BIGINT, last_batch_id VARCHAR, updated_at TIMESTAMP,
            PRIMARY KEY(source, table_name, freq))""")
    conn.execute("""
        CREATE TABLE stock_dividend (
            code VARCHAR, ex_date BIGINT, record_date BIGINT, ann_date BIGINT,
            end_date BIGINT, cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE,
            cash_div DOUBLE, stk_div DOUBLE, stk_bo_rate DOUBLE, stk_co_rate DOUBLE,
            div_rat DOUBLE, div_proc VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, ex_date))""")
    # 600000 存量区间
    conn.execute("INSERT INTO stock_daily VALUES ('600000', ?), ('600000', ?)",
                 [_ms("2026-07-08"), _ms("2026-07-10")])
    conn.execute("INSERT INTO stock_minutes VALUES ('600000', ?), ('600000', ?)",
                 [_ms("2026-07-10 09:30:00"), _ms("2026-07-10 09:31:00")])
    return conn


def _cfg(**over) -> QFQOrchestratorConfig:
    base = dict(enabled=True, require_bootstrap=False, price_source="xtquant")
    base.update(over)
    return QFQOrchestratorConfig.load(raw=base)


def _init_aux(aux_path: str) -> None:
    """建测试辅助库的因子快照表（qfq_maintenance 侧表，init_sqlite_schema 不含）。

    _observe_factors 对 adj_factor / fund_adj 均执行
    ``SELECT code, time, adj_factor FROM {table}``，故两表列名一致，空表即可。
    """
    aconn = sqlite3.connect(aux_path)
    try:
        aconn.execute(
            "CREATE TABLE IF NOT EXISTS adj_factor "
            "(code TEXT, time INTEGER, adj_factor REAL)")
        aconn.execute(
            "CREATE TABLE IF NOT EXISTS fund_adj "
            "(code TEXT, time INTEGER, adj_factor REAL)")
        aconn.commit()
    finally:
        aconn.close()


def _orch(cfg, tmpdir) -> QFQResidentOrchestrator:
    aux_path = os.path.join(tmpdir, "qfq_aux.db")
    _init_aux(aux_path)
    return QFQResidentOrchestrator(
        cfg, aux_db=aux_path,
        fetcher=_fetcher(), calendar=_FakeCalendar())


def _insert_dividend(conn, code="600000", ex_ms=EX_PAST_MS):
    conn.execute(
        "INSERT INTO stock_dividend (code, ex_date, cash_div, stk_div, div_rat, div_proc) "
        "VALUES (?,?,?,?,?, '实施')", [code, ex_ms, 0.5, 0.0, 0.0])


def _fake_engine_factory(status="committed", calls=None):
    """monkeypatch 用 fake apply_reanchor_for_security：写 committed 事件并返回结果。"""

    def fake(conn, *, asset_type, code, event_id=None, **kw):
        if calls is not None:
            calls.append({"code": code, "event_id": event_id, **kw})
        if status == "committed":
            now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT OR IGNORE INTO qfq_reanchor_event "
                "(event_id, event_type, asset_type, code, status, "
                " trigger_surface, created_at, first_seen_at, last_seen_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [event_id, "reanchor", asset_type, code, "committed",
                 kw.get("trigger_surface", "resident_v2"), now, now, now])
        return SimpleNamespace(status=status, event_id=event_id, error=(
            None if status == "committed" else f"fake_{status}"))
    return fake


@pytest.fixture()
def tmpdir_path():
    with tempfile.TemporaryDirectory() as d:
        yield d


# ---------------------------------------------------------------------------
# 红线 1：disabled → no-op
# ---------------------------------------------------------------------------
def test_disabled_noop(tmpdir_path):
    conn = _new_conn()
    orch = _orch(_cfg(enabled=False), tmpdir_path)
    _insert_dividend(conn)
    cid = orch.begin_cycle(conn)
    s = orch.run_post_ingest(conn, cycle_id=cid, run_id="r1", as_of_ms=AS_OF_MS)
    assert s.error == "orchestrator disabled"
    # 未处理任何 trigger、未动水位
    assert conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM source_watermark").fetchone()[0] == 0
    conn.close()


def test_can_coordinate_watermark_disabled():
    cfg = _cfg(enabled=False)
    assert not cfg.can_coordinate_watermark("stock_daily")
    cfg2 = _cfg(enabled=True)
    assert cfg2.can_coordinate_watermark("stock_daily")
    assert not cfg2.can_coordinate_watermark("stock_dividend")  # 非价格表


# ---------------------------------------------------------------------------
# 红线 2：require_bootstrap fail-closed
# ---------------------------------------------------------------------------
def test_require_bootstrap_fail_closed(tmpdir_path):
    conn = _new_conn()
    orch = _orch(_cfg(require_bootstrap=True), tmpdir_path)
    _insert_dividend(conn)
    cid = orch.begin_cycle(conn)
    orch.defer_watermark(conn, cycle_id=cid, source="tushare", table="stock_daily",
                         freq="daily", candidate_watermark=_ms("2026-07-28"))
    s = orch.run_post_ingest(conn, cycle_id=cid, run_id="r1", as_of_ms=AS_OF_MS)
    assert s.bootstrap_required is True
    # 不处理 trigger、水位 intent 停留 pending、source_watermark 未推进
    assert conn.execute("SELECT COUNT(*) FROM qfq_trigger_queue").fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM qfq_watermark_intent WHERE cycle_id=?", [cid]
    ).fetchone()[0] == "pending"
    assert conn.execute("SELECT COUNT(*) FROM source_watermark").fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM qfq_cycle_run WHERE cycle_id=?", [cid]).fetchone()[0] == "failed"
    conn.close()


# ---------------------------------------------------------------------------
# 红线 3：scheduled 到期晋升
# ---------------------------------------------------------------------------
def test_promote_scheduled_due(tmpdir_path):
    conn = _new_conn()
    orch = _orch(_cfg(), tmpdir_path)
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    # 一个已到期的 scheduled + 一个未到期的
    for tid, eff in [("t_due", EX_PAST_MS), ("t_future", EX_FUTURE_MS)]:
        conn.execute(
            "INSERT INTO qfq_trigger_queue (trigger_id, asset_type, code, trigger_type, "
            " detection_source, effective_date, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?, 'scheduled', ?, ?)",
            [tid, "STOCK", "600000", "stock_dividend", "stock_dividend", eff, now, now])
    n = orch.promote_scheduled_due(conn, as_of_ms=AS_OF_MS)
    assert n == 1
    assert conn.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t_due'"
                        ).fetchone()[0] == "pending"
    assert conn.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t_future'"
                        ).fetchone()[0] == "scheduled"
    conn.close()


# ---------------------------------------------------------------------------
# restart recovery：stale in_progress + retryable_failed 到期
# ---------------------------------------------------------------------------
def test_recover_stale_and_retry_due(tmpdir_path):
    conn = _new_conn()
    orch = _orch(_cfg(claim_lease_sec=60), tmpdir_path)
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    stale_at = (datetime.now(BJ_TZ) - timedelta(seconds=600)).isoformat(timespec="seconds")
    # attempt=0 → 回 pending；attempt=2 → retryable_failed 且必须带 next_retry_at
    for tid, att in [("t_a0", 0), ("t_a2", 2)]:
        conn.execute(
            "INSERT INTO qfq_trigger_queue (trigger_id, asset_type, code, trigger_type, "
            " detection_source, effective_date, status, attempt_count, claimed_by, "
            " claimed_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?, 'in_progress', ?, 'dead_run', ?, ?, ?)",
            [tid, "STOCK", "600000", "stock_dividend", "stock_dividend",
             EX_PAST_MS, att, stale_at, now, now])
    n = orch.recover_stale_in_progress(conn, "r2")
    assert n == 2
    st0, nr0 = conn.execute(
        "SELECT status, next_retry_at FROM qfq_trigger_queue WHERE trigger_id='t_a0'"
    ).fetchone()
    st2, nr2 = conn.execute(
        "SELECT status, next_retry_at FROM qfq_trigger_queue WHERE trigger_id='t_a2'"
    ).fetchone()
    assert st0 == "pending"
    assert st2 == "retryable_failed" and nr2 is not None  # 无 next_retry_at 会永久卡死
    # 到期领回
    n2 = orch.recover_pending_due(conn, "r2")
    assert n2 == 1
    assert conn.execute("SELECT status FROM qfq_trigger_queue WHERE trigger_id='t_a2'"
                        ).fetchone()[0] == "pending"
    conn.close()


# ---------------------------------------------------------------------------
# e2e：发现 → 领取 → fake 引擎 committed → 水位提交
# ---------------------------------------------------------------------------
def test_e2e_commit_flow(tmpdir_path, monkeypatch):
    import quantstudio.pipeline.qfq_reanchor_engine as eng
    calls = []
    monkeypatch.setattr(eng, "apply_reanchor_for_security",
                        _fake_engine_factory("committed", calls))
    conn = _new_conn()
    orch = _orch(_cfg(), tmpdir_path)
    _insert_dividend(conn)
    # 预置一条同券 pending 欠账（应被 committed 解决）
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO qfq_pending_backfill (asset_type, code, table_name, freq, "
        " range_start, range_end, reason, status, created_at, updated_at) "
        "VALUES ('STOCK','600000','stock_daily','daily',?,?,'test','pending',?,?)",
        [EX_PAST_MS, EX_PAST_MS, now, now])
    cid = orch.begin_cycle(conn)
    orch.defer_watermark(conn, cycle_id=cid, source="tushare", table="stock_daily",
                         freq="daily", candidate_watermark=_ms("2026-07-28"))
    s = orch.run_post_ingest(conn, cycle_id=cid, run_id="r3", as_of_ms=AS_OF_MS)

    assert s.triggers_found == 1
    assert s.claimed == 1
    assert s.committed == 1
    assert s.status == "finalized"
    # trigger committed + last_event_id 与事件一致（崩溃恢复锚点）
    st, ev = conn.execute(
        "SELECT status, last_event_id FROM qfq_trigger_queue").fetchone()
    assert st == "committed" and ev is not None
    assert conn.execute(
        "SELECT status FROM qfq_reanchor_event WHERE event_id=?", [ev]
    ).fetchone()[0] == "committed"
    # 阶段4：编排器显式选择 authoritative rebase 模型 + 传递 capture 审计二元组 + resident trigger_surface
    assert calls[0]["model"] == "fresh_authoritative_rebase"
    assert calls[0]["fresh_source"] == "xtquant"
    assert calls[0]["trigger_surface"] == "resident_v2"
    assert calls[0]["fresh_capture_id"]
    assert len(calls[0]["fresh_metadata_sha256"]) == 64
    assert tuple(calls[0]["ex_dates_ms"]) == (EX_PAST_MS,)
    # pending 欠账被解决
    assert conn.execute(
        "SELECT status FROM qfq_pending_backfill WHERE code='600000'"
    ).fetchone()[0] == "resolved"
    # 水位延迟提交：intent committed + source_watermark 实际推进
    assert s.watermarks_committed == 1
    assert conn.execute(
        "SELECT status FROM qfq_watermark_intent WHERE cycle_id=?", [cid]
    ).fetchone()[0] == "committed"
    assert conn.execute(
        "SELECT last_date FROM source_watermark WHERE table_name='stock_daily'"
    ).fetchone()[0] == _ms("2026-07-28")
    # cycle 终态
    assert conn.execute(
        "SELECT status FROM qfq_cycle_run WHERE cycle_id=?", [cid]
    ).fetchone()[0] == "finalized"
    conn.close()


# ---------------------------------------------------------------------------
# 失败退避：retryable_failed + 精确欠账 + gate 不过 → 水位 held
# ---------------------------------------------------------------------------
def test_failure_backoff_and_watermark_hold(tmpdir_path, monkeypatch):
    import quantstudio.pipeline.qfq_reanchor_engine as eng
    monkeypatch.setattr(eng, "apply_reanchor_for_security",
                        _fake_engine_factory("failed"))
    conn = _new_conn()
    orch = _orch(_cfg(retry_max=5), tmpdir_path)
    _insert_dividend(conn)
    cid = orch.begin_cycle(conn)
    orch.defer_watermark(conn, cycle_id=cid, source="tushare", table="stock_daily",
                         freq="daily", candidate_watermark=_ms("2026-07-28"))
    s = orch.run_post_ingest(conn, cycle_id=cid, run_id="r4", as_of_ms=AS_OF_MS)

    assert s.retryable_failed == 1
    st, att, nra = conn.execute(
        "SELECT status, attempt_count, next_retry_at FROM qfq_trigger_queue").fetchone()
    assert st == "retryable_failed" and att == 1 and nra is not None
    # 精确欠账（STOCK 两张表）
    rows = conn.execute(
        "SELECT table_name, status FROM qfq_pending_backfill WHERE code='600000' "
        "ORDER BY table_name").fetchall()
    assert {r[0] for r in rows} == {"stock_daily", "stock_minutes"}
    assert all(r[1] == "retryable_failed" for r in rows)
    # gate 不过 → 水位 held、source_watermark 未推进（红线：无提前推进路径）
    assert s.status == "finalized_held"
    assert s.watermarks_held == 1
    assert conn.execute(
        "SELECT status, hold_reason FROM qfq_watermark_intent WHERE cycle_id=?", [cid]
    ).fetchone()[0] == "held"
    assert conn.execute("SELECT COUNT(*) FROM source_watermark").fetchone()[0] == 0
    conn.close()


# ---------------------------------------------------------------------------
# retry_max 耗尽 → dead_letter
# ---------------------------------------------------------------------------
def test_dead_letter_on_retry_exhaustion(tmpdir_path, monkeypatch):
    import quantstudio.pipeline.qfq_reanchor_engine as eng
    monkeypatch.setattr(eng, "apply_reanchor_for_security",
                        _fake_engine_factory("failed"))
    conn = _new_conn()
    orch = _orch(_cfg(retry_max=1), tmpdir_path)
    _insert_dividend(conn)
    cid = orch.begin_cycle(conn)
    s = orch.run_post_ingest(conn, cycle_id=cid, run_id="r5", as_of_ms=AS_OF_MS)
    assert s.dead_letter == 1
    st, dla = conn.execute(
        "SELECT status, dead_letter_at FROM qfq_trigger_queue").fetchone()
    assert st == "dead_letter" and dla is not None
    # 欠账登记为 dead_letter
    assert conn.execute(
        "SELECT COUNT(*) FROM qfq_pending_backfill WHERE status='dead_letter'"
    ).fetchone()[0] == 2
    # gate 必不过（dead_letter_max 默认 0）
    assert s.status == "finalized_held"
    conn.close()


# ---------------------------------------------------------------------------
# 红线：崩溃恢复 —— engine committed 后不得重复调引擎
# ---------------------------------------------------------------------------
def test_crash_recovery_skips_engine(tmpdir_path, monkeypatch):
    import quantstudio.pipeline.qfq_reanchor_engine as eng

    def must_not_call(*a, **kw):  # pragma: no cover
        raise AssertionError("崩溃恢复路径不得重调引擎")
    monkeypatch.setattr(eng, "apply_reanchor_for_security", must_not_call)

    conn = _new_conn()
    orch = _orch(_cfg(), tmpdir_path)
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    ev = "e" * 40
    # 模拟：上一轮引擎已 committed（事件在库），trigger 状态未更新即崩溃
    conn.execute(
        "INSERT INTO qfq_reanchor_event (event_id, event_type, asset_type, code, "
        " status, trigger_surface, created_at, first_seen_at, last_seen_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [ev, "reanchor", "STOCK", "600000", "committed", "resident_v2", now, now, now])
    conn.execute(
        "INSERT INTO qfq_trigger_queue (trigger_id, asset_type, code, trigger_type, "
        " detection_source, effective_date, payload_hash, status, last_event_id, "
        " created_at, updated_at) "
        "VALUES ('t_crash','STOCK','600000','stock_dividend','stock_dividend',?,"
        " 'ph','pending',?,?,?)",
        [EX_PAST_MS, ev, now, now])
    cid = orch.begin_cycle(conn)
    s = orch.run_post_ingest(conn, cycle_id=cid, run_id="r6", as_of_ms=AS_OF_MS)
    # 只补状态，不重算
    assert s.committed == 1
    st, lev = conn.execute(
        "SELECT status, last_event_id FROM qfq_trigger_queue WHERE trigger_id='t_crash'"
    ).fetchone()
    assert st == "committed" and lev == ev
    conn.close()


# ---------------------------------------------------------------------------
# bootstrap：plan / run / completed 推进（fail-closed 解锁路径）
# ---------------------------------------------------------------------------
def test_bootstrap_plan_run_completes(tmpdir_path, monkeypatch):
    import quantstudio.pipeline.qfq_reanchor_engine as eng
    monkeypatch.setattr(eng, "apply_reanchor_for_security",
                        _fake_engine_factory("committed"))
    conn = _new_conn()
    orch = _orch(_cfg(require_bootstrap=True), tmpdir_path)
    _insert_dividend(conn)
    assert orch.bootstrap_completed(conn) is False
    plan = orch.bootstrap_plan(conn, as_of_ms=AS_OF_MS)
    assert plan.total == 1 and plan.items == [("STOCK", "600000")]
    run_id = conn.execute(
        "SELECT bootstrap_run_id FROM qfq_bootstrap_run").fetchone()[0]
    r = orch.bootstrap_run(conn, run_id=run_id, as_of_ms=AS_OF_MS,
                           fetcher=_fetcher())
    assert r["completed"] == 1 and r["remaining"] == 0
    # run 状态推进为 completed → fail-closed 解锁
    assert conn.execute(
        "SELECT status FROM qfq_bootstrap_run WHERE bootstrap_run_id=?", [run_id]
    ).fetchone()[0] == "completed"
    assert orch.bootstrap_completed(conn) is True
    audit = orch.bootstrap_audit(conn, run_id)
    assert audit["clean"] is True
    conn.close()
