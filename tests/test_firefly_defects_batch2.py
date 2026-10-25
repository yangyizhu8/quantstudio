# -*- coding: utf-8 -*-
"""firefly 缺陷清单第二批（#7/#8/#9/#10/#11）修复回归测试（2026-10-26）。

对应 .specify/bugs/firefly-engine-defects-20261025/{assessment,batch2-proposal}.md：
- #7 水位防倒退（GREATEST+COALESCE）：writers 主路径 + orchestrator 回退 upsert
    两形态；负向：旧行 NULL 首写不吞、新值 NULL 不清水位。
- #8 export 通道时间窗透传 + 大表路由（路由断言在 test_mcp_wide_text_routing.py
    联动更新）；负向：空窗/空值归 None（不下推保持原语义）。
- #9 freqs 注释级：零行为改动，由既有 test_qfq_orchestrator_types.py 回归背书
    （本文件不重复断言注释文本）。
- #10 EpsBackfillGap 与 writer gate 同开关：默认 WARN 不判负；env 开→error；
    负向：缺口为 0 两态均不产生该项。
- #11 小样本豁免（预照准参数 200/5%）：仅拒批通道；负向：灾难率仍判负、
    超绝对下限仍判负、拉取失败通道不豁免、清单可配归零。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.qfq_reanchor_schema import (  # noqa: E402
    init_duckdb_schema, init_sqlite_schema)
from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig  # noqa: E402
from quantstudio.pipeline.qfq_resident_orchestrator import (  # noqa: E402
    QFQResidentOrchestrator)
from quantstudio.pipeline.writers import DuckDBWriter  # noqa: E402
from quantstudio.pipeline.daemon import ResidentCollector  # noqa: E402

NOW_MS = 1_770_000_000_000
WM_NEW = 1_777_000_000_000          # 「今天」级水位
WM_OLD = 1_672_300_000_000          # 2023-01 级水位（倒退写入尝试）


# ══════════════════════════════════════════════════════════════════════
# #7 水位防倒退
# ══════════════════════════════════════════════════════════════════════
def _wm_row(conn, source="tushare", table="stock_daily_valuation", freq="daily"):
    return conn.execute(
        "SELECT last_date FROM source_watermark WHERE source=? AND table_name=? "
        "AND freq=?", [source, table, freq]).fetchone()


def test_bug7_writer_guard_blocks_regression(tmp_path):
    """#7 正向（writers 主路径）：旧水位 2026 级，写入 2022 级窗口终点 → 不倒退。"""
    db = tmp_path / "wm.duckdb"
    w = DuckDBWriter({"type": "duckdb", "path": str(db)})
    w.advance_watermark("tushare", "stock_daily_valuation", "daily", WM_NEW, "b1")
    w.advance_watermark("tushare", "stock_daily_valuation", "daily", WM_OLD, "b2")
    row = w.execute_read(
        "SELECT last_date FROM source_watermark WHERE table_name='stock_daily_valuation'")[0]
    assert row[0] == WM_NEW, f"水位倒退：{row[0]} < {WM_NEW}"


def test_bug7_writer_null_old_row_first_write(tmp_path):
    """#7 负向：旧行 last_date NULL（脏数据/历史行）→ 首写正常落值（COALESCE 兜底）。"""
    db = tmp_path / "wm2.duckdb"
    w = DuckDBWriter({"type": "duckdb", "path": str(db)})
    w.execute_read("INSERT INTO source_watermark (source, table_name, freq, last_date, "
                   "last_batch_id, updated_at, source_generation, cutover_id) VALUES "
                   "('x','t','daily',NULL,'b',NULL,'g','c')")
    w.advance_watermark("x", "t", "daily", WM_NEW, "b1")
    row = w.execute_read("SELECT last_date FROM source_watermark WHERE table_name='t'")[0]
    assert row[0] == WM_NEW


