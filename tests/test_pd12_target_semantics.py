# -*- coding: utf-8 -*-
"""P-D12 目标市值语义修复测试（WP-B / B1+B2+B3，2026-08-26）。

设计：docs/pd12-target-value-semantics-design.md（Step 2 审计通过 + 两处细化）
根因：docs/evidence/b2-target-value-semantics-20260826.md（E-2 双端同构接线降级）

矩阵（设计 §5 + 审计细化① T13）：
  T1 空仓全额 | T2 存量补差 | T3 减仓 | T4 清仓保底 | T5 微调跳过（0.5%）
  T6 delta<1 手告警 | T7 px 缺失回退 | T8 拆单作用于 delta
  T9 双端同构 | T10 阈值一致性 | T11 compliance 存量回归 | T13 三面等价（审计细化①）

hermetic：无 DB 依赖；与 test_b2_target_value_semantics_repro.py 同款引擎构造。
"""
import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import quantstudio.strategy_compiler.source_import as si  # noqa: E402
from quantstudio.backtest.ptrade_api import _api  # noqa: E402


# ========== 辅助构造 ==========

PX = 1.058
CODE = "515050.SS"
QMT = "515050.SH"


def _make_engine(cash=200_000.0):
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test_pd12.db", strategy={},
        start="2026-07-01", end="2026-07-31",
        engine_profile="daily-bar-v1",
        match_price_mode="close",
    )
    engine.account.cash = cash
    return engine


def _attach(prices):
    engine = _make_engine()
    _api.attach_day(engine, None, None, "2026-07-27", "2026-07-24", prices=prices)
    return engine


def _seed(engine, volume, avg_cost=1.018):
    from quantstudio.backtest.backtest_engine import Position
    engine.account.positions[QMT] = Position(
        code=QMT, volume=volume, avg_cost=avg_cost, can_sell=volume)
    return engine.account.positions[QMT]


@pytest.fixture
def wiring_env():
    """快照/还原接线层全局（_QSLastCloseState + _api 可变字段）。"""
    from quantstudio.backtest import ptrade_api as pai
    saved_cache = (pai._QSLastCloseState.cache, pai._QSLastCloseState.stamp)
    pai._QSLastCloseState.cache = {"515050": ("2026-07-24", PX)}
    pai._QSLastCloseState.stamp = "2026-07-24"
    saved_api = {f: getattr(pai._api, f, None)
                 for f in ("_engine", "_prices", "_current_date", "_prev_date")}
    yield pai
    pai._QSLastCloseState.cache, pai._QSLastCloseState.stamp = saved_cache
    for f, v in saved_api.items():
        setattr(pai._api, f, v)


def _volume_after(pai, engine):
    pos = engine.account.positions.get(QMT)
    return pos.volume if pos else 0


# ========== T1：空仓全额（与修复前等价） ==========

def test_t1_empty_position_full_buy(wiring_env):
    pai = wiring_env
    engine = _attach(prices={QMT: PX})
    pai.order_target_value(CODE, 20_000)
    assert _volume_after(pai, engine) == 18_900  # 20000/1.058 → 18900 整手


# ========== T2：存量加仓补差（B2 复现① 场景，新接线绿） ==========

def test_t2_existing_position_delta_buy(wiring_env):
    pai = wiring_env
    engine = _attach(prices={QMT: PX})
    _seed(engine, 40_600, avg_cost=1.018)
    pai.order_target_value(CODE, 44_859.2)     # current=42,954.8 delta=1,904.4
    vol = _volume_after(pai, engine)
    assert 40_600 < vol <= 40_600 + 1_900, (
        f"补差应 ≤1,900 股（delta 1,904/1.058≈1,799→1,700 股），实际 {vol}")


# ========== T3：减仓（B2 复现② 场景） ==========

def test_t3_reduce_position(wiring_env):
    pai = wiring_env
    engine = _attach(prices={QMT: PX})
    _seed(engine, 50_000, avg_cost=1.0)
    pai.order_target_value(CODE, 30_000)        # current=52,900 delta=-22,900
    vol = _volume_after(pai, engine)
    assert vol < 50_000, f"减仓应减少持仓，实际 {vol}"


