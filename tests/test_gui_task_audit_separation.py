from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QCoreApplication

import quantstudio.gui.workers as workers
from quantstudio.gui.tabs.task_tab import TaskTab
from quantstudio.pipeline.daemon import ResidentCollector


@pytest.fixture(scope="module", autouse=True)
def qcore_app():
    return QCoreApplication.instance() or QCoreApplication([])


class FakeCollector:
    def __init__(self, *, task_ok=True, audit_ok=True, audit_error=None):
        self.task_ok = task_ok
        self.audit_ok = audit_ok
        self.audit_error = audit_error
        self.execute_calls = []
        self.audit_calls = 0
        self.closed = False

    def resolve_source_chain(self, task):
        return ["mcp"]

    def execute_task(self, task, mode=None, run_quality_audit=True):
        self.execute_calls.append((task, mode, run_quality_audit))
        return self.task_ok

    def _run_full_quality_audit(self):
        self.audit_calls += 1
        if self.audit_error is not None:
            raise self.audit_error
        return self.audit_ok

    def close(self):
        self.closed = True


def _run_locked_worker(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(
        ResidentCollector, "from_configs", classmethod(lambda cls, *args: fake)
    )
    monkeypatch.setattr(workers, "_collector_run_lock_path", lambda: tmp_path / "collector.lock")
    worker = workers.LockedTaskWorker(
        task={"name": "mcp_etf_daily"}, config_dir=tmp_path,
        mode="full_range", run_quality_audit=True,
    )
    successes, errors, progress = [], [], []
    worker.finished_ok.connect(successes.append)
    worker.finished_err.connect(errors.append)
    worker.progress.connect(progress.append)
    worker.run()
    return successes, errors, progress


class DummyLabel:
    def __init__(self):
        self.text = None

    def setText(self, text):
        self.text = text


class DummyTooltip:
    def __init__(self):
        self.content = None

    def setContent(self, text):
        self.content = text


class DummyTab:
    def __init__(self):
        self.status_label = DummyLabel()
        self._running_tasks = {"mcp_etf_daily": "full_range"}
        self._task_queue = None
        self._collect_tooltip = DummyTooltip()
        self.statuses = []
        self.refresh_calls = 0

    def _set_task_status(self, name, status):
        self.statuses.append((name, status))

    def _set_status_text(self, msg):
        self.status_label.setText(msg)
        self.status_label.tooltip = msg

    def refresh(self):
        self.refresh_calls += 1


def test_task_tab_displays_success_with_audit_warning_instead_of_failure():
    tab = DummyTab()
    result = {
        "task_ok": True,
        "quality_audit_ran": True,
        "quality_audit_ok": False,
    }

    TaskTab._on_task_done(tab, {"name": "mcp_etf_daily"}, True, result)

    assert tab.status_label.text == "\u26a0\ufe0f mcp_etf_daily \u62c9\u53d6\u6210\u529f\uff1b\u5168\u5e93\u8d28\u91cf\u5ba1\u8ba1\u672a\u901a\u8fc7"
    assert tab.statuses == [("mcp_etf_daily", "\u6210\u529f\uff08\u5ba1\u8ba1\u544a\u8b66\uff09")]
    assert tab.refresh_calls == 1
    assert tab._collect_tooltip is None


class RunAllCollector(FakeCollector):
    def __init__(self):
        super().__init__(task_ok=True, audit_ok=True)
        self._last_qfq_cycle_summary = None
        self._last_task_qfq_managed = False
        self._last_task_watermark_candidate_created = False
        self._last_task_actual_source = None

    def execute_task(self, task, mode=None, run_quality_audit=True):
        self.execute_calls.append((task, mode, run_quality_audit))
        self._last_task_qfq_managed = True
        self._last_task_watermark_candidate_created = True
        self._last_task_actual_source = "mcp"
        if task["name"] == "held_price":
            self._last_qfq_cycle_summary = SimpleNamespace(
                status="finalized_held", error=None,
                watermarks_committed=0, watermarks_held=1)
        else:
            self._last_qfq_cycle_summary = SimpleNamespace(
                status="finalized", error=None,
                watermarks_committed=1, watermarks_held=0)
        return True


def test_qfq_warning_classifier_covers_hold_missing_cycle_and_terminal_gap():
    assert TaskTab._qfq_warning_from_result({
        "ok": True, "qfq_managed": True,
        "qfq_cycle": {"status": "finalized_held", "watermarks_held": 1},
    })[0] is True
    assert TaskTab._qfq_warning_from_result({
        "ok": True, "qfq_managed": True, "qfq_cycle": None,
    }) == (True, "missing_qfq_cycle_result")
    assert TaskTab._qfq_warning_from_result({
        "ok": True, "qfq_managed": True, "watermark_candidate_created": True,
        "qfq_cycle": {"status": "finalized", "watermarks_committed": 0,
                      "watermarks_held": 0},
    }) == (True, "watermark_candidate_without_terminal_intent")
    # Empty/no-new-data runs produce no candidate and are not false positives.
    assert TaskTab._qfq_warning_from_result({
        "ok": True, "qfq_managed": True, "watermark_candidate_created": False,
        "qfq_cycle": {"status": "finalized", "watermarks_committed": 0,
                      "watermarks_held": 0},
    }) == (False, None)


class DummyButton:
    def __init__(self):
        self.enabled = None
        self.text = None

    def setEnabled(self, value):
        self.enabled = value

    def setText(self, value):
        self.text = value


class DummyRunAllTab:
    def __init__(self):
        self.tasks = [{"name": "ok_price"}, {"name": "held_price"}]
        self._running_tasks = {"ok_price": "incremental", "held_price": "incremental"}
        self.run_all_btn = DummyButton()
        self.run_all_full_btn = DummyButton()
        self._run_all_active_mode = "incremental"
        self.status_label = DummyLabel()
        self._collect_tooltip = DummyTooltip()
        self.statuses = []
        self.refresh_calls = 0

    def _set_task_status(self, name, status):
        self.statuses.append((name, status))

    def _set_status_text(self, msg):
        self.status_label.setText(msg)
        self.status_label.tooltip = msg

    def _reset_run_all_buttons(self):
        self.run_all_btn.setText("▶ 全部执行（增量）")
        self.run_all_btn.setEnabled(True)
        self.run_all_full_btn.setText("▶▶ 全部执行（全量）")
        self.run_all_full_btn.setEnabled(True)
        self._run_all_active_mode = None

    def refresh(self):
        self.refresh_calls += 1


def test_run_all_tab_surfaces_held_watermark_task():
    tab = DummyRunAllTab()
    result = {
        "ok_count": 2, "total": 2, "quality_audit_ok": True,
        "results": [
            {"name": "ok_price", "ok": True, "qfq_managed": True,
             "watermark_candidate_created": True,
             "qfq_cycle": {"status": "finalized", "watermarks_committed": 1,
                           "watermarks_held": 0}},
            {"name": "held_price", "ok": True, "qfq_managed": True,
             "watermark_candidate_created": True,
             "qfq_cycle": {"status": "finalized_held", "watermarks_committed": 0,
                           "watermarks_held": 1}},
        ],
    }

    TaskTab._on_run_all_done(tab, result)

    assert "held_price" in tab.status_label.text
    assert ("held_price", "\u6210\u529f\uff08\u6c34\u4f4d\u95e8\u63a7\u544a\u8b66\uff09") in tab.statuses
    assert tab.refresh_calls == 1
    assert tab._collect_tooltip is None


def test_run_all_done_restores_both_buttons():
    tab = DummyRunAllTab()
    result = {"ok_count": 2, "total": 2, "quality_audit_ok": True,
              "results": [
                  {"name": "ok_price", "ok": True},
                  {"name": "held_price", "ok": True},
              ]}

    TaskTab._on_run_all_done(tab, result)

    assert tab.run_all_btn.text == "\u25b6 \u5168\u90e8\u6267\u884c\uff08\u589e\u91cf\uff09"
    assert tab.run_all_btn.enabled is True
    assert tab.run_all_full_btn.text == "\u25b6\u25b6 \u5168\u90e8\u6267\u884c\uff08\u5168\u91cf\uff09"
    assert tab.run_all_full_btn.enabled is True
    assert tab._run_all_active_mode is None


def test_run_all_done_full_mode_labels_status():
    tab = DummyRunAllTab()
    tab._run_all_active_mode = "full_range"
    result = {"ok_count": 1, "total": 1, "quality_audit_ok": True,
              "results": [{"name": "ok_price", "ok": True}]}

    TaskTab._on_run_all_done(tab, result)

    assert "\u5168\u91cf" in tab.status_label.text
    assert "1/1" in tab.status_label.text
    assert "\u589e\u91cf" not in tab.status_label.text
    assert tab._run_all_active_mode is None


def test_watermark_display_falls_back_to_latest_actual_source():
    import pandas as pd
    wms = pd.DataFrame([
        {"source": "xtquant", "table_name": "etf_daily", "freq": "daily",
         "last_date": 1785859200000, "updated_at": "2026-08-05 10:00:00"},
        {"source": "tushare", "table_name": "etf_daily", "freq": "daily",
         "last_date": 1785945600000, "updated_at": "2026-08-06 10:00:00"},
    ])
    date_text, source = TaskTab._resolve_watermark_cached(
        SimpleNamespace(), wms, "mcp", "etf_daily", "daily")
    assert source == "tushare"
    assert date_text != "\u65e0"
    # An exact configured-source watermark always wins over a newer fallback row.
    date_text, source = TaskTab._resolve_watermark_cached(
        SimpleNamespace(), wms, "xtquant", "etf_daily", "daily")
    assert source == "xtquant"


@pytest.mark.parametrize("cycle,candidate", [
    ({"status": "finalized_held", "watermarks_committed": 0,
      "watermarks_held": 1, "error": None}, True),
    ({"status": "finalized", "watermarks_committed": 0,
      "watermarks_held": 0, "error": None}, True),
])
def test_single_task_tab_displays_qfq_watermark_warning(cycle, candidate):
    tab = DummyTab()
    result = {
        "task_ok": True, "quality_audit_ran": False,
        "quality_audit_ok": None, "qfq_managed": True,
        "watermark_candidate_created": candidate, "qfq_cycle": cycle,
    }

    TaskTab._on_task_done(tab, {"name": "mcp_etf_daily"}, True, result)

    assert "QFQ" in tab.status_label.text
    assert tab.statuses == [
        ("mcp_etf_daily", "\u6210\u529f\uff08\u6c34\u4f4d\u95e8\u63a7\u544a\u8b66\uff09")]
    assert tab.refresh_calls == 1
