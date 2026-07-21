"""B0/B2 同步测试：Ptrade CSV 导入器解析验证。

基于真实样本（私募工作文件/ptrade_samples/）验证规格表（interface-contract.md 附录 B）。
这是 B0 的收尾：证明规格表正确、导入器能解析真实数据，为 B2 扫清返工风险。

验证目标：
1. 三件套加载（GBK 编码、前缀匹配文件名）
2. 表头严格匹配规格表（任何偏差立即报错，而非静默错误）
3. 字段标准化正确（买/卖→buy/sell、成交量→int、日期解析）
4. 净值反推自洽（末值在合理量级）
5. Log.txt 信号提取与交易详情交叉一致
6. 代码后缀标准化（.SZ/.SH 保留，与本地引擎互通）
"""
from pathlib import Path

import pytest
import pandas as pd

# 样本目录（B0 探样位置，由用户提供）
def _resolve_samples_dir() -> Path:
    import os
    env = os.environ.get("PTRADE_SAMPLES_DIR")
    if env:
        roots = [Path(env)]
    else:
        workspace_parent = Path(__file__).resolve().parents[2]
        roots = sorted(workspace_parent.glob("*/ptrade_samples"))
    candidates = []
    for root in roots:
        if root.exists():
            candidates.extend([root] + [p for p in root.iterdir() if p.is_dir()])
    valid = [candidate for candidate in candidates
             if (candidate / "Log.txt").exists() and len(list(candidate.glob("*.csv"))) >= 2]
    if valid:
        # The canonical small-cap sample is the richest fixture (largest CSV set).
        return max(valid, key=lambda d: sum(p.stat().st_size for p in d.glob("*.csv")))
    return roots[0] if roots else workspace_parent / "ptrade_samples"


SAMPLES_DIR = _resolve_samples_dir()
_TRADES_GLOB = "交易详情*.csv"
_HOLDINGS_GLOB = "持仓明细*.csv"
_HAS_SAMPLE_TRIPLET = (
    next(SAMPLES_DIR.glob(_TRADES_GLOB), None) is not None
    and next(SAMPLES_DIR.glob(_HOLDINGS_GLOB), None) is not None
    and (SAMPLES_DIR / "Log.txt").exists()
)
pytestmark = pytest.mark.skipif(
    not _HAS_SAMPLE_TRIPLET,
    reason=f"Ptrade sample triplet not found: {SAMPLES_DIR}")


# ========== 三件套加载 ==========

def test_load_trades_csv_parses_real_sample():
    """B0 核心：交易详情 CSV 能按规格表解析真实样本"""
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    bl = PtradeBaseline()
    trades_file = next(SAMPLES_DIR.glob('交易详情*.csv'), None)
    assert trades_file is not None, "未找到交易详情 CSV"
    df = bl.load_trades_csv(trades_file)

    # B.2 字段标准化验证
    assert set(['date', 'code', 'direction', 'volume', 'price', 'commission']).issubset(df.columns)
    # 方向只有 buy/sell（B.6 标准化自 买/卖）
    assert set(df['direction'].unique()).issubset({'buy', 'sell'})
    # 成交量是整数（B.6 从 float 转 int）
    assert df['volume'].dtype in ('int64', 'int32')
    assert (df['volume'] > 0).all()
    # 日期范围符合规格（B.7）
    assert df['date'].min() >= pd.Timestamp('2026-01-01')
    assert df['date'].max() <= pd.Timestamp('2026-04-30')


def test_load_holdings_csv_parses_real_sample():
    """B0 核心：持仓明细 CSV 能按规格表解析真实样本"""
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    bl = PtradeBaseline()
    holdings_file = next(SAMPLES_DIR.glob('持仓明细*.csv'), None)
    assert holdings_file is not None
    df = bl.load_holdings_csv(holdings_file)

    # B.3 字段标准化验证
    assert set(['date', 'code', 'volume', 'last_price', 'avg_cost', 'market_value', 'cum_pnl']).issubset(df.columns)
    assert df['volume'].dtype in ('int64', 'int32')
    # 市值 = 仓位 × 最新价（自洽性检查，容许 0.01 元舍入误差）
    recon = df['volume'] * df['last_price']
    assert ((recon - df['market_value']).abs() < 0.01).all(), "市值列与 仓位×最新价 不一致"
    # 小市值策略每日 5 只持仓（B.7 样本统计）
    daily_counts = df.groupby('date').size()
    assert daily_counts.mode().iloc[0] == 5, f"主力持仓数应为 5，实际 {daily_counts.mode().iloc[0]}"


def test_load_log_txt_parses_real_sample():
    """B0：Log.txt UTF-8 段能解析，UTF-16 混乱段跳过"""
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    bl = PtradeBaseline()
    log_file = SAMPLES_DIR / 'Log.txt'
    if not log_file.exists():
        pytest.skip("Log.txt 不存在")
    lines = bl.load_log_txt(log_file)
    assert len(lines) > 0
    # 有效行都含 ' - INFO - '
    assert all(' - INFO - ' in line for line in lines)


