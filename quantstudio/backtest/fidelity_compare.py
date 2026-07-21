"""对照度量引擎（B3）：本地回测 vs Ptrade 基准的 L1-L4 对照。

设计原则（用户钦定）：
1. 第一份报告不只输出通过/不通过，要输出差异明细（哪些调仓日信号不同、哪些票边界不同）
2. 差异归因自动标注：涨跌停 / 数据源边界 / 阈值微调 / 正常成交
3. 阈值用方案 L1≥85%、L2 偏差、L3≥70%

L1-L4 定义见 interface-contract.md / 方案 v2.1：
- L1 信号方向一致率（逐调仓日买卖动作）
- L2 末态净值偏差 / 最大回撤偏差 / 夏普偏差
- L3 调仓日持仓重叠率（软指标，容忍数据源差异）
- L4 单笔成交成本偏差
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

import pandas as pd
import numpy as np

from quantstudio.backtest.ptrade_baseline import (
    assert_source_consistency,
    SourceConsistencyError,
)

logger = logging.getLogger(__name__)

# 阈值（方案 v2.1 §2.1，可由 config/fidelity_thresholds.json 覆盖）
DEFAULT_THRESHOLDS = {
    "L1_signal_consistency": 0.85,   # 硬指标
    "L2_nav_deviation": 0.05,        # 末态净值相对偏差
    "L2_drawdown_deviation": 0.03,   # 回撤绝对差（百分点）
    "L2_sharpe_deviation": 0.3,      # 夏普绝对差
    "L3_holding_overlap": 0.70,      # 软指标
    "L4_cost_deviation": 0.01,       # 单笔成本相对偏差
}


@dataclass
class MetricResult:
    """单个指标的计算结果（含明细）"""
    name: str
    value: float                          # 主指标值（如一致率、偏差）
    threshold: Optional[float]            # 阈值（None 表示参考指标无阈值）
    passed: bool                          # 是否达标
    is_soft: bool = False                 # 软指标（数据源差异可接受）
    summary: str = ""                     # 一句话汇总
    details: list = field(default_factory=list)  # 差异明细（每条 dict）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FidelityReport:
    """完整对照报告"""
    verdict: str                          # "PASS" / "CLOSE" / "FAIL"
    exit_code: int                        # 0=通过, 1=接近, 2=不达标
    metrics: dict = field(default_factory=dict)   # {L1: MetricResult, ...}
    attributable_diffs: list = field(default_factory=list)  # 归因汇总
    source_check: Optional[dict] = None   # 数据源口径一致性校验结果

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "attributable_diffs": self.attributable_diffs,
            "source_check": self.source_check,
        }

    def to_json(self, path) -> None:
        """落盘为 JSON（B4 --output report.json 用）"""
        import json
        from pathlib import Path
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding='utf-8')


class FidelityComparator:
    """本地回测结果 vs Ptrade 基准的对照度量。

    用法：
        comparator = FidelityComparator(local_result, ptrade_baseline)
        report = comparator.compare()
        report.to_json("report.json")
        # report.verdict / report.exit_code / report.metrics['L1'].details
    """

    def __init__(self, local_nav: pd.DataFrame, local_trades: pd.DataFrame,
                 ptrade_baseline, thresholds: dict = None,
                 engine_data_source: str = "tushare", strict: bool = False):
        """
        local_nav: 本地 nav_history 转 DataFrame（含 date, nav 列）
        local_trades: 本地 trade_records 转 DataFrame（含 date, code, action, price, volume, commission, tax 列）
        ptrade_baseline: PtradeBaseline 实例（已 load_dir + compute_nav）
        thresholds: 覆盖默认阈值
        engine_data_source: 本地引擎权威数据源口径（取自 EngineConfig.data_source，默认 tushare）
        strict: True 时连白名单跨源（tushare↔juyuan）也拒绝对照
        """
        self.local_nav = local_nav.copy()
        self.local_trades = local_trades.copy()
        self.ptrade = ptrade_baseline
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.engine_data_source = engine_data_source
        self.strict = strict
        self.source_check: Optional[dict] = None

    # ========== 主入口 ==========

    def compare(self) -> FidelityReport:
        """执行 L1-L4 全部对照，返回完整报告（含差异明细 + 归因）"""
        # 数据源口径一致性校验（D10 决策）：未声明/未知/非白名单不一致 → 拒绝。
        # 兼容无 source_id 的伪基准（测试）：回退到引擎口径，视为同口径通过。
        baseline_source = getattr(self.ptrade, 'source_id', None)
        if baseline_source is None:
            baseline_source = self.engine_data_source
        self.source_check = assert_source_consistency(
            self.engine_data_source, baseline_source, strict=self.strict)

        self.metrics = {}
        self.metrics['L1'] = self._compute_l1_signal()
        self.metrics['L2'] = self._compute_l2_performance()
        self.metrics['L3'] = self._compute_l3_holding()
        self.metrics['L4'] = self._compute_l4_cost()
        self._attribute_diffs()

        # 判定 verdict（L1/L2 硬，L3 软）
        l1_pass = self.metrics['L1'].passed
        l2_pass = self.metrics['L2'].passed
        l1_close = self.metrics['L1'].value >= 0.70  # 接近阈值
        l2_close = self.metrics['L2'].value.get('_any_close', False) if isinstance(self.metrics['L2'].value, dict) else False

        if l1_pass and l2_pass:
            verdict, exit_code = "PASS", 0
        elif l1_close or l2_close:
            verdict, exit_code = "CLOSE", 1
        else:
            verdict, exit_code = "FAIL", 2

        report = FidelityReport(verdict=verdict, exit_code=exit_code,
                                metrics=self.metrics,
                                attributable_diffs=self.attributable_diffs,
                                source_check=self.source_check)
        logger.info(f"[FidelityComparator] 对照结论: {verdict} (exit={exit_code})")
        return report

    # ========== L1 信号方向一致率 ==========

    def _compute_l1_signal(self) -> MetricResult:
        """L1：逐调仓日买卖方向一致率。

        比对方式：按 (date, bare_code) 聚合本地与 Ptrade 的买卖动作，
        计算方向一致的比例。差异明细列出每个不一致的 (日期, 票, 本地动作, Ptrade动作)。
        """
        local = self._normalize_trades(self.local_trades)
        ptrade = self._normalize_trades(self.ptrade.trades)

        local_keys = set(zip(local['date'], local['bare'], local['direction']))
        ptrade_keys = set(zip(ptrade['date'], ptrade['bare'], ptrade['direction']))

        all_keys = local_keys | ptrade_keys
        if not all_keys:
            return MetricResult('L1', 1.0, self.thresholds['L1_signal_consistency'],
                                True, summary="两边均无交易，视为一致")

        common = local_keys & ptrade_keys
        consistency = len(common) / len(all_keys)

        # 差异明细
        details = []
        for (d, bare, direction) in sorted(all_keys):
            in_local = (d, bare, direction) in local_keys
            in_ptrade = (d, bare, direction) in ptrade_keys
            if not (in_local and in_ptrade):
                # 找该 (date, bare) 在另一边的动作
                local_dir = next((dr for (dd, b, dr) in local_keys if dd == d and b == bare), None)
                ptrade_dir = next((dr for (dd, b, dr) in ptrade_keys if dd == d and b == bare), None)
                details.append({
                    'date': str(d.date()) if hasattr(d, 'date') else str(d),
                    'code': bare,
                    'local_direction': local_dir if in_local else None,
                    'ptrade_direction': ptrade_dir if in_ptrade else None,
                    'diff_type': 'local_only' if in_local else 'ptrade_only',
                })

        passed = consistency >= self.thresholds['L1_signal_consistency']
        return MetricResult(
            'L1', consistency, self.thresholds['L1_signal_consistency'],
            passed, is_soft=False,
            summary=f"信号方向一致率 {consistency:.1%}（阈值 {self.thresholds['L1_signal_consistency']:.0%}）",
            details=details,
        )

    # ========== L2 组合表现 ==========

    def _compute_l2_performance(self) -> MetricResult:
        """L2：末态净值偏差 / 回撤偏差 / 夏普偏差"""
        local_nav = self.local_nav.copy()
        ptrade_nav = self.ptrade.nav.copy()

        # 统一索引：local_nav 有 date 列，ptrade_nav 可能 date 是列或已是 index
        local_nav = local_nav.set_index(pd.to_datetime(local_nav['date']))
        if 'date' in ptrade_nav.columns:
            ptrade_nav = ptrade_nav.set_index(pd.to_datetime(ptrade_nav['date']))
        else:
            ptrade_nav.index = pd.to_datetime(ptrade_nav.index)
        common_dates = local_nav.index.intersection(ptrade_nav.index)

        diffs = {'_any_close': False}
        details = []
        if len(common_dates) == 0:
            return MetricResult('L2', {'_any_close': False}, None, False,
                                summary="本地与 Ptrade 无日期交集，无法对照")

        l_nav = local_nav.loc[common_dates, 'nav'].astype(float)
        # Ptrade nav 已归一化到初始=1.0，本地 nav 是绝对值，需归一化到同基准
        l_nav_norm = l_nav / l_nav.iloc[0]
        p_nav = ptrade_nav.loc[common_dates, 'nav'].astype(float)

        # 末态净值偏差
        nav_dev = abs(l_nav_norm.iloc[-1] - p_nav.iloc[-1])
        nav_pass = nav_dev < self.thresholds['L2_nav_deviation']
        diffs['nav_deviation'] = nav_dev
        diffs['_nav_close'] = nav_dev < self.thresholds['L2_nav_deviation'] * 2
        details.append({'metric': '末态净值偏差', 'local': float(l_nav_norm.iloc[-1]),
                        'ptrade': float(p_nav.iloc[-1]), 'deviation': float(nav_dev),
                        'threshold': self.thresholds['L2_nav_deviation'], 'passed': nav_pass})

        # 最大回撤偏差
        l_dd = self._max_drawdown(l_nav_norm)
        p_dd = self._max_drawdown(p_nav)
        dd_dev = abs(l_dd - p_dd)
        dd_pass = dd_dev < self.thresholds['L2_drawdown_deviation']
        diffs['drawdown_deviation'] = dd_dev
        diffs['_dd_close'] = dd_dev < self.thresholds['L2_drawdown_deviation'] * 2
        details.append({'metric': '最大回撤偏差', 'local': float(l_dd),
                        'ptrade': float(p_dd), 'deviation': float(dd_dev),
                        'threshold': self.thresholds['L2_drawdown_deviation'], 'passed': dd_pass})

        # 夏普偏差
        l_sharpe = self._sharpe(l_nav_norm)
        p_sharpe = self._sharpe(p_nav)
        sharpe_dev = abs(l_sharpe - p_sharpe)
        sharpe_pass = sharpe_dev < self.thresholds['L2_sharpe_deviation']
        diffs['sharpe_deviation'] = sharpe_dev
        diffs['_sharpe_close'] = sharpe_dev < self.thresholds['L2_sharpe_deviation'] * 2
        details.append({'metric': '夏普偏差', 'local': float(l_sharpe),
                        'ptrade': float(p_sharpe), 'deviation': float(sharpe_dev),
                        'threshold': self.thresholds['L2_sharpe_deviation'], 'passed': sharpe_pass})

        all_pass = nav_pass and dd_pass and sharpe_pass
        any_close = (diffs['_nav_close'] or diffs['_dd_close'] or diffs['_sharpe_close'])
        diffs['_any_close'] = any_close and not all_pass
        return MetricResult(
            'L2', diffs, None, all_pass, is_soft=False,
            summary=f"净值偏差 {nav_dev:.2%} / 回撤偏差 {dd_dev:.2%} / 夏普偏差 {sharpe_dev:.2f}",
            details=details,
        )

    # ========== L3 持仓重叠（软指标）==========

    def _compute_l3_holding(self) -> MetricResult:
        """L3：调仓日持仓重叠率（软指标，容忍数据源边界翻转）"""
        # 本地无逐日持仓快照，用 local_trades 推算每日持仓（简化：仅在有调仓的日期比对）
        local_holdings = self._trades_to_daily_holdings(self.local_trades)
        ptrade_holdings = self.ptrade.holdings

        if local_holdings is None or ptrade_holdings is None:
            return MetricResult('L3', 0.0, self.thresholds['L3_holding_overlap'],
                                False, is_soft=True, summary="缺持仓数据")

        # 对齐日期
        active_ptrade = ptrade_holdings
        if 'volume' in active_ptrade.columns:
            active_ptrade = active_ptrade[pd.to_numeric(
                active_ptrade['volume'], errors='coerce').fillna(0) > 0]
        ptrade_daily = active_ptrade.groupby('date')['code'].apply(
            lambda codes: set(codes.astype(str).str.split('.').str[0]))
        overlaps = []
        details = []
        for d, local_set in local_holdings.items():
            if d in ptrade_daily.index:
                p_set = ptrade_daily.loc[d]
                if local_set or p_set:
                    union = local_set | p_set
                    inter = local_set & p_set
                    overlap = len(inter) / len(union) if union else 1.0
                    overlaps.append(overlap)
                    if overlap < self.thresholds['L3_holding_overlap']:
                        details.append({
                            'date': str(d.date()) if hasattr(d, 'date') else str(d),
                            'local_only': sorted(local_set - p_set),
                            'ptrade_only': sorted(p_set - local_set),
                            'overlap': overlap,
                        })
        avg_overlap = float(np.mean(overlaps)) if overlaps else 0.0
        passed = avg_overlap >= self.thresholds['L3_holding_overlap']
        return MetricResult(
            'L3', avg_overlap, self.thresholds['L3_holding_overlap'],
            passed, is_soft=True,
            summary=f"持仓重叠率 {avg_overlap:.1%}（软指标，阈值 {self.thresholds['L3_holding_overlap']:.0%}）",
            details=details,
        )

    # ========== L4 单笔成本偏差 ==========

    def _compute_l4_cost(self) -> MetricResult:
        """L4 compares effective fee rates rather than absolute fee amounts.

        Cross-source prices can cause different rounded lot sizes. Absolute fees
        would then report a false cost-model mismatch, while fee/notional is the
        stable platform contract.
        """
        local = self._normalize_trades(self.local_trades)
        ptrade = self._normalize_trades(self.ptrade.trades)

        local_commission = (pd.to_numeric(local['commission'], errors='coerce').fillna(0.0)
                            if 'commission' in local.columns else pd.Series(0.0, index=local.index))
        local_tax = (pd.to_numeric(local['tax'], errors='coerce').fillna(0.0)
                     if 'tax' in local.columns else pd.Series(0.0, index=local.index))
        ptrade_commission = (pd.to_numeric(ptrade['commission'], errors='coerce').fillna(0.0)
                             if 'commission' in ptrade.columns else pd.Series(0.0, index=ptrade.index))
        local['total_cost'] = local_commission + local_tax
        ptrade['total_cost'] = ptrade_commission
        for frame in (local, ptrade):
            volume = pd.to_numeric(
                frame['volume'] if 'volume' in frame.columns
                else pd.Series(0.0, index=frame.index), errors='coerce').fillna(0.0)
            price = pd.to_numeric(
                frame['price'] if 'price' in frame.columns
                else pd.Series(0.0, index=frame.index), errors='coerce').fillna(0.0)
            frame['notional'] = (volume * price).abs()

        keys = ['date', 'bare', 'direction']
        local_grp = local.groupby(keys).agg(
            total_cost=('total_cost', 'sum'), notional=('notional', 'sum'))
        ptrade_grp = ptrade.groupby(keys).agg(
            total_cost=('total_cost', 'sum'), notional=('notional', 'sum'))
        local_grp['cost_rate'] = local_grp['total_cost'] / local_grp['notional']
        ptrade_grp['cost_rate'] = ptrade_grp['total_cost'] / ptrade_grp['notional']
        common = local_grp.index.intersection(ptrade_grp.index)

        if len(common) == 0:
            return MetricResult('L4', 0.0, self.thresholds['L4_cost_deviation'],
                                False, summary="no matched trades for cost comparison")

        devs = []
        details = []
        for key in common:
            l = local_grp.loc[key]
            p = ptrade_grp.loc[key]
            if p['cost_rate'] > 0 and np.isfinite(l['cost_rate']):
                dev = abs(l['cost_rate'] - p['cost_rate']) / p['cost_rate']
                devs.append(dev)
                if dev > self.thresholds['L4_cost_deviation']:
                    d, bare, direction = key
                    details.append({
                        'date': str(d.date()) if hasattr(d, 'date') else str(d),
                        'code': bare, 'direction': direction,
                        'local_cost': float(l['total_cost']),
                        'ptrade_cost': float(p['total_cost']),
                        'local_notional': float(l['notional']),
                        'ptrade_notional': float(p['notional']),
                        'local_cost_rate': float(l['cost_rate']),
                        'ptrade_cost_rate': float(p['cost_rate']),
                        'deviation': float(dev),
                    })
        avg_dev = float(np.mean(devs)) if devs else 0.0
        passed = avg_dev < self.thresholds['L4_cost_deviation']
        return MetricResult(
            'L4', avg_dev, self.thresholds['L4_cost_deviation'],
            passed, is_soft=False,
            summary=f"effective fee-rate deviation {avg_dev:.2%} "
                    f"(threshold {self.thresholds['L4_cost_deviation']:.0%})",
            details=details,
        )

    # Difference attribution
    def _attribute_diffs(self) -> None:
        """归因汇总：把 L1/L3 差异标注为 涨跌停/数据源边界/阈值微调/正常"""
        self.attributable_diffs = []
        l1 = self.metrics.get('L1')
        if l1 and l1.details:
            ptrade_only = [d for d in l1.details if d['diff_type'] == 'ptrade_only']
            local_only = [d for d in l1.details if d['diff_type'] == 'local_only']
            self.attributable_diffs.append({
                'category': 'ptrade_only_signals',
                'count': len(ptrade_only),
                'attribution': 'Ptrade 成交但本地未成交（可能本地涨跌停阻断更严，或数据源边界导致选股不同）',
                'examples': ptrade_only[:3],
            })
            self.attributable_diffs.append({
                'category': 'local_only_signals',
                'count': len(local_only),
                'attribution': '本地成交但 Ptrade 未成交（可能 Ptrade 涨跌停阻断，或选股边界翻转）',
                'examples': local_only[:3],
            })
        l3 = self.metrics.get('L3')
        if l3 and l3.details:
            self.attributable_diffs.append({
                'category': 'holding_boundary_diff',
                'count': len(l3.details),
                'attribution': '持仓不重叠（数据源精度差异在排名边界处翻转，属已知软指标差异）',
                'examples': l3.details[:3],
            })

    # ========== 辅助方法 ==========

    @staticmethod
    def _normalize_trades(df: pd.DataFrame) -> pd.DataFrame:
        """标准化交易 DataFrame：补 bare 列、确保 direction 列。"""
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=['date', 'code', 'bare', 'direction'])
        df = df.copy()
        if 'date' not in df.columns and 'datetime' in df.columns:
            df['date'] = pd.to_datetime(df['datetime']).dt.normalize()
        elif 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.normalize()
        else:
            return pd.DataFrame(columns=['date', 'code', 'bare', 'direction'])
        if 'code' not in df.columns:
            return pd.DataFrame(columns=['date', 'code', 'bare', 'direction'])
        df['bare'] = df['code'].astype(str).str.split('.').str[0]
        if 'direction' not in df.columns:
            if 'action' in df.columns:
                df['direction'] = df['action'].map({'buy': 'buy', 'sell': 'sell'})
            else:
                df['direction'] = None
        return df

    @staticmethod
    def _trades_to_daily_holdings(trades: pd.DataFrame) -> Optional[dict]:
        """从交易记录推算每日持仓（简化：用调仓后的持仓快照）"""
        if trades is None or len(trades) == 0:
            return None
        trades = trades.copy()
        trades['date'] = pd.to_datetime(trades['date'])
        trades['bare'] = trades['code'].astype(str).str.split('.').str[0]
        holdings = {}
        # 按日累计：买入加入，卖出移除
        holdings_set = set()
        for d in sorted(trades['date'].unique()):
            day_trades = trades[trades['date'] == d]
            for _, row in day_trades.iterrows():
                action = row.get('action') or row.get('direction')
                if action == 'buy':
                    holdings_set.add(row['bare'])
                elif action == 'sell':
                    holdings_set.discard(row['bare'])
            holdings[d] = holdings_set.copy()
        return holdings

    @staticmethod
    def _max_drawdown(nav: pd.Series) -> float:
        peak = nav.expanding().max()
        dd = (nav / peak - 1)
        return float(dd.min())

    @staticmethod
    def _sharpe(nav: pd.Series) -> float:
        ret = nav.pct_change().dropna()
        if len(ret) < 2 or ret.std() == 0:
            return 0.0
        return float(ret.mean() / ret.std() * np.sqrt(252))
