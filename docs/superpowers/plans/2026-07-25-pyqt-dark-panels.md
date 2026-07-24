# PyQt Dark Panels and Shared Backtest Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the requested PyQt panels follow the Fluent dark theme, remove the redundant quality/backtest detail panels, route all backtest messages to the shared bottom log, and start with the left navigation expanded.

**Architecture:** Keep `setTheme(Theme.DARK)` as the only application-wide theme switch. Use QFluentWidgets `ScrollArea` plus narrowly scoped page styles for affected native Qt containers, keep `BacktestWorker` signals intact, and bridge those signals into the existing root-logger-to-GUI pipeline from `BacktestTab`.

**Tech Stack:** Python 3.9+, PyQt6, PyQt6-Fluent-Widgets 1.11+, pytest, Python logging

---

## File Structure

- Modify `quantstudio/gui/tabs/source_tab.py`: replace the native scroll area with the Fluent scroll area and expose it for verification.
- Modify `quantstudio/gui/tabs/config_editor_tab.py`: add one scoped dark-panel stylesheet and apply it to the data-source, task, and alignment scroll contents.
- Modify `quantstudio/gui/tabs/quality_tab.py`: remove the lower detail panel while preserving the table detail column.
- Modify `quantstudio/gui/tabs/backtest_tab.py`: remove the page-local log widget and forward worker messages through the module logger without duplicates.
- Modify `quantstudio/gui/main_window.py`: explicitly expand the public navigation interface after registering navigation items.
- Create `tests/test_gui_dark_panels.py`: provide offscreen Qt fixtures and focused regression tests for all requested UI behavior.

### Task 1: Add Offscreen GUI Test Infrastructure and Source Scroll Test

**Files:**
- Create: `tests/test_gui_dark_panels.py`
- Modify: `quantstudio/gui/tabs/source_tab.py:7-10,26-74`

- [ ] **Step 1: Write the failing source-scroll test and shared fixtures**

Create `tests/test_gui_dark_panels.py` with:

```python
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import ScrollArea


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class DummyMainWindow:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.db_helper = None

    @property
    def root_path(self):
        return self.config_dir.parent

    def hold_worker(self, worker):
        pass


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def config_dir(tmp_path):
    path = tmp_path / "config"
    path.mkdir()
    _write_json(
        path / "sources_config.json",
        {
            "sources": {
                "tushare": {
                    "enabled": False,
                    "token": "${TUSHARE_TOKEN}",
                }
            }
        },
    )
    _write_json(
        path / "data_config.json",
        {
            "type": "duckdb",
            "path": "data/quantstudio.db",
            "quarantine": {
                "retention_days": 30,
                "auto_archive": True,
            },
        },
    )
    _write_json(
        path / "collector_tasks.json",
        {
            "daemon_schedule": {
                "daily_time": "17:00",
                "check_interval_sec": 300,
            },
            "tasks": [],
        },
    )
    _write_json(
        path / "alignment_rules.json",
        {"schemas": {}, "source_mappings": {}},
    )
    return path


def test_source_tab_uses_transparent_fluent_scroll_area(qapp, config_dir):
    from quantstudio.gui.tabs.source_tab import SourceTab

    tab = SourceTab(DummyMainWindow(config_dir))

    assert isinstance(tab.scroll_area, ScrollArea)
    assert "background: transparent" in tab.scroll_area.styleSheet()
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py::test_source_tab_uses_transparent_fluent_scroll_area -v
```

Expected: FAIL because `SourceTab` has no `scroll_area` member and still constructs native `QScrollArea`.

- [ ] **Step 3: Replace the native scroll area with the Fluent implementation**

In `quantstudio/gui/tabs/source_tab.py`, change the imports to:

```python
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QMessageBox)
from qfluentwidgets import (
    GroupHeaderCardWidget, CheckBox, LineEdit, PushButton, ScrollArea)
```

Change the start and end of `_setup_ui()` to:

