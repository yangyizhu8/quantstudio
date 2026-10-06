# -*- coding: utf-8 -*-
"""POS-01 cost_basis 摊薄口径契约测试（V1，六步④验收件）。

平台实证黄金：四象限ETF轮动案例（2026-10-06 归因），PTrade Log.txt 512890 双锚点：
  - 2024-09-02 盘后成本显示 0.973 ← 摊薄链推演 0.97345（四舍五入 0.973）
  - 2024-09-11 盘后成本显示 0.972 ← 加权买入 1900@0.944 后 0.97224（四舍五入 0.972）
口径：卖出摊薄扣费（净得=成交额−佣金−印花税−过户费）+ 买入加权不含费。
证据：docs/cost-basis-diluted-alignment-design.md + knowledge/contracts/position-fields.md §1
"""
import pytest

from quantstudio.backtest.backtest_engine import (
    BacktestEngine, Position, TradeCost, Account, BacktestResult,
)


def _make_engine(method="diluted"):
    """轻量引擎实例：绕过 __init__，仅装配成交记账所需的属性。"""
    eng = object.__new__(BacktestEngine)
    eng.cost = TradeCost(commission_rate=0.00035, min_commission=5.0,
                         stamp_tax_rate=0.001, transfer_fee_rate=0.00001)
    eng.cost_basis_method = method
    eng.account = Account()
    eng.account.cash = 1e8  # 资金充足：绕过买入资金检查（本测试只验成本记账）
    eng.result = BacktestResult()
    eng._stamp_tax_rate = lambda date: 0.0  # ETF 免印花税（平台案例同口径）
    eng._apply_slippage = lambda price, side: price  # 平台实证 0 滑点
    eng.etf_t0 = False
    eng._is_t0 = lambda code: False
    return eng


class TestDilutedContractGolden:
    """契约黄金：平台 512890 实证序列逐位复现（V2 主锚点的前置单测）。"""

    def test_platform_golden_sequence_512890(self):
        eng = _make_engine("diluted")
        eng.account.positions["512890.SH"] = Position(
            code="512890.SH", volume=0, avg_cost=0.0)
        pos = eng.account.positions["512890.SH"]

        def step_buy(price, vol):
            pos.can_sell = pos.volume  # 简化：全部可卖（T+1 细节不影响成本记账）
            eng._execute_buy("512890.SH", price, buy_shares=vol, date="t")

        def step_sell(price, vol):
            pos.can_sell = pos.volume
            eng._execute_sell("512890.SH", price, sell_shares=vol, date="t")

        # 平台实证序列（费用自动按 TradeCost 计——与平台回测同费率）
        step_buy(0.925, 54000)    # 01-02 建仓
        assert pos.avg_cost == pytest.approx(0.925, abs=1e-9)
        step_sell(0.946, 500)     # 02-01 盈利卖出（摊薄）
        step_sell(1.004, 1500)    # 03-01
        step_sell(1.015, 41500)   # 04-01 大额盈利卖出 → 成本深度摊薄
        step_sell(1.047, 200)     # 05-06
        step_sell(1.071, 200)     # 06-03
        step_buy(1.078, 40600)    # 07-01 加权加仓
        assert pos.avg_cost == pytest.approx(0.97094, abs=5e-6)
        step_buy(1.025, 1800)     # 08-01
        step_buy(1.006, 500)      # 09-02 → 锚点 1（契约=平台显示 3 位）
        assert pos.volume == 53000
        assert round(pos.avg_cost, 3) == 0.973, \
            f"锚点1失败: {pos.avg_cost:.6f} 期望显示 0.973"
        step_buy(0.944, 1900)     # 09-11 防御加仓 → 锚点 2
        assert pos.volume == 54900
        assert round(pos.avg_cost, 3) == 0.972, \
            f"锚点2失败: {pos.avg_cost:.6f} 期望显示 0.972"


class TestDilutedSemantics:
    def test_loss_sell_dilutes_cost_upward(self):
        """亏损卖出：摊薄法抬高剩余成本（与盈利卖出方向相反）。"""
        eng = _make_engine("diluted")
        eng.account.positions["X.SH"] = Position(
            code="X.SH", volume=10000, avg_cost=1.0, can_sell=10000)
        eng._execute_sell("X.SH", 0.5, sell_shares=5000, date="t")
        # cost_total = 1.0×10000 − (2500 − 5 − 0.025) = 7505.025 → /5000
        assert eng.account.positions["X.SH"].avg_cost == pytest.approx(
            7505.025 / 5000, abs=1e-9)

    def test_negative_dilution_clamped_to_zero_with_audit(self):
        """极端大额盈利卖出 → 摊薄成本总额为负 → 钳 0（审计留痕由 logger 记录）。"""
        eng = _make_engine("diluted")
        eng.account.positions["X.SH"] = Position(
            code="X.SH", volume=1000, avg_cost=1.0, can_sell=1000)
        eng._execute_sell("X.SH", 100.0, sell_shares=500, date="t")
        # 10000 − 49995 ≪ 0 → 钳 0
        assert eng.account.positions["X.SH"].avg_cost == 0.0

    def test_close_position_resets_cost_then_rebuild(self):
        """清仓归 0 + 重建仓成本为新成交价（不含费）。"""
        eng = _make_engine("diluted")
        eng.account.positions["X.SH"] = Position(
            code="X.SH", volume=100, avg_cost=2.0, can_sell=100)
        eng._execute_sell("X.SH", 3.0, sell_all=True, date="t")
        pos = eng.account.positions["X.SH"]
        assert pos.volume == 0 and pos.avg_cost == 0
        eng._execute_buy("X.SH", 4.0, buy_shares=200, date="t")
        assert pos.avg_cost == pytest.approx(4.0, abs=1e-9)

    def test_moving_avg_legacy_unchanged_on_sell(self):
        """legacy 口径：卖出不改成本（2026-10-06 前行为，V3 回退开关）。"""
        eng = _make_engine("moving_avg")
        eng.account.positions["X.SH"] = Position(
            code="X.SH", volume=52000, avg_cost=0.925, can_sell=52000)
        eng._execute_sell("X.SH", 1.015, sell_shares=41500, date="t")
        assert eng.account.positions["X.SH"].avg_cost == pytest.approx(0.925, abs=1e-12)


class TestBuyWeightingParity:
    def test_buy_identical_across_methods(self):
        """买入式双口径同构（均不含费加权）——差异仅在卖出摊薄。"""
        for method in ("diluted", "moving_avg"):
            eng = _make_engine(method)
            eng.account.positions["X.SH"] = Position(
                code="X.SH", volume=100, avg_cost=1.0)
            eng._execute_buy("X.SH", 2.0, buy_shares=100, date="t")
            assert eng.account.positions["X.SH"].avg_cost == pytest.approx(1.5, abs=1e-12)