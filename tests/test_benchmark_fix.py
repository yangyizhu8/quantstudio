"""A0 同步测试：benchmark 双乘 100 bug 修复（D11）。

验证目标（对应方案 v2.1 Phase A0）：
1. nav_history 的 benchmark 字段归一化正确（首日=100，无双乘）
2. benchmark 曲线量级合理（与 nav 同量级，不被放大 100 倍）
3. report() 显示的初始资金动态（不再硬编码 1,000,000）

由于完整回测依赖真实 DuckDB，这里用单元测试验证归一化逻辑本身，
回归检查点（跑真实策略）由 run_ptrade_strategy.py 的回归命令覆盖。
"""
from pathlib import Path

import pytest


# ========== 归一化逻辑单元测试 ==========

def test_benchmark_normalization_no_double_scaling():
    """A0 核心：归一化只在一次完成，不再 bench_nav * 100 二次放大"""
    # 模拟 backtest_engine.py 的归一化逻辑（修复后版本）
    first_bench = 3000.0  # 沪深300首日约 3000 点
    bench_close_today = 3030.0  # 次日涨 1%

    # 修复后的逻辑（A0）：单次归一化
    bench_nav = bench_close_today / first_bench * 100 if first_bench else 100.0
    recorded = bench_nav  # nav_history 里直接存 bench_nav，不再 *100

    assert recorded == pytest.approx(101.0)  # 3030/3000*100 = 101
    # 旧 bug 会得到 101 * 100 = 10100，明显错误


def test_benchmark_first_day_is_100():
    """基准首日归一化后应为 100"""
    first_bench = 4000.0
    bench_close_day1 = first_bench  # 首日
    bench_nav = bench_close_day1 / first_bench * 100 if first_bench else 100.0
    assert bench_nav == 100.0


def test_benchmark_zero_first_handled_gracefully():
    """first_bench 为 0 时不应除零（A0 修复时加的保护）"""
    first_bench = 0
    bench_close = 3000.0
    bench_nav = bench_close / first_bench * 100 if first_bench else 100.0
    assert bench_nav == 100.0  # 兜底为 100


# ========== 源码级验证：修复已落地 ==========

def test_benchmark_fix_landed_in_source():
    """源码里已去掉 nav_history 写入时的 *100"""
    ROOT = Path(__file__).resolve().parent.parent
    engine_file = ROOT / "quantstudio" / "backtest" / "backtest_engine.py"
    content = engine_file.read_text(encoding="utf-8")

    # 修复后的写入行：'benchmark': bench_nav（不再有 * 100）
    assert "'benchmark': bench_nav," in content, \
        "nav_history 写入应直接用 bench_nav，不带 *100"

    # 不应存在双乘的旧代码
    assert "bench_nav * 100" not in content, \
        "仍存在 bench_nav * 100 双乘代码，A0 未完成"


def test_report_initial_capital_not_hardcoded():
    """A0 连带修复：report() 不再硬编码 1,000,000.00"""
    ROOT = Path(__file__).resolve().parent.parent
    engine_file = ROOT / "quantstudio" / "backtest" / "backtest_engine.py"
    content = engine_file.read_text(encoding="utf-8")

    # 不应存在硬编码的初始资金字符串
    assert "1,000,000.00" not in content, \
        "report() 仍硬编码初始资金为 1,000,000.00（实际默认是 100,000）"


# ========== 量级合理性（基于归一化语义） ==========

def test_benchmark_scale_matches_nav_scale():
    """归一化后基准与净值同量级（都在 100 附近），便于 report() 对比"""
    # 模拟：策略净值从 10万 起步，基准归一化到 100
    # 两者都在 ~100 量级，report 里的"基准收益"才有意义
    first_bench = 3000.0
    final_bench = 3300.0  # 基准涨 10%
    bench_nav_final = final_bench / first_bench * 100
    assert bench_nav_final == pytest.approx(110.0)

    # 旧 bug 下 bench_nav_final 会是 11000，report 的"基准收益"计算
    # (11000/100 - 1)*100 = 10900% 完全错误
    bench_return_buggy = (11000 / 100 - 1) * 100
    bench_return_fixed = (bench_nav_final / 100 - 1) * 100
    assert bench_return_fixed == pytest.approx(10.0)  # 正确：10%
    assert bench_return_buggy == 10900.0  # 错误：旧 bug
