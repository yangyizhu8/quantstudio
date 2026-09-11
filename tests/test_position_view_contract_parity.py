# -*- coding: utf-8 -*-
"""持仓视图契约 parity（2026-09-04 持仓视图契约修复 · 审计条件④）。

单一规范来源：`CONTRACT` 常量 dict —— 本地适配器与转换侧 _QSPositionView **共同断言**，
任一侧字段名/语义漂移即失败（替代「三处各自维护」）。

- 本地侧：BacktestEngine._get_ptrade_positions（唯一适配器）
- 转换侧：source_import._QS_POSITION_VIEW_EXT 渲染后的 _QSPositionView
  （平台运行时无本地代码可导入，靠本测试机械防漂移）
"""
import pytest

# ── 唯一规范表（改这里 = 改契约；两侧断言同时受约束）────────────────────────
# 给定同一组底层数值（数量 100 / 成本 10.0 / 可卖 80 / 现价 11.0 / 键 002830.SZ），
# 两种运行时都必须产出下列**同名同义**字段。
CONTRACT = {
    "sid": "002830.SZ",
    "amount": 100,
    "enable_amount": 80,
    "cost_basis": 10.0,
    "last_sale_price": 11.0,
    "avg_cost": 10.0,
    "market_value": 1100.0,   # last_sale_price × amount
}
KEY = "002830.SZ"
VOLUME, COST, ENABLE, PRICE = 100, 10.0, 80, 11.0


def _render_conversion_view():
    """渲染 P-D11 模板并 exec，返回转换侧 get_positions/get_position 命名空间。"""
    from quantstudio.strategy_compiler.source_import import _render_position_view_ext

    class _PlatformPos:
        def __init__(self):
            self.amount = VOLUME
            self.enable_amount = ENABLE
            self.cost_basis = COST
            self.last_sale_price = PRICE
            self.market_value = VOLUME * PRICE

    ns = {
        "get_positions": lambda security=None: {f"002830.XSHE": _PlatformPos()},
        "get_position": lambda security: _PlatformPos(),
        "_qs_shape_check": lambda *a, **k: None,   # 形状检查由本测试外的套件覆盖
    }
    exec(_render_position_view_ext("# [qs-import-generated] parity test"), ns)
    return ns


def test_local_adapter_satisfies_contract():
    """本地侧：唯一适配器产物逐字段等于规范表。"""
    from quantstudio.backtest.backtest_engine import Account, Position as EnginePosition, BacktestEngine
    from quantstudio.backtest.ptrade_api import _api

    engine = object.__new__(BacktestEngine)
    engine.account = Account(cash=1000.0, positions={
        KEY: EnginePosition(KEY, volume=VOLUME, avg_cost=COST, can_sell=ENABLE)})

    pos = engine._get_ptrade_positions({KEY: PRICE})[KEY]
    for field, expected in CONTRACT.items():
        assert getattr(pos, field) == pytest.approx(expected), (
            "本地适配器字段 %s 期望 %r 实得 %r" % (field, expected, getattr(pos, field, None)))
    # 不得泄漏引擎 dataclass 字段
    assert not hasattr(pos, "volume") and not hasattr(pos, "can_sell")


def test_engine_positions_property_satisfies_contract():
    """本地侧：context.portfolio.positions 委托后同样满足规范表（本修复的回归靶点）。"""
    from quantstudio.backtest.backtest_engine import Account, Position as EnginePosition, BacktestEngine
    from quantstudio.backtest.ptrade_api import Portfolio, _api

    engine = object.__new__(BacktestEngine)
    engine.account = Account(cash=1000.0, positions={
        KEY: EnginePosition(KEY, volume=VOLUME, avg_cost=COST, can_sell=ENABLE)})
    prev_e, prev_p = getattr(_api, "_engine", None), getattr(_api, "_prices", None)
    _api._engine, _api._prices = engine, {KEY: PRICE}
    try:
        pos = Portfolio(1000.0, {}).positions[KEY]
        for field, expected in CONTRACT.items():
            assert getattr(pos, field) == pytest.approx(expected), (
                "Portfolio.positions 字段 %s 期望 %r 实得 %r"
                % (field, expected, getattr(pos, field, None)))
    finally:
        _api._engine, _api._prices = prev_e, prev_p


def test_conversion_side_view_satisfies_same_contract():
    """转换侧：渲染后的 _QSPositionView 对同一组底层数值产出同名字段。"""
    ns = _render_conversion_view()
    out = ns["get_positions"]()
    assert list(out.keys()) == [KEY], "转换侧键必须归一为 .SS/.SZ（.XSHE 别名不得残留）"
    pos = out[KEY]
    for field, expected in CONTRACT.items():
        assert getattr(pos, field) == pytest.approx(expected), (
            "转换侧字段 %s 期望 %r 实得 %r" % (field, expected, getattr(pos, field, None)))


def test_contract_table_is_the_single_source():
    """两侧断言的字段集合必须与规范表完全一致（防只改一侧）。"""
    ns = _render_conversion_view()
    conv = ns["get_positions"]()[KEY]
    for field in CONTRACT:
        assert hasattr(conv, field), "转换侧缺字段 %s" % field
