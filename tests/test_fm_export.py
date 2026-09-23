# -*- coding: utf-8 -*-
"""FM 代答两工件导出 · 自测（2026-09-22，实施侧；V1–V6 对应的可测面）。

零主库接触：临时 DuckDB（经 DuckDBWriter 建真 schema）+ 临时导出目录。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline import fm_export as fx          # noqa: E402
from quantstudio.pipeline.writers import DuckDBWriter      # noqa: E402

D0 = "2026-09-18"
D1 = "2026-09-19"      # 倒数第二（= as_of 期望值）
D2 = "2026-09-20"      # 最新（无次日数据 ⇒ 不作 as_of）


def _ms(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "shadow.db"
    out = tmp_path / "fm_out"
    monkeypatch.setenv(fx.FM_EXPORT_DIR_ENV, str(out))
    monkeypatch.delenv(fx.FM_EXPORT_ENV, raising=False)
    w = DuckDBWriter({"type": "duckdb", "path": str(db)})
    conn = w.shared_conn()
    yield conn, out, w
    try:
        w.close()
    except Exception:
        pass


def _seed(conn, table, days, n_codes=3, extra_cols=""):
    for d in days:
        for i in range(n_codes):
            cols = "code, time" + (", " + extra_cols if extra_cols else "")
            vals = "?, ?" + (", " + ", ".join("?" for _ in extra_cols.split(",")) if extra_cols else "")
            params = ["%06d" % i, _ms(d)] + ([1.0] * len(extra_cols.split(",")) if extra_cols else [])
            if table.endswith("minutes"):
                cols += ", freq"; vals += ", ?"; params.append("1min")
            conn.execute('INSERT INTO "%s" (%s) VALUES (%s)' % (table, cols, vals), params)


# ── V1 工件落地 + as_of 口径 ────────────────────────────────────────────
def test_resolve_as_of_is_second_to_last_trading_day(env):
    conn, out, _ = env
    _seed(conn, "stock_daily", [D0, D1, D2], extra_cols="close")
    ref = fx.resolve_as_of(conn)
    assert ref["as_of_date"] == D1, ref              # 倒数第二个交易日（最新 D2 无次日 ⇒ 不作 as_of）
    assert ref["latest_ms"] == _ms(D2)
    assert ref["health"] == fx.HEALTH_COMPLETE, ref


def test_export_produces_both_parquet_and_watermark(env):
    import pyarrow.parquet as pq
    conn, out, _ = env
    _seed(conn, "stock_daily", [D0, D1, D2], extra_cols="close")
    _seed(conn, "stock_daily_valuation", [D0, D1, D2], extra_cols="circ_mv,total_mv,pe_ttm,pb,turnover_rate")
    for t in fx.WATERMARK_TABLES:
        if t not in ("stock_daily", "stock_daily_valuation"):
            _seed(conn, t, [D0, D1, D2], n_codes=2, extra_cols="close")
    r = fx.run_fm_export(conn, out_dir=out, keep=10)
    assert r["ok"] is True, r
    assert r["as_of"] == D1
    for t in fx.ARTIFACT1_TABLES:
        p = out / r["artifacts"][t]["file"]
        assert p.exists()
        tbl = pq.read_table(str(p))
        assert tbl.num_rows == 3, (t, tbl.num_rows)          # 3 codes × 1 day（全截面）
        md = tbl.schema.metadata or {}
        assert md.get(b"as_of", b"").decode() == D1
        assert md.get(b"generator", b"").decode().startswith("quantstudio.fm_export")
    wj = json.loads((out / r["artifacts"]["watermark"]["file"]).read_text(encoding="utf-8"))
    assert wj["as_of"] == D1 and len(wj["tables"]) == 4
    assert "monthly" in wj["tables"]["stock_daily"]
    assert "min/max" in wj["note"] or "禁用" in wj["note"]


def test_watermark_monthly_matrix_is_case10_caliber(env):
    """案例十口径：逐月矩阵（strftime('%Y-%m')），非 min/max 范围式。"""
    conn, out, _ = env
    _seed(conn, "stock_daily", [D0, D1, D2], extra_cols="close")
    for t in fx.WATERMARK_TABLES:
        if t != "stock_daily":
            _seed(conn, t, [D0], n_codes=1, extra_cols="close")
    r = fx.run_fm_export(conn, out_dir=out)
    wj = json.loads((out / r["artifacts"]["watermark"]["file"]).read_text(encoding="utf-8"))
    m = wj["tables"]["stock_daily"]["monthly"]
    assert "2026-09" in m and m["2026-09"] == 9, m      # 3 days × 3 codes
    assert wj["tables"]["stock_daily"]["max_time_ms"] == _ms(D2)


# ── 四态健康标注 ───────────────────────────────────────────────────────
def test_health_single_day(env):
    conn, out, _ = env
    _seed(conn, "stock_daily", [D2], extra_cols="close")
    ref = fx.resolve_as_of(conn)
    assert ref["health"] == fx.HEALTH_SINGLE and ref["as_of_date"] == D2, ref


def test_health_empty(env):
    conn, out, _ = env
    ref = fx.resolve_as_of(conn)
    assert ref["health"] == fx.HEALTH_EMPTY and ref["as_of_ms"] is None, ref


def test_health_fallback_when_table_lags_reference(env):
    conn, out, _ = env
    _seed(conn, "stock_daily", [D0, D1, D2], extra_cols="close")
    _seed(conn, "stock_daily_valuation", [D0, D1], extra_cols="circ_mv,total_mv,pe_ttm,pb,turnover_rate")
    for t in fx.WATERMARK_TABLES:
        if t not in ("stock_daily", "stock_daily_valuation"):
            _seed(conn, t, [D0, D1, D2], n_codes=1, extra_cols="close")
    r = fx.run_fm_export(conn, out_dir=out)
    assert r["artifacts"]["stock_daily_valuation"]["health"] == fx.HEALTH_FALLBACK, r["artifacts"]
    assert r["health"] == fx.HEALTH_FALLBACK, r["health"]


# ── V4 轮转 ────────────────────────────────────────────────────────────
def test_rotation_keeps_latest_n_and_same_batch(env):
    conn, out, _ = env
    # 本用例只测 rotate()：直接构造多份历史件（不同 as_of），无需播种数据
    out.mkdir(parents=True, exist_ok=True)
    for i, d in enumerate(["20260910", "20260911", "20260912"]):
        (out / ("fm_stock_daily_asof_%s.parquet" % d)).write_bytes(b"x")
        (out / ("fm_watermark_asof_%s.json" % d)).write_text("{}", encoding="utf-8")
    (out / ("fm_stock_daily_asof_20260913.parquet")).write_bytes(b"x")
    (out / ("fm_watermark_asof_20260913.json")).write_text("{}", encoding="utf-8")
    removed = fx.rotate(out, keep=2)
    left = sorted(p.name for p in out.glob("fm_*_asof_*.*"))
    assert all("20260913" in n or "20260912" in n for n in left), left
    assert any("20260910" in n for n in removed) and any("20260911" in n for n in removed), removed


# ── V3 失败必告警（禁静默）──────────────────────────────────────────────
def test_failure_preserves_old_artifacts_and_writes_status(env, monkeypatch):
    conn, out, _ = env
    _seed(conn, "stock_daily", [D0, D1, D2], extra_cols="close")
    r1 = fx.run_fm_export(conn, out_dir=out)
    assert r1["ok"] is True
    old = sorted(p.name for p in out.glob("fm_*_asof_*.*"))
    assert old
    # 注入失败：让 export_table 抛异常
    monkeypatch.setattr(fx, "export_table", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("injected")))
    r2 = fx.run_fm_export(conn, out_dir=out)
    assert r2["ok"] is False and "injected" in r2["error"]
    assert sorted(p.name for p in out.glob("fm_*_asof_*.*")) == old, "失败不得覆盖/删除旧件"
    st = json.loads((out / "_status.json").read_text(encoding="utf-8"))
    assert st["consecutive_failures"] == 1 and "injected" in st["last_error"]
    assert st["last_success_asof"] == D1, st        # 上次成功信息保留


# ── 开关等效 ───────────────────────────────────────────────────────────
def test_kill_switch_skips(env, monkeypatch):
    conn, out, _ = env
    monkeypatch.setenv(fx.FM_EXPORT_ENV, "0")
    r = fx.run_fm_export(conn, out_dir=out)
    assert r.get("skipped") is True and r["ok"] is True
    assert not list(out.glob("fm_*_asof_*.*")), "关闭态不得产出工件"
