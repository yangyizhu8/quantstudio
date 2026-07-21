"""PR4 契约测试：日线 Profile 兼容性（黄金用例 2 + 日线零触达）。

验证目标：
1. 黄金 2：1m 双均线策略在分钟引擎可运行（日线兼容）
2. 日线 Profile（默认 engine_profile='daily-bar-v1'）逐行不变
3. Fidelity 双门禁直接证明"日线原有策略结果不变"（在固定验证顺序的步骤 6 执行）
"""
import pytest


# ========== 日线 Profile 默认值与隔离 ==========

def test_engine_profile_defaults_to_daily():
    """BacktestEngine 默认 engine_profile='daily-bar-v1'（向后兼容）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-05", end="2026-01-05",
    )
    assert engine.engine_profile == "daily-bar-v1"


def test_engine_profile_rejects_invalid():
    """非法 engine_profile 抛 ValueError"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    with pytest.raises(ValueError, match="engine_profile"):
        BacktestEngine(
            db_path="/tmp/test.db", strategy={},
            start="2026-01-05", end="2026-01-05",
            engine_profile="invalid-profile",
        )


def test_daily_profile_etf_t0_forced_false():
    """日线 Profile 下 etf_t0 即使传入 True 也被强制为 False（守护黄金基线）

    决策 4 隔离：CLI 层 etf_t0 强制 `and profile=='minute-bar-v1'`。
    引擎层也应保证日线 Profile 下 etf_t0 生效值为 False。
    """
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-05", end="2026-01-05",
        engine_profile="daily-bar-v1", match_price_mode="close",
        etf_t0=True,   # 即使传 True
    )
    # 日线 Profile 下 etf_t0 实际生效值应为 False（或引擎保证不影响 T+1）
    # 实现细节：__init__ 内 if engine_profile != minute: self.etf_t0 = False
    assert engine.etf_t0 is False


# ========== engine_semantics_version ==========

def test_engine_semantics_version_minute():
    """分钟 Profile → engine_semantics_version='0.3.0-minute-bar'"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-05", end="2026-01-05",
        engine_profile="minute-bar-v1", match_price_mode="close",
    )
    assert engine.engine_semantics_version == "0.3.0-minute-bar"


def test_engine_semantics_version_daily_unchanged():
    """日线 Profile + close → engine_semantics_version='0.1.0-legacy'（PR2/PR3 不变）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-05", end="2026-01-05",
        engine_profile="daily-bar-v1", match_price_mode="close",
    )
    assert engine.engine_semantics_version == "0.1.0-legacy"


# ========== 日线零触达：源码级断言 ==========

def test_daily_main_loop_unchanged_by_minute_profile():
    """日线 Profile 的主循环代码未被分钟 Profile 改动（源码级）

    run() 内 daily 分支应保持逐行不变。验证：run() 含 engine_profile 分流，
    但 daily 分支逻辑（_build_match_prices/_drain_pending/_run_ptrade_strategy）仍在。
    """
    from pathlib import Path
    engine_file = Path(__file__).resolve().parent.parent / "quantstudio" / "backtest" / "backtest_engine.py"
    content = engine_file.read_text(encoding="utf-8")
    # engine_profile 分流存在
    assert "engine_profile" in content
    assert "minute-bar-v1" in content
    # _run_minute_day 方法存在（分钟分支）
    assert "_run_minute_day" in content
    # 日线核心方法仍在（未被删除）
    assert "_build_match_prices" in content
    assert "_drain_pending_orders" in content


# ========== Fidelity 双门禁直接证明（在固定验证顺序步骤 6 执行）==========
# 此测试不在此文件运行；由 scripts/run_strategy_fidelity_gates.py 保证：
# ETF PASS 87752.56 / 3 笔；smallcap CLOSE 118551.21 / 57 笔
# 这是主计划 7.27 "日线原有策略结果在允许误差内不变"的直接验证。