def test_bug7_orchestrator_fallback_guard(tmp_path):
    """#7 正向（orchestrator 回退 upsert·无 last_run_id 形态）：守卫同样生效。"""
    duck = tmp_path / "orch7.db"
    dconn = duckdb.connect(str(duck))
    init_duckdb_schema(dconn)
    aux = tmp_path / "aux7.db"
    import sqlite3
    auxc = sqlite3.connect(str(aux), timeout=30)
    init_sqlite_schema(auxc)
    auxc.close()
    orch = QFQResidentOrchestrator(
        QFQOrchestratorConfig(enabled=True), main_db=str(duck), aux_db=str(aux))
    assert orch.watermark_advancer is None       # 命中回退 upsert 路径
    orch._advance_watermark(dconn, "mcp", "etf_daily", "daily", WM_NEW, "run1")
    orch._advance_watermark(dconn, "mcp", "etf_daily", "daily", WM_OLD, "run2")
    row = _wm_row(dconn, "mcp", "etf_daily")
    assert row[0] == WM_NEW
    # last_run_id 形态（FP-5 迁移后）：同守卫
    dconn.execute("ALTER TABLE source_watermark ADD COLUMN last_run_id VARCHAR")
    orch._advance_watermark(dconn, "mcp", "stock_daily", "daily", WM_NEW, "run1")
    orch._advance_watermark(dconn, "mcp", "stock_daily", "daily", WM_OLD, "run2")
    row = _wm_row(dconn, "mcp", "stock_daily")
    assert row[0] == WM_NEW
    dconn.close()


# ══════════════════════════════════════════════════════════════════════
# #8 export 时间窗透传
# ══════════════════════════════════════════════════════════════════════
def _mk_adapter(monkeypatch, cfg=None):
    from quantstudio.pipeline.sources import mcp_adapter as ma
    a = ma.MCPAdapter(dict(cfg or {"name": "mcp"}))
    monkeypatch.setattr(a, "supports_task", lambda table, freq: (True, ""))
    return a


def test_bug8_norm_window_date():
    from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter
    f = MCPAdapter._norm_window_date
    assert f(None) is None and f("") is None
    assert f("2026-01-01") == "20260101"
    assert f("20260101") == "20260101"
    assert f("2026-01-01T00:00:00") == "20260101"


def test_bug8_export_receives_time_window(monkeypatch, tmp_path):
    """#8 正向：export_dataset 收到归一化的 time_start/time_end（服务端下推）。"""
    import pandas as pd
    a = _mk_adapter(monkeypatch)
    captured = {}

    class _Art:
        artifact_id = "j0/s0"

    buf = io.BytesIO()
    pd.DataFrame({"v": [1, 2]}).to_parquet(buf)
    _Art.parquet_bytes = buf.getvalue()

    def fake_export(dataset_id, page_size=50000, **kw):
        captured.update(kw, dataset_id=dataset_id)
        return [_Art()]

    monkeypatch.setattr(a.client, "export_dataset", fake_export)
    monkeypatch.setattr(a, "_landing_path",
                        lambda prefix, aid: tmp_path / f"{prefix}_{aid.replace('/', '_')}.parquet")
    raw, meta = a.fetch_table("sw_daily", "2026-01-01", "2026-01-31", freq="daily")
    assert captured["dataset_id"] == "sw_daily"
    assert captured["time_start"] == "20260101" and captured["time_end"] == "20260131"
    assert len(raw) == 2 and meta.get("big_table_export") is True

    # 负向：空窗（全量语义）→ None 不下推（保持原语义）
    captured.clear()
    a.fetch_table("sw_daily", None, None, freq="daily")
    assert captured["time_start"] is None and captured["time_end"] is None


# ══════════════════════════════════════════════════════════════════════
# #10 EpsBackfillGap 与 writer gate 同开关
# ══════════════════════════════════════════════════════════════════════
def _mk_audit_db(tmp_path, with_gap=True):
    db = tmp_path / "qa.duckdb"
    conn = duckdb.connect(str(db))
    # ann_date/end_date 用 BIGINT epoch（真实 fin_indicator 口径；VARCHAR 会触发
    # _audit_future_and_pit 的无关 Binder 错）
    conn.execute("CREATE TABLE fin_indicator (code VARCHAR, end_date BIGINT, "
                 "ann_date BIGINT, eps DOUBLE)")
    conn.execute("CREATE TABLE income_statement (code VARCHAR, end_date BIGINT, "
                 "ann_date BIGINT, basic_eps DOUBLE)")
    if with_gap:
        conn.execute("INSERT INTO fin_indicator VALUES "
                     "('000001', 1767225600000, 1769817600000, NULL)")
        conn.execute("INSERT INTO income_statement VALUES "
                     "('000001', 1767225600000, 1769817600000, 1.5)")
    conn.close()
    return db


