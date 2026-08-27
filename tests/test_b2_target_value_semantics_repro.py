# -*- coding: utf-8 -*-
"""B2 最小复现：order_target_value 目标市值语义在本地拆单接线后失效（P-D12 / E-2）。

背景（docs/dual-end-alignment-master-plan.md WP-B / 证据 E-2）：
    2026-08-22 A1 本地拆单接线（ptrade_api.py L2299-2357）把模块级
    ``order_target_value = _qs_wire_order_target_value``，该包装经
    ``_qs_split_order(n = value / px)`` 按**全额目标市值**折股数后调用
    **shares 语义**的 ``order()`` —— 完全绕过引擎 ``_immediate_execute``
    的 target_value delta 分支（backtest_engine.py L695-711）。

实证（tech_etf 20260825_220017 trades.csv）：
    2026-07-27 已持 515050.SH 40,600 股（现值 42,954.8 @1.058），
    策略 order_target_value('515050.SS', ~44,859) 本应补差 ~1,900 股，
    实际全额再买 42,400 股（金字塔加仓，双端同 bug —— 转换侧注入模板同构）。

本测试为「先红后绿」复现：断言按**原生 target 语义**书写（B2 修复后转绿，
作为永久回归）。对照组直调引擎原生路径，证明引擎 delta 逻辑本身正确、
缺陷隔离在接线层。

hermetic：不连真实库（引擎临时 db_path + config=None 跳过 preload）。
"""
import pytest


# ========== 辅助构造 ==========

