# -*- coding: utf-8 -*-
"""空数据防线单测（T027 · US3）。

断言：
  1. 默认（fail_closed 关闭）行为与改动前逐位一致：拿不到连接 → 返回 None + 诊断计数；
  2. fail_closed 开启 → 拿不到连接即抛 DatabaseAccessError（显式失败，不空跑）；
  3. 线程本地隔离：一个线程开启不影响其他线程（回测在 QThread，纯增益）；
  4. 结果校验：无净值记录判失败；**合法零交易**（有净值、无成交）不误判。
"""
from __future__ import annotations

import threading

import pytest

from quantstudio.backtest.providers import duckdb_data_access as dda
from quantstudio.backtest.run_ptrade_strategy import _assert_result_not_empty


@pytest.fixture(autouse=True)
def _reset_flag():
    dda.set_fail_closed(False)
    yield
    dda.set_fail_closed(False)


def test_default_returns_none_and_keeps_contract(tmp_path):
    """默认关闭：既有契约不变（不抛、返回 None），零行为漂移。

    注：路径不存在时既有实现本就不记诊断（只有"文件存在但连接失败"才记），
    此处只断言契约本身。
    """
    acc = dda.DuckDBDataAccess(tmp_path / "missing.db")
    assert acc.fail_closed is False
    assert acc._get_conn() is None


def test_fail_closed_raises_when_db_missing(tmp_path):
    dda.set_fail_closed(True)
    acc = dda.DuckDBDataAccess(tmp_path / "missing.db")
    assert acc.fail_closed is True
    with pytest.raises(dda.DatabaseAccessError) as ei:
        acc._get_conn()
    assert "无法打开数据主库" in str(ei.value)


def test_fail_closed_explicit_arg_overrides_thread_default(tmp_path):
    dda.set_fail_closed(True)
    acc = dda.DuckDBDataAccess(tmp_path / "missing.db", fail_closed=False)
    assert acc.fail_closed is False
    assert acc._get_conn() is None


def test_fail_closed_is_thread_local(tmp_path):
    dda.set_fail_closed(True)
    seen = {}

    def worker():
        seen["v"] = dda.current_fail_closed()
        acc = dda.DuckDBDataAccess(tmp_path / "missing.db")
        seen["acc"] = acc.fail_closed
        seen["conn"] = acc._get_conn()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert dda.current_fail_closed() is True      # 本线程仍是 True
    assert seen["v"] is False, "线程本地：子线程不得被主线程开关污染"
    assert seen["acc"] is False
    assert seen["conn"] is None


class _Result:
    def __init__(self, nav, trades=None):
        self.nav_history = nav
        self.trade_records = trades if trades is not None else []


def test_assert_result_not_empty_rejects_empty_nav(tmp_path):
    with pytest.raises(RuntimeError) as ei:
        _assert_result_not_empty(_Result([]), tmp_path)
    assert "未产出任何净值记录" in str(ei.value)


def test_assert_result_allows_legit_zero_trade(tmp_path):
    """合法零交易（有逐日净值、0 笔成交）必须通过——不得误判为空数据。"""
    _assert_result_not_empty(_Result([{"date": "2026-01-05", "nav": 1.0}], trades=[]), tmp_path)


def test_assert_result_passes_with_nav(tmp_path):
    _assert_result_not_empty(_Result([{"date": "2026-01-05", "nav": 1.0}]), tmp_path)
