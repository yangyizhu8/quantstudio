"""GUI 停止语义 v3.1 验收测试（协作式停止 L1/L2 + 续传游标）。

隔离纪律（派单条件③）：一切测试用临时目录，**绝不触碰 data/quantstudio.db**
与真实 data/task_resume/（游标目录经 QUANTSTUDIO_RESUME_DIR 重定向到 tmp_path）。

替身纪律（GUI P0 自递归教训）：被测代码一律**真实实现**——
ResidentCollector 的真实方法经 __new__ 构造宿主 + 注入协作者调用（不复制实现），
LockedTaskWorker / LockedRunAllWorker / TaskTab 均真实构造；仅替身外部依赖
（adapter / aligner / validator / writer / collector）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pandas as pd

qt_widgets = pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("qfluentwidgets")

QApplication = qt_widgets.QApplication

from quantstudio.gui.tabs.task_tab import TaskTab
from quantstudio.gui.workers import LockedRunAllWorker, LockedTaskWorker
from quantstudio.pipeline.daemon import ResidentCollector
from quantstudio.pipeline.task_resume import (
    TaskCancelled,
    TaskResumeCursor,
    UNIT_TRADE_DATE,
    resume_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_PROFILE_DIR = REPO_ROOT / "config" / "profiles" / "mcp_only"
PROBE_TABLE = "stock_basic"          # 非 _QFQ_PRICE_TABLES / 非 _STREAMING_TABLES 语义
WM = "2023-12-31"


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture(autouse=True)
def isolated_resume_dir(tmp_path, monkeypatch):
    """游标目录隔离到 tmp_path（绝不写真实 data/task_resume/）。"""
    d = tmp_path / "task_resume"
    monkeypatch.setenv("QUANTSTUDIO_RESUME_DIR", str(d))
    return d


def _shard(day: str, rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame({"trade_date": [day] * rows, "code": ["000001.SZ"] * rows})


class _StubAligner:
    schemas = {PROBE_TABLE: {"time_key": "time"}}

    def align(self, df, table, source, adj_factor_factor=None, adj_factor_df=None,
              freq="daily", **kwargs):
        return df, {"ok": True}


class _StubValidator:
    def validate(self, df, table, batch_id, source, expected_freq=None):
        return SimpleNamespace(passed_df=df, rejected_rows=[], fixed_count=0)


class _StubAudit:
    def __init__(self):
        self.records = []

    def record(self, *args, **kwargs):
        self.records.append((args, kwargs))


class _StubAdapter:
    def __init__(self, shards):
        self.shards = shards

    def fetch_table_streaming(self, table, start, end, freq="daily", codes=None):
        return {"source": "probe"}, iter(self.shards)

    def fetch_table(self, table, start, end, freq="daily", codes=None):
        return _shard(str(start)), {"source": "probe"}


def _streaming_host(cancel_check, processed, watermarks):
    """真实 ResidentCollector 宿主：__new__ + 注入协作者（方法体全部真实）。"""
    rc = ResidentCollector.__new__(ResidentCollector)
    rc._task_cancel_check = cancel_check
    rc._task_cancelled = False
    rc._task_resume = None
    rc._qfq_cycle_id = None
    rc.aligner = _StubAligner()
    rc.validator = _StubValidator()
    rc.batch_audit = _StubAudit()
    rc._qfq_snapshot_kwargs = lambda table, batch_id: {
        "adj_latest_map": {}, "adj_earliest_map": {}}
    rc._get_safe_watermark = lambda source, table, freq: WM
    rc._max_date = lambda df, table: str(df["trade_date"].iloc[-1])
    rc._failure_gate = lambda *a, **k: (True, 0.0, 0.05)
    rc._advance_or_defer_watermark = (
        lambda source, table, freq, new_wm, batch_id: watermarks.append(new_wm))

    def _stamp(res, table, batch_id, source, task=None, adj_latest_map=None):
        processed.append(str(res.passed_df["trade_date"].iloc[0]))
        return len(res.passed_df)

    rc._stamp_and_write = _stamp
    return rc


def _run_streaming(host, shards, start="2024-01-01", end="2024-01-10",
                   cancel_check=None):
    host._task_cancel_check = cancel_check
    task = {"name": "probe_task", "table": PROBE_TABLE, "freq": "daily",
            "mode": "full_range"}
    return host._run_with_source_streaming(
        task, "probe", "batch_probe", "2024-01-01T00:00:00",
        _StubAdapter(shards), PROBE_TABLE, "daily", None, start, end)


def test_v2_day_batch_cancel_fires_at_injected_boundary(isolated_resume_dir):
    """V2：注入「第 N 个日批后命中」→ 任务收口；已写日保留、水位不推进。"""
    processed, watermarks = [], []
    hit_after = 1
    state = {"n": 0}

    def cancel_check():
        # 每次边界调用自增：第 hit_after 个日批边界后命中（模拟用户中途点停止）
        state["n"] += 1
        return state["n"] >= hit_after

    host = _streaming_host(cancel_check, processed, watermarks)
    shards = [_shard("2024-01-01"), _shard("2024-01-02"), _shard("2024-01-03")]

    with pytest.raises(TaskCancelled):
        _run_streaming(host, shards, cancel_check=cancel_check)

    assert processed == ["2024-01-01"], "命中边界后不得继续处理后续日批"
    assert watermarks == [], "停止不得推进官方水位"
    cur = json.loads((resume_dir() / (host._task_resume.task_id + ".json")).read_text(encoding="utf-8"))
    assert cur["last_completed"] == {"type": UNIT_TRADE_DATE, "value": "2024-01-01"}


def test_v2_resume_starts_after_cursor(isolated_resume_dir):
    """V2+/V8（前半）：续传运行从游标 +1 起拉（跳过已完成日批）。"""
    processed, watermarks = [], []
    state = {"n": 0}

    def cancel_check():
        state["n"] += 1
        return state["n"] >= 1

    # 第一次：停止于第 1 个日批
    host1 = _streaming_host(cancel_check, processed, watermarks)
    with pytest.raises(TaskCancelled):
        _run_streaming(host1, [_shard("2024-01-01"), _shard("2024-01-02")],
                       cancel_check=cancel_check)
    assert processed == ["2024-01-01"]

    # 第二次：新宿主（同一窗口）→ 游标命中 → start 推进到 2024-01-02
    processed2, watermarks2 = [], []
    host2 = _streaming_host(lambda: False, processed2, watermarks2)
    effective_start = host2._open_day_resume(
        {"name": "probe_task", "mode": "full_range"}, "probe", PROBE_TABLE, "daily",
        "2024-01-01", "2024-01-10")
    assert effective_start == "2024-01-02", "续传必须跳过已完成的 2024-01-01"
    assert host2._task_resume.resumed_from["value"] == "2024-01-01"

    _run_streaming(host2, [_shard("2024-01-02")], cancel_check=lambda: False)
    assert processed2 == ["2024-01-02"]
    assert watermarks2 == ["2024-01-02"], "续传运行正常完成才推进水位"


def test_v5_backward_compat_without_cancel_check(isolated_resume_dir):
    """V5：cancel_check=None → 行为与现版一致（零游标、零边界抛错、零水位改变）。"""
    processed, watermarks = [], []
    host = _streaming_host(None, processed, watermarks)
    # 未下发取消谓词时不得创建游标；起点保持原样
    assert host._open_day_resume({"name": "t", "mode": "full_range"},
                                 "probe", PROBE_TABLE, "daily",
                                 "2024-01-01", "2024-01-10") == "2024-01-01"
    assert host._task_resume is None, "无取消谓词 = 一次性任务未启用 → 不得建游标"

    ok = _run_streaming(host, [_shard("2024-01-01"), _shard("2024-01-02")])
    assert ok is True
    assert processed == ["2024-01-01", "2024-01-02"], "无停止谓词时全部分片正常处理"
    assert watermarks == ["2024-01-02"]
    assert not any(resume_dir().glob("*.json")), "向后兼容：不产生游标文件"


def test_normal_completion_clears_cursor(isolated_resume_dir):
    """V9-①：任务自然成功完成 → 游标清除（官方水位已接管）。"""
    processed, watermarks = [], []
    host = _streaming_host(lambda: False, processed, watermarks)
    _run_streaming(host, [_shard("2024-01-01")], cancel_check=lambda: False)
    cursor = host._task_resume
    assert cursor.path.exists(), "边界处应已推进并落盘游标"
    cursor.clear(reason="completed")
    assert not cursor.path.exists()


def test_v9_cursor_invalidated_on_window_mismatch(isolated_resume_dir):
    """V9-②：窗口指纹不一致 → 判废（不使用旧游标、删除文件）。"""
    c1 = TaskResumeCursor("t", "incremental", "2024-01-01", "2024-02-01", WM)
    c1.advance(UNIT_TRADE_DATE, "2024-01-05")
    assert c1.path.exists()
    c2 = TaskResumeCursor("t", "incremental", "2024-03-01", "2024-04-01", WM)
    assert c2.try_resume(WM) is None
    assert not c1.path.exists(), "判废必须清除游标文件"


def test_v9_cursor_invalidated_when_watermark_moved(isolated_resume_dir):
    """V9-③：官方水位越过/偏离窗口 → 判废。"""
    c1 = TaskResumeCursor("t", "incremental", "2024-01-01", "2024-02-01", WM)
    c1.advance(UNIT_TRADE_DATE, "2024-01-05")
    # 水位已前进到游标之后 → 无需续传
    assert TaskResumeCursor("t", "incremental", "2024-01-01", "2024-02-01",
                            "2024-01-05").try_resume("2024-01-05") is None
    # 水位基线变化 → 判废
    c1.advance(UNIT_TRADE_DATE, "2024-01-05")
    assert TaskResumeCursor("t", "incremental", "2024-01-01", "2024-02-01",
                            "2024-02-20").try_resume("2024-02-20") is None


def test_v9_cursor_write_failure_degrades_to_v2(isolated_resume_dir, monkeypatch):
    """V9-④：游标写失败 → 静默降级 v2（全窗重拉，向安全侧失败）。"""
    c = TaskResumeCursor("t", "incremental", "2024-01-01", "2024-02-01", WM)
    monkeypatch.setattr("quantstudio.pipeline.task_resume.resume_dir",
                        lambda: Path("Z:/nonexistent/resume"))
    c.advance(UNIT_TRADE_DATE, "2024-01-05")     # 不得抛异常
    assert c.degraded is True, "写失败必须置降级位（后续不再尝试写）"
    assert c.try_resume(WM) is None, "降级后按无游标处理 = v2 全窗执行"


class _FakeCollector:
    """worker 层替身：仅替身外部依赖（collector 本身），worker 代码全真实。"""

    def __init__(self, *, stop_after=None, cancel_trigger=None):
        self.calls = []
        self.progress_cbs = []
        self.audit_calls = 0
        self.closed = False
        self.stop_after = stop_after
        self.cancel_trigger = cancel_trigger

    def resolve_source_chain(self, task):
        return ["mcp"]

    def execute_task(self, task, mode=None, run_quality_audit=True, cancel_check=None,
                     progress_cb=None):
        self.calls.append((task.get("name"), cancel_check))
        self.progress_cbs.append(progress_cb)
        if self.cancel_trigger is not None and self.cancel_trigger():
            raise TaskCancelled("stop requested at boundary")
        return True

    def _run_full_quality_audit(self):
        self.audit_calls += 1
        return True

    def close(self):
        self.closed = True


def _patch_collector(monkeypatch, fake):
    monkeypatch.setattr(ResidentCollector, "from_configs",
                        classmethod(lambda cls, *a: fake))


def test_v1_batch_stop_between_tasks(tmp_path, monkeypatch):
    """V1：L1 批间停止——当前任务完成后不再派发下一任务。"""
    import quantstudio.gui.workers as workers
    monkeypatch.setattr(workers, "_collector_run_lock_path",
                        lambda: tmp_path / "collector.lock")
    flag = {"stop": False}
    fake = _FakeCollector(cancel_trigger=lambda: flag["stop"])
    _patch_collector(monkeypatch, fake)

    tasks = [{"name": "t1"}, {"name": "t2"}, {"name": "t3"}]
    worker = LockedRunAllWorker(tasks=tasks, config_dir=tmp_path,
                                cancel_check=lambda: flag["stop"])
    results = []
    worker.finished_ok.connect(results.append)
    flag["stop"] = True          # 批跑开始前已置旗标（等价于第 1 个任务前命中）
    worker.run()

    assert results, "停止路径仍须回报结果（不得静默）"
    payload = results[0]
    assert payload["stopped"] is True and payload["cancelled"] is True
    assert fake.calls == [], "L1 命中：不得派发任何任务"
    assert fake.audit_calls == 0, "停止后不跑全库质量审计"


def test_v1_batch_stop_after_first_task(tmp_path, monkeypatch):
    """V1 变体：第 1 个任务完成后置旗标 → 第 2 个任务不派发。"""
    import quantstudio.gui.workers as workers
    monkeypatch.setattr(workers, "_collector_run_lock_path",
                        lambda: tmp_path / "collector.lock")
    flag = {"stop": False}
    fake = _FakeCollector(cancel_trigger=lambda: flag["stop"])
    _patch_collector(monkeypatch, fake)

    class _FakeAfterFirst(_FakeCollector):
        def execute_task(self, task, mode=None, run_quality_audit=True,
                         cancel_check=None, progress_cb=None):
            self.calls.append((task.get("name"), cancel_check))
            flag["stop"] = True          # 第 1 个任务完成 → 用户点停止
            return True

    fake2 = _FakeAfterFirst()
    _patch_collector(monkeypatch, fake2)
    worker = LockedRunAllWorker(tasks=[{"name": "t1"}, {"name": "t2"}, {"name": "t3"}],
                                config_dir=tmp_path,
                                cancel_check=lambda: flag["stop"])
    results = []
    worker.finished_ok.connect(results.append)
    worker.run()
    payload = results[0]
    assert [c[0] for c in fake2.calls] == ["t1"], "第 2 个任务不得派发（L1 批间停止）"
    assert payload["stopped"] is True and payload["cancelled"] is True


def test_worker_passes_cancel_check_through(tmp_path, monkeypatch):
    """接线验证：LockedTaskWorker 把 cancel_check 透传到 collector.execute_task。"""
    import quantstudio.gui.workers as workers
    monkeypatch.setattr(workers, "_collector_run_lock_path",
                        lambda: tmp_path / "collector.lock")
    fake = _FakeCollector()
    _patch_collector(monkeypatch, fake)
    sentinel = lambda: False
    worker = LockedTaskWorker(task={"name": "t1"}, config_dir=tmp_path,
                              cancel_check=sentinel)
    ok = []
    worker.finished_ok.connect(ok.append)
    worker.run()
    assert fake.calls == [("t1", sentinel)]
    assert fake.progress_cbs and fake.progress_cbs[0] is not None, \
        "缺陷③：停止能力启用时须同时接线段级进度回调（A4 文案依赖）"
    assert ok and ok[0]["task_ok"] is True


def test_v3_tab_stop_state_machine_and_buttons(app, monkeypatch):
    """V3：running→stop_requested→stopping→stopped 全路径 + 幂等 + 按钮复原。"""
    from types import MethodType
    tab = TaskTab(_StubMainWindow())
    # running
    tab._begin_task_run(worker=object())
    assert tab._stop_state == "running" and tab.stop_btn.isEnabled()
    # 请求停止（幂等：第二次点击无副作用）
    tab._request_stop()
    assert tab._stop_requested is True and tab._stop_state == "stop_requested"
    assert tab.stop_btn.isEnabled() is False
    text_after_first = tab.status_label.text()
    tab._request_stop()
    assert tab.status_label.text() == text_after_first
    # 等待期进度 → stopping
    tab._on_collect_progress("执行 t2 (2/5)")
    assert tab._stop_state == "stopping"
    assert "正在停止" in tab.status_label.text()
    assert "2/5" in tab.status_label.text()
    # 收口 → stopped
    tab._finish_stop("已停止（水位保持，下次续传）")
    assert tab._stop_state == "stopped"
    assert tab._stop_requested is False
    assert tab.stop_btn.isEnabled() is False
    assert tab.status_label.text() == "已停止（水位保持，下次续传）"


def test_v3_cancelled_task_done_marks_stopped(app):
    """V3：TaskCancelled 到达 _on_task_done → 标「已停止」而非「失败」。"""
    tab = TaskTab(_StubMainWindow())
    name = tab.tasks[0]["name"]          # 真实任务名（refresh 会过滤非可见任务）
    tab._begin_task_run(worker=object())
    tab._running_tasks[name] = "incremental"
    tab._stop_requested = True
    tab._stop_state = "stopping"
    tab._on_task_done({"name": name}, False, TaskCancelled("stopped at boundary"))
    assert tab._task_status.get(name) == "已停止"
    assert tab._stop_state == "stopped"
    assert "已停止" in tab.status_label.text()


class _StubDbHelper:
    def get_watermarks(self):
        return pd.DataFrame(columns=["table_name", "freq", "source", "watermark"])


class _StubMainWindow:
    """TaskTab 构造 harness：只补宿主接口，不替身被测方法。"""

    def __init__(self):
        self.app_root = REPO_ROOT
        self.config_dir = MCP_PROFILE_DIR
        self.current_profile = "mcp_only"
        self.db_helper = _StubDbHelper()
        self.workers = []

    def profile_options(self):
        return [("mcp_only", "MCP-only"), ("traditional", "Traditional")]

    def hold_worker(self, w):
        self.workers.append(w)


def test_resume_start_handles_epoch_ms_cursor_value(isolated_resume_dir):
    """V6 现场回归：生产游标 last_completed 是**毫秒时间戳字符串**（非日期串）。

    实证取值来自真实运行态游标 data/task_resume/mcp_etf_minutes__incremental__*.json：
    last_completed.value = "1767578760000"。旧实现（只认 YYYY-MM-DD）返回 None
    → 游标被静默忽略、退回全窗重拉——续传语义整体失效且无任何报错。
    """
    from quantstudio.pipeline.task_resume import next_trade_date_after
    assert next_trade_date_after("1767578760000") == "2026-01-06"
    assert next_trade_date_after("2026-05-10") == "2026-05-11"
    assert next_trade_date_after("20260510") == "2026-05-11"
    assert next_trade_date_after(None) is None

    # 端到端：毫秒形态游标必须真正被 _open_day_resume 采用（不得静默退回起点）
    processed, watermarks = [], []
    host = _streaming_host(None, processed, watermarks)
    host._task_cancel_check = lambda: False
    cursor = TaskResumeCursor("probe_task", "incremental", "2026-01-01",
                              "2026-09-12", None)
    host._task_resume = cursor
    cursor.advance(UNIT_TRADE_DATE, "1767578760000")

    effective = host._open_day_resume({"name": "probe_task", "mode": "incremental"},
                                      "probe", PROBE_TABLE, "daily",
                                      "2026-01-01", "2026-09-12")
    assert effective == "2026-01-06", "毫秒形态游标必须被采纳为续传起点"


# ==================== A4 修复段（V6 实测缺陷回归） ====================
A4_DATES = ["2024-05-10", "2024-05-11", "2024-05-12"]


def _a4_host(cancel_check, progress, processed):
    """真实 ResidentCollector 宿主：A4 段方法体全部真实，仅注入协作者。"""
    rc = ResidentCollector.__new__(ResidentCollector)
    rc._task_cancel_check = cancel_check
    rc._task_cancelled = False
    rc._task_resume = None
    rc._qfq_cycle_id = None
    rc._task_progress_cb = progress.append
    rc._A4_MAX_WINDOWS = 20
    rc.aligner = _StubAligner()
    rc.validator = _StubValidator()
    rc.writer = SimpleNamespace(shared_conn=lambda: object(), reconnect=lambda: None)
    rc._update_detector = SimpleNamespace(
        query_updated_since=lambda since: [
            {"table_name": PROBE_TABLE, "update_source": "repair", "trade_date": d}
            for d in A4_DATES])

    def _stamp(res, table, batch_id, source, **kw):
        processed.append(str(res.passed_df["trade_date"].iloc[0]))
        return len(res.passed_df)

    rc._stamp_and_write = _stamp
    return rc


def _a4_cursor():
    return TaskResumeCursor("probe_task", "incremental", "2024-01-01",
                            "2024-06-30", WM)


def _run_a4(host, monkeypatch):
    monkeypatch.setattr(
        "quantstudio.pipeline.update_detector.load_last_sync",
        lambda conn, table: "2024-01-01")
    return host._check_cloud_updates_and_repull(
        "mcp", PROBE_TABLE, "daily", _StubAdapter([]), "batch_a4")


def test_a4_segment_honours_stop_and_records_progress(isolated_resume_dir, monkeypatch):
    """缺陷①：A4 段每窗口后检查取消（旧版整段无检查点，停止被忽略）。"""
    processed, progress = [], []
    state = {"n": 0}

    def cancel_check():
        state["n"] += 1
        return state["n"] >= 2          # 第 2 个 A4 窗口边界命中

    host = _a4_host(cancel_check, progress, processed)
    cursor = _a4_cursor()
    host._task_resume = cursor

    with pytest.raises(TaskCancelled):
        _run_a4(host, monkeypatch)

    assert processed == A4_DATES[:2], "命中后不得继续重拉剩余窗口"
    assert cursor.a4_completed() == set(A4_DATES[:2]), "缺陷②：A4 进度必须落盘"
    assert any("A4 修复段" in p for p in progress), "缺陷③：A4 段进度必须上报 GUI"


def test_a4_progress_does_not_corrupt_main_resume_point(isolated_resume_dir, monkeypatch):
    """数据缺口防线：A4 进度**不得**写进 last_completed。

    若把修复日写进主游标，续跑会从修复日起拉，跳过从未拉取的主窗口区间。
    """
    processed, progress = [], []
    host = _a4_host(None, progress, processed)
    cursor = _a4_cursor()
    host._task_resume = cursor

    _run_a4(host, monkeypatch)

    assert processed == A4_DATES
    data = json.loads(cursor.path.read_text(encoding="utf-8"))
    assert data["last_completed"] is None, "A4 不得改写主续传点"
    assert set(data["a4"]["completed_windows"]) == set(A4_DATES)
    assert cursor.a4_completed() == set(A4_DATES)


def test_a4_resume_skips_already_repaired_windows(isolated_resume_dir, monkeypatch):
    """缺陷②续传侧：再次运行跳过已修复窗口（A4 段进度不丢失）。"""
    processed, progress = [], []
    host = _a4_host(None, progress, processed)
    cursor = _a4_cursor()
    host._task_resume = cursor
    cursor.advance_a4(A4_DATES[0], total=len(A4_DATES))

    _run_a4(host, monkeypatch)

    assert processed == A4_DATES[1:], "已修复窗口应跳过，仅处理剩余窗口"
    assert cursor.a4_completed() == set(A4_DATES)


def test_a4_boundary_is_checked_even_for_empty_windows(isolated_resume_dir, monkeypatch):
    """边界完整性：空窗口（无数据）同样走取消检查，不得绕过。"""
    processed, progress = [], []
    state = {"n": 0}

    def cancel_check():
        state["n"] += 1
        return True                     # 首个边界即命中

    host = _a4_host(cancel_check, progress, processed)
    host._task_resume = _a4_cursor()
    host.aligner = _StubAligner()

    class _EmptyAdapter(_StubAdapter):
        def fetch_table(self, table, start, end, freq="daily", codes=None):
            return pd.DataFrame({"trade_date": [], "code": []}), {"source": "probe"}

    monkeypatch.setattr(
        "quantstudio.pipeline.update_detector.load_last_sync",
        lambda conn, table: "2024-01-01")
    with pytest.raises(TaskCancelled):
        host._check_cloud_updates_and_repull(
            "mcp", PROBE_TABLE, "daily", _EmptyAdapter([]), "batch_a4_empty")
    assert processed == [], "空窗口不写库"
    assert state["n"] == 1, "空窗口也必须到达段边界（首个即命中停止）"