# ========== 表头严格匹配（规格表防漂移）==========

def test_trades_header_strict_match():
    """B.2 表头必须严格匹配 8 列，任何字段重命名/增减立即报错"""
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    bl = PtradeBaseline()
    trades_file = next(SAMPLES_DIR.glob('交易详情*.csv'), None)
    # 加载成功即说明表头匹配（load_trades_csv 内部会校验）
    bl.load_trades_csv(trades_file)  # 不抛异常即通过


def test_holdings_header_strict_match():
    """B.3 表头必须严格匹配 9 列"""
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    bl = PtradeBaseline()
    holdings_file = next(SAMPLES_DIR.glob('持仓明细*.csv'), None)
    bl.load_holdings_csv(holdings_file)  # 不抛异常即通过


# ========== 净值反推自洽 ==========

def test_nav_reverse_calculation_self_consistent():
    """B.5 净值反推：首日接近初始资金，末值在合理量级"""
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    bl = PtradeBaseline()
    bl.load_dir(SAMPLES_DIR)
    nav = bl.compute_nav()

    assert len(nav) > 0
    # 首日总资产应接近 10 万（扣手续费后略低）
    first_total = nav['total'].iloc[0]
    assert 99_000 < first_total < 100_500, f"首日总资产异常: {first_total}"
    # 末值在合理区间（小市值策略不会爆仓也不会翻倍）
    last_nav = nav['nav'].iloc[-1]
    assert 0.5 < last_nav < 2.0, f"末态净值异常: {last_nav}"


# ========== Log.txt 与交易详情交叉一致 ==========

def test_log_signals_match_trades_csv():
    """B.4 验证：Log.txt 订单意图与交易详情 CSV 实际成交的关系。

    关键语义（B0 探样发现）：
    - Log 记录"下单意图"（策略想做什么），CSV 记录"实际成交"
    - Log ⊇ CSV：CSV 的每笔成交在 Log 中都有对应意图（csv_only 应为 0）
    - Log 有但 CSV 没有 = 未成交/废单（涨停买不进、跌停卖不出）+ CSV 区间外的意图
    - 这个差异本身就是 L1 对照的宝贵数据（Ptrade 平台的订单失败率）

    注意：Log 用 .XSHE/.XSHG 后缀，CSV 用 .SZ/.SH，需归一化到裸码。
    """
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    bl = PtradeBaseline()
    bl.load_dir(SAMPLES_DIR)

    log_signals = bl.extract_log_signals()
    if len(log_signals) == 0:
        pytest.skip("Log.txt 未提取到订单信号")

    # 归一化到裸码（后缀互通）
    log_signals = log_signals.assign(bare=log_signals['code'].str.split('.').str[0])
    csv_trades = bl.trades.assign(bare=bl.trades['code'].str.split('.').str[0])

    # 核心断言：CSV 每笔成交在 Log 中都有意图（csv_only 应为 0 或极少）
    csv_keys = set(zip(csv_trades['date'], csv_trades['bare'], csv_trades['direction']))
    log_keys = set(zip(log_signals['date'], log_signals['bare'], log_signals['direction']))
    csv_not_in_log = csv_keys - log_keys
    # 允许少量异常（数据导出时间差），但不应超过 5%
    csv_coverage = 1 - len(csv_not_in_log) / len(csv_keys)
    assert csv_coverage > 0.95, \
        f"CSV 成交在 Log 中找不到意图的比例过高: {1-csv_coverage:.1%}（{len(csv_not_in_log)}/{len(csv_keys)}）"

    # 辅助断言：Log 意图数 >= CSV 成交数（Log ⊇ CSV）
    assert len(log_signals) >= len(csv_trades) * 0.9, \
        f"Log 意图数({len(log_signals)}) 不应远少于 CSV 成交数({len(csv_trades)})"


# ========== 代码后缀标准化 ==========

def test_code_suffix_interoperable():
    """B.6 代码后缀：Ptrade 导出 .SZ/.SH，本地引擎也接受（互通）"""
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    bl = PtradeBaseline()
    bl.load_dir(SAMPLES_DIR)

    # 交易详情和持仓明细的代码都是 .SZ/.SH 格式
    for code in bl.trades['code'].unique():
        assert code.endswith('.SZ') or code.endswith('.SH'), f"非预期代码格式: {code}"
    for code in bl.holdings['code'].unique():
        assert code.endswith('.SZ') or code.endswith('.SH'), f"非预期代码格式: {code}"


# ========== load_dir 自动加载 ==========

def test_load_dir_finds_all_three_files():
    """B.1 load_dir 按前缀匹配自动找到三件套"""
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    bl = PtradeBaseline()
    bl.load_dir(SAMPLES_DIR)
    assert bl.trades is not None
    assert bl.holdings is not None
