"""B3 同步测试：对照度量引擎 fidelity_compare。

验证目标（用户实现要点）：
1. L1-L4 四个指标都能计算，输出数值 + 阈值 + passed
2. 差异明细输出（不只通过/不通过）：L1 列出哪些调仓日信号不同
3. 归因自动标注（ptrade_only / local_only / holding_boundary_diff）
4. verdict 判定：PASS(0) / CLOSE(1) / FAIL(2)
5. report.to_json() 落盘
6. L3 是软指标（数据源差异可接受）
"""
import json
import tempfile
from pathlib import Path

import pytest
import pandas as pd
import numpy as np


# ========== 测试数据构造 ==========

def _make_local_nav(dates, start_nav=100000):
    """构造本地净值 DataFrame"""
    np.random.seed(42)
    navs = [start_nav]
    for _ in range(len(dates) - 1):
        navs.append(navs[-1] * (1 + np.random.randn() * 0.01))
    return pd.DataFrame({'date': dates, 'nav': navs})


def _make_local_trades(rows):
    """构造本地交易 DataFrame。rows: [(date, code, action, volume, price, commission, tax)]"""
    return pd.DataFrame(rows, columns=['date', 'code', 'action', 'volume', 'price', 'commission', 'tax'])


class _FakePtradeBaseline:
    """伪造 PtradeBaseline（避免依赖真实样本）"""
    def __init__(self, trades_df, nav_df, holdings_df=None):
        self.trades = trades_df
        self._nav = nav_df
        self.holdings = holdings_df

    @property
    def nav(self):
        return self._nav


def _make_ptrade_trades(rows):
    """rows: [(date_str, code, direction, volume, price, commission)]"""
    df = pd.DataFrame(rows, columns=['date', 'code', 'direction', 'volume', 'price', 'commission'])
    df['date'] = pd.to_datetime(df['date'])
    return df


def _make_ptrade_nav(dates, values):
    """构造 Ptrade 归一化净值（初始=1.0）"""
    df = pd.DataFrame({'date': pd.to_datetime(dates), 'nav': values})
    return df.set_index('date')


# ========== L1 信号一致率 ==========

def test_l1_perfect_match_returns_100_percent():
    """L1：本地与 Ptrade 交易完全一致 → 100%"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    dates = ['2026-01-05', '2026-01-06', '2026-01-07']
    local_trades = _make_local_trades([
        ('2026-01-05', '002830.SZ', 'buy', 1000, 19.42, 6.77, 0),
        ('2026-01-14', '002888.SZ', 'sell', 1000, 20.0, 6.8, 10),
    ])
    ptrade_trades = _make_ptrade_trades([
        ('2026-01-05', '002830.SZ', 'buy', 1000, 19.42, 6.77),
        ('2026-01-14', '002888.SZ', 'sell', 1000, 20.0, 6.8),
    ])
    local_nav = _make_local_nav(dates)
    ptrade_nav = _make_ptrade_nav(dates, [1.0, 1.01, 1.02])
    bl = _FakePtradeBaseline(ptrade_trades, ptrade_nav)
    cmp = FidelityComparator(local_nav, local_trades, bl)
    report = cmp.compare()
    assert report.metrics['L1'].value == 1.0
    assert bool(report.metrics['L1'].passed) is True


def test_l1_outputs_diff_details_not_just_pass_fail():
    """B3 核心：L1 输出差异明细（不只通过/不通过）"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    local_trades = _make_local_trades([
        ('2026-01-05', '002830.SZ', 'buy', 1000, 19.42, 6.77, 0),
        ('2026-01-14', '002888.SZ', 'sell', 1000, 20.0, 6.8, 10),
    ])
    # Ptrade 缺少 002830 的买入，多了一只 002999 的买入
    ptrade_trades = _make_ptrade_trades([
        ('2026-01-05', '002999.SZ', 'buy', 1000, 10.0, 5.0),
        ('2026-01-14', '002888.SZ', 'sell', 1000, 20.0, 6.8),
    ])
    local_nav = _make_local_nav(['2026-01-05', '2026-01-14'])
    ptrade_nav = _make_ptrade_nav(['2026-01-05', '2026-01-14'], [1.0, 1.01])
    bl = _FakePtradeBaseline(ptrade_trades, ptrade_nav)
    report = FidelityComparator(local_nav, local_trades, bl).compare()
    l1 = report.metrics['L1']
    assert len(l1.details) > 0  # 有差异明细
    # 明细应含 002830（本地有 Ptrade 无）和 002999（Ptrade 有本地无）
    codes_in_diff = [d['code'] for d in l1.details]
    assert '002830' in codes_in_diff
    assert '002999' in codes_in_diff