```python
def _setup_ui(self):
    self.scroll_area = ScrollArea()
    self.scroll_area.setWidgetResizable(True)
    inner = QWidget()
    self.inner_layout = QVBoxLayout(inner)

    # existing source card construction remains unchanged

    self.scroll_area.setWidget(inner)
    self.scroll_area.enableTransparentBackground()
    outer = QVBoxLayout(self)
    outer.addWidget(self.scroll_area)
```

Do not change source definitions, credential widgets, load logic, or save logic.

- [ ] **Step 4: Run the source-scroll test**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py::test_source_tab_uses_transparent_fluent_scroll_area -v
```

Expected: PASS.

- [ ] **Step 5: Commit the source-scroll change**

```bash
git add tests/test_gui_dark_panels.py quantstudio/gui/tabs/source_tab.py
git commit -m "fix: apply Fluent dark scroll area to sources"
```

### Task 2: Apply Scoped Dark Styling to Configuration Pages

**Files:**
- Modify: `tests/test_gui_dark_panels.py`
- Modify: `quantstudio/gui/tabs/config_editor_tab.py:28-34,248-321,336-423,425-533,593-656`

- [ ] **Step 1: Write failing tests for the three affected configuration pages**

Append to `tests/test_gui_dark_panels.py`:

```python
@pytest.mark.parametrize(
    ("page_attr", "scroll_attr", "content_attr", "object_name"),
    [
        (
            "sources_page",
            "sources_scroll",
            "sources_scroll_content",
            "sourcesScrollContent",
        ),
        (
            "tasks_page",
            "tasks_scroll",
            "tasks_scroll_content",
            "tasksScrollContent",
        ),
        (
            "alignment_page",
            "alignment_scroll",
            "alignment_scroll_content",
            "alignmentScrollContent",
        ),
    ],
)
def test_config_pages_use_scoped_dark_scroll_contents(
    qapp,
    config_dir,
    page_attr,
    scroll_attr,
    content_attr,
    object_name,
):
    from quantstudio.gui.tabs.config_editor_tab import ConfigEditorTab

    tab = ConfigEditorTab(DummyMainWindow(config_dir))
    page = getattr(tab, page_attr)
    scroll = getattr(tab, scroll_attr)
    content = getattr(tab, content_attr)

    assert content.objectName() == object_name
    assert "background: transparent" in scroll.styleSheet()
    assert "background-color: #202020" in page.styleSheet()
    assert "color: #ffffff" in page.styleSheet()


def test_task_cards_have_scoped_dark_object_name(qapp, config_dir):
    from quantstudio.gui.tabs.config_editor_tab import ConfigEditorTab

    _write_json(
        config_dir / "collector_tasks.json",
        {
            "daemon_schedule": {
                "daily_time": "17:00",
                "check_interval_sec": 300,
            },
            "tasks": [
                {
                    "name": "stock daily",
                    "table": "stock_daily",
                    "freq": "daily",
                    "enabled": True,
                    "source": "xtquant",
                    "start_date": "2018-01-01",
                    "codes": ["ALL"],
                    "max_workers": 4,
                    "rate_limit": {
                        "calls_per_min": 60,
                        "wait_on_429": True,
                    },
                }
            ],
        },
    )

    tab = ConfigEditorTab(DummyMainWindow(config_dir))

    assert tab.task_widgets
    task_frame = tab.task_widgets[0][2]
    assert task_frame.objectName() == "taskCard"
```

- [ ] **Step 2: Run the configuration tests to verify they fail**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py -k "config_pages or task_cards" -v
```

Expected: FAIL because the scroll/content members and object names do not exist, the pages have no scoped dark stylesheet, and `task_widgets` currently stores two-tuples.

- [ ] **Step 3: Add one narrowly scoped stylesheet constant**

After `logger = logging.getLogger(__name__)` in `quantstudio/gui/tabs/config_editor_tab.py`, add:

