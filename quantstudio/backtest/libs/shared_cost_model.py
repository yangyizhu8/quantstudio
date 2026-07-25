"""A股成本模型 — 统一费用计算。

核心特性：
- 滑点：支持双模式（tick最小变动价位 + ratio比例）
- 印花税：0.05%（2023年8月起正确值）
- 流量费：开源实现有，ChinaAEngine没有 → 可选加入
- 过户费：统一为双边万0.1

用法:
    from fusion_bridge.shared_cost_model import SharedCostModel

    model = SharedCostModel(
        commission_rate=0.00035,    # 万3.5（对齐 Ptrade 实际 ~万3.49）
        commission_min=5.0,         # 最低5元
        stamp_tax=0.001,            # 千1（对齐 Ptrade，卖出单向）
        transfer_fee=0.00001,       # 万0.1（双边）
        slippage_mode="ratio",      # "ratio" 或 "tick"
        slippage_ratio=0.0,         # ratio模式：0%（对齐 Ptrade，无滑点）
        slippage_tick_size=0.01,    # tick模式：最小变动价
        slippage_tick_count=0,      # tick模式：跳数（0=无滑点）
        flow_fee=0.0,               # 流量费（元/笔），0=不收
    )
    actual_price, total_cost = model.calc(price=10.5, volume=1000, direction="buy", code="000001.SZ")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SharedCostModel:
    """统一的A股交易成本模型。

    融合了 开源实现 的双滑点模式 + ChinaAEngine 的精确费率结构。
    """

    # 佣金
    commission_rate: float = 0.00035   # 万3.5（对齐 Ptrade 实际 ~万3.49）
    commission_min: float = 5.0        # 最低5元

    # 印花税（卖出单向）
    stamp_tax: float = 0.001           # 千1（对齐 Ptrade）

    # 过户费（双边）
    transfer_fee: float = 0.00001      # 万0.1

    # 滑点
    slippage_mode: Literal["ratio", "tick"] = "ratio"
    slippage_ratio: float = 0.0        # ratio模式：0%（对齐 Ptrade，无滑点）
    slippage_tick_size: float = 0.01   # tick模式：A股最小变动价1分
    slippage_tick_count: int = 2       # tick模式：跳2个最小单位

    # 流量费（元/笔），大多数券商为0
    flow_fee: float = 0.0

    # 价格精度
    price_decimals: int = 2            # 股票2位，ETF 3位

    def apply_slippage(self, price: float, direction: str) -> float:
        """计算滑点后价格。

        Args:
            price: 原始价格。
            direction: "buy" 或 "sell"。

        Returns:
            滑点后价格（按精度四舍五入）。
        """
        if self.slippage_mode == "tick":
            # tick模式：按最小变动价位跳数（开源实现方案）
            slip = self.slippage_tick_size * self.slippage_tick_count
            if direction == "buy":
                return round(price + slip, self.price_decimals)
            else:
                return round(price - slip, self.price_decimals)
        else:
            # ratio模式：按比例（ChinaAEngine方案，但开源实现除以2更精确）
            ratio = self.slippage_ratio / 2
            if direction == "buy":
                return round(price * (1 + ratio), self.price_decimals)
            else:
                return round(price * (1 - ratio), self.price_decimals)

    def calc_commission(self, notional: float) -> float:
        """佣金 = max(成交额×费率, 最低佣金)。"""
        return max(notional * self.commission_rate, self.commission_min)

    def calc_stamp_tax(self, notional: float, direction: str) -> float:
        """印花税（仅卖出收取）。"""
        if direction == "sell":
            return notional * self.stamp_tax
        return 0.0

    def calc_transfer_fee(self, notional: float, code: str) -> float:
        """过户费（双边收取）。

        注意：ChinaAEngine按成交额万0.1双边收取（统一口径）。
        开源实现仅沪市按笔收取，口径不同。此处采用统一口径。
        """
        return notional * self.transfer_fee

    def calc(
        self,
        price: float,
        volume: int,
        direction: str,
        code: str = "",
    ) -> tuple[float, float]:
        """计算完整交易成本。

        Args:
            price: 原始委托价格。
            volume: 成交数量（股）。
            direction: "buy" 或 "sell"。
            code: 股票代码（保留用于未来沪深差异化）。

        Returns:
            (滑点后实际成交价, 总交易成本)
        """
        if volume <= 0:
            return price, 0.0

        actual_price = self.apply_slippage(price, direction)
        notional = actual_price * volume

        commission = self.calc_commission(notional)
        stamp = self.calc_stamp_tax(notional, direction)
        transfer = self.calc_transfer_fee(notional, code)
        flow = self.flow_fee

        total_cost = commission + stamp + transfer + flow
        return actual_price, total_cost

    def summary(self) -> dict:
        """返回成本模型摘要（用于日志/报告）。"""
        return {
            "commission_rate": f"{self.commission_rate*10000:.1f}万",
            "commission_min": f"¥{self.commission_min:.0f}",
            "stamp_tax": f"{self.stamp_tax*10000:.1f}万 (卖出)",
            "transfer_fee": f"{self.transfer_fee*10000:.1f}万 (双边)",
            "slippage_mode": self.slippage_mode,
            "slippage_value": (
                f"{self.slippage_tick_size * self.slippage_tick_count:.2f}元"
                if self.slippage_mode == "tick"
                else f"{self.slippage_ratio*100:.1f}%"
            ),
            "flow_fee": f"¥{self.flow_fee:.1f}/笔",
        }