# ========== T4：清仓保底 ==========

def test_t4_value_zero_clears(wiring_env):
    pai = wiring_env
    engine = _attach(prices={QMT: PX})
    _seed(engine, 40_600, avg_cost=1.018)
    pai.order_target_value(CODE, 0)
    assert _volume_after(pai, engine) == 0


# ========== T5：微调跳过（0.5% 阈值） ==========

def test_t5_below_rebalance_threshold(wiring_env):
    pai = wiring_env
    engine = _attach(prices={QMT: PX})
    _seed(engine, 40_600, avg_cost=1.018)      # current=42,954.8
    result = pai.order_target_value(CODE, 43_100)  # delta=145 → 0.34% < 0.5%
    vol = _volume_after(pai, engine)
    assert vol == 40_600, f"微调应跳过，实际 {vol}"
    assert result is not None and getattr(result, 'status', '') == 'rejected'
    assert 'below_rebalance_threshold' in getattr(result, 'reason', '')


# ========== T6：delta < 1 手告警（B3） ==========

def test_t6_delta_below_one_lot_warning(wiring_env, caplog):
    pai = wiring_env
    engine = _attach(prices={QMT: PX})
    _seed(engine, 4_000, avg_cost=1.018)       # current=4,232 阈值 0.5%=21.16
    result = pai.order_target_value(CODE, 4_296)    # delta=64 ∈ (21.16, 105.8)
    assert _volume_after(pai, engine) == 4_000, "delta<1手应 no-op"
    assert result is not None and 'delta_below_one_lot' in getattr(result, 'reason', '')


# ========== T7：px 缺失回退原生 ==========

def test_t7_px_missing_fallback(wiring_env, monkeypatch):
    pai = wiring_env
    engine = _attach(prices={QMT: PX})
    captured = []
    monkeypatch.setattr(pai._QSOrderWiringState, "target_orig",
                        lambda sec, val, *a, **kw: captured.append((sec, val)) or "orig")
    monkeypatch.setattr(pai._QSLastCloseState, "cache", {})
    pai.order_target_value(CODE, 20_000)
    assert captured == [(CODE, 20_000.0)]   # px=0 → 原生路径


# ========== T8：拆单作用于 delta ==========

def test_t8_split_applies_to_delta(wiring_env):
    pai = wiring_env
    engine = _attach(prices={QMT: PX})
    _seed(engine, 40_000, avg_cost=1.0)      # current=42,320
    # delta = 516,680 → 488,355 股 > 49,000 → 拆单
    pai.order_target_value(CODE, 559_000)
    vol = _volume_after(pai, engine)
    # delta 516,680/1.058 ≈ 488,355 → 拆 10 段×49,000 + 尾段；总持股 ≤40,000+490,000
    assert vol > 40_000, "应有加仓"
    assert vol <= 40_000 + 488_400, f"拆单应作用于 delta 而非全额，实际 {vol}"


# ========== T9：双端同构（B1 模板 exec vs B2 本地接线） ==========

def _b1_template_ns():
    """exec 渲染后的 B1 模板（P-D12 版）→ 命名空间。"""
    captured = []

    def _fake_target(security, value, *a, **kw):
        captured.append(("target_orig", security, value))
        return "tid"

    def _fake_order(security, amount, *a, **kw):
        captured.append(("order", security, amount))
        return "oid"

    ns = {"order_target_value": _fake_target, "order": _fake_order,
          "order_value": lambda *a, **kw: None,
          "order_percent": lambda *a, **kw: None,
          "order_target_percent": lambda *a, **kw: None,
          "get_history": lambda *a, **kw: None,
          "current_price": lambda *a, **kw: 0.0,
          "get_position": lambda code: type("P", (), {"amount": 0})(),
          "_qs_norm_code": lambda c: c,
          "log": type("L", (), {"warning": staticmethod(lambda *a, **kw: None),
                                "info": staticmethod(lambda *a, **kw: None)})()}
    exec(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), ns)
    ns["_captured"] = captured
    return ns