```python
_DARK_CONFIG_STYLE = """
QWidget#sourcesScrollContent,
QWidget#tasksScrollContent,
QWidget#alignmentScrollContent {
    background-color: #202020;
    color: #ffffff;
}
QWidget#sourcesScrollContent QLabel,
QWidget#tasksScrollContent QLabel,
QWidget#alignmentScrollContent QLabel {
    color: #ffffff;
}
QFrame#taskCard {
    background-color: #292929;
    color: #ffffff;
    border: 1px solid #454545;
    border-radius: 6px;
}
QFrame#taskCard QLabel {
    color: #ffffff;
}
"""
```

This stylesheet targets only the three affected content roots and task cards. Do not add broad `QWidget`, `QLineEdit`, `QComboBox`, or `QTableWidget` selectors that would override Fluent interaction states.

- [ ] **Step 4: Apply named members and transparent backgrounds on the source page**

In `_build_sources_page()`, replace the local scroll setup with:

```python
self.sources_scroll = ScrollArea()
self.sources_scroll.setWidgetResizable(True)
self.sources_scroll_content = QWidget()
self.sources_scroll_content.setObjectName("sourcesScrollContent")
self.sources_form = QGridLayout(self.sources_scroll_content)
```

Replace the final scroll setup with:

```python
self.sources_scroll.setWidget(self.sources_scroll_content)
self.sources_scroll.enableTransparentBackground()
page.setStyleSheet(_DARK_CONFIG_STYLE)
layout.addWidget(self.sources_scroll)
```

Keep credential creation and save behavior unchanged.

- [ ] **Step 5: Apply named members and task-card styling on the task page**

In `_build_tasks_page()`, replace the local scroll setup with:

```python
self.tasks_scroll = ScrollArea()
self.tasks_scroll.setWidgetResizable(True)
self.tasks_scroll_content = QWidget()
self.tasks_scroll_content.setObjectName("tasksScrollContent")
scroll_layout = QVBoxLayout(self.tasks_scroll_content)
```

Replace the final scroll setup with:

```python
self.tasks_scroll.setWidget(self.tasks_scroll_content)
self.tasks_scroll.enableTransparentBackground()
page.setStyleSheet(_DARK_CONFIG_STYLE)
layout.addWidget(self.tasks_scroll, 1)
```

At the start of `_build_task_card()`, add the object name:

```python
frame = QFrame()
frame.setObjectName("taskCard")
frame.setFrameShape(QFrame.Shape.Box)
```

Change the append at the end of `_build_task_card()` to retain the frame for focused testing:

```python
self.task_widgets.append((task, widgets, frame))
return frame
```

Update `_save_tasks_config()` to preserve its existing behavior with the new tuple shape:

```python
for task, widgets, _frame in self.task_widgets:
```

No task configuration fields or source-priority behavior may change.

- [ ] **Step 6: Apply named members and transparent backgrounds on the alignment page**

In `_build_alignment_page()`, replace the local scroll setup with:

```python
self.alignment_scroll = ScrollArea()
self.alignment_scroll.setWidgetResizable(True)
self.alignment_scroll_content = QWidget()
self.alignment_scroll_content.setObjectName("alignmentScrollContent")
scroll_layout = QVBoxLayout(self.alignment_scroll_content)
```

Promote the tables to members while leaving their content unchanged:

```python
self.schema_table = TableWidget()
```

and:

```python
self.mapping_table = TableWidget()
```

Replace all local `schema_table` and `map_table` references with those members. Replace the final scroll setup with:

```python
self.alignment_scroll.setWidget(self.alignment_scroll_content)
self.alignment_scroll.enableTransparentBackground()
page.setStyleSheet(_DARK_CONFIG_STYLE)
layout.addWidget(self.alignment_scroll, 1)
```

- [ ] **Step 7: Run the configuration-page tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py -k "config_pages or task_cards" -v
```

Expected: PASS.

- [ ] **Step 8: Run configuration behavior regression tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_filter_stock_by_status.py tests/test_gui_dark_panels.py -v
```

