# -*- coding: utf-8 -*-
"""C1（终审阻断项）：超时/取消/强杀三条路径必须补写「因回测停止」归因 flag。"""
import json

import pytest

from quantstudio.pipeline import backtest_window as bw
from quantstudio.pipeline.backtest_window import (
    CollectorStopContext, CollectorStopTimeout, WindowCancelled, note_force_killed)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(bw, "_data_root", lambda: tmp_path)
    return tmp_path


def _flag_path(root):
    return bw.stop_flag_path()


def _mk_ctx(**kw):
    return CollectorStopContext("s.py", **kw)


def test_timeout_writes_flag(root, monkeypatch):
    """等待超时（异常从 __enter__ 抛出）⇒ flag 必须已落盘（归因不丢）。"""
    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: True)

    def _timeout(*a, **kw):
        raise CollectorStopTimeout("采集互斥锁在等待窗口内未释放")

    monkeypatch.setattr(bw, "wait_daemon_exit", _timeout)
    with pytest.raises(CollectorStopTimeout):
        _mk_ctx().__enter__()
    doc = json.loads(_flag_path(root).read_text(encoding="utf-8"))
    assert doc["reason"] == "backtest"
    assert "等待超时" in doc["note"]


def test_cancel_writes_flag(root, monkeypatch):
    """用户取消等待 ⇒ 同样补写归因（停止请求已发出，daemon 将在边界退出）。"""
    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: True)

    def _cancel(*a, **kw):
        raise WindowCancelled("用户取消")

    monkeypatch.setattr(bw, "wait_daemon_exit", _cancel)
    with pytest.raises(WindowCancelled):
        _mk_ctx().__enter__()
    doc = json.loads(_flag_path(root).read_text(encoding="utf-8"))
    assert doc["reason"] == "backtest"
    assert "取消" in doc["note"]


def test_note_force_killed_writes_flag(root):
    """GUI 强杀成功后的补写入口 ⇒ flag 落盘且带强杀归因。"""
    note_force_killed()
    doc = json.loads(_flag_path(root).read_text(encoding="utf-8"))
    assert doc["reason"] == "backtest"
    assert "强制终止" in doc["note"]


def test_gui_kill_branch_calls_note_force_killed():
    """GUI 强杀分支必须调用补写入口（源码级断言，不启 Qt）。"""
    import inspect
    from quantstudio.gui.tabs import backtest_tab as tab
    src = inspect.getsource(tab.BacktestTab._handle_stop_timeout)
    assert "note_force_killed" in src, "backtest_tab 强杀分支必须调用 note_force_killed()"


def test_normal_path_unaffected(root, monkeypatch):
    """回归：正常路径（等待成功）flag 仍由 __exit__ 写，语义不变。"""
    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: True)
    monkeypatch.setattr(bw, "wait_daemon_exit", lambda *a, **kw: 0.0)
    monkeypatch.setattr(bw.CollectorStopContext, "_acquire_lock", lambda self: None)  # 免锁
    with _mk_ctx() as ctx:
        pass
    doc = json.loads(_flag_path(root).read_text(encoding="utf-8"))
    assert "回测结束" in doc["note"]
