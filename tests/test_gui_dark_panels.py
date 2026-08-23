from __future__ import annotations

import json
import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

qt_widgets = pytest.importorskip("PyQt6.QtWidgets")
qfluentwidgets = pytest.importorskip("qfluentwidgets")

QApplication = qt_widgets.QApplication
QLabel = qt_widgets.QLabel
QPalette = pytest.importorskip("PyQt6.QtGui").QPalette
NavigationInterface = qfluentwidgets.NavigationInterface
ScrollArea = qfluentwidgets.ScrollArea
Theme = qfluentwidgets.Theme
qconfig = qfluentwidgets.qconfig
setTheme = qfluentwidgets.setTheme

from quantstudio.gui.main_window import MainWindow
from quantstudio.gui.tabs.backtest_tab import BacktestTab
from quantstudio.gui.tabs.config_editor_tab import ConfigEditorTab
from quantstudio.gui.tabs.quality_tab import QualityTab
from quantstudio.gui.tabs.source_tab import SourceTab


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def source_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sources_config.json").write_text(
        json.dumps({"sources": {}}), encoding="utf-8"
    )
    return config_dir


@pytest.fixture
def config_editor_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    configs = {
        "data_config.json": {},
        "sources_config.json": {"sources": {}},
        "collector_tasks.json": {
            "daemon_schedule": {
                "daily_time": "17:00",
                "check_interval_sec": 300,
            },
            "tasks": [
                {
                    "name": "daily stock",
                    "table": "stock_daily",
                    "freq": "daily",
                    "enabled": True,
                    "source": "xtquant",
                    "source_priority": ["xtquant", "tushare"],
                    "passthrough_option": {"keep": True},
                    "start_date": "2018-01-01",
                    "end_date": "2026-07-24",
                    "codes": ["ALL"],
                    "max_workers": 4,
                    "rate_limit": {
                        "calls_per_min": 60,
                        "wait_on_429": True,
                    },
                }
            ],
        },
        "alignment_rules.json": {"schemas": {}, "source_mappings": {}},
    }
    for name, config in configs.items():
        (config_dir / name).write_text(json.dumps(config), encoding="utf-8")
    return config_dir


class EmptyStackedWidget:
    def count(self):
        return 0


class DummyMainWindow:
    """测试夹具：对齐 Tab 实际使用的 MainWindow 公共面。

    P2 修复：生产代码演进后 SourceTab/ConfigEditorTab 引用了
    _current_label / _reset_in_progress / _daemon_running_in_config / app_root，
    夹具此前未跟进，导致 4 条测试在构造期 AttributeError（未测到真实断言）。
    """

    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.root_path = config_dir.parent if config_dir is not None else None
        self.app_root = config_dir.parent if config_dir is not None else None
        self.stackedWidget = EmptyStackedWidget()
        self._tabs = {}
        # 守卫状态（与 MainWindow 默认态一致）
        self.current_profile = "mcp"
        self._reset_in_progress = False
        self._reset_mode = None
        self._reset_sources = []

    def _current_label(self) -> str:
        return "MCP权威源（测试）"

    def _daemon_running_in_config(self, config_dir) -> bool:
        return False


def test_main_window_expands_navigation_without_animation(
    app, monkeypatch, tmp_path
):
    calls = []

    monkeypatch.setattr(
        NavigationInterface,
        "expand",
        lambda self, useAni=True: calls.append(useAni),
    )
    monkeypatch.setattr(MainWindow, "_create_tab", lambda self, row: qt_widgets.QWidget())
    monkeypatch.setattr(MainWindow, "_integrate_log_panel", lambda self: None)
    monkeypatch.setattr(MainWindow, "_install_log_handler", lambda self: None)

    window = MainWindow(object(), tmp_path)

    assert calls == [False]
    window.close()
    app.processEvents()


def test_quality_tab_keeps_details_in_four_column_table(app):
    tab = QualityTab(DummyMainWindow(None))

    assert tab.check_table.columnCount() == 4
    assert tab.check_table.horizontalHeaderItem(3).text() == "详情"
    assert not hasattr(tab, "detail_text")


def test_source_tab_uses_transparent_fluent_scroll_area(
    app, source_config, monkeypatch
):
    transparent_calls = []
    original_enable = ScrollArea.enableTransparentBackground

    def enable_transparent_background(scroll_area):
        transparent_calls.append(scroll_area)
        original_enable(scroll_area)

    monkeypatch.setattr(
        ScrollArea,
        "enableTransparentBackground",
        enable_transparent_background,
    )

    tab = SourceTab(DummyMainWindow(source_config))

    assert isinstance(tab.scroll_area, ScrollArea)
    assert transparent_calls == [tab.scroll_area]


def test_source_tab_dark_scroll_content_uses_dark_background_and_white_text(
    app, source_config
):
    original_theme = qconfig.theme
    tab = None
    try:
        setTheme(Theme.DARK)
        tab = SourceTab(DummyMainWindow(source_config))
        tab.show()
        app.processEvents()

        assert tab.scroll_content.objectName() == "sourceScrollContent"
        assert tab.scroll_area.widget() is tab.scroll_content
        assert tab.scroll_content.palette().color(QPalette.ColorRole.Window).name() == "#0d1117"
        assert tab.scroll_content.palette().color(QPalette.ColorRole.WindowText).name() == "#e6edf3"
        # P2b：皮肤 PageHeader 引入自带样式标签后，findChild(QLabel) 会先命中
        # eyebrow 标签（自定义 #6e7681）。断言目标改为「无自定义样式且非 fluent
        # 卡片头（objectName=headerLabel）」的普通标签（如"注：…"note 标签），
        # 其文字色由 #sourceScrollContent 的 QLabel 规则给到 #e6edf3。
        plain_labels = [
            l for l in tab.scroll_content.findChildren(QLabel)
            if not l.styleSheet() and l.objectName() != "headerLabel"
        ]
        assert plain_labels, "scroll_content 内应存在无自定义样式的普通标签"
        assert plain_labels[0].palette().color(
            QPalette.ColorRole.WindowText
        ).name() == "#e6edf3"

        rendered = tab.scroll_content.grab().toImage()
        background = rendered.pixelColor(rendered.width() - 2, rendered.height() - 2)
        assert background.name() == "#0d1117"
    finally:
        if tab is not None:
            tab.close()
            app.processEvents()
        setTheme(original_theme)