def test_l1_below_threshold_fails():
    """L1 一致率低于 85% → 不达标"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    local_trades = _make_local_trades([
        ('2026-01-05', '000001.SZ', 'buy', 100, 10, 5, 0),
        ('2026-01-06', '000002.SZ', 'buy', 100, 10, 5, 0),
    ])
    ptrade_trades = _make_ptrade_trades([
        ('2026-01-05', '000003.SZ', 'buy', 100, 10, 5),
        ('2026-01-06', '000004.SZ', 'buy', 100, 10, 5),
    ])  # 完全不重合
    local_nav = _make_local_nav(['2026-01-05', '2026-01-06'])
    ptrade_nav = _make_ptrade_nav(['2026-01-05', '2026-01-06'], [1.0, 1.0])
    report = FidelityComparator(local_nav, local_trades, _FakePtradeBaseline(ptrade_trades, ptrade_nav)).compare()
    assert not report.metrics['L1'].passed
    assert report.metrics['L1'].value == 0.0


# ========== L2 组合表现 ==========

def test_l2_nav_deviation_within_threshold():
    """L2：净值偏差在阈值内 → 该项达标"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    dates = ['2026-01-05', '2026-01-06', '2026-01-07']
    local_nav = pd.DataFrame({'date': dates, 'nav': [100000, 101000, 102000]})
    # Ptrade 归一化 1.0→1.02（末值一致）
    ptrade_nav = _make_ptrade_nav(dates, [1.0, 1.01, 1.02])
    local_trades = _make_local_trades([])
    report = FidelityComparator(local_nav, local_trades, _FakePtradeBaseline(_make_ptrade_trades([]), ptrade_nav)).compare()
    l2 = report.metrics['L2']
    nav_detail = next(d for d in l2.details if d['metric'] == '末态净值偏差')
    assert nav_detail['passed'] == True


def test_l2_nav_deviation_exceeds_threshold():
    """L2：净值偏差超阈值 → 该项不达标"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    dates = ['2026-01-05', '2026-01-06']
    local_nav = pd.DataFrame({'date': dates, 'nav': [100000, 120000]})  # +20%
    ptrade_nav = _make_ptrade_nav(dates, [1.0, 1.01])  # +1%
    report = FidelityComparator(local_nav, _make_local_trades([]),
                                _FakePtradeBaseline(_make_ptrade_trades([]), ptrade_nav)).compare()
    l2 = report.metrics['L2']
    assert not l2.passed  # numpy bool 兼容（用 not 而非 is False）


# ========== 归因 ==========

def test_attribution_categorizes_diffs():
    """B3 核心：差异归因自动标注（ptrade_only / local_only）"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    local_trades = _make_local_trades([('2026-01-05', '000001.SZ', 'buy', 100, 10, 5, 0)])
    ptrade_trades = _make_ptrade_trades([('2026-01-05', '000002.SZ', 'buy', 100, 10, 5)])
    local_nav = _make_local_nav(['2026-01-05'])
    ptrade_nav = _make_ptrade_nav(['2026-01-05'], [1.0])
    report = FidelityComparator(local_nav, local_trades,
                                _FakePtradeBaseline(ptrade_trades, ptrade_nav)).compare()
    categories = [a['category'] for a in report.attributable_diffs]
    assert 'ptrade_only_signals' in categories
    assert 'local_only_signals' in categories


