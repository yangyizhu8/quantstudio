# -*- coding: utf-8 -*-
"""2026-11-04 回归：GUI worker 传 None 覆盖默认等待上限 ⇒ float(None) TypeError。

现象（用户实测）：进程常驻开启时点回测，弹「回测错误」：
  TypeError: float() argument must be a string or a real number, not 'NoneType'
  ... run_backtest(...) → CollectorStopContext(...) → ...
根因：`944e99b` 在 workers.py 加了
  coexistence_stop_timeout=self.params.get('coexistence_stop_timeout')
键不存在时 `.get()` 返回 None，**显式传 None 覆盖签名默认 60** ⇒ float(None)。
"""
from __future__ import annotations

import inspect

from quantstudio.backtest.run_ptrade_strategy import (
    _norm_coexist_timeouts,
)
from quantstudio.gui import workers


def test_norm_none_falls_back_to_defaults():
    """显式 None 必须落回默认（防任何调用方把 None 击穿到 float()）。"""
    assert _norm_coexist_timeouts() == (60, 30)
    assert _norm_coexist_timeouts(None, None) == (60, 30)


def test_norm_values_pass_through():
    assert _norm_coexist_timeouts(120, 15) == (120.0, 15.0)
    assert _norm_coexist_timeouts("90", None) == (90.0, 30)


def test_run_backtest_normalizes_before_ctx():
    src = inspect.getsource(
        __import__("quantstudio.backtest.run_ptrade_strategy", fromlist=["x"]).run_backtest)
    assert "_norm_coexist_timeouts" in src, "入口必须先归一再构造 CollectorStopContext"


def test_worker_never_passes_none_explicitly():
    """worker 只在**非 None** 时传共存等待参数（回归根因侧修复）。"""
    src = inspect.getsource(workers.BacktestWorker.run)
    # 不得再出现裸的 params.get(...) 直传形式
    assert "coexistence_stop_timeout=self.params.get" not in src
    assert "coexistence_boundary_timeout=self.params.get" not in src
    # 必须存在条件组装
    assert "_coexist_kw" in src
    assert "if _stop_t is not None" in src
    assert "**_coexist_kw" in src