def test_t9_dual_end_homology(wiring_env):
    """双端同构：空仓 target 20,000 → B1 模板与 B2 本地接线产出 (code, amount) 序列一致。"""
    pai = wiring_env
    engine = _attach(prices={QMT: PX})
    ns = _b1_template_ns()
    # B1 模板内 _qs_last_close_lookup 用注入缓存——空仓时现值=0 → delta=全额
    ns["_QSLastCloseState"].cache = {"515050": ("2026-07-24", PX)}
    ns["order_target_value"]("515050.SS", 20_000)
    b1_orders = [c for c in ns["_captured"] if c[0] == "order"]

    # B2 本地（空仓 → delta=全额拆单）
    b2_captured = []
    import quantstudio.backtest.ptrade_api as pai_mod
    orig_order = pai_mod._QSOrderWiringState.order_orig
    pai_mod._QSOrderWiringState.order_orig = (
        lambda sec, amt, *a, **kw: b2_captured.append((sec, amt)) or "oid")
    try:
        pai.order_target_value(CODE, 20_000)
    finally:
        pai_mod._QSOrderWiringState.order_orig = orig_order

    assert b1_orders, "B1 模板应产出订单"
    assert [(c[1], c[2]) for c in b1_orders] == b2_captured, (
        f"双端订单序列分叉：B1={b1_orders} vs B2={b2_captured}")


# ========== T10：阈值一致性（防漂移） ==========

def test_t10_threshold_consistency():
    from quantstudio.backtest.backtest_engine import BacktestEngine
    import inspect
    sig = inspect.signature(BacktestEngine.__init__)
    engine_default = sig.parameters['min_rebalance_pct'].default
    from quantstudio.backtest.ptrade_api import _QS_MIN_REBALANCE_PCT as wiring_val
    ns = _b1_template_ns()
    template_val = ns["_QS_MIN_REBALANCE_PCT"]
    assert wiring_val == engine_default == template_val == 0.005, (
        f"阈值漂移：engine={engine_default} wiring={wiring_val} template={template_val}")


# ========== T13：三面等价（审计细化①） ==========

def test_t13_three_way_equivalence(wiring_env):
    """wiring-delta == engine-native-delta 逐笔等价（同场景三路径）。

    场景：持仓 40,600@1.058 → target 44,859.2（delta 1,904.4 → 1,700 股）
      引擎原生（target_orig 直调）→ _immediate_execute delta 分支
      新接线（_qs_wire_order_target_value）→ B2 修复后路径
    断言：两者成交量相等（B2 复现测试已证旧接线红——本例补第三面）。
    """
    pai = wiring_env
    # 引擎原生
    engine1 = _attach(prices={QMT: PX})
    _seed(engine1, 40_600, avg_cost=1.018)
    r1 = pai._QSOrderWiringState.target_orig(CODE, 44_859.2)
    vol1 = engine1.account.positions.get(QMT).volume
    # 新接线
    engine2 = _attach(prices={QMT: PX})
    _seed(engine2, 40_600, avg_cost=1.018)
    r2 = pai.order_target_value(CODE, 44_859.2)
    vol2 = engine2.account.positions.get(QMT).volume
    assert vol1 == vol2, (
        f"接线/引擎原生分叉：native={vol1} wiring={vol2}"
        f"（native order={r1}, wiring order={r2}）")


# ========== B1 模板渲染与门控 ==========

def test_b1_template_contains_pd12_marker():
    rendered = si._QS_ORDER_SPLIT_EXT
    assert "_QS_MIN_REBALANCE_PCT" in rendered
    assert "_qs_pos_amount" in rendered
    assert "delta_below_one_lot" in rendered
    assert "below_rebalance_threshold" in rendered


def test_b1_template_compiles():
    compile(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), "<pd12>", "exec")


def test_t11_compliance_stock_cases_still_green():
    """T11 隐含验证：compliance 全套件在 CI 中跑——此处跑关键 5 用例确认。"""
    import subprocess
    r = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/test_ptrade_contract_compliance.py", "-q", "--no-header",
         "-p", "no:cacheprovider", "--tb=no"],
        capture_output=True, text=True, cwd=str(pathlib.Path(__file__).parents[1]))
    assert r.returncode == 0, f"compliance 套件红：{r.stdout[-500:]}"
