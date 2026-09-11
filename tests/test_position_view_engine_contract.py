# -*- coding: utf-8 -*-
"""持仓视图契约（本地三入口）—— 2026-09-04 修复的常设回归。

契约：docs/portfolio-position-view-contract-design.md
覆盖：三入口一致 / 只读快照 / 空仓语义 / 键归一（含 BJ）/ 无引擎分支同形状（审计条件⑤）
      / last_sale_price 命中与回退两态（审计条件②）
"""
import pytest

from quantstudio.backtest.backtest_engine import (Account, BacktestEngine,
                                                  Position as EnginePosition)
from quantstudio.backtest.ptrade_api import Portfolio, _api

CODE = "002830.SZ"
CONTRACT_FIELDS = ("sid", "amount", "enable_amount", "cost_basis",
                   "last_sale_price", "avg_cost", "market_value")


def _bare_engine(volume=100, cost=10.0, can_sell=80, pending=0):
    eng = object.__new__(BacktestEngine)
    p = EnginePosition(CODE, volume=volume, avg_cost=cost, can_sell=can_sell)
    p.pending_sell_shares = pending
    eng.account = Account(cash=1000.0, positions={CODE: p})
    return eng


class _Attached:
    """把测试引擎挂到 _api 单例上并恢复（防跨测试残留）。"""

    def __init__(self, engine, prices):
        self.engine, self.prices = engine, prices

    def __enter__(self):
        self.prev = (getattr(_api, "_engine", None), getattr(_api, "_prices", None))
        _api._engine, _api._prices = self.engine, self.prices
        return _api

    def __exit__(self, *exc):
        _api._engine, _api._prices = self.prev
        return False


def test_three_entry_points_are_identical():
    """context.portfolio.positions 与 get_positions() 必须逐位一致（审计增补同源测试）。"""
    with _Attached(_bare_engine(), {CODE: 11.0}) as api:
        a = Portfolio(1000.0, {}).positions
        b = api.get_positions()
        assert list(a) == list(b) == [CODE]
        for f in CONTRACT_FIELDS:
            assert getattr(a[CODE], f) == pytest.approx(getattr(b[CODE], f)), \
                "入口不一致：字段 %s（portfolio.positions vs get_positions）" % f


def test_enable_amount_is_can_sell_minus_pending():
    """契约字段映射：enable_amount = can_sell − pending_sell_shares。"""
    eng = _bare_engine(volume=100, can_sell=70, pending=20)
    with _Attached(eng, {CODE: 11.0}) as api:
        pos = Portfolio(1000.0, {}).positions[CODE]
        assert pos.amount == 100
        assert pos.enable_amount == 50, "enable_amount 应为 can_sell − pending = 70 − 20"
        assert api.get_position(CODE).enable_amount == 50


def test_last_sale_price_hit_then_fallback():
    """审计条件②：有价必命中；无价才回退 avg_cost（回退不得成为常态路径）。"""
    eng = _bare_engine(volume=100, cost=10.0, can_sell=100)

    with _Attached(eng, {CODE: 11.0}):
        hit = Portfolio(1000.0, {}).positions[CODE]
        assert hit.last_sale_price == pytest.approx(11.0), "有价时必须命中 _prices，不得回落成本"
        assert hit.market_value == pytest.approx(1100.0)

    with _Attached(eng, {}):                      # 无价快照（盘前/收盘后空窗 或 该标的当日无行情）
        miss = Portfolio(1000.0, {}).positions[CODE]
        assert miss.last_sale_price == pytest.approx(10.0), "无价时显式回退 avg_cost"


def test_positions_is_read_only_snapshot():
    """D2 只读快照：策略改返回容器不得影响引擎持仓。"""
    eng = _bare_engine()
    with _Attached(eng, {CODE: 11.0}):
        snap = Portfolio(1000.0, {}).positions
        snap.clear()
        snap["999999.SZ"] = "junk"
    assert CODE in eng.account.positions, "返回容器被清空影响了引擎持仓"
    assert "999999.SZ" not in eng.account.positions


def test_empty_position_semantics():
    """Ptrade 真实平台行为：空仓返回 amount=0 的 Position（非 None）。"""
    with _Attached(_bare_engine(), {CODE: 11.0}) as api:
        pos = api.get_position(CODE)
        assert pos.amount == 100
        empty = api.get_position("999999.SZ")
        assert empty is not None and empty.amount == 0


def test_key_normalization_includes_bj_and_exact_match():
    """键归一 .SS/.SZ/.BJ 精确匹配；别名不得命中（alias 故意不感知）。"""
    with _Attached(_bare_engine(), {CODE: 11.0}) as api:
        keys = list(Portfolio(1000.0, {}).positions)
        assert keys == [CODE]
        assert "002830.XSHE" not in keys, "别名必须不命中（exact-match 键语义）"
    assert api._to_ptrade_code("600519") == "600519.SS"
    assert api._to_ptrade_code("920001").endswith(".BJ"), "北交所代码须归一到 .BJ"


def test_no_engine_branch_returns_same_shape():
    """审计条件⑤：无引擎回退分支必须返回同一契约形状（不是引擎 dataclass）。"""
    eng = _bare_engine(volume=100, cost=10.0, can_sell=60)
    init = eng._get_ptrade_positions({CODE: 12.0})     # 构造期快照（契约对象）
    with _Attached(None, {}):                          # 无引擎
        out = Portfolio(1000.0, init).positions
        assert list(out) == [CODE]
        for f in CONTRACT_FIELDS:
            assert hasattr(out[CODE], f), "无引擎分支缺契约字段 %s" % f
        assert out[CODE].amount == 100 and out[CODE].enable_amount == 60
        assert out[CODE].last_sale_price == pytest.approx(12.0)
        assert not hasattr(out[CODE], "volume"), "无引擎分支不得退回引擎 dataclass"