def _run_audit(db):
    from quantstudio.pipeline.quality_audit import DataQualityAuditor
    schemas = {"fin_indicator": {"columns": {}, "primary_key": []},
               "income_statement": {"columns": {}, "primary_key": []}}
    return DataQualityAuditor(db, schemas).run()


def test_bug10_default_gate_off_downgrades_to_warning(tmp_path, monkeypatch):
    """#10 正向：默认配置（QS_AUTO_BACKFILL_EPS 关）→ WARN 不判负。"""
    monkeypatch.delenv("QS_AUTO_BACKFILL_EPS", raising=False)
    report = _run_audit(_mk_audit_db(tmp_path))
    items = [i for i in report.issues if i.check == "EpsBackfillGap"]
    assert items and items[0].severity == "warning", items
    assert not any(i.check == "EpsBackfillGap" and i.severity == "error"
                   for i in report.issues)


def test_bug10_gate_on_keeps_error(tmp_path, monkeypatch):
    """#10 负向对照：QS_AUTO_BACKFILL_EPS=1 → 维持 error 判负。"""
    monkeypatch.setenv("QS_AUTO_BACKFILL_EPS", "1")
    report = _run_audit(_mk_audit_db(tmp_path))
    items = [i for i in report.issues if i.check == "EpsBackfillGap"]
    assert items and items[0].severity == "error", items
    assert report.passed is False


def test_bug10_no_gap_no_issue(tmp_path, monkeypatch):
    """#10 负向：缺口=0 → 两态均不产生该项。"""
    monkeypatch.delenv("QS_AUTO_BACKFILL_EPS", raising=False)
    report = _run_audit(_mk_audit_db(tmp_path, with_gap=False))
    assert not [i for i in report.issues if i.check == "EpsBackfillGap"]


# ══════════════════════════════════════════════════════════════════════
# #11 小样本豁免（预照准：abs_floor=200 / hard_cap=0.05 / 仅拒批通道）
# ══════════════════════════════════════════════════════════════════════
def _gate(quality_gate=None):
    d = ResidentCollector.__new__(ResidentCollector)
    d.tasks_cfg = {"quality_gate": quality_gate or {}}
    return d


def test_bug11_customer_case_small_sample_passes():
    """#11 正向：客户实测 108/5384=2.006%（>1% 阈值）→ 豁免通过。"""
    ok, rate, _ = _gate()._failure_gate({}, 108, 5384, source="mcp",
                                        is_reject=True, freq="daily")
    assert ok is True, (rate,)


def test_bug11_large_sample_original_path_unchanged():
    """#11 正向：同 108 条在 159,136 行 → 原比率路径通过（豁免不参与）。"""
    ok, _, _ = _gate()._failure_gate({}, 108, 159_136, source="mcp",
                                     is_reject=True, freq="daily")
    assert ok is True


def test_bug11_negative_catastrophic_rate_still_fails():
    """#11 负向：5000/10000=50% 灾难拒绝率 → 硬帽拦截，仍判负。"""
    ok, _, _ = _gate()._failure_gate({}, 5000, 10_000, source="mcp",
                                     is_reject=True, freq="daily")
    assert ok is False


def test_bug11_negative_over_abs_floor_fails():
    """#11 负向：failed=250（>200 下限）且 rate=5% → 仍判负。"""
    ok, _, _ = _gate()._failure_gate({}, 250, 5000, source="mcp",
                                     is_reject=True, freq="daily")
    assert ok is False


def test_bug11_negative_fetch_failure_channel_not_exempt():
    """#11 负向：拉取失败通道（is_reject=False）小样本不豁免——真失败必须严格。"""
    ok, _, _ = _gate()._failure_gate({}, 5, 10, source="mcp", is_reject=False)
    assert ok is False


def test_bug11_config_abs_floor_zero_disables_exemption():
    """#11 负向：max_reject_abs_floor=0 → 豁免关闭（可配回退语义）。"""
    ok, _, _ = _gate({"max_reject_abs_floor": 0})._failure_gate(
        {}, 108, 5384, source="mcp", is_reject=True, freq="daily")
    assert ok is False
