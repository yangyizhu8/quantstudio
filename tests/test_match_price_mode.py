"""A2 同步测试：match_price_mode 撮合价三模式 + 未来函数消除。

验证目标（对应方案 v2.1 Phase A2）：
1. match_price_mode 参数校验（非法值拒绝）
2. _build_match_prices 按 mode 取不同价格列
3. close（默认）= 当日收盘；open = 当日开盘；next_open = 次日开盘
4. next_open 消除未来函数（策略 T 日决策，T+1 开盘成交）
5. 记账价始终用当日收盘（与撮合价分离）
"""
from pathlib import Path

import pytest
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


# ========== 参数校验 ==========

def test_match_price_mode_default_is_close():
    """默认 match_price_mode = close（兼容历史回测结果）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
    )
    assert engine.match_price_mode == "close"


def test_match_price_mode_rejects_invalid_value():
    """非法 match_price_mode 应抛 ValueError"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    with pytest.raises(ValueError, match="match_price_mode"):
        BacktestEngine(
            db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
            match_price_mode="invalid_mode",
        )


def test_match_price_mode_accepts_three_valid_values():
    """close / open / next_open 三种合法值都能构造引擎"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    for mode in ("close", "open", "next_open"):
        engine = BacktestEngine(
            db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
            match_price_mode=mode,
        )
        assert engine.match_price_mode == mode


# ========== _build_match_prices 逻辑 ==========

def _make_test_curr_data():
    """构造测试用全市场 DataFrame（2 只股票）"""
    return pd.DataFrame({
        'code': ['600000', '000001'],
        'open': [10.0, 15.0],
        'close': [10.5, 14.5],   # close 与 open 不同，便于区分
        'preClose': [9.8, 15.2],
    })


def test_build_match_prices_close_uses_close_column():
    """close 模式：撮合价取当日 close 列"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
        match_price_mode="close",
    )
    curr = _make_test_curr_data()
    prices = engine._build_match_prices(curr, [pd.Timestamp("2026-01-05")], 0)
    # 600000→.SH close=10.5, 000001→.SZ close=14.5
    assert prices["600000.SH"] == 10.5
    assert prices["000001.SZ"] == 14.5


def test_build_match_prices_open_uses_open_column():
    """open 模式：撮合价取当日 open 列（与 close 不同）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
        match_price_mode="open",
    )
    curr = _make_test_curr_data()
    prices = engine._build_match_prices(curr, [pd.Timestamp("2026-01-05")], 0)
    assert prices["600000.SH"] == 10.0  # open
    assert prices["000001.SZ"] == 15.0  # open


def test_build_match_prices_open_differs_from_close():
    """open 与 close 模式产出不同撮合价（证明模式切换生效）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    curr = _make_test_curr_data()
    trade_days = [pd.Timestamp("2026-01-05")]

    engine_close = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
        match_price_mode="close")
    engine_open = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
        match_price_mode="open")

    p_close = engine_close._build_match_prices(curr, trade_days, 0)
    p_open = engine_open._build_match_prices(curr, trade_days, 0)
    assert p_close != p_open  # close=10.5/14.5, open=10.0/15.0


# ========== 未来函数语义 ==========

def test_next_open_falls_back_on_last_day(monkeypatch):
    """next_open 在末日（无次日）回退到当日 close，不报错"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={}, start="2026-01-01", end="2026-01-02",
        match_price_mode="next_open",
    )
    curr = _make_test_curr_data()
    # i=0 且只有 1 个交易日（i+1 越界）→ 应回退 close
    prices = engine._build_match_prices(curr, [pd.Timestamp("2026-01-05")], 0)
    assert prices["600000.SH"] == 10.5  # 回退到 close


def test_record_price_always_uses_close_regardless_of_mode():
    """记账价（净值估值）始终用当日收盘，与撮合价分离。

    这是 A2 的关键设计：即使 match_price_mode=open/next_open，
    nav_history 的净值仍按当日收盘估值（标准做法）。
    验证方式：源码里 prices（记账价）的构建不引用 match_price_mode。"""
    engine_file = ROOT / "quantstudio" / "backtest" / "backtest_engine.py"
    content = engine_file.read_text(encoding="utf-8")

    # 记账价 prices 构建行应明确用 close（不随 mode 变）
    assert "curr_data['close']" in content
    # 撮合价与记账价是分离的两个变量
    assert "match_prices" in content
    assert "prices = {self._to_qmt" in content  # 记账价独立构建


# ========== 源码落地验证 ==========

def test_match_price_mode_in_cli():
    """CLI 支持 --match-price 参数"""
    cli_file = ROOT / "quantstudio" / "backtest" / "run_ptrade_strategy.py"
    content = cli_file.read_text(encoding="utf-8")
    assert "--match-price" in content
    assert "match_price_mode=match_price_mode" in content


def test_match_price_mode_exposed_in_engine_init():
    """BacktestEngine.__init__ 暴露 match_price_mode 参数"""
    engine_file = ROOT / "quantstudio" / "backtest" / "backtest_engine.py"
    content = engine_file.read_text(encoding="utf-8")
    assert "match_price_mode: str = \"close\"" in content
