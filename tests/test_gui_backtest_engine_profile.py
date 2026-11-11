"""GUI 回测 tab 引擎 Profile 支持（2026-11-11）。

覆盖：
  · `_read_engine_profile` 五态（有声明 / 无声明 / 不存在 / 乱码 / BOM+GBK 不抛异常）
  · 目录扫描（正式目录 + 生成目录；[生成] 前缀；userData 仍为路径字符串）
  · engine_combo 默认 daily、恰两档
"""
from __future__ import annotations

import codecs
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

qt_widgets = pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("qfluentwidgets")
QApplication = qt_widgets.QApplication

from quantstudio.gui.tabs.backtest_tab import (   # noqa: E402
    DEFAULT_ENGINE_PROFILE,
    _read_engine_profile,
)


# 模块级 QApplication（沿 test_gui_backtest_timer.py 惯例）：offscreen，不弹窗
@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


# ---------------- _read_engine_profile 五态 ----------------

def test_declared_minute(tmp_path):
    p = tmp_path / "s.py"
    p.write_text("# engine_profile=minute-bar-v1\n", encoding="utf-8")
    assert _read_engine_profile(p) == "minute-bar-v1"


def test_declared_daily_inline(tmp_path):
    p = tmp_path / "s.py"
    p.write_text(
        "# design_version=2.3  engine_profile=daily-bar-v1  match_price_mode=open\n",
        encoding="utf-8")
    assert _read_engine_profile(p) == "daily-bar-v1"


def test_undeclared_returns_default(tmp_path):
    p = tmp_path / "s.py"
    p.write_text("def initialize(context):\n    pass\n", encoding="utf-8")
    assert _read_engine_profile(p) == DEFAULT_ENGINE_PROFILE


def test_missing_file_returns_default(tmp_path):
    assert _read_engine_profile(tmp_path / "nope.py") == DEFAULT_ENGINE_PROFILE


def test_binary_garbage_returns_default(tmp_path):
    p = tmp_path / "s.py"
    p.write_bytes(b"\xff\xfe\x00\x00broken")
    assert _read_engine_profile(p) == DEFAULT_ENGINE_PROFILE


def test_utf8_bom_with_declaration_never_raises(tmp_path):
    """BOM 前缀 + 声明：可读到，或安全退默认；**绝不抛异常**（②审补强第五态）。"""
    p = tmp_path / "s.py"
    p.write_bytes(codecs.BOM_UTF8 + b"# engine_profile=minute-bar-v1\n")
    assert _read_engine_profile(p) in ("minute-bar-v1", DEFAULT_ENGINE_PROFILE)


def test_gbk_encoded_never_raises(tmp_path):
    """GBK 编码中文注释：生成目录 agent 产物编码不可保证，不得抛异常。"""
    p = tmp_path / "s.py"
    p.write_bytes("# \u4e2d\u6587\u6ce8\u91ca\n".encode("gbk"))
    assert _read_engine_profile(p) == DEFAULT_ENGINE_PROFILE


def test_declaration_beyond_head_window_ignored(tmp_path):
    """只读前 60 行（性能契约）：第 61 行的声明不计入。"""
    p = tmp_path / "s.py"
    lines = ["# filler %d" % i for i in range(60)] + ["# engine_profile=minute-bar-v1"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _read_engine_profile(p) == DEFAULT_ENGINE_PROFILE


def test_default_none_probes_declaration_presence(tmp_path):
    """default=None ⇒ 「是否有声明」探测器（错配校验依赖此语义区分声明/未声明）。"""
    a = tmp_path / "a.py"
    a.write_text("# engine_profile=minute-bar-v1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("# no declaration here\n", encoding="utf-8")
    assert _read_engine_profile(a, default=None) == "minute-bar-v1"
    assert _read_engine_profile(b, default=None) is None


# ---------------- 目录扫描 + 控件 ----------------

def _make_mw(root):
    class _MW:
        root_path = Path(root)
    return _MW()


def _layout(tmp_path):
    """建最小目录树：1 个正式策略 + 1 个下划线策略 + 1 个有代码的生成策略 + 1 个空的。"""
    strat = tmp_path / "quantstudio" / "backtest" / "strategies"
    strat.mkdir(parents=True)
    (strat / "alpha.py").write_text("# x\n", encoding="utf-8")
    (strat / "_private.py").write_text("# x\n", encoding="utf-8")
    gen = tmp_path / "output" / "generated_strategies"
    (gen / "beta").mkdir(parents=True)
    (gen / "beta" / "strategy.py").write_text(
        "# engine_profile=minute-bar-v1\n", encoding="utf-8")
    (gen / "gamma").mkdir(parents=True)          # 无 strategy.py ⇒ 不应出现
    return tmp_path


def test_refresh_scans_formal_and_generated(app, tmp_path):
    from quantstudio.gui.tabs.backtest_tab import BacktestTab
    _layout(tmp_path)
    tab = BacktestTab(_make_mw(tmp_path))
    labels = [tab.strategy_combo.itemText(i) for i in range(tab.strategy_combo.count())]
    datas = [tab.strategy_combo.itemData(i) for i in range(tab.strategy_combo.count())]

    assert "alpha.py" in labels
    assert "_private.py" not in labels, "下划线开头的策略应被排除（既有行为）"
    gen_idx = [i for i, l in enumerate(labels) if l.startswith("[生成] ")]
    assert len(gen_idx) == 1 and "beta" in labels[gen_idx[0]]
    assert not any("gamma" in l for l in labels), "无 strategy.py 的子目录不应出现"
    assert gen_idx[0] > labels.index("alpha.py"), "[生成] 应排在正式策略之后"
    assert all(isinstance(d, str) for d in datas), "userData 契约：仍为路径字符串"


def test_engine_combo_two_options_default_daily(app, tmp_path):
    from quantstudio.gui.tabs.backtest_tab import BacktestTab
    (tmp_path / "quantstudio" / "backtest" / "strategies").mkdir(parents=True)
    tab = BacktestTab(_make_mw(tmp_path))
    assert tab.engine_combo.count() == 2
    assert tab.engine_combo.currentData() == "daily-bar-v1", "默认必须 daily（零回归）"
    items = [tab.engine_combo.itemData(i) for i in range(2)]
    assert items == ["daily-bar-v1", "minute-bar-v1"]
