"""B·因子等价识别（rebase 预检第二级判据）防回归测试（2026-10-21 专项 B）。

判据映射：
  A40 构造「qfq 一致 + 因子版本差」→ 第二级放行（factor_equivalent=True），不 block；
  A41 **负向**：真实数据漂移（改一列 / 每 bar 乱缩放）→ 仍 block（防放宽）；
  A42 覆盖检查与 NULL 判据不受影响（既有语义）；
  A43 env `QS_QFQ_FACTOR_EQUIVALENCE=0` → 逐位还原旧行为（回退门）；
  A44 既有 reanchor 套件 + 本批 44 例全绿（回归门，由 CI/实施时复跑）；
  A45 生产观察窗：放行率与 blocked 并列留痕（观察期，人工/运维核对）。

运行：.venv/Scripts/python -m pytest tests/test_qfq_factor_equivalence.py -q
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantstudio.pipeline.qfq_reanchor_engine import (
    _factor_equivalent_analysis,
    _factor_equivalent_enabled,
)

RAW = ["open", "high", "low", "close"]
T0 = 1_700_000_000_000
STEP = 86_400_000


def _frame(n=30, ratios=None, base_price=10.0, seed=7, corrupt_cols=()):
    """构造不一致行表：库内 raw=base，fresh raw=base×ratio（逐 bar 可变）。"""
    if ratios is None:
        ratios = [1.0 + 1e-3 * (i % 5) for i in range(n)]
    rng = np.random.default_rng(seed)
    t = T0 + np.arange(n, dtype="int64") * STEP
    db = {c: base_price * (1.0 + 0.01 * rng.random(n)) for c in RAW}
    fresh = {}
    for c in RAW:
        r = np.array(ratios, dtype=float)
        if c in corrupt_cols:
            r = r * (1.0 + 0.37 * rng.random(n))      # 真实漂移：逐 bar 乱缩放
        fresh[c] = db[c] * r
    df = pd.DataFrame({"time": t})
    for c in RAW:
        df[f"db_{c}"] = db[c]
        df[f"fresh_{c}"] = fresh[c]
    return df


# ---------------- A40：结构化比例差 ⇒ 放行 ----------------
def test_a40_piecewise_constant_ratio_is_equivalent():
    # 因子版本分 3 段，每段内比值恒定（真实 af 版本阶跃的形态）
    ratios = [1.0066] * 10 + [1.0133] * 10 + [1.0200] * 10
    out = _factor_equivalent_analysis(_frame(n=30, ratios=ratios))
    assert out["equivalent"] is True, out
    assert out["reason"] == "factor_equivalent"
    assert out["mismatch_bars"] == 30
    assert out["ratio_median"] == pytest.approx(1.0133, rel=1e-3)


def test_a40_daily_jitter_ratio_is_equivalent():
    # af 逐日微调（~1e-3 jitter）形态：比值连续、小幅变化 ⇒ 仍放行
    ratios = [1.0 + 3e-4 * np.sin(i / 3.0) for i in range(40)]
    out = _factor_equivalent_analysis(_frame(n=40, ratios=ratios))
    assert out["equivalent"] is True, out
    assert out["day_jump_p95"] is not None and out["day_jump_p95"] < 0.05


# ---------------- A41：负向（真实漂移必须仍 block）----------------
def test_a41_single_bar_corruption_is_not_equivalent():
    """只改 close 一列的逐 bar 乱缩放 ⇒ 跨列比值不一致 ⇒ 拒绝。"""
    out = _factor_equivalent_analysis(_frame(n=30, corrupt_cols=("close",)))
    assert out["equivalent"] is False
    assert "cross_column_inconsistent" in out["reason"]


def test_a41_all_columns_random_scale_is_not_equivalent():
    """全列同缩放但逐 bar 随机 ⇒ 比值不连续 ⇒ 拒绝（真实数据损坏形态）。"""
    n = 30
    rng = np.random.default_rng(11)
    ratios = list(1.0 + 0.4 * rng.random(n))
    out = _factor_equivalent_analysis(_frame(n=n, ratios=ratios))
    assert out["equivalent"] is False
    assert "ratio_discontinuity" in out["reason"]


def test_a41_small_sample_is_refused():
    """证据不足（不一致行 < 8）⇒ 不放行（fail-closed，防单点巧合）。"""
    out = _factor_equivalent_analysis(_frame(n=4, ratios=[1.0066] * 4))
    assert out["equivalent"] is False
    assert "insufficient_evidence" in out["reason"]


def test_a41_out_of_bounds_ratio_is_refused():
    out = _factor_equivalent_analysis(_frame(n=20, ratios=[5e4] * 20))
    assert out["equivalent"] is False
    assert "ratio_out_of_bounds" in out["reason"]


def test_a41_zero_or_nonfinite_ratio_is_refused():
    df = _frame(n=20, ratios=[1.0066] * 20)
    df.loc[3, "fresh_close"] = 0.0
    out = _factor_equivalent_analysis(df)
    assert out["equivalent"] is False
    assert "ratio_undefined" in out["reason"] or "ratio_nonpositive" in out["reason"]


# ---------------- A43：env 回退门 ----------------
def test_a43_env_switch_restores_old_behaviour(monkeypatch):
    monkeypatch.setenv("QS_QFQ_FACTOR_EQUIVALENCE", "0")
    assert _factor_equivalent_enabled() is False
    monkeypatch.setenv("QS_QFQ_FACTOR_EQUIVALENCE", "1")
    assert _factor_equivalent_enabled() is True
    monkeypatch.delenv("QS_QFQ_FACTOR_EQUIVALENCE", raising=False)
    assert _factor_equivalent_enabled() is True


def test_a43_disabled_env_means_engine_does_not_analyze(monkeypatch):
    """env=0 时引擎不做第二级判定（逐位还原旧行为：仍然 block）。"""
    monkeypatch.setenv("QS_QFQ_FACTOR_EQUIVALENCE", "0")
    import quantstudio.pipeline.qfq_reanchor_engine as eng
    src = open(eng.__file__, encoding="utf-8").read()
    # 两处第二级入口都必须以 _factor_equivalent_enabled() 为闸
    # （日线：if n and _factor_equivalent_enabled():；分钟：if _factor_equivalent_enabled():）
    assert src.count("if n and _factor_equivalent_enabled():") >= 1
    assert src.count("if _factor_equivalent_enabled():") >= 1


# ---------------- A40b：端到端（真实 _check_daily_raw_align 的 SQL join 路径）----------------
def _mk_etf_daily_db(conn, ratios, n=30, corrupt_close=False):
    """建 etf_daily（库内 raw）+ 返回 fresh df（raw×ratio）。"""
    rng = np.random.default_rng(3)
    t = np.array([T0 + i * STEP for i in range(n)], dtype="int64")
    base = 10.0 * (1.0 + 0.01 * rng.random(n))
    conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT, open DOUBLE, "
                 "high DOUBLE, low DOUBLE, close DOUBLE)")
    rows = [("513530", int(t[i]), base[i], base[i] * 1.01, base[i] * 0.99, base[i])
            for i in range(n)]
    conn.executemany("INSERT INTO etf_daily VALUES (?,?,?,?,?,?)", rows)
    fresh = {"code": ["513530"] * n, "time": t}
    for c, mult in (("open", 1.0), ("high", 1.01), ("low", 0.99), ("close", 1.0)):
        vals = []
        for i in range(n):
            r = ratios[i]
            if corrupt_close and c == "close":
                r = r * 1.37
            vals.append(base[i] * mult * r)
        fresh[c] = vals
    return pd.DataFrame(fresh)


def test_a40b_end_to_end_daily_check_passes_with_factor_ratio():
    import duckdb
    from quantstudio.pipeline.qfq_reanchor_engine import _check_daily_raw_align
    conn = duckdb.connect()
    ratios = [1.0066] * 15 + [1.0133] * 15
    df = _mk_etf_daily_db(conn, ratios)
    flags = _check_daily_raw_align(conn, "ETF", "513530", df, None)
    assert flags["factor_equivalent"] is True, flags
    assert flags["raw_mismatch"] == 30
    assert flags["detail"]["equivalent"] is True
    conn.close()


def test_a41b_end_to_end_daily_check_still_blocks_on_real_drift():
    import duckdb
    from quantstudio.pipeline.qfq_reanchor_engine import (
        _check_daily_raw_align, ReanchorBlocked)
    conn = duckdb.connect()
    ratios = [1.0066] * 15 + [1.0133] * 15
    df = _mk_etf_daily_db(conn, ratios, corrupt_close=True)
    with pytest.raises(ReanchorBlocked) as ei:
        _check_daily_raw_align(conn, "ETF", "513530", df, None)
    assert "daily_raw_mismatch" in str(ei.value)
    conn.close()


def test_a43b_end_to_end_env_off_restores_block():
    import duckdb
    from quantstudio.pipeline.qfq_reanchor_engine import (
        _check_daily_raw_align, ReanchorBlocked)
    conn = duckdb.connect()
    ratios = [1.0066] * 15 + [1.0133] * 15
    df = _mk_etf_daily_db(conn, ratios)
    import os
    old = os.environ.get("QS_QFQ_FACTOR_EQUIVALENCE")
    os.environ["QS_QFQ_FACTOR_EQUIVALENCE"] = "0"
    try:
        with pytest.raises(ReanchorBlocked):
            _check_daily_raw_align(conn, "ETF", "513530", df, None)
    finally:
        if old is None:
            os.environ.pop("QS_QFQ_FACTOR_EQUIVALENCE", None)
        else:
            os.environ["QS_QFQ_FACTOR_EQUIVALENCE"] = old
    conn.close()


# ---------------- 空输入 ----------------
def test_empty_mismatch_is_not_equivalent():
    out = _factor_equivalent_analysis(None)
    assert out["equivalent"] is False and out["reason"] == "no_mismatch_rows"
    out2 = _factor_equivalent_analysis(pd.DataFrame())
    assert out2["equivalent"] is False