def _make_engine(cash=200_000.0):
    """hermetic 引擎：daily-bar + close 即时撮合（与 08-25 双端回测同路径）。"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test_b2.db", strategy={},
        start="2026-07-01", end="2026-07-31",
        engine_profile="daily-bar-v1",
        match_price_mode="close",
    )
    engine.account.cash = cash
    return engine


def _attach(prices, curr_date="2026-07-27", prev_date="2026-07-24"):
    """attach _api（curr/prev data 为 None → 涨跌停 pctChg 走 0 回退，不触库）。"""
    from quantstudio.backtest.ptrade_api import _api
    engine = _make_engine()
    _api.attach_day(engine, None, None, curr_date, prev_date, prices=prices)
    return engine


def _seed_position(engine, volume, avg_cost, can_sell=None, px_code="515050.SH"):
    from quantstudio.backtest.backtest_engine import Position
    engine.account.positions[px_code] = Position(
        code=px_code, volume=volume, avg_cost=avg_cost,
        can_sell=volume if can_sell is None else can_sell)
    return engine.account.positions[px_code]


@pytest.fixture
def last_close_cache():
    """种入/还原统一链 ① 层前收缓存（模块级全局，测试后恢复）。"""
    from quantstudio.backtest import ptrade_api as pai
    saved = (pai._QSLastCloseState.cache, pai._QSLastCloseState.stamp)
    pai._QSLastCloseState.cache = {"515050": ("2026-07-24", 1.058)}
    pai._QSLastCloseState.stamp = "2026-07-24"
    yield pai
    pai._QSLastCloseState.cache, pai._QSLastCloseState.stamp = saved


@pytest.fixture(autouse=True)
def api_state_clean():
    """快照/还原全局 _api 可变状态（各用例独立，防测试间污染）。

    背景（2026-08-26）：全文件合跑时 2 红 3 绿 → 3 红 2 绿——`_attach` 把
    新引擎绑到同一全局 `_api`，前序用例（含预期红的复现用例）的引擎/价格/日期
    状态泄漏到后续保底用例。单跑通过、合跑失败 = 测试污染，非产品回归。
    本 fixture 在用例前后快照/还原 _api 的引擎绑定与数据注入字段。
    """
    from quantstudio.backtest import ptrade_api as pai
    fields = ("_engine", "_prices", "_current_date", "_prev_date",
              "_current_day_data", "_prev_day_data")
    saved = {f: getattr(pai._api, f, None) for f in fields}
    yield
    for f, v in saved.items():
        setattr(pai._api, f, v)


def _wired_otv(pai, security, value):
    """策略注入面：模块级 order_target_value（拆单接线后的名字）。"""
    return pai.order_target_value(security, value)


def _native_otv(pai, security, value):
    """对照组：绕过接线，直调 PtradeAPI 原生方法（引擎 delta 路径）。"""
    return pai._QSOrderWiringState.target_orig(security, value)


PX = 1.058
CODE = "515050.SS"
QMT = "515050.SH"


# ========== 复现用例（当前预期 RED；B2 修复后转绿 = 永久回归） ==========

def test_repro_existing_position_full_value_buy(last_close_cache):
    """复现①（E-2 主场景）：持仓存在时 target 语义退化为全额买入。

    原生语义：delta = 44,859.2 - 40,600×1.058(42,954.8) = 1,904.4 → 补差 ≤1,900 股。
    接线现状：全额买 int(44,859.2/1.058/100)×100 = 42,400 股（金字塔）。
    """
    pai = last_close_cache
    engine = _attach(prices={QMT: PX})
    _seed_position(engine, volume=40_600, avg_cost=1.018)
    result = _wired_otv(pai, CODE, 44_859.2)
    pos = engine.account.positions.get(QMT)
    new_volume = pos.volume if pos else 0
    assert new_volume <= 40_600 + 1_900, (
        "order_target_value 存量持仓场景退化为全额买入："
        f"期望 ≤42,500 股（delta 补差），实际 {new_volume:,} 股"
        f"（order 返回 {result}）—— E-2 / P-D12 复现")


def test_repro_reduce_target_becomes_buy(last_close_cache):
    """复现②（更严重方向）：target < 现值的减仓指令变成全额买入。

    原生语义：delta = 30,000 - 50,000×1.058(52,900) = -22,900 → 卖出减仓。
    接线现状：全额买 int(30,000/1.058/100)×100 = 28,300 股 → 持仓反而暴增。
    """
    pai = last_close_cache
    engine = _attach(prices={QMT: PX})
    _seed_position(engine, volume=50_000, avg_cost=1.000)
    _wired_otv(pai, CODE, 30_000)
    pos = engine.account.positions.get(QMT)
    new_volume = pos.volume if pos else 0
    assert new_volume < 50_000, (
        "order_target_value 减仓方向失效：目标 30,000 < 现值 52,900 应卖出，"
        f"实际持仓 {new_volume:,} 股（不减反增）—— E-2 / P-D12 复现")


# ========== 对照/保底用例（当前预期 GREEN：缺陷隔离与安全边界证明） ==========

def test_control_engine_native_delta_works(last_close_cache):
    """对照组：引擎原生 order_target_value delta 逻辑本身正确（缺陷在接线层）。"""
    pai = last_close_cache
    engine = _attach(prices={QMT: PX})
    _seed_position(engine, volume=40_600, avg_cost=1.018)
    result = _native_otv(pai, CODE, 44_859.2)
    pos = engine.account.positions.get(QMT)
    new_volume = pos.volume if pos else 0
    assert new_volume <= 40_600 + 1_900, (
        f"引擎原生路径 delta 失效（{new_volume:,} 股，order={result}）"
        "—— 若此处红，缺陷层级需上移到 backtest_engine 重查")


def test_control_value_zero_clears(last_close_cache):
    """保底：value=0 清仓不经拆单路径，原生清仓仍工作（B1 修复必须保留的边界）。"""
    pai = last_close_cache
    engine = _attach(prices={QMT: PX})
    _seed_position(engine, volume=40_600, avg_cost=1.018)
    _wired_otv(pai, CODE, 0)
    pos = engine.account.positions.get(QMT)
    assert (pos is None) or (pos.volume == 0), "value=0 清仓路径被破坏"


def test_control_empty_position_full_buy_equivalent(last_close_cache):
    """空仓场景接线与原生语义等价（全额建仓，无存量差）。"""
    pai = last_close_cache
    engine = _attach(prices={QMT: PX})
    _wired_otv(pai, CODE, 20_000)
    pos = engine.account.positions.get(QMT)
    assert pos is not None and pos.volume == 18_900, (
        f"空仓全额建仓异常：volume={pos.volume if pos else None}")