@pytest.mark.parametrize(
    ("page_name", "scroll_name", "content_name", "object_name"),
    [
        ("sources_page", "sources_scroll", "sources_scroll_content", "sourcesScrollContent"),
        ("tasks_page", "tasks_scroll", "tasks_scroll_content", "tasksScrollContent"),
        # P2c：alignment 页已从 GUI 摘除（字段对齐规则改由 Schema 校验 +
        # ConfigLint 保证，_build_alignment_page 为死代码，周一解冻后另行
        # 清理——O2 裁定单列），此处移除对应参数项。
    ],
)
def test_config_editor_pages_use_scoped_dark_scroll_panels(
    app,
    config_editor_config,
    monkeypatch,
    page_name,
    scroll_name,
    content_name,
    object_name,
):
    transparent_calls = []
    original_enable = ScrollArea.enableTransparentBackground

    def enable_transparent_background(scroll_area):
        transparent_calls.append(scroll_area)
        original_enable(scroll_area)

    monkeypatch.setattr(
        ScrollArea,
        "enableTransparentBackground",
        enable_transparent_background,
    )

    tab = ConfigEditorTab(DummyMainWindow(config_editor_config))
    page = getattr(tab, page_name)
    scroll = getattr(tab, scroll_name)
    content = getattr(tab, content_name)

    assert isinstance(scroll, ScrollArea)
    assert scroll in transparent_calls
    assert content.objectName() == object_name
    style = page.styleSheet()
    assert f"#{object_name}" in style
    assert "#0d1117" in style
    assert "#e6edf3" in style


def test_config_editor_task_frame_has_scoped_object_name(app, config_editor_config):
    tab = ConfigEditorTab(DummyMainWindow(config_editor_config))

    assert len(tab.task_widgets) == 1
    task_frame = tab.task_widgets[0][2]
    assert task_frame.objectName() == "taskCard"


def test_save_tasks_preserves_source_priority_and_passthrough_fields(
    app, config_editor_config, monkeypatch
):
    tab = ConfigEditorTab(DummyMainWindow(config_editor_config))
    _task, widgets, _frame = tab.task_widgets[0]
    widgets["max_workers"].setValue(9)
    monkeypatch.setattr(qt_widgets.QMessageBox, "information", lambda *args: None)

    tab._save_tasks_config()

    saved = json.loads(
        (config_editor_config / "collector_tasks.json").read_text(encoding="utf-8")
    )["tasks"][0]
    assert saved["source"] == "xtquant"
    assert saved["source_priority"] == ["xtquant", "tushare"]
    assert saved["passthrough_option"] == {"keep": True}
    assert saved["end_date"] == "2026-07-24"
    assert saved["max_workers"] == 9


@pytest.fixture
def backtest_tab(app, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return BacktestTab(DummyMainWindow(config_dir))


def test_backtest_progress_logs_once_and_updates_status(backtest_tab, caplog):
    message = "加载策略: demo.py"

    with caplog.at_level(logging.INFO, logger="quantstudio.gui.tabs.backtest_tab"):
        backtest_tab._on_progress(message)

    matching = [record for record in caplog.records if record.getMessage() == message]
    assert len(matching) == 1
    assert backtest_tab.status_label.text() == message
    assert not hasattr(backtest_tab, "log_text")


def test_backtest_finished_does_not_repeat_completion_log(
    backtest_tab, caplog, monkeypatch
):
    class StubBacktestResultWindow:
        def __init__(self, output_dir, root_path):
            self.output_dir = output_dir
            self.root_path = root_path

        def show(self):
            pass

        def export_report_images(self):
            # P3：_on_finished 内 QTimer.singleShot(0, ...export_report_images)
            # 会立即求值该属性；缺失会抛 AttributeError 落入 except 分支，
            # 调用真实 QMessageBox.warning 模态弹窗 → offscreen 永久阻塞。
            pass

    monkeypatch.setattr(
        "quantstudio.gui.backtest_result_window.BacktestResultWindow",
        StubBacktestResultWindow,
    )
    # P3 兜底：即便出现其它异常路径，也不允许真实模态弹窗挂死测试
    monkeypatch.setattr(qt_widgets.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(qt_widgets.QMessageBox, "critical", lambda *a, **k: None)
    message = "回测完成: output/run-1"

    with caplog.at_level(logging.INFO, logger="quantstudio.gui.tabs.backtest_tab"):
        backtest_tab._on_progress(message)
        backtest_tab._on_finished({"output_dir": "output/run-1"})

    matching = [record for record in caplog.records if record.getMessage() == message]
    assert len(matching) == 1


def test_backtest_error_logs_once_and_shows_message_box(
    backtest_tab, caplog, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "critical",
        lambda *args: calls.append(args),
    )

    with caplog.at_level(logging.ERROR, logger="quantstudio.gui.tabs.backtest_tab"):
        backtest_tab._on_error("engine failed")

    matching = [
        record for record in caplog.records if "engine failed" in record.getMessage()
    ]
    assert len(matching) == 1
    assert len(calls) == 1
