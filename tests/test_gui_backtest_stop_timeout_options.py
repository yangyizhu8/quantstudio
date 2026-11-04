# -*- coding: utf-8 -*-
"""2026-11-04 缺陷回归：GUI 回测遇「采集停止超时」必须给出三选项，而非裸 traceback。

缺陷链（实测）：
  workers.py 异常块把 CollectorStopTimeout **降级成字符串** ⇒ backtest_tab._on_error 的
  isinstance 恒 False ⇒ 已实现的 `_handle_stop_timeout`（继续等待/强制终止/取消）永不触发
  ⇒ 用户只看到「回测错误」+ 一屏 traceback。
"""
from __future__ import annotations

import inspect

from quantstudio.gui import workers
from quantstudio.gui.tabs import backtest_tab
from quantstudio.pipeline.backtest_window import CollectorStopTimeout, WindowCancelled


def test_worker_passes_timeout_exception_as_object():
    """worker 必须原样传递异常对象（类型信息不能丢）。"""
    src = inspect.getsource(workers.BacktestWorker.run)
    assert "CollectorStopTimeout" in src and "WindowCancelled" in src
    assert "self.finished_err.emit(e)" in src, "结构化异常必须走 emit(e) 而非字符串"


def test_tab_routes_timeout_to_three_options():
    src = inspect.getsource(backtest_tab.BacktestTab._on_error)
    assert "isinstance(err, CollectorStopTimeout)" in src
    assert "_handle_stop_timeout(err)" in src


def test_tab_treats_window_cancelled_as_cancel():
    src = inspect.getsource(backtest_tab.BacktestTab._on_error)
    assert "WindowCancelled" in src
    assert "is_cancel = isinstance(err, (BacktestCancelled, WindowCancelled))" in src


def test_continue_waiting_escalates_timeout():
    """「继续等待」应递增等待上限（长任务常 >60s），封顶 15 分钟。"""
    src = inspect.getsource(backtest_tab.BacktestTab._handle_stop_timeout)
    assert "coexistence_stop_timeout" in src
    assert "min(cur * 2, 900)" in src


def test_worker_passes_wait_timeout_params():
    src = inspect.getsource(workers.BacktestWorker.run)
    assert "coexistence_stop_timeout=self.params.get('coexistence_stop_timeout')" in src
    assert "coexistence_boundary_timeout=self.params.get('coexistence_boundary_timeout')" in src


def test_cli_path_still_has_its_own_contract():
    """CLI 路径不受影响：仍按 exit 4 + stderr 提示（口径不互相污染）。"""
    from quantstudio.backtest import run_ptrade_strategy as R
    src = inspect.getsource(R)
    assert "except CollectorStopTimeout as e:" in src
    assert "--coexistence-stop-timeout" in src or "coexistence-stop-timeout" in src


def test_exception_types_are_distinct():
    assert not issubclass(CollectorStopTimeout, WindowCancelled)
    assert not issubclass(WindowCancelled, CollectorStopTimeout)