Expected: PASS. If an existing unrelated test fails, record the exact pre-existing failure and do not modify unrelated configuration behavior.

- [ ] **Step 9: Commit the configuration-page styling**

```bash
git add tests/test_gui_dark_panels.py quantstudio/gui/tabs/config_editor_tab.py
git commit -m "fix: apply scoped dark styles to config panels"
```

### Task 3: Remove the Quality Detail Panel

**Files:**
- Modify: `tests/test_gui_dark_panels.py`
- Modify: `quantstudio/gui/tabs/quality_tab.py:21-26,55-110`

- [ ] **Step 1: Write the failing quality-page structure test**

Append to `tests/test_gui_dark_panels.py`:

```python
def test_quality_tab_keeps_detail_column_without_detail_panel(qapp, config_dir):
    from quantstudio.gui.tabs.quality_tab import QualityTab

    tab = QualityTab(DummyMainWindow(config_dir))

    assert tab.check_table.columnCount() == 4
    assert tab.check_table.horizontalHeaderItem(3).text() == "详情"
    assert not hasattr(tab, "detail_text")
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py::test_quality_tab_keeps_detail_column_without_detail_panel -v
```

Expected: FAIL because `QualityTab.detail_text` is still created.

- [ ] **Step 3: Remove the lower detail panel and aggregation**

In `quantstudio/gui/tabs/quality_tab.py`, change the qfluentwidgets import to:

```python
from qfluentwidgets import TableWidget, PushButton, GroupHeaderCardWidget
```

Delete the `detail_group`, `detail_layout`, and `self.detail_text` block from `_setup_ui()`.

In `_run_all_checks()`, delete:

```python
all_detail = []
```

Delete:

```python
all_detail.append(f"[{num}] {name}\n{result}\n{detail}\n")
```

Delete:

```python
self.detail_text.setPlainText("\n".join(all_detail))
```

Keep the four-column table and this summary assignment unchanged:

```python
self.check_table.setItem(i, 3, QTableWidgetItem(detail.split("\n")[0][:80]))
```

- [ ] **Step 4: Run the quality-page test**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py::test_quality_tab_keeps_detail_column_without_detail_panel -v
```

Expected: PASS.

- [ ] **Step 5: Run the existing quality-audit tests**

Run:

```bash
python -m pytest tests/test_quality_audit.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit the quality-page simplification**

```bash
git add tests/test_gui_dark_panels.py quantstudio/gui/tabs/quality_tab.py
git commit -m "fix: remove redundant quality detail panel"
```

### Task 4: Route Backtest Messages to the Shared Logger

**Files:**
- Modify: `tests/test_gui_dark_panels.py`
- Modify: `quantstudio/gui/tabs/backtest_tab.py:8-14,32-123,134-220`

- [ ] **Step 1: Write failing progress and page-structure tests**

Append to `tests/test_gui_dark_panels.py`:

```python
import logging


def test_backtest_progress_uses_shared_logging_once(qapp, config_dir, caplog):
    from quantstudio.gui.tabs.backtest_tab import BacktestTab

    tab = BacktestTab(DummyMainWindow(config_dir))

    with caplog.at_level(logging.INFO, logger=tab.__module__):
        tab._on_progress("[3/10] 2026-01-05")

    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("[3/10] 2026-01-05") == 1
    assert tab.status_label.text() == "[3/10] 2026-01-05"
    assert not hasattr(tab, "log_text")
```

- [ ] **Step 2: Write failing completion and error de-duplication tests**

Append:

