"""tests/test_daemon_qfq_integration.py — Task #83 daemon 接入 QFQ 编排的红线测试。

覆盖用户红线清单（daemon 侧接入，编排器本体见 test_qfq_resident_orchestrator.py）：
  1. disabled 模式保持旧行为（水位推进逐位走 writer.advance_watermark 旧路径）；
  2. 四价格表不得存在提前推进水位的代码路径：
     - enabled + 周期已开 → defer（写 qfq_watermark_intent pending，不动 source_watermark）；
     - enabled + 无活跃周期 → fail-closed 保持水位（丢弃 candidate，不推进也不 defer）；
  3. 不是"只加了 daemon 调用"：trigger/watermark/restart 语义齐备
     - qfq_begin_cycle 开新周期时 supersede 崩溃残留 pending intent（restart 清障）；
     - qfq_run_post_ingest 消费 run_id、清 cycle_id；
  4. quality_audit QFQ 专项门控：qfq_thresholds=None 完全跳过（disabled 不新增失败）；
     非 None 时 dead_letter / held intent 被检出；
  5. config_lint qfq_orchestrator 块 fail-fast：缺失安全、非法 ERROR、
     enabled 但依赖源未启用 ERROR、未知键 WARN；
  6. daemon_lifecycle.run_one_cycle 接入：enabled 走 begin→post-ingest 并写
     qfq_phase run_state；begin 失败 fail-closed 任务照跑；stop 中断跳过 post-ingest。

全部用内存 DuckDB + fake writer/collector，不依赖 xtquant、不碰正式库。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema

BJ_TZ = timezone(timedelta(hours=8))
WM = 1_753_632_000_000  # 任意候选水位（epoch-ms）


# ---------------------------------------------------------------------------
# 公共 fakes
# ---------------------------------------------------------------------------

class _FakeWriter:
    """记录 advance_watermark 调用的假 writer（shared_conn 用内存 DuckDB）。"""

    def __init__(self, conn):
        self._conn = conn
        self.advanced = []          # 旧路径调用记录
        self.db_path = ":memory:"

    def shared_conn(self):
        return self._conn

    def advance_watermark(self, source, table, freq, last_date, batch_id):
        self.advanced.append((source, table, freq, last_date, batch_id))


class _FakeCalendar:
    def is_trading_day(self, ms):  # pragma: no cover
        return True

    def prev_trading_day(self, ms):  # pragma: no cover
        return ms - 86_400_000


def _qfq_conn():
    """内存 DuckDB + QFQ schema + source_watermark 表。"""
    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_watermark (
            source VARCHAR, table_name VARCHAR, freq VARCHAR,
            last_date BIGINT, last_batch_id VARCHAR, updated_at TIMESTAMP,
            PRIMARY KEY(source, table_name, freq))""")
    # 事件发现扫描依赖（空表即可，不注入分红 → post-ingest 零 trigger）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_dividend (
            code VARCHAR, ex_date BIGINT, record_date BIGINT, ann_date BIGINT,
            end_date BIGINT, cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE,
            cash_div DOUBLE, stk_div DOUBLE, stk_bo_rate DOUBLE, stk_co_rate DOUBLE,
            div_rat DOUBLE, div_proc VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, ex_date))""")
    return conn


def _make_collector(conn, qfq_block=None, tmpdir=None, with_orch=True):
    """绕过 from_configs 构造最小 ResidentCollector（只带 QFQ 方法所需字段）。"""
    from quantstudio.pipeline.daemon import ResidentCollector
    c = ResidentCollector.__new__(ResidentCollector)
    c.tasks_cfg = {"qfq_orchestrator": qfq_block} if qfq_block is not None else {}
    c.writer = _FakeWriter(conn)
    c._qfq_cfg_obj = None
    c._qfq_orch = None
    c._qfq_cycle_id = None
    if with_orch and qfq_block and qfq_block.get("enabled"):
        # 预置编排器（fake fetcher/calendar，aux 库落 tmpdir），
        # 绕开 _qfq_orchestrator 里的 XtquantFreshFetcher 构造
        from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator
        from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
        from quantstudio.pipeline.qfq_fresh_capture import FakeFreshFetcher
        aux = None
        if tmpdir is not None:
            aux = os.path.join(str(tmpdir), "qfq_aux.db")
            a = sqlite3.connect(aux)
            a.execute("CREATE TABLE IF NOT EXISTS adj_factor "
                      "(code TEXT, time INTEGER, adj_factor REAL)")
            a.execute("CREATE TABLE IF NOT EXISTS fund_adj "
                      "(code TEXT, time INTEGER, adj_factor REAL)")
            a.commit()
            a.close()
        c._qfq_orch = QFQResidentOrchestrator(
            QFQOrchestratorConfig.from_dict(dict(qfq_block)),
            aux_db=aux, fetcher=FakeFreshFetcher({}), calendar=_FakeCalendar(),
            watermark_advancer=c.writer.advance_watermark)
    return c


_ENABLED = {"enabled": True, "require_bootstrap": False, "price_source": "xtquant"}


# ===========================================================================
# 红线 1：disabled → 水位推进逐位走旧路径
# ===========================================================================

class TestWatermarkRedlines:

    def test_disabled_price_table_uses_legacy_path(self):
        conn = _qfq_conn()
        c = _make_collector(conn)  # 无 qfq 块 → enabled=False 安全默认
        c._advance_or_defer_watermark("xtquant", "stock_daily", "daily", WM, "b1")
        assert c.writer.advanced == [("xtquant", "stock_daily", "daily", WM, "b1")]
        # 不写任何 intent
        assert conn.execute(
            "SELECT COUNT(*) FROM qfq_watermark_intent").fetchone()[0] == 0
        conn.close()

    def test_disabled_explicit_false_uses_legacy_path(self):
        conn = _qfq_conn()
        c = _make_collector(conn, qfq_block={"enabled": False}, with_orch=False)
        c._advance_or_defer_watermark("tushare", "etf_minutes", "1min", WM, "b2")
        assert len(c.writer.advanced) == 1
        conn.close()

    def test_enabled_non_price_table_uses_legacy_path(self, tmp_path):
        conn = _qfq_conn()
        c = _make_collector(conn, qfq_block=_ENABLED, tmpdir=tmp_path)
        c._qfq_cycle_id = "cyc_x"  # 即使周期开着，非价格表也走旧路径
        c._advance_or_defer_watermark("tushare", "stock_dividend", "daily", WM, "b3")
        assert len(c.writer.advanced) == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM qfq_watermark_intent").fetchone()[0] == 0
        conn.close()

    def test_enabled_with_cycle_defers_not_advances(self, tmp_path):
        """红线：enabled + 周期开 → 写 pending intent，绝不动 source_watermark。"""
        conn = _qfq_conn()
        c = _make_collector(conn, qfq_block=_ENABLED, tmpdir=tmp_path)
        cid = c.qfq_begin_cycle()
        assert cid is not None and c._qfq_cycle_id == cid
        for table, freq in [("stock_daily", "daily"), ("stock_minutes", "1min"),
                            ("etf_daily", "daily"), ("etf_minutes", "1min")]:
            c._advance_or_defer_watermark("xtquant", table, freq, WM, "b4")
        # 四表全部 defer，旧路径零调用
        assert c.writer.advanced == []
        rows = conn.execute(
            "SELECT table_name, status FROM qfq_watermark_intent "
            "WHERE cycle_id=? ORDER BY table_name", [cid]).fetchall()
        assert {r[0] for r in rows} == {"stock_daily", "stock_minutes",
                                        "etf_daily", "etf_minutes"}
        assert all(r[1] == "pending" for r in rows)
        assert conn.execute(
            "SELECT COUNT(*) FROM source_watermark").fetchone()[0] == 0
        conn.close()

    def test_enabled_without_cycle_fail_closed_holds(self, tmp_path):
        """红线：enabled 但无活跃周期 → 既不推进也不 defer（水位保持）。"""
        conn = _qfq_conn()
        c = _make_collector(conn, qfq_block=_ENABLED, tmpdir=tmp_path)
        assert c._qfq_cycle_id is None
        c._advance_or_defer_watermark("xtquant", "stock_daily", "daily", WM, "b5")
        assert c.writer.advanced == []
        assert conn.execute(
            "SELECT COUNT(*) FROM qfq_watermark_intent").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM source_watermark").fetchone()[0] == 0
        conn.close()


# ===========================================================================
# 红线 3：restart 语义（begin_cycle supersede 清障）+ 周期生命周期
# ===========================================================================

class TestCycleLifecycle:

    def test_begin_cycle_disabled_returns_none(self):
        conn = _qfq_conn()
        c = _make_collector(conn)
        assert c.qfq_begin_cycle() is None
        assert c.qfq_run_post_ingest("r0") is None
        conn.close()

    def test_begin_cycle_supersedes_stale_pending_intents(self, tmp_path):
        """崩溃残留 pending intent（归属周期已终结/缺失）→ 新周期开启时清障。"""
        conn = _qfq_conn()
        c = _make_collector(conn, qfq_block=_ENABLED, tmpdir=tmp_path)
        # 残留 1：归属周期不存在（qfq_cycle_run 无记录 = 建表前崩溃）
        conn.execute(
            "INSERT INTO qfq_watermark_intent (cycle_id, source, table_name, freq, "
            " candidate_watermark, status) VALUES ('cyc_dead1','xtquant',"
            " 'stock_daily','daily', ?, 'pending')", [str(WM)])
        # 残留 2：归属周期已 failed
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO qfq_cycle_run (cycle_id, phase, status, started_at, updated_at) "
            "VALUES ('cyc_dead2','post_ingest','failed', ?, ?)", [now, now])
        conn.execute(
            "INSERT INTO qfq_watermark_intent (cycle_id, source, table_name, freq, "
            " candidate_watermark, status) VALUES ('cyc_dead2','xtquant',"
            " 'etf_daily','daily', ?, 'pending')", [str(WM)])
        cid = c.qfq_begin_cycle()
        assert cid is not None
        stale = conn.execute(
            "SELECT status FROM qfq_watermark_intent "
            "WHERE cycle_id IN ('cyc_dead1','cyc_dead2')").fetchall()
        assert [s[0] for s in stale] == ["superseded", "superseded"]
        # 水位从未被这些残留推进
        assert conn.execute(
            "SELECT COUNT(*) FROM source_watermark").fetchone()[0] == 0
        conn.close()

    def test_post_ingest_clears_cycle_and_active_intents_survive(self, tmp_path):
        """post-ingest 后 cycle_id 清空；本周期 intent 不被自己 supersede。"""
        conn = _qfq_conn()
        c = _make_collector(conn, qfq_block=_ENABLED, tmpdir=tmp_path)
        cid = c.qfq_begin_cycle()
        c._advance_or_defer_watermark("xtquant", "stock_daily", "daily", WM, "b6")
        summary = c.qfq_run_post_ingest("r1")
        assert summary is not None and summary.cycle_id == cid
        assert c._qfq_cycle_id is None          # 周期已消费
        # 无 trigger → gate 通过 → 水位提交（走 watermark_advancer 即旧 writer 入口）
        assert summary.status == "finalized"
        assert c.writer.advanced == [("xtquant", "stock_daily", "daily", str(WM), f"qfq_{cid}")] \
            or len(c.writer.advanced) == 1  # batch_id 形态由编排器决定，只保证恰好一次提交
        assert conn.execute(
            "SELECT status FROM qfq_watermark_intent WHERE cycle_id=?", [cid]
        ).fetchone()[0] == "committed"
        conn.close()


# ===========================================================================
# 红线 4：quality_audit QFQ 门控（None=跳过；非 None 检出）
# ===========================================================================

def _audit_db(tmp_path, dead_letter=0, held=0):
    db = tmp_path / "audit.db"
    conn = duckdb.connect(str(db))
    init_duckdb_schema(conn)
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(dead_letter):
        conn.execute(
            "INSERT INTO qfq_trigger_queue (trigger_id, asset_type, code, "
            " trigger_type, detection_source, effective_date, status, "
            " created_at, updated_at) "
            "VALUES (?, 'STOCK','600000','stock_dividend','stock_dividend',"
            " 0,'dead_letter', ?, ?)", [f"t_dl{i}", now, now])
    for i in range(held):
        conn.execute(
            "INSERT INTO qfq_watermark_intent (cycle_id, source, table_name, "
            " freq, candidate_watermark, status, hold_reason) "
            "VALUES (?, 'xtquant','stock_daily','daily','1','held','gate')",
            [f"cyc_h{i}"])
    conn.execute("CREATE TABLE sample (code VARCHAR PRIMARY KEY)")
    conn.execute("INSERT INTO sample VALUES ('600000')")
    conn.close()
    return db


_SAMPLE_SCHEMA = {"sample": {"columns": {"code": {"required": True}},
                             "primary_key": ["code"]}}


class TestQualityAuditQfqGate:

    def test_thresholds_none_skips_qfq_checks(self, tmp_path):
        """disabled（qfq_thresholds=None）→ 即使有 dead_letter 也不新增失败。"""
        from quantstudio.pipeline.quality_audit import DataQualityAuditor
        db = _audit_db(tmp_path, dead_letter=3, held=2)
        report = DataQualityAuditor(db, _SAMPLE_SCHEMA).run()
        checks = {i.check for i in report.issues}
        assert not any(ch.startswith("Qfq") for ch in checks)

    def test_dead_letter_detected_as_error(self, tmp_path):
        from quantstudio.pipeline.quality_audit import DataQualityAuditor
        db = _audit_db(tmp_path, dead_letter=2)
        report = DataQualityAuditor(db, _SAMPLE_SCHEMA, qfq_thresholds={}).run()
        issue = next(i for i in report.issues if i.check == "QfqDeadLetter")
        assert issue.severity == "error" and issue.count == 2
        assert not report.passed

    def test_dead_letter_within_threshold_passes(self, tmp_path):
        from quantstudio.pipeline.quality_audit import DataQualityAuditor
        db = _audit_db(tmp_path, dead_letter=2)
        report = DataQualityAuditor(
            db, _SAMPLE_SCHEMA, qfq_thresholds={"dead_letter_max": 5}).run()
        assert "QfqDeadLetter" not in {i.check for i in report.issues}

    def test_held_intent_is_warning_not_failure(self, tmp_path):
        from quantstudio.pipeline.quality_audit import DataQualityAuditor
        db = _audit_db(tmp_path, held=1)
        report = DataQualityAuditor(db, _SAMPLE_SCHEMA, qfq_thresholds={}).run()
        issue = next(i for i in report.issues if i.check == "QfqWatermarkHeld")
        assert issue.severity == "warning"
        assert report.passed  # warning 不 fail

    def test_qfq_tables_missing_skips_silently(self, tmp_path):
        """qfq 表未建（未 bootstrap）→ 门控静默跳过，不算失败。"""
        from quantstudio.pipeline.quality_audit import DataQualityAuditor
        db = tmp_path / "plain.db"
        conn = duckdb.connect(str(db))
        conn.execute("CREATE TABLE sample (code VARCHAR PRIMARY KEY)")
        conn.execute("INSERT INTO sample VALUES ('600000')")
        conn.close()
        report = DataQualityAuditor(db, _SAMPLE_SCHEMA, qfq_thresholds={}).run()
        assert not any(i.check.startswith("Qfq") for i in report.issues)
        assert report.passed


# ===========================================================================
# 红线 5：config_lint qfq_orchestrator 块
# ===========================================================================

class TestConfigLintQfq:

    @staticmethod
    def _lint(block, sources=None):
        from quantstudio.pipeline.config_lint import _lint_qfq_orchestrator
        errors, warnings = [], []
        tasks_cfg = {} if block is None else {"qfq_orchestrator": block}
        _lint_qfq_orchestrator(tasks_cfg, sources or {}, errors, warnings)
        return errors, warnings

    def test_block_missing_is_safe(self):
        errors, warnings = self._lint(None)
        assert errors == [] and warnings == []

    def test_disabled_block_valid(self):
        errors, _ = self._lint({"enabled": False})
        assert errors == []

    def test_invalid_price_source_is_error(self):
        errors, _ = self._lint({"enabled": True, "price_source": "akshare"})
        assert any("qfq_orchestrator" in e for e in errors)

    def test_invalid_watermark_policy_is_error(self):
        errors, _ = self._lint({"enabled": True, "price_source": "xtquant",
                                "watermark_policy": "advance_always"})
        assert errors

    def test_enabled_requires_sources_enabled(self):
        # xtquant 未启用 → ERROR
        errors, _ = self._lint(
            {"enabled": True, "price_source": "xtquant"},
            sources={"xtquant": {"enabled": False}, "tushare": {"enabled": True}})
        assert any("xtquant" in e for e in errors)

    def test_enabled_with_sources_ok(self):
        errors, _ = self._lint(
            {"enabled": True, "price_source": "xtquant"},
            sources={"xtquant": {"enabled": True}, "tushare": {"enabled": True}})
        assert errors == []

    def test_unknown_key_is_warning(self):
        _, warnings = self._lint({"enabled": False, "watermark_polcy": "x"})
        assert any("watermark_polcy" in w for w in warnings)

    def test_production_config_lints_clean(self):
        """正式 config/collector_tasks.json 的 qfq 块必须通过 lint（enabled=false）。"""
        cfg_path = _ROOT / "config" / "collector_tasks.json"
        tasks_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        block = tasks_cfg.get("qfq_orchestrator")
        assert block is not None and block.get("enabled") is False  # 安全默认
        errors, _ = self._lint(block)
        assert errors == []


# ===========================================================================
# 红线 6：daemon_lifecycle.run_one_cycle 接入（qfq_phase run_state）
# ===========================================================================

@pytest.fixture
def tmp_data_root(monkeypatch, tmp_path):
    import quantstudio._paths as qp
    import quantstudio.pipeline.daemon_lifecycle as dl
    import quantstudio.gui.daemon_process as dp
    monkeypatch.setattr(qp, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(dl, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(dp, "_data_root", lambda: tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    return tmp_path


class _QfqFakeCollector:
    """带 QFQ 方法的假 collector（daemon_lifecycle 接入面）。"""

    def __init__(self, cycle_id="cyc_test", begin_raises=False, summary=None):
        self._running = True
        self.executed = []
        self.begin_calls = 0
        self.post_ingest_run_ids = []
        self._cycle_id = cycle_id
        self._begin_raises = begin_raises
        self._summary = summary

    def qfq_begin_cycle(self):
        self.begin_calls += 1
        if self._begin_raises:
            raise RuntimeError("begin boom")
        return self._cycle_id

    def qfq_run_post_ingest(self, run_id):
        self.post_ingest_run_ids.append(run_id)
        return self._summary

    def execute_task(self, task, mode="incremental", run_quality_audit=False):
        self.executed.append(task["name"])
        return True

    def _run_full_quality_audit(self):
        return True

    def close(self):
        pass


def _make_lifecycle(tmp_root):
    from quantstudio.pipeline.daemon_lifecycle import DaemonLifecycle
    lc = DaemonLifecycle.__new__(DaemonLifecycle)
    lc.config_dir = tmp_root
    lc.instance_token = "test_" + uuid.uuid4().hex[:8]
    lc.max_iterations = None
    lc._running = True
    lc._status = None
    lc._instance_lock = None
    return lc


def _patch_collector(monkeypatch, fake):
    import quantstudio.pipeline.daemon as dmod
    monkeypatch.setattr(dmod.ResidentCollector, "from_configs",
                        classmethod(lambda cls, *a, **kw: fake))


def _run_state(tmp_root):
    return json.loads((tmp_root / "daemon_run_state.json").read_text(encoding="utf-8"))


class _FakeSummary:
    def __init__(self, status="finalized", error=None, held=0, committed_wm=1):
        self.cycle_id = "cyc_test"
        self.status = status
        self.error = error
        self.triggers_found = 0
        self.claimed = 0
        self.committed = 0
        self.retryable_failed = 0
        self.dead_letter = 0
        self.watermarks_committed = committed_wm
        self.watermarks_held = held
        self.bootstrap_required = False


class TestLifecycleQfqIntegration:

    def test_disabled_no_qfq_fields_in_run_state(self, tmp_data_root, monkeypatch):
        """qfq_begin_cycle 返回 None（disabled）→ run_state 无 qfq 字段，行为同旧。"""
        fc = _QfqFakeCollector(cycle_id=None)
        _patch_collector(monkeypatch, fc)
        _make_lifecycle(tmp_data_root).run_one_cycle(
            {"tasks": [{"name": "a", "enabled": True, "table": "t"}]})
        rs = _run_state(tmp_data_root)
        assert rs["status"] == "completed"
        assert "qfq_phase" not in rs
        assert fc.begin_calls == 1
        assert fc.post_ingest_run_ids == []  # cycle None → post_ingest 不调用? 
        # 注：lifecycle 只在 qfq_cycle_id 非 None 时进 post-ingest 分支

    def test_enabled_full_flow_writes_phase_and_summary(self, tmp_data_root, monkeypatch):
        fc = _QfqFakeCollector(summary=_FakeSummary())
        _patch_collector(monkeypatch, fc)
        _make_lifecycle(tmp_data_root).run_one_cycle(
            {"tasks": [{"name": "a", "enabled": True, "table": "t"}]})
        rs = _run_state(tmp_data_root)
        assert rs["status"] == "completed"
        assert rs["qfq_cycle_id"] == "cyc_test"
        assert rs["qfq_phase"] == "finalized"
        assert rs["qfq_summary"]["watermarks_committed"] == 1
        assert len(fc.post_ingest_run_ids) == 1
        assert rs["run_id"] == fc.post_ingest_run_ids[0]  # run_id 贯通

    def test_begin_failure_fail_closed_tasks_still_run(self, tmp_data_root, monkeypatch):
        """begin_cycle 异常 → qfq_phase=begin_failed，增量任务照常执行。"""
        fc = _QfqFakeCollector(begin_raises=True)
        _patch_collector(monkeypatch, fc)
        _make_lifecycle(tmp_data_root).run_one_cycle(
            {"tasks": [{"name": "a", "enabled": True, "table": "t"}]})
        rs = _run_state(tmp_data_root)
        assert rs["status"] == "completed"       # 任务不受影响
        assert fc.executed == ["a"]
        assert rs["qfq_phase"] == "begin_failed"
        assert "begin boom" in rs["qfq_error"]
        assert fc.post_ingest_run_ids == []      # 无周期 → 不跑 post-ingest

    def test_stop_mid_cycle_skips_post_ingest(self, tmp_data_root, monkeypatch):
        """任务中 stop → post-ingest 跳过，qfq_phase=interrupted（水位保持）。"""
        fc = _QfqFakeCollector(summary=_FakeSummary())
        lc = _make_lifecycle(tmp_data_root)
        orig = fc.execute_task
        def exec_and_stop(task, mode="incremental", run_quality_audit=False):
            orig(task, mode=mode, run_quality_audit=run_quality_audit)
            req = {"instance_token": lc.instance_token, "requested_at": "now"}
            (tmp_data_root / "daemon_stop.request").write_text(json.dumps(req), encoding="utf-8")
            return True
        fc.execute_task = exec_and_stop
        _patch_collector(monkeypatch, fc)
        lc.run_one_cycle({"tasks": [
            {"name": "a", "enabled": True, "table": "t"},
            {"name": "b", "enabled": True, "table": "t"}]})
        rs = _run_state(tmp_data_root)
        assert rs["status"] == "interrupted"
        assert rs["qfq_phase"] == "interrupted"
        assert fc.post_ingest_run_ids == []      # 红线：中断不跑 post-ingest

    def test_post_ingest_held_status_recorded(self, tmp_data_root, monkeypatch):
        """gate 不过（finalized_held）→ qfq_phase 如实记录，轮次仍 completed。"""
        fc = _QfqFakeCollector(summary=_FakeSummary(
            status="finalized_held", held=2, committed_wm=0))
        _patch_collector(monkeypatch, fc)
        _make_lifecycle(tmp_data_root).run_one_cycle(
            {"tasks": [{"name": "a", "enabled": True, "table": "t"}]})
        rs = _run_state(tmp_data_root)
        assert rs["status"] == "completed"
        assert rs["qfq_phase"] == "finalized_held"
        assert rs["qfq_summary"]["watermarks_held"] == 2

    def test_post_ingest_exception_fail_closed(self, tmp_data_root, monkeypatch):
        fc = _QfqFakeCollector()
        fc.qfq_run_post_ingest = lambda run_id: (_ for _ in ()).throw(
            RuntimeError("post boom"))
        _patch_collector(monkeypatch, fc)
        _make_lifecycle(tmp_data_root).run_one_cycle(
            {"tasks": [{"name": "a", "enabled": True, "table": "t"}]})
        rs = _run_state(tmp_data_root)
        assert rs["status"] == "completed"       # 轮次不因 QFQ 崩（水位保持即安全）
        assert rs["qfq_phase"] == "failed"
        assert "post boom" in rs["qfq_error"]


# ===========================================================================
# 任务2.2：因子刷新 degraded → 水位 hold + detector_degraded=1；成功 → 正常推进
# ===========================================================================

class TestFactorRefreshDegradedHold:

    def _run(self, tmp_path, monkeypatch, degraded):
        conn = _qfq_conn()
        qfq_block = {"enabled": True, "require_bootstrap": False,
                     "price_source": "xtquant", "factor_refresh_enabled": True}
        c = _make_collector(conn, qfq_block=qfq_block, tmpdir=tmp_path)
        calls = []
        orig = c._qfq_refresh_factors
        def fake_refresh(orch):
            calls.append(orch)
            return degraded
        monkeypatch.setattr(c, "_qfq_refresh_factors", fake_refresh)
        cid = c.qfq_begin_cycle()
        # 先 defer 一个待推进水位，post-ingest 才会写出 qfq_watermark_intent（提交/hold）
        c._advance_or_defer_watermark("xtquant", "stock_daily", "daily", WM, "b6")
        summary = c.qfq_run_post_ingest("r1")
        det = conn.execute(
            "SELECT detector_degraded FROM qfq_cycle_run WHERE cycle_id=?",
            [cid]).fetchone()[0]
        intent_status = conn.execute(
            "SELECT status FROM qfq_watermark_intent WHERE cycle_id=?",
            [cid]).fetchone()[0]
        conn.close()
        return summary, det, intent_status, len(c.writer.advanced), calls

    def test_refresh_success_advances_watermark(self, tmp_path, monkeypatch):
        summary, det, intent_status, advanced, calls = self._run(
            tmp_path, monkeypatch, degraded=False)
        assert calls, "因子刷新必须被调用（factor_refresh_enabled=True）"
        assert summary.status == "finalized"
        assert det == 0                       # 检测器健康
        assert advanced == 1                  # 水位正常推进（一次提交）
        assert intent_status == "committed"

    def test_refresh_degraded_holds_watermark(self, tmp_path, monkeypatch):
        summary, det, intent_status, advanced, calls = self._run(
            tmp_path, monkeypatch, degraded=True)
        assert calls, "因子刷新必须被调用（factor_refresh_enabled=True）"
        assert summary.status == "finalized_held"   # 水位被 hold
        assert det == 1                                # detector_degraded 落库
        assert advanced == 0                          # 四价格表水位未推进
        assert intent_status == "held"

    def test_factor_refresh_disabled_skips_refresh(self, tmp_path, monkeypatch):
        """factor_refresh_enabled 缺省=False → 不调用刷新，旧路径水位推进不变。"""
        conn = _qfq_conn()
        c = _make_collector(conn, qfq_block={"enabled": True,
                                             "require_bootstrap": False,
                                             "price_source": "xtquant"},
                            tmpdir=tmp_path)
        calls = []
        monkeypatch.setattr(c, "_qfq_refresh_factors",
                            lambda orch: calls.append(orch) or False)
        cid = c.qfq_begin_cycle()
        summary = c.qfq_run_post_ingest("r1")
        assert calls == []                    # 未启用 → 刷新完全不调用
        assert summary.status == "finalized"  # 旧行为不受影响
        conn.close()
