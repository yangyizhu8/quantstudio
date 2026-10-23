"""E1′ tick 归一 + E5 整段系统性偏移检测 —— 判据矩阵（A46–A53）。

设计依据（2026-10-23 ②审通过 + ρ 取证标定）：
  · E1′：跨列比值一致容差 = max(1e-9, 2×tick/该行价格)
    —— 分钟复权往返末位误差实测 p95 1e-5~1e-4、行级尾差最大 ≈1.2 tick（1.63e-3×7.3 元），
       故取 2 tick；下限 1e-9 保证日线严格基线。
  · E5：整段系统性偏移（段内近恒定 + 显著 + ≤1e-2）⇒ 不放行（拦 H3 型真偏移）；
    ≥1e-2 视为因子版本阶跃（正常漂移）放行。
  · E5 必须**先于** E1′：H3 偏移常落在 1 tick 以内（E1′ 会放行），只能由 E5 拦下。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from quantstudio.pipeline.qfq_reanchor_engine import (
    RAW_COLS,
    _FACTOR_EQUIV_COL_TOL_FLOOR,
    _FACTOR_EQUIV_SEG_MAX_ABS,
    _FACTOR_EQUIV_SEG_MIN_BARS,
    _FACTOR_EQUIV_TICK_MULT,
    _factor_equivalent_analysis,
)

TICK_STOCK = 0.01
DAY_MS = 86_400_000
T0 = 1_767_573_600_000  # 2026-01-05 09:30 CST 口径


def _mk(n=240, price=10.0, days=1, ratios=None, delta_high=0.0,
        noise=None, seed=5):
    """构造不一致行表。

    - ``ratios``：逐行公共比值（各列相同 ⇒ 跨列一致）
    - ``delta_high``：high 列相对基准列的**恒定**相对偏移（E5 模拟）
    - ``noise``：逐行独立随机噪声幅度（相对），叠加在 delta_high 之上
    """
    rng = np.random.default_rng(seed)
    total = n * days
    t = T0 + np.arange(total, dtype="int64") * (DAY_MS // max(1, total))
    if ratios is None:
        ratios = np.ones(total)
    ratios = np.asarray(ratios, dtype=float)
    base_px = price * (1.0 + 0.001 * rng.random(total))
    df = pd.DataFrame({"time": t})
    for c in RAW_COLS:
        df[f"db_{c}"] = base_px
        r = ratios.copy()
        if c == "high":
            r = r * (1.0 + delta_high)
            if noise:
                r = r * (1.0 + rng.uniform(-noise, noise, total))
        df[f"fresh_{c}"] = base_px * r
    return df


# ---------------- A46：tick 归一容差放行（正例，非整段） ----------------
def test_a46_within_tick_tolerance_is_released():
    # price=10 → tick/price=1e-3，容差=2 tick=2e-3；偏移 0.5 tick 且**逐 bar 变化**
    # （非整段恒定）⇒ E1′ 放行、E5 不触发 ⇒ 放行
    n = 240
    t = T0 + np.arange(n, dtype="int64") * (DAY_MS // n)
    rng = np.random.default_rng(3)
    base_px = 10.0 * (1.0 + 0.001 * rng.random(n))
    df = pd.DataFrame({"time": t})
    offs = rng.uniform(-0.5e-3, 0.5e-3, n)     # 逐 bar 变化 ⇒ 非整段
    for c in RAW_COLS:
        df[f"db_{c}"] = base_px
        df[f"fresh_{c}"] = base_px * (1.0 + (offs if c == "high" else 0.0))
    out = _factor_equivalent_analysis(df, asset_type="STOCK")
    assert out["equivalent"] is True, out
    assert out["reason"] == "factor_equivalent"
    assert out["segment_offset"] is False


# ---------------- A47：超容差拒绝（负向） ----------------
def test_a47_beyond_tick_tolerance_is_rejected():
    # 偏移 5 tick ≫ 容差（2 tick）⇒ 必须拒绝（E5 或 E1′ 任一拦下即可）
    df = _mk(n=240, price=10.0, delta_high=5e-3)
    out = _factor_equivalent_analysis(df, asset_type="STOCK")
    assert out["equivalent"] is False, out
    assert ("cross_column_inconsistent" in out["reason"]
            or "ratio_segment_offset" in out["reason"]), out


# ---------------- A48：整段系统性偏移拒绝（负向，H3 模拟） ----------------
def test_a48_constant_segment_offset_is_rejected():
    # 整日恒定偏移 0.5 tick（落在 E1′ 的 2 tick 容差内 ⇒ E1′ 会放行，必须由 E5 拦下）
    df = _mk(n=240, price=10.0, delta_high=0.5e-3)
    out = _factor_equivalent_analysis(df, asset_type="STOCK")
    assert out["equivalent"] is False, out
    assert out["segment_offset"] is True, out
    assert "ratio_segment_offset" in out["reason"], out


# ---------------- A49：随机舍入噪声放行（正例） ----------------
def test_a49_random_rounding_noise_is_released():
    # 逐 bar 独立噪声（幅度 1 tick）⇒ 段内不同步（std 大 / median≈0）⇒ 不触发 E5
    df = _mk(n=240, price=10.0, noise=1e-3, seed=11)
    out = _factor_equivalent_analysis(df, asset_type="STOCK")
    assert out["equivalent"] is True, out
    assert out["segment_offset"] is False


# ---------------- A49b：超 1e-2 的跨列偏移不属"版本阶跃" ⇒ 仍拒（由 E1′ 拦） ----------------
def test_a49b_large_segment_shift_is_not_released():
    # |median|=2e-2 > E5 上限 1e-2 ⇒ E5 **不**按整段偏移处理（避免误杀版本阶跃），
    # 但 2% 的**跨列**偏差不是因子阶跃（因子阶跃对各列同向 ⇒ 跨列偏差为 0），
    # 必须由 E1′ 拒绝 ⇒ 结论：不放行，但 reason 不是 ratio_segment_offset。
    df = _mk(n=240, price=10.0, delta_high=2e-2)
    out = _factor_equivalent_analysis(df, asset_type="STOCK")
    assert out["segment_offset"] is False, out
    assert out["equivalent"] is False, out
    assert "cross_column_inconsistent" in out["reason"], out


# ---------------- A50：ETF tick 路由（0.001） ----------------
def test_a50_etf_uses_etf_tick():
    # price=10：ETF tick/price=1e-4（容差 2e-4）、STOCK tick/price=1e-3（容差 2e-3）
    df = _mk(n=240, price=10.0, delta_high=3e-4)
    out_etf = _factor_equivalent_analysis(df, asset_type="ETF")
    assert out_etf["equivalent"] is False, out_etf     # 3e-4 > ETF 容差 2e-4 ⇒ 拒
    out_stk = _factor_equivalent_analysis(df, asset_type="STOCK")
    assert out_stk["equivalent"] is True, out_stk     # 3e-4 < STOCK 容差 2e-3 ⇒ 放行


# ---------------- A51：日线严格下限（零回归） ----------------
def test_a51_daily_floor_keeps_1e9_semantics():
    # price 极大（1e9）⇒ tick/price=1e-11 < 1e-9 下限 ⇒ 容差取 1e-9
    df = _mk(n=240, price=1e9, delta_high=5e-9)
    out = _factor_equivalent_analysis(df, asset_type="STOCK")
    # 5e-9 偏移在 1e-9 下限之上 ⇒ 拒绝（与旧 1e-9 语义一致）
    assert out["equivalent"] is False, out
    assert "cross_column_inconsistent" in out["reason"], out


# ---------------- A52：常量与 env 回退门 ----------------
def test_a52_constants_and_env_toggle(monkeypatch):
    assert _FACTOR_EQUIV_COL_TOL_FLOOR == 1e-9
    assert _FACTOR_EQUIV_TICK_MULT == 2.0
    assert _FACTOR_EQUIV_SEG_MAX_ABS == 1e-2
    assert _FACTOR_EQUIV_SEG_MIN_BARS == 30
    from quantstudio.pipeline.qfq_reanchor_engine import _factor_equivalent_enabled
    monkeypatch.setenv("QS_QFQ_FACTOR_EQUIVALENCE", "0")
    assert _factor_equivalent_enabled() is False
    monkeypatch.setenv("QS_QFQ_FACTOR_EQUIVALENCE", "1")
    assert _factor_equivalent_enabled() is True


# ---------------- A53：真实数据重放（离线，依赖预抽取的重放数据集） ----------------
_REPLAY = r"D:\miniQMT策略实盘\QuantStudio\data\forensics_1021\minute_rho_replay.parquet"


@pytest.mark.skipif(not os.path.exists(_REPLAY),
                    reason="需预抽取的重放数据集（scripts/extract_minute_rho_replay.py）")
def test_a53_real_replay_releases_and_no_segment_offset():
    """真实数据重放：三码均应放行（equivalent=True），且 E5 不触发（无整段偏移形态）。

    数据：三码全历史 1min 的 fresh（daemon 导出分片）× db（50.54GB 备份快照）join，
    取「与引擎口径一致的不一致行」（|Δ|>1e-9）。
    """
    df = pd.read_parquet(_REPLAY)
    for code in ("000726", "000729", "000563"):
        sub = df[df["code"] == code]
        assert len(sub) > 1000, (code, len(sub))
        eps = 1e-9
        mask = np.zeros(len(sub), dtype=bool)
        for c in RAW_COLS:
            d = (sub[f"{c}_f"].to_numpy(float) - sub[f"{c}_b"].to_numpy(float))
            mask |= np.abs(d) > eps
        mm = sub[mask]
        assert len(mm) >= 8, (code, len(mm))
        payload = pd.DataFrame({"time": mm["time"].astype("int64")})
        for c in RAW_COLS:
            payload[f"db_{c}"] = mm[f"{c}_b"].astype(float)
            payload[f"fresh_{c}"] = mm[f"{c}_f"].astype(float)
        out = _factor_equivalent_analysis(payload, asset_type="STOCK")
        assert out["equivalent"] is True, (code, out)
        assert out["segment_offset"] is False, (code, out)