```python
def test_backtest_completion_is_not_logged_twice(
    qapp, config_dir, caplog, monkeypatch
):
    import quantstudio.gui.backtest_result_window as result_module
    from quantstudio.gui.tabs.backtest_tab import BacktestTab

    class FakeResultWindow:
        def __init__(self, *args):
            pass

        def show(self):
            pass

    monkeypatch.setattr(result_module, "BacktestResultWindow", FakeResultWindow)
    tab = BacktestTab(DummyMainWindow(config_dir))
    message = "回测完成: output/run-1"

    with caplog.at_level(logging.INFO, logger=tab.__module__):
        tab._on_progress(message)
        tab._on_finished({"output_dir": "output/run-1"})

    assert [record.getMessage() for record in caplog.records].count(message) == 1


def test_backtest_error_uses_shared_logging_once(
    qapp, config_dir, caplog, monkeypatch
):
    from PyQt6.QtWidgets import QMessageBox
    from quantstudio.gui.tabs.backtest_tab import BacktestTab

    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: None)
    tab = BacktestTab(DummyMainWindow(config_dir))

    with caplog.at_level(logging.ERROR, logger=tab.__module__):
        tab._on_error("ValueError: broken")

    matching = [
        record
        for record in caplog.records
        if "ValueError: broken" in record.getMessage()
    ]
    assert len(matching) == 1
```

- [ ] **Step 3: Run the backtest tests to verify they fail**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py -k "backtest" -v
```

Expected: FAIL because the local log widget still exists and `_on_progress()` does not use the Python logger.

- [ ] **Step 4: Remove the page-local log controls and stale imports**

In `quantstudio/gui/tabs/backtest_tab.py`, change the widget imports to:

```python
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QMessageBox)
from qfluentwidgets import (
    ComboBox, LineEdit, PushButton, ProgressBar,
    GroupHeaderCardWidget, DoubleSpinBox, SpinBox)
```

Delete the entire `# 5. 日志面板` block from `_setup_ui()`.

Delete the `_on_run()` log-clear block:

```python
# 清空日志
self.log_text.clear()
```

- [ ] **Step 5: Log each worker message exactly once**

Replace `_on_progress()` with:

```python
def _on_progress(self, msg):
    """将 Worker 状态转发到底部公共日志栏。"""
    logger.info("%s", msg)
    self.status_label.setText(msg[:80])
```

Keep `_on_day_progress()` log-free because `BacktestWorker` emits a matching `progress` signal for every day.

In `_on_finished()`, keep the button, progress, status, and result-window behavior, but delete:

```python
self.log_text.appendPlainText(f"\n✅ 回测完成，结果导出至: {output_dir}")
```

Do not add another completion logger call there; the Worker already emits `回测完成: ...` through `_on_progress()` immediately before `finished_ok`.

Replace `_on_error()` with:

```python
def _on_error(self, err):
    """回测出错。"""
    self.run_btn.setEnabled(True)
    self.stop_btn.setEnabled(False)
    self.progress_bar.setVisible(False)
    self.status_label.setText("❌ 回测失败")
    logger.error("回测失败: %s", err)
    QMessageBox.critical(self, "回测错误", err[:500])
```

- [ ] **Step 6: Run the focused backtest tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py -k "backtest" -v
```

Expected: PASS.

- [ ] **Step 7: Run existing backtest configuration tests**

Run:

```bash
python -m pytest tests/test_match_price_mode.py tests/test_engine_config.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit the shared-log routing**

```bash
git add tests/test_gui_dark_panels.py quantstudio/gui/tabs/backtest_tab.py
git commit -m "fix: route backtest progress to shared GUI log"
```

### Task 5: Expand the Left Navigation at Startup

**Files:**
- Modify: `tests/test_gui_dark_panels.py`
- Modify: `quantstudio/gui/main_window.py:69-78`

- [ ] **Step 1: Write the failing navigation initialization test**

Append to `tests/test_gui_dark_panels.py`:

```python
def test_main_window_requests_expanded_navigation_at_startup(
    qapp, tmp_path, monkeypatch
):
    from PyQt6.QtWidgets import QWidget
    from qfluentwidgets import NavigationInterface
    from quantstudio.gui.main_window import MainWindow

    calls = []
    original_expand = NavigationInterface.expand

    def record_expand(self, useAni=True):
        calls.append(useAni)
        return original_expand(self, useAni)

    monkeypatch.setattr(NavigationInterface, "expand", record_expand)
    monkeypatch.setattr(MainWindow, "_create_tab", lambda self, row: QWidget())
    monkeypatch.setattr(MainWindow, "_integrate_log_panel", lambda self: None)
    monkeypatch.setattr(MainWindow, "_install_log_handler", lambda self: None)

    window = MainWindow(db_helper=object(), config_dir=tmp_path)

    assert calls == [False]
    window.close()
```

