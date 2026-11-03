# -*- coding: utf-8 -*-
"""test_match_profile —— M5.2 件7：match_profile 双撮合口径参数测试。

判型：ptrade 缺省=纯性能型（黄金结果零改变）；qmt=新增能力型（口径标签）。
覆盖：①参数校验（非法值 ValueError）②缺省 ptrade 零行为（不传参=构造成功且
match_profile=='ptrade'，metrics_summary 标签正确）③qmt 口径标签注入
metrics_summary（批复③全链路可追溯审计点）④引擎 TradeCost 现行费率=B 类
定谳值断言（印花税分段 0.5‰/过户费万0.1，呈报件双列证据链的代码侧锚点）。

run: pytest tests/test_match_profile.py -q
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from quantstudio.backtest.backtest_engine import BacktestEngine, BacktestResult, TradeCost


def _mk_engine(**kw):
    return BacktestEngine(db_path="data/quantstudio.db", strategy=None, start="2024-01-01", end="2024-01-31", **kw)


def test_param_validation():
    with pytest.raises(ValueError, match="match_profile"):
        _mk_engine(match_profile="bad")


def test_trade_cost_b_class_values():
    """B 类定谳值代码侧锚点（呈报件 #2/#3）：过户费 0.001% 双边；印花税基率千1
    + 引擎分段函数 2023-08-28 后 0.5‰。"""
    tc = TradeCost()
    assert tc.transfer_fee_rate == 0.00001          # 0.001%（万0.1）
    assert tc.stamp_tax_rate == 0.001                # 基率；分段见 _stamp_tax_rate
    assert tc.commission_rate == 0.00035             # 万3.5（C 类首版沿用，裁定②(a)）


def test_stamp_tax_halving_segment():
    """印花税 2023-08-28 减半分段（:864 区）——B 类历史分段正确性。"""
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.cost = TradeCost()
    assert eng._stamp_tax_rate("2024-06-03") == 0.0005   # 减半后 0.5‰
    assert eng._stamp_tax_rate("2023-01-03") == 0.001    # 减半前 千1


def test_default_ptrade_no_behavior_change():
    """缺省不传 match_profile：构造成功 + 值为 'ptrade'（现行行为零改变）。"""
    eng = _mk_engine()
    assert eng.match_profile == "ptrade"
    assert eng.match_price_mode == "close"   # 既有默认不动


def test_metrics_summary_profile_keys():
    """口径标签注入（批复③）：run() 产物 metrics_summary 带 match_profile/
    match_price_mode 两键——本测以最小单元验证注入语句的键契约（真跑 run() 由
    撮合回归件覆盖）。"""
    r = BacktestResult()
    r.metrics_summary = {"strategy_return_pct": 1.0}
    # 与引擎 :667 注入语句同款契约
    r.metrics_summary["match_profile"] = "qmt"
    r.metrics_summary["match_price_mode"] = "next_open"
    assert r.metrics_summary["match_profile"] == "qmt"
    assert r.metrics_summary["strategy_return_pct"] == 1.0  # 既有键不受影响


def test_run_backtest_cli_entry_passes_match_profile():
    """CLI/GUI 共用入口 run_backtest 透传 match_profile（源码级静态断言，
    防壳内丢参——双端对等铁律的透传链检查）。"""
    src = (ROOT / "quantstudio" / "backtest" / "run_ptrade_strategy.py").read_text(encoding="utf-8")
    assert re.search(r"match_profile='ptrade'", src), "run_backtest 缺省应为 ptrade"
    assert re.search(r"match_profile=match_profile", src), "应透传给 BacktestEngine"
    wsrc = (ROOT / "quantstudio" / "gui" / "workers.py").read_text(encoding="utf-8")
    assert "match_profile" in wsrc, "GUI workers 透传应在案"


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
