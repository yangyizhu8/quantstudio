"""回测计时功能测试（BacktestTab time_label + QElapsedTimer/QTimer 生命周期）

覆盖已批准修订版方案（2026-08-27）：
- 初始态：time_label 基线文本、计时器未激活
- _fmt_elapsed 边界（纯函数，零漂移格式化）
- _on_run 启动计时（归零 + active，_freeze_text 复位）
- _on_finished 停止定格（freeze "⏱ 用时 ..."）
- _on_stop 真定格（stop tick + setText，按按下时刻为准）
- _on_error 定格幂等（不覆盖 _on_stop 已定格值，也不重算跳秒）
- 连跑重置（无计时器泄漏、无残留定格）
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

qt_widgets = pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("qfluentwidgets")

QApplication = qt_widgets.QApplication

from quantstudio.gui.tabs import backtest_tab as bt_mod
from quantstudio.gui.tabs.backtest_tab import BacktestTab

_QAPP_KEEPALIVE = []


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    # 模块 fixture 拆除后若 QApplication 失去最后一个 Python 引用，
    # sip 会删除 C++ QApplication 并连带删除 qfluentwidgets 的 qconfig 单例，
    # 导致后续模块创建控件时报 "QConfig has been deleted"。保持全局引用防 GC。
    _QAPP_KEEPALIVE.append(instance)
    yield instance


class _DummyMainWindow:
    def __init__(self, root_path):
        self.root_path = root_path
        self._held = None

    def hold_worker(self, worker):
        self._held = worker


@pytest.fixture
def tab(app, tmp_path, monkeypatch):
    # 让 _on_run 完整走到 Worker 创建：db_path 指向已存在文件 + 拦截真实 Worker 类
    db_file = tmp_path / "dummy.duckdb"
    db_file.write_bytes(b"")
    monkeypatch.setattr(bt_mod, "db_path", lambda: db_file)
    return BacktestTab(_DummyMainWindow(tmp_path))


def _arm(tab, monkeypatch):
    """策略下拉 + 拦截 BacktestWorker → 可完整走 _on_run 到 Worker 创建与计时启动。"""
    tab.strategy_combo.addItem("demo.py", userData="demo.py")
    tab.strategy_combo.setCurrentIndex(0)
    captured = {"started": False}

    class StubWorker:
        def __init__(self, strategy_path, params):
            self._cancelled = False

        progress = SimpleNamespace(connect=lambda *a: None)
        day_progress = SimpleNamespace(connect=lambda *a: None)
        finished_ok = SimpleNamespace(connect=lambda *a: None)
        finished_err = SimpleNamespace(connect=lambda *a: None)

        def start(self):
            captured["started"] = True

        def cancel(self):
            self._cancelled = True

    monkeypatch.setattr(bt_mod, "BacktestWorker", StubWorker)
    monkeypatch.setattr(qt_widgets.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(qt_widgets.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(qt_widgets.QMessageBox, "critical", lambda *a, **k: None)
    return captured


def test_time_label_initial(tab):
    assert tab.time_label.text() == "⏱ 已用时 00:00:00"
    assert tab._tick.isActive() is False
    assert tab._freeze_text is None
    assert tab.stop_btn.isEnabled() is False


@pytest.mark.parametrize("ms,expected", [
    (0, "00:00:00"),
    (500, "00:00:00"),          # 不满 1s 舍入
    (59_999, "00:00:59"),
    (60_000, "00:01:00"),
    (3_600_000, "01:00:00"),
    (3_660_000, "01:01:00"),
    (100 * 3600 * 1000, "100:00:00"),  # >99h 自然进位不截断
    (-1, "00:00:00"),           # 负数钳制为 0
])
def test_fmt_elapsed_bounds(ms, expected):
    assert BacktestTab._fmt_elapsed(ms) == expected


def test_run_starts_timer(tab, monkeypatch):
    captured = _arm(tab, monkeypatch)
    tab._on_run()
    assert captured["started"] is True
    assert tab._tick.isActive() is True
    assert tab.time_label.text() == "⏱ 已用时 00:00:00"
    assert tab._freeze_text is None


def test_finished_stops_and_freezes(tab, monkeypatch):
    _arm(tab, monkeypatch)
    tab._on_run()
    tab._on_finished({"output_dir": "output/run-1"})
    assert tab._tick.isActive() is False
    assert tab.time_label.text().startswith("⏱ 用时 ")
    assert tab._freeze_text is not None


def test_stop_freezes_at_press(tab, monkeypatch):
    """修订①：_on_stop 即 stop tick + setText，最终用时以按下时刻为准。"""
    _arm(tab, monkeypatch)
    tab._on_run()
    tab._on_stop()
    assert tab._tick.isActive() is False
    frozen = tab.time_label.text()
    assert frozen.startswith("⏱ 用时 ")
    assert tab._freeze_text == frozen
    assert tab.status_label.text() == "正在停止..."


def test_error_does_not_overwrite_stop_freeze(tab, monkeypatch):
    """取消路径（cancel→finished_err→_on_error）：定格幂等，不覆盖按时刻值、不跳秒。"""
    _arm(tab, monkeypatch)
    tab._on_run()
    tab._on_stop()
    pressed = tab.time_label.text()
    assert pressed.startswith("⏱ 用时 ")
    tab._on_error("engine failed")
    assert tab._tick.isActive() is False
    assert tab.time_label.text() == pressed   # 幂等：保持按下时刻定格
    assert tab.status_label.text() == "❌ 回测失败"


def test_error_freezes_without_stop(tab, monkeypatch):
    _arm(tab, monkeypatch)
    tab._on_run()
    tab._on_error("engine failed")
    assert tab._tick.isActive() is False
    assert tab.time_label.text().startswith("⏱ 用时 ")
    assert tab._freeze_text is not None


def test_rerun_resets(tab, monkeypatch):
    """连跑：归零重开，无残留定格、无计时器泄漏（同一 _tick 实例）。"""
    _arm(tab, monkeypatch)
    tab._on_run()
    tab._on_error("x")
    tick1 = tab._tick
    tab._on_run()
    assert tab._tick is tick1
    assert tab._tick.isActive() is True
    assert tab.time_label.text() == "⏱ 已用时 00:00:00"
    assert tab._freeze_text is None