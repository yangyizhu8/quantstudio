"""A3 同步测试：Order 对象 + 订单失败语义（D3）。

验证目标（对应方案 v2.1 Phase A3）：
1. order_target_value/order/order_value/order_target 返回 Order 对象（不再是字符串）
2. 涨跌停阻断返回 Order(status=rejected, reason=limit_up_blocked/down_blocked)
3. 成功成交返回 Order(status=filled)，含 filled_amount/price
4. 老策略兼容：if order: 判定为是否成交（__bool__）
5. Order 预留 filled/target/amount 字段（为 Phase E 实盘部分成交）
"""
from pathlib import Path

import pytest
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


# ========== Order dataclass ==========

def test_order_is_dataclass_with_required_fields():
    """Order 含 A3 设计的全部字段（含 Phase E 预留的 amount/pending）"""
    from quantstudio.backtest.backtest_engine import Order
    o = Order(order_id="ord_1", security="600000.SH", direction="buy",
              target=10000.0, filled=10000.0, target_amount=1000, filled_amount=1000,
              price=10.0, status="filled", reason="", created_dt="2026-01-05")
    assert o.order_id == "ord_1"
    assert o.security == "600000.SH"
    assert o.direction == "buy"
    assert o.filled == 10000.0
    assert o.target_amount == 1000
    assert o.filled_amount == 1000
    assert o.status == "filled"


def test_order_bool_true_when_filled():
    """成功成交的 Order 在 if 语境为 True（老策略兼容）"""
    from quantstudio.backtest.backtest_engine import Order
    filled = Order(order_id="x", security="600000.SH", direction="buy",
                   filled=1000.0, filled_amount=100, price=10.0, status="filled")
    assert bool(filled) is True
    assert "filled" if filled else "rejected"  # 老策略 if order: 用法


def test_order_bool_false_when_rejected():
    """被阻断的 Order 在 if 语境为 False（老策略兼容）"""
    from quantstudio.backtest.backtest_engine import Order
    rejected = Order(order_id="x", security="600000.SH", direction="buy",
                     status="rejected", reason="limit_up_blocked")
    assert bool(rejected) is False


def test_order_str_readable():
    """Order.__str__ 可读，便于日志"""
    from quantstudio.backtest.backtest_engine import Order
    filled = Order(order_id="x", security="600000.SH", direction="buy",
                   filled_amount=100, price=10.5, status="filled")
    rejected = Order(order_id="y", security="000001.SZ", direction="sell",
                     status="rejected", reason="limit_down_blocked")
    assert "filled" in str(filled)
    assert "100" in str(filled)
    assert "rejected" in str(rejected)
    assert "limit_down_blocked" in str(rejected)


# ========== _immediate_execute 返回 Order ==========

def _make_engine_with_cash(cash=1_000_000):
    """构造带资金和价格的引擎（不连真实库，用 mock 价格）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
    )
    engine.account.cash = cash
    return engine


def test_immediate_execute_no_price_returns_rejected_order():
    """无价格（价格<=0）返回 Order(status=rejected, reason=no_price)"""
    engine = _make_engine_with_cash()
    # prices 为空 → 取不到价
    order = engine._immediate_execute("600000.SH", target_value=10000,
                                      prices={}, date="2026-01-05", curr_data=None)
    assert order.status == "rejected"
    assert order.reason == "no_price"


def test_immediate_execute_buy_success_returns_filled_order():
    """买入成功返回 Order(status=filled)，含成交股数和价格"""
    engine = _make_engine_with_cash(cash=100_000)
    prices = {"600000.SH": 10.0}
    # curr_data 提供求涨跌幅判断（pct_chg=0 非涨停）
    curr_data = pd.DataFrame({'code': ['600000'], 'close': [10.0], 'preClose': [10.0]})
    order = engine._immediate_execute("600000.SH", target_value=10000,
                                      prices=prices, date="2026-01-05", curr_data=curr_data)
    assert order.status == "filled"
    assert order.filled_amount > 0
    assert order.price > 10.0  # 含买入滑点 (10 * 1.001)
    assert order.direction == "buy"


def test_immediate_execute_sell_success_returns_filled_order():
    """卖出成功返回 Order(status=filled)"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_engine_with_cash(cash=10_000)
    # 先建仓
    engine.account.positions["600000.SH"] = Position(code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    prices = {"600000.SH": 11.0}
    curr_data = pd.DataFrame({'code': ['600000'], 'close': [11.0], 'preClose': [11.0]})
    order = engine._immediate_execute("600000.SH", target_value=0,  # 清仓
                                      prices=prices, date="2026-01-05", curr_data=curr_data)
    assert order.status == "filled"
    assert order.filled_amount == 1000
    assert order.direction == "sell"


# ========== 涨跌停阻断 ==========

def test_immediate_execute_limit_up_blocked_returns_rejected():
    """买入涨停股返回 Order(status=rejected, reason=limit_up_blocked)"""
    engine = _make_engine_with_cash(cash=100_000)
    prices = {"600000.SH": 11.0}
    # 涨停：close/preClose - 1 = 10% （主板涨停 10%）
    curr_data = pd.DataFrame({'code': ['600000'], 'close': [11.0], 'preClose': [10.0]})
    order = engine._immediate_execute("600000.SH", target_value=10000,
                                      prices=prices, date="2026-01-05", curr_data=curr_data)
    assert order.status == "rejected"
    assert order.reason == "limit_up_blocked"
    assert order.filled_amount == 0
    assert bool(order) is False  # 老策略 if order: 判 False


def test_immediate_execute_limit_down_blocked_returns_rejected():
    """卖出跌停股返回 Order(status=rejected, reason=limit_down_blocked)"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_engine_with_cash(cash=10_000)
    engine.account.positions["600000.SH"] = Position(code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    prices = {"600000.SH": 9.0}
    # 跌停：close/preClose - 1 = -10%
    curr_data = pd.DataFrame({'code': ['600000'], 'close': [9.0], 'preClose': [10.0]})
    order = engine._immediate_execute("600000.SH", target_value=0,
                                      prices=prices, date="2026-01-05", curr_data=curr_data)
    assert order.status == "rejected"
    assert order.reason == "limit_down_blocked"


# ========== 源码落地验证 ==========

def test_no_meaningless_order_id_string_in_ptrade_api():
    """A3 核心验收：ptrade_api.py 不再返回无意义的 f"order_{id(security)}" 字符串"""
    ptrade_api_file = ROOT / "quantstudio" / "backtest" / "ptrade_api.py"
    content = ptrade_api_file.read_text(encoding="utf-8")
    assert 'f"order_{id(security)}"' not in content, \
        "ptrade_api.py 仍返回无意义订单字符串，A3 未完成"
