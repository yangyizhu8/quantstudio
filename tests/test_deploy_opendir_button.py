# -*- coding: utf-8 -*-
"""PD-0014：qs-deploy GUI「打开输出目录」按钮验收测试。

覆盖方案 §4 五项验收中可机检的部分：
1. 快捷页与高级页**各有一枚**「打开输出目录」按钮（findChild 实测）；
2. 未转换时点击 → 提示分支（不抛异常、不调 startfile）；
3. 转换 PASS 后点击 → `os.startfile` 实参 == **产物所在目录**（monkeypatch 断言）；
4. 目录不存在 → 报错分支（不调 startfile）；
5. 产物目录派生的纯函数行为（空 artifacts → None；product/report/runbook 任一可派生）。
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

pytest.importorskip("PyQt6")
pytest.importorskip("qfluentwidgets")

#: 全进程常驻 QApplication（沿用 tests/test_deploy_gui.py 的权威写法）：
#: qfluentwidgets 的 QConfig 是 QObject 单例，QApplication 若被回收则其 C++ 侧悬空
#: → 后续 Qt 用例报 `wrapped C/C++ object of type QConfig has been deleted`。
_QT_APP = None


def _make_tab():
    """构造 DeployTab（offscreen）；QApplication 提到模块级持有（见上方注释）。"""
    global _QT_APP
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    from quantstudio.gui.tabs.deploy_tab import DeployTab
    return DeployTab(), _QT_APP


@pytest.fixture(autouse=True)
def _reset_infobar():
    """每次用例前复位 InfoBar 替身记录。"""
    _FakeInfoBar.reset()
    yield


def test_both_pages_have_open_dir_button():
    """验收 1：两页各一枚「打开输出目录」按钮。"""
    from qfluentwidgets import PushButton
    tab, _app = _make_tab()
    btns = [b for b in tab.findChildren(PushButton)
            if b.text() == "打开输出目录"]
    assert len(btns) == 2, [b.text() for b in btns]


def test_click_without_conversion_shows_hint(monkeypatch):
    """验收 2：未转换 → 提示分支（不抛异常、不调 startfile）。"""
    import quantstudio.gui.tabs.deploy_tab as mod
    tab, _app = _make_tab()
    called = []
    monkeypatch.setattr(mod.os, "startfile", lambda p: called.append(p))
    monkeypatch.setattr(mod, "InfoBar", _FakeInfoBar())
    tab._last_out_dir = None
    tab._open_out_dir()          # 不抛异常即过
    assert called == []
    assert _FakeInfoBar.infos, "应给出「无输出目录」提示"


def test_click_after_conversion_opens_artifact_dir(monkeypatch, tmp_path):
    """验收 3：转换后点击 → startfile 实参 == 产物目录。"""
    import quantstudio.gui.tabs.deploy_tab as mod
    tab, _app = _make_tab()
    out = tmp_path / "Demo_account_Real_account" / "全球资产轮动"
    out.mkdir(parents=True)
    product = out / "strat_0c202b_trade.py"
    product.write_text("# x", encoding="utf-8")
    tab._last_out_dir = mod.DeployTab._out_dir_from_artifacts(
        {"product": str(product)})
    assert tab._last_out_dir == out
    called = []
    monkeypatch.setattr(mod.os, "startfile", lambda p: called.append(p))
    monkeypatch.setattr(mod, "InfoBar", _FakeInfoBar())
    tab._open_out_dir()
    assert called == [str(out.resolve())], called


def test_click_missing_dir_reports_error(monkeypatch, tmp_path):
    """验收 4：目录不存在 → 报错分支（不调 startfile）。"""
    import quantstudio.gui.tabs.deploy_tab as mod
    tab, _app = _make_tab()
    tab._last_out_dir = tmp_path / "不存在_的_目录"
    called = []
    monkeypatch.setattr(mod.os, "startfile", lambda p: called.append(p))
    monkeypatch.setattr(mod, "InfoBar", _FakeInfoBar())
    tab._open_out_dir()
    assert called == []
    assert _FakeInfoBar.errors, "应给出「输出目录不存在」错误"


def test_out_dir_from_artifacts_derivation():
    """验收 5：目录派生纯函数（空 → None；product 优先；report/runbook 兜底）。"""
    from quantstudio.gui.tabs.deploy_tab import DeployTab
    assert DeployTab._out_dir_from_artifacts(None) is None
    assert DeployTab._out_dir_from_artifacts({}) is None
    assert DeployTab._out_dir_from_artifacts({"product": ""}) is None
    assert DeployTab._out_dir_from_artifacts(
        {"report": "/tmp/x/report.md"}) == Path("/tmp/x")
    assert DeployTab._out_dir_from_artifacts(
        {"runbook": "/tmp/y/runbook.md"}) == Path("/tmp/y")
    # product 优先
    assert DeployTab._out_dir_from_artifacts(
        {"product": "/tmp/p/a.py", "report": "/tmp/r/r.md"}) == Path("/tmp/p")


class _FakeInfoBar:
    """InfoBar 替身：记录 info/error 反馈（避免真弹窗阻塞测试）。"""
    infos: list = []
    errors: list = []

    @classmethod
    def info(cls, title, content="", **kw):
        cls.infos.append((title, content))
        return None

    @classmethod
    def error(cls, title, content="", **kw):
        cls.errors.append((title, content))
        return None

    @classmethod
    def reset(cls):
        cls.infos = []
        cls.errors = []
