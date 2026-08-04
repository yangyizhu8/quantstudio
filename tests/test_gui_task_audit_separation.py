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


def test_task_success_is_not_converted_to_failure_by_unrelated_full_audit(monkeypatch, tmp_path):
    fake = FakeCollector(task_ok=True, audit_ok=False)

    successes, errors, progress = _run_locked_worker(monkeypatch, tmp_path, fake)

    assert errors == []
    assert len(successes) == 1
    assert successes[0]["task_ok"] is True
    assert successes[0]["quality_audit_ran"] is True
    assert successes[0]["quality_audit_ok"] is False
    assert fake.execute_calls[0][2] is False
    assert fake.audit_calls == 1
    assert any("\u5168\u5e93" in msg for msg in progress)
    assert fake.closed is True


def test_final_signal_is_emitted_only_after_collector_close(monkeypatch, tmp_path):
    fake = FakeCollector(task_ok=True, audit_ok=False)
    monkeypatch.setattr(
        ResidentCollector, "from_configs", classmethod(lambda cls, *args: fake)
    )
    monkeypatch.setattr(workers, "_collector_run_lock_path", lambda: tmp_path / "collector.lock")
    worker = workers.LockedTaskWorker(
        task={"name": "mcp_etf_daily"}, config_dir=tmp_path,
        mode="full_range", run_quality_audit=True,
    )
    closed_at_signal = []
    worker.finished_ok.connect(lambda _: closed_at_signal.append(fake.closed))

    worker.run()

    assert closed_at_signal == [True]


def test_real_task_failure_remains_failure_even_when_audit_also_fails(monkeypatch, tmp_path):
    fake = FakeCollector(task_ok=False, audit_ok=False)

    successes, errors, _ = _run_locked_worker(monkeypatch, tmp_path, fake)

    assert successes == []
    assert len(errors) == 1
    assert "\u4efb\u52a1\u62c9\u53d6\u5931\u8d25" in errors[0]
    assert "\u5168\u5e93\u8d28\u91cf\u5ba1\u8ba1\u540c\u65f6\u672a\u901a\u8fc7" in errors[0]
    assert fake.audit_calls == 1


def test_audit_exception_is_reported_as_warning_payload_not_task_failure(monkeypatch, tmp_path):
    fake = FakeCollector(task_ok=True, audit_error=RuntimeError("audit boom"))

    successes, errors, _ = _run_locked_worker(monkeypatch, tmp_path, fake)

    assert errors == []
    assert successes[0]["quality_audit_ok"] is False
    assert successes[0]["quality_audit_error"] == "RuntimeError: audit boom"


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
