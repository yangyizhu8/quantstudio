"""F1: PyQt rebalance_mode 通用配置透出测试

覆盖任务书 §3.7：
- GUI 默认值为 legacy
- PyQt 参数字典包含 rebalance_mode
- Worker 将值传入 EngineConfig
- 不提供参数时仍为 legacy
- close/open + callback_basket 被 GUI 阻断
- 默认 GUI 参数下 EngineConfig 与修复前字段逐项一致（黄金行为等价）
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

qt_widgets = pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("qfluentwidgets")

QApplication = qt_widgets.QApplication

from quantstudio.gui.tabs.backtest_tab import BacktestTab
from quantstudio.gui import workers as workers_mod
from quantstudio.backtest.backtest_engine import EngineConfig


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
def backtest_tab(app, tmp_path):
    return BacktestTab(_DummyMainWindow(tmp_path))


def _arm_tab(tab, monkeypatch, match_mode="close", rebalance_mode=None):
    """让 _on_run 能走到 Worker 创建：提供策略项 + 拦截 Worker 类。"""
    tab.strategy_combo.addItem("demo.py", userData="demo.py")
    tab.strategy_combo.setCurrentIndex(0)
    idx = tab.match_price_combo.findData(match_mode)
    assert idx >= 0
    tab.match_price_combo.setCurrentIndex(idx)
    if rebalance_mode is not None:
        ridx = tab.rebalance_mode_combo.findData(rebalance_mode)
        assert ridx >= 0
        tab.rebalance_mode_combo.setCurrentIndex(ridx)
    captured = {}

    class StubWorker:
        def __init__(self, strategy_path, params):
            captured["strategy_path"] = strategy_path
            captured["params"] = params
            self.progress = SimpleNamespace(connect=lambda *a: None)
            self.day_progress = SimpleNamespace(connect=lambda *a: None)
            self.finished_ok = SimpleNamespace(connect=lambda *a: None)
            self.finished_err = SimpleNamespace(connect=lambda *a: None)

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(
        "quantstudio.gui.tabs.backtest_tab.BacktestWorker", StubWorker)
    monkeypatch.setattr(
        qt_widgets.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(
        qt_widgets.QMessageBox, "warning", lambda *a, **k: None)
    return captured


def test_gui_rebalance_mode_default_is_legacy(backtest_tab):
    assert backtest_tab.rebalance_mode_combo.currentData() == "legacy"


def test_gui_run_params_include_rebalance_mode(backtest_tab, monkeypatch):
    captured = _arm_tab(backtest_tab, monkeypatch, match_mode="next_open",
                        rebalance_mode="callback_basket")
    backtest_tab._on_run()
    assert captured["params"]["rebalance_mode"] == "callback_basket"


def test_gui_run_params_default_rebalance_mode_is_legacy(backtest_tab, monkeypatch):
    captured = _arm_tab(backtest_tab, monkeypatch)
    backtest_tab._on_run()
    assert captured["params"]["rebalance_mode"] == "legacy"


def test_gui_blocks_callback_basket_with_close(backtest_tab, monkeypatch):
    warnings = []
    captured = _arm_tab(backtest_tab, monkeypatch, match_mode="close",
                        rebalance_mode="callback_basket")
    monkeypatch.setattr(
        qt_widgets.QMessageBox, "warning",
        lambda *a, **k: warnings.append(a))
    backtest_tab._on_run()
    assert warnings, "close + callback_basket 必须被阻断并提示"
    assert "started" not in captured


def test_gui_blocks_callback_basket_with_open(backtest_tab, monkeypatch):
    warnings = []
    captured = _arm_tab(backtest_tab, monkeypatch, match_mode="open",
                        rebalance_mode="callback_basket")
    monkeypatch.setattr(
        qt_widgets.QMessageBox, "warning",
        lambda *a, **k: warnings.append(a))
    backtest_tab._on_run()
    assert warnings, "open + callback_basket 必须被阻断并提示"
    assert "started" not in captured


def _run_worker_with_params(monkeypatch, params):
    """同步执行 BacktestWorker.run()，捕获引擎构造 kwargs 与 run_backtest 直传 kwargs。

    P4 修复：BacktestWorker 改走 run_ptrade_strategy.run_backtest 共用入口，
    引擎在 run_ptrade_strategy 命名空间内构造（L19 from-import 为导入期绑定）。
    原补丁点 backtest_engine.BacktestEngine 改的是源模块属性，不影响调用方
    命名空间里已绑定的名字 → StubEngine 永不构造（KeyError engine_kwargs），
    且补丁失效后真实路径可能触碰生产库。补丁点迁至调用点命名空间。
    """
    captured = {}

    def fake_load_strategy(path):
        return {}, SimpleNamespace()

    class StubEngine:
        def __init__(self, **kwargs):
            captured["engine_kwargs"] = kwargs

        def run(self):
            result = SimpleNamespace(nav_history=[], trade_records=[])
            return result, "output/stub"

    monkeypatch.setattr(
        "quantstudio.backtest.run_ptrade_strategy.load_strategy",
        fake_load_strategy)
    monkeypatch.setattr(
        "quantstudio.backtest.run_ptrade_strategy.BacktestEngine", StubEngine)

    # O3：spy 记录 worker → run_backtest 的直传 kwargs（rebalance_mode 等）
    import quantstudio.backtest.run_ptrade_strategy as _rps
    _real_run_backtest = _rps.run_backtest

    def _spy_run_backtest(*args, **kwargs):
        captured["run_kwargs"] = kwargs
        return _real_run_backtest(*args, **kwargs)

    monkeypatch.setattr(_rps, "run_backtest", _spy_run_backtest)

    worker = workers_mod.BacktestWorker("demo.py", params)
    worker.run()
    return captured


def _base_params(**overrides):
    params = {
        "db_path": "data/quantstudio.db",
        "start": "2026-01-01",
        "end": "2026-01-31",
        "capital": 100000,
        "commission": 0.00035,
        "stamp_tax": 0.001,
        "slippage": 0.0,
        "match_price_mode": "close",
    }
    params.update(overrides)
    return params


def test_worker_passes_rebalance_mode_into_engine_config(monkeypatch):
    captured = _run_worker_with_params(
        monkeypatch, _base_params(rebalance_mode="callback_basket",
                                  match_price_mode="next_open"))
    # O3：worker → run_backtest 直传链显式断言
    assert captured["run_kwargs"]["rebalance_mode"] == "callback_basket"
    assert captured["run_kwargs"]["match_price_mode"] == "next_open"
    config = captured["engine_kwargs"]["config"]
    assert isinstance(config, EngineConfig)
    assert config.rebalance_mode == "callback_basket"


def test_worker_defaults_to_legacy_without_param(monkeypatch):
    captured = _run_worker_with_params(monkeypatch, _base_params())
    # O3：未提供参数时 worker 侧默认 legacy
    assert captured["run_kwargs"]["rebalance_mode"] == "legacy"
    config = captured["engine_kwargs"]["config"]
    assert config.rebalance_mode == "legacy"


def test_worker_default_engine_config_matches_pre_fix_fields(monkeypatch):
    """默认 GUI 参数下 EngineConfig 其他字段与修复前逐项一致（黄金等价）。"""
    captured = _run_worker_with_params(monkeypatch, _base_params())
    config = captured["engine_kwargs"]["config"]
    base = EngineConfig.default()
    assert config.db_path == base.db_path.__class__("data/quantstudio.db")
    assert config.output_dir == base.output_dir
    assert config.research_dir == base.research_dir
    assert config.data_source == base.data_source == "tushare"
    assert config.rebalance_mode == "legacy"