def test_attribution_examples_included():
    """归因含 examples（前几条差异，便于定位）"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    local_trades = _make_local_trades([('2026-01-05', '000001.SZ', 'buy', 100, 10, 5, 0)])
    ptrade_trades = _make_ptrade_trades([('2026-01-05', '000002.SZ', 'buy', 100, 10, 5)])
    local_nav = _make_local_nav(['2026-01-05'])
    ptrade_nav = _make_ptrade_nav(['2026-01-05'], [1.0])
    report = FidelityComparator(local_nav, local_trades,
                                _FakePtradeBaseline(ptrade_trades, ptrade_nav)).compare()
    for attr in report.attributable_diffs:
        assert 'examples' in attr
        assert 'attribution' in attr  # 人类可读的归因说明


# ========== verdict 与 exit_code ==========

def test_verdict_pass_when_l1_l2_meet_thresholds():
    """全部达标 → PASS, exit 0"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    dates = ['2026-01-05', '2026-01-06']
    local_trades = _make_local_trades([('2026-01-05', '000001.SZ', 'buy', 100, 10, 5, 0)])
    ptrade_trades = _make_ptrade_trades([('2026-01-05', '000001.SZ', 'buy', 100, 10, 5)])
    local_nav = pd.DataFrame({'date': dates, 'nav': [100000, 101000]})
    ptrade_nav = _make_ptrade_nav(dates, [1.0, 1.01])
    report = FidelityComparator(local_nav, local_trades,
                                _FakePtradeBaseline(ptrade_trades, ptrade_nav)).compare()
    assert report.verdict == "PASS"
    assert report.exit_code == 0


def test_verdict_fail_when_l1_far_below():
    """L1 严重低于阈值 → FAIL, exit 2"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    local_trades = _make_local_trades([
        ('2026-01-05', '000001.SZ', 'buy', 100, 10, 5, 0),
        ('2026-01-06', '000002.SZ', 'buy', 100, 10, 5, 0),
    ])
    ptrade_trades = _make_ptrade_trades([
        ('2026-01-05', '000003.SZ', 'buy', 100, 10, 5),
        ('2026-01-06', '000004.SZ', 'buy', 100, 10, 5),
    ])
    local_nav = _make_local_nav(['2026-01-05', '2026-01-06'])
    ptrade_nav = _make_ptrade_nav(['2026-01-05', '2026-01-06'], [1.0, 1.0])
    report = FidelityComparator(local_nav, local_trades,
                                _FakePtradeBaseline(ptrade_trades, ptrade_nav)).compare()
    assert report.verdict == "FAIL"
    assert report.exit_code == 2


# ========== report.to_json ==========

def test_report_to_json_writes_file():
    """report.to_json() 落盘（B4 --output report.json 用）"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    local_trades = _make_local_trades([('2026-01-05', '000001.SZ', 'buy', 100, 10, 5, 0)])
    ptrade_trades = _make_ptrade_trades([('2026-01-05', '000001.SZ', 'buy', 100, 10, 5)])
    local_nav = _make_local_nav(['2026-01-05', '2026-01-06'])
    ptrade_nav = _make_ptrade_nav(['2026-01-05', '2026-01-06'], [1.0, 1.01])
    report = FidelityComparator(local_nav, local_trades,
                                _FakePtradeBaseline(ptrade_trades, ptrade_nav)).compare()
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='w') as f:
        path = f.name
    report.to_json(path)
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    assert 'verdict' in data
    assert 'metrics' in data
    assert 'L1' in data['metrics']
    assert data['metrics']['L1']['value'] == 1.0


# ========== L3 软指标 ==========

def test_l3_is_soft_metric():
    """L3 标记为软指标（is_soft=True）"""
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    local_trades = _make_local_trades([])
    local_nav = _make_local_nav(['2026-01-05'])
    ptrade_nav = _make_ptrade_nav(['2026-01-05'], [1.0])
    report = FidelityComparator(local_nav, local_trades,
                                _FakePtradeBaseline(_make_ptrade_trades([]), ptrade_nav)).compare()
    assert report.metrics['L3'].is_soft is True


def test_l4_compares_fee_rate_when_lot_sizes_differ():
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    local = _make_local_trades([
        ('2026-01-05', '000001.SZ', 'buy', 1000, 10.0, 3.5, 0.0)])
    ptrade = _make_ptrade_trades([
        ('2026-01-05', '000001.SZ', 'buy', 1200, 10.0, 4.2)])
    nav = _make_local_nav(['2026-01-05'])
    report = FidelityComparator(
        nav, local, _FakePtradeBaseline(ptrade, _make_ptrade_nav(['2026-01-05'], [1.0]))
    ).compare()
    assert report.metrics['L4'].passed
    assert report.metrics['L4'].value < 1e-12