The test proves startup explicitly requests expansion through the public API. Manual collapse remains supported because production code does not call `setCollapsible(False)`.

- [ ] **Step 2: Run the navigation test to verify it fails**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py::test_main_window_requests_expanded_navigation_at_startup -v
```

Expected: FAIL with `assert [] == [False]`.

- [ ] **Step 3: Expand navigation after all items are registered**

At the end of `MainWindow._setup_navigation()`, after connecting `currentChanged`, add:

```python
self.navigationInterface.expand(useAni=False)
```

Do not call `setCollapsible(False)`, persist state, or access `navigationInterface.panel`.

- [ ] **Step 4: Run the navigation test**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py::test_main_window_requests_expanded_navigation_at_startup -v
```

Expected: PASS.

- [ ] **Step 5: Commit the navigation default**

```bash
git add tests/test_gui_dark_panels.py quantstudio/gui/main_window.py
git commit -m "fix: expand Fluent navigation on startup"
```

### Task 6: Complete GUI Regression and Manual Verification

**Files:**
- Verify: `tests/test_gui_dark_panels.py`
- Verify: `quantstudio/gui/tabs/source_tab.py`
- Verify: `quantstudio/gui/tabs/config_editor_tab.py`
- Verify: `quantstudio/gui/tabs/quality_tab.py`
- Verify: `quantstudio/gui/tabs/backtest_tab.py`
- Verify: `quantstudio/gui/main_window.py`

- [ ] **Step 1: Scan for removed-widget references**

Run:

```bash
rg -n "detail_text|self\.log_text" quantstudio/gui/tabs/quality_tab.py quantstudio/gui/tabs/backtest_tab.py
```

Expected: no output.

- [ ] **Step 2: Run all focused GUI tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_dark_panels.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run targeted business regressions**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_quality_audit.py tests/test_match_price_mode.py tests/test_engine_config.py tests/test_filter_stock_by_status.py -v
```

Expected: all tests PASS.

- [ ] **Step 4: Run the full test suite**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Expected: all collected tests PASS. If failures are unrelated to the changed GUI modules, compare against the current dirty-worktree baseline and report exact failures rather than modifying unrelated files.

- [ ] **Step 5: Start the real application for visual verification**

Run:

```bash
QT_QPA_PLATFORM=windows python main_gui.py
```

Verify in the running application:

- The left navigation is expanded immediately after startup.
- Clicking the Fluent collapse control still collapses it.
- The standalone data-source page has no white scroll viewport and its text is readable.
- Configuration editor pages “数据源凭证”, “采集任务”, and “字段对齐规则” have dark content backgrounds and white text.
- Quality checks show only the table; the table still includes the “详情” column.
- The backtest page has no local “回测日志” card.
- Starting a backtest sends loading, daily progress, completion, and error messages only to the bottom shared log.

Close the application normally after verification.

- [ ] **Step 6: Review the final diff for scope**

Run:

```bash
git diff --check
git diff -- quantstudio/gui/tabs/source_tab.py quantstudio/gui/tabs/config_editor_tab.py quantstudio/gui/tabs/quality_tab.py quantstudio/gui/tabs/backtest_tab.py quantstudio/gui/main_window.py tests/test_gui_dark_panels.py
```

Expected: no whitespace errors; only the approved UI, logging, navigation, and focused test changes appear.

- [ ] **Step 7: Commit any final test-only corrections**

If Task 6 required test-only corrections, commit them separately:

```bash
git add tests/test_gui_dark_panels.py
git commit -m "test: cover PyQt dark panel regressions"
```

If no corrections were required, do not create an empty commit.
