"""真实方法回归测试：TaskTab 顶部状态栏写入口 _set_status_text（P0 自递归修复）。

背景（2026-09-11）：
    quantstudio/gui/tabs/task_tab.py 的 _set_status_text 内部被误写成
    ``self._set_status_text(msg)``（自调用），13 个状态写入路径全部触发无限递归
    -> RecursionError -> GUI 退出。该缺陷由 67ca882 引入。

缺陷为何长期逃逸（本测试要堵的缺口）：
    tests/test_gui_task_audit_separation.py 用 DummyTab **复制了一份**
    _set_status_text 实现（替身），真实类方法从未被执行。替身与真身分叉后，
    真身坏掉而测试全绿。

本测试的硬约束：
    1) 必须走**真实 TaskTab**（offscreen 构造）或经 types.MethodType 绑定
       **真实类方法**；禁止复制/重写实现。
    2) status_label 必须是真实 QLabel（验证 setText / setToolTip 真落位）。
"""
from __future__ import annotations

import ast
import inspect
import os
import textwrap
import time
from pathlib import Path
from types import MethodType, SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pandas as pd

qt_widgets = pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("qfluentwidgets")

QApplication = qt_widgets.QApplication
QLabel = qt_widgets.QLabel

from quantstudio.gui.tabs.task_tab import TaskTab

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_PROFILE_DIR = REPO_ROOT / "config" / "profiles" / "mcp_only"

# 生产实文案（task_tab.py:436；unicode 转义书写，避免控制台编码影响）
LIVE_MSG = "\U0001F7E2 \u5e38\u9a7b\u91c7\u96c6\u8fdb\u7a0b\u5df2\u542f\u52a8"


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


class _StubDbHelper:
    """仅提供 TaskTab._render_tasks 所需的水位缓存（空表即可）。"""

    def get_watermarks(self):
        return pd.DataFrame(columns=["table_name", "freq", "source", "watermark"])


class _StubMainWindow:
    """构造 harness：只补 TaskTab.__init__ 依赖的宿主接口，不替身被测方法。"""

    def __init__(self, config_dir: Path):
        self.app_root = REPO_ROOT
        self.config_dir = config_dir
        self.current_profile = "mcp_only"
        self.db_helper = _StubDbHelper()

    def profile_options(self):
        return [("mcp_only", "MCP-only"), ("traditional", "Traditional")]


def _bind_real_method_target():
    """最小 harness：真实 QLabel + MethodType 绑定的**真实类方法**（禁止复制实现）。"""
    obj = SimpleNamespace()
    obj.status_label = QLabel("")
    obj._set_status_text = MethodType(TaskTab._set_status_text, obj)
    return obj


@pytest.fixture(scope="module")
def status_tab(app):
    """优先构造真实 TaskTab（offscreen）；依赖过重时回退到绑定真实类方法的 harness。"""
    try:
        return TaskTab(_StubMainWindow(MCP_PROFILE_DIR))
    except Exception:  # pragma: no cover - 环境依赖兜底，方法真身仍经 MethodType 绑定
        return _bind_real_method_target()


def test_status_text_writes_label_text_and_tooltip(status_tab):
    """用例 A：真实写入口必须把 msg 同时落到 status_label 的 text 与 toolTip。"""
    status_tab._set_status_text(LIVE_MSG)
    assert status_tab.status_label.text() == LIVE_MSG
    assert status_tab.status_label.toolTip() == LIVE_MSG


def test_status_text_never_calls_itself(status_tab):
    """用例 B（防回归）：_set_status_text 内部不得出现对自身的调用。"""
    source = inspect.getsource(TaskTab._set_status_text)
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("def ")
    )
    assert "self._set_status_text(" not in body, (
        "_set_status_text 函数体内出现自调用，会触发无限递归（P0 回归）"
    )

    # AST 加固：捕获 TaskTab._set_status_text(self, ...) / 别名调用等其它自调用写法
    func_def = ast.parse(textwrap.dedent(source)).body[0]
    self_calls = [
        node
        for node in ast.walk(func_def)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_set_status_text"
    ]
    assert not self_calls, f"_set_status_text 内部存在自调用：{ast.dump(self_calls[0])}"

    # 调用必须常量时间返回（自递归会抛 RecursionError 或显著拖长耗时）
    started = time.monotonic()
    status_tab._set_status_text("constant-time probe")
    assert time.monotonic() - started < 2.0
