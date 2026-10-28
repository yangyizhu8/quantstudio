# -*- coding: utf-8 -*-
"""停止采集内核单测（口径：真停 + 由用户决定恢复）。

覆盖：
  · 停止请求只对当前 daemon token 生效；无 daemon 不写；
  · 等待读数为真实秒数、阶段随阈值升级；超时抛 CollectorStopTimeout；可取消；
  · 上下文：无采集时不发停止、不留假标记；发了停止则写「因回测停止」标记；
  · **不重启采集**：整个路径没有任何启动动作；
  · 锁被占用时等待并最终超时（不抢占）。
"""
from __future__ import annotations

import json
import os

import pytest
from filelock import FileLock

from quantstudio.pipeline import backtest_window as bw


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(bw, "_data_root", lambda: tmp_path)
    return tmp_path


# ── 停止请求 ───────────────────────────────────────────────────────────────
def test_request_stop_requires_daemon_status(root):
    assert bw.request_stop("backtest") is False
    assert bw.read_stop_request() is None


def test_request_stop_writes_reason_and_token(root):
    # 用**存活** pid（当前进程）：request_stop 现在会先做 pid_exists 判定
    bw.daemon_status_path().write_text(
        json.dumps({"pid": os.getpid(), "instance_token": "tok1", "create_time": 0}),
        encoding="utf-8")
    assert bw.request_stop("backtest") is True
    req = bw.read_stop_request()
    assert req["instance_token"] == "tok1"
    assert req["reason"] == "backtest"
    assert req["owner_pid"] > 0


def test_request_stop_skips_when_pid_dead(root):
    """status 残留但进程已退出 ⇒ 不写 stop.request（否则会留假的「因回测停止」）。"""
    bw.daemon_status_path().write_text(
        json.dumps({"pid": 999999, "instance_token": "tok1", "create_time": 0}),
        encoding="utf-8")
    assert bw.request_stop("backtest") is False
    assert bw.read_stop_request() is None


def test_daemon_alive_false_when_pid_dead(root):
    bw.daemon_status_path().write_text(
        json.dumps({"pid": 999999, "create_time": 0}), encoding="utf-8")
    assert bw.daemon_alive() is False


def test_daemon_alive_false_without_status(root):
    assert bw.daemon_alive() is False


# ── 等待 ────────────────────────────────────────────────────────────────────
def test_wait_returns_immediately_when_daemon_gone(root, monkeypatch):
    monkeypatch.setattr(bw, "daemon_alive", lambda: False)
    readings = []
    waited = bw.wait_daemon_exit(1.0, on_wait=lambda s, p: readings.append((s, p)), poll=0.01)
    assert waited >= 0
    assert readings == [], "已不在跑时不得报'等待中'读数"


def test_wait_reports_real_readings_and_phases(root, monkeypatch):
    monkeypatch.setattr(bw, "daemon_alive", lambda: True)
    readings = []
    with pytest.raises(bw.CollectorStopTimeout):
        bw.wait_daemon_exit(0.25, on_wait=lambda s, p: readings.append((s, p)),
                            poll=0.02, boundary_hint=0.05)
    assert readings, "等待期间必须回调真实读数"
    phases = [p for _, p in readings]
    assert bw.PHASE_WAIT_ROUND in phases
    assert bw.PHASE_WAIT_UNIT in phases          # 超阈值后阶段升级
    waited = [w for w, _ in readings]
    assert waited == sorted(waited)


def test_wait_cancel_raises(root, monkeypatch):
    monkeypatch.setattr(bw, "daemon_alive", lambda: True)
    with pytest.raises(bw.WindowCancelled):
        bw.wait_daemon_exit(5.0, should_cancel=lambda: True, poll=0.01)


# ── 上下文：不重启、留痕 ────────────────────────────────────────────────────
def test_context_no_daemon_does_not_stop_or_flag(root, monkeypatch):
    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: False)
    with bw.CollectorStopContext(poll=0.01):
        pass
    assert bw.read_stop_flag() is None, "无采集在跑时不得留'因回测停止'标记"


def test_context_stop_writes_flag_and_never_restarts(root, monkeypatch):
    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: True)
    monkeypatch.setattr(bw, "wait_daemon_exit", lambda *a, **k: 0.0)
    with bw.CollectorStopContext(poll=0.01):
        pass
    flag = bw.read_stop_flag()
    assert flag is not None and flag["reason"] == "backtest"
    assert "保持停止" in flag["note"]


def test_context_clears_previous_flag_on_enter(root, monkeypatch):
    bw.write_stop_flag("backtest", "上一轮")
    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: False)
    with bw.CollectorStopContext(poll=0.01):
        assert bw.read_stop_flag() is None


def test_context_exception_still_flags_stopped(root, monkeypatch):
    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: True)
    monkeypatch.setattr(bw, "wait_daemon_exit", lambda *a, **k: 0.0)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with bw.CollectorStopContext(poll=0.01):
            raise Boom("回测内部爆炸")
    flag = bw.read_stop_flag()
    assert flag is not None and "异常结束" in flag["note"]


def test_context_waits_for_lock_then_times_out(root, monkeypatch):
    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: False)
    holder = FileLock(str(bw.run_lock_path()), timeout=0, preserve_lock_file=True)
    holder.acquire()
    try:
        with pytest.raises(bw.CollectorStopTimeout):
            with bw.CollectorStopContext(timeout=0.2, poll=0.01):
                pass
    finally:
        holder.release()


def test_lock_path_matches_daemon_lifecycle():
    from quantstudio.pipeline.daemon_lifecycle import collector_run_lock_path
    assert bw.run_lock_path().name == collector_run_lock_path().name == ".collector_run.lock"


# ── run_backtest 接入 ──────────────────────────────────────────────────────
def test_run_backtest_no_coexistence_does_not_stop(root, monkeypatch):
    from quantstudio.backtest import run_ptrade_strategy as rps

    calls = {"stop": 0}

    def _fake_request(reason="backtest", owner_pid=None):
        calls["stop"] += 1
        return False

    monkeypatch.setattr(bw, "request_stop", _fake_request)

    class _R:
        nav_history = [{"date": "2026-01-05", "nav": 1.0}]

    monkeypatch.setattr(rps, "_run_backtest_impl",
                        lambda *a, **k: (_R(), "out", "eng"))
    rps.run_backtest("s.py", "2020-01-01", "2020-12-31", coexistence=False)
    assert calls["stop"] == 0, "关闭共存时不得停采集（纯增益回退）"


def test_run_backtest_coexistence_stops_and_keeps_stopped(root, monkeypatch):
    from quantstudio.backtest import run_ptrade_strategy as rps

    calls = {"stop": 0, "impl": 0}

    def _fake_request(reason="backtest", owner_pid=None):
        calls["stop"] += 1
        return True

    class _R:
        nav_history = [{"date": "2026-01-05", "nav": 1.0}]

    def _fake_impl(*a, **k):
        calls["impl"] += 1
        return (_R(), "out", "eng")

    monkeypatch.setattr(bw, "request_stop", _fake_request)
    monkeypatch.setattr(bw, "wait_daemon_exit", lambda *a, **k: 0.0)
    monkeypatch.setattr(rps, "_precheck_db_available", lambda db_path=None: None)
    monkeypatch.setattr(rps, "_check_data_readiness", lambda db=None: True)
    monkeypatch.setattr(rps, "_run_backtest_impl", _fake_impl)

    msgs = []
    _f = root / "s.py"
    _f.write_text("def initialize(context):\n    pass\n", encoding="utf-8")
    rps.run_backtest(str(_f), "2020-01-01", "2020-12-31",
                     wait_callback=lambda m: msgs.append(m))

    assert calls["stop"] == 1 and calls["impl"] == 1
    assert any("手动启动" in m for m in msgs), "必须提示需手动启动采集"
    assert bw.read_stop_flag() is not None

# ── 停采集前的前置校验（2026-10-28 实证缺陷）──────────────────────────────
def test_missing_strategy_does_not_stop_collector(root, monkeypatch):
    """路径不存在 ⇒ **不得停采集**（否则低级错误让用户白停一次，还要手动恢复）。"""
    from quantstudio.backtest import run_ptrade_strategy as rps

    calls = {"stop": 0}

    def _fake_request(reason="backtest", owner_pid=None):
        calls["stop"] += 1
        return True

    monkeypatch.setattr(bw, "request_stop", _fake_request)
    monkeypatch.setattr(rps, "_run_backtest_impl",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应进入引擎")))

    with pytest.raises(FileNotFoundError) as ei:
        rps.run_backtest("不存在的策略.py", "2020-01-01", "2020-12-31")

    assert "采集未停止" in str(ei.value)
    assert calls["stop"] == 0, "前置校验失败时绝不允许停采集"


def test_existing_strategy_passes_precheck(root, monkeypatch):
    from quantstudio.backtest import run_ptrade_strategy as rps

    f = root / "s.py"
    f.write_text("def initialize(context):\n    pass\n", encoding="utf-8")

    calls = {"stop": 0}

    def _fake_request(reason="backtest", owner_pid=None):
        calls["stop"] += 1
        return True

    class _R:
        nav_history = [{"date": "2026-01-05", "nav": 1.0}]

    monkeypatch.setattr(bw, "request_stop", _fake_request)
    monkeypatch.setattr(bw, "wait_daemon_exit", lambda *a, **k: 0.0)
    monkeypatch.setattr(rps, "_precheck_db_available", lambda db_path=None: None)
    monkeypatch.setattr(rps, "_check_data_readiness", lambda db=None: True)
    monkeypatch.setattr(rps, "_run_backtest_impl", lambda *a, **k: (_R(), "out", "eng"))

    rps.run_backtest(str(f), "2020-01-01", "2020-12-31")
    assert calls["stop"] == 1, "校验通过后才停采集"

def test_wait_callback_is_separate_from_progress(root, monkeypatch):
    """回归：等待读数只能走 wait_callback；误用三参数的 progress_callback 会 TypeError。"""
    from quantstudio.backtest import run_ptrade_strategy as rps

    waits = []
    prog_args = []

    def _prog(current, total, date_str):
        prog_args.append((current, total, date_str))

    f = root / "s.py"
    f.write_text("def initialize(context):\n    pass\n", encoding="utf-8")

    class _R:
        nav_history = [{"date": "2026-01-05", "nav": 1.0}]

    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: True)
    monkeypatch.setattr(bw, "wait_daemon_exit", lambda *a, **k: 0.0)
    monkeypatch.setattr(rps, "_precheck_db_available", lambda db_path=None: None)
    monkeypatch.setattr(rps, "_check_data_readiness", lambda db=None: True)
    monkeypatch.setattr(rps, "_run_backtest_impl", lambda *a, **k: (_R(), "out", "eng"))

    rps.run_backtest(str(f), "2020-01-01", "2020-12-31",
                     wait_callback=lambda m: waits.append(m),
                     progress_callback=_prog)

    assert waits, "等待读数必须走 wait_callback"
    assert any("手动启动" in m for m in waits)
    assert prog_args == [], "progress_callback 只接受引擎的三参数调用，不应被读数污染"

# ── daemon_alive：AccessDenied 必须按"活着"处理（阶段 B 实证）─────────────
def test_daemon_alive_true_when_create_time_denied(root, monkeypatch):
    """高完整性 daemon 的 create_time 读不到 ⇒ 仍按活着（否则等待 0 秒被跳过）。"""
    import psutil

    bw.daemon_status_path().write_text(
        json.dumps({"pid": 4321, "create_time": 111.0}), encoding="utf-8")

    class _P:
        def is_running(self):
            return True

        def create_time(self):
            raise psutil.AccessDenied(pid=4321)

    monkeypatch.setattr(psutil, "Process", lambda pid: _P())
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    assert bw.daemon_alive() is True


def test_daemon_alive_false_when_gone(root, monkeypatch):
    import psutil

    bw.daemon_status_path().write_text(
        json.dumps({"pid": 4321, "create_time": 111.0}), encoding="utf-8")

    def _boom(pid):
        raise psutil.NoSuchProcess(pid=pid)

    monkeypatch.setattr(psutil, "Process", _boom)
    assert bw.daemon_alive() is False


def test_daemon_alive_false_on_pid_reuse(root, monkeypatch):
    """PID 复用（create_time 差 > 1s）必须判死，避免把别人的进程当 daemon。"""
    import psutil

    bw.daemon_status_path().write_text(
        json.dumps({"pid": 4321, "create_time": 100.0}), encoding="utf-8")

    class _P:
        def is_running(self):
            return True

        def create_time(self):
            return 500.0

    monkeypatch.setattr(psutil, "Process", lambda pid: _P())
    assert bw.daemon_alive() is False

# ── F-6：采集持库时共存流程仍可执行；停采集后复检把关 ─────────────────────
def test_ctx_recheck_rejects_not_ready_after_stop(root, monkeypatch):
    """停采集后复检不就绪 ⇒ 显式 raise（禁止空数据静默跑完）。"""
    from quantstudio.backtest import run_ptrade_strategy as rps
    import pytest as _pytest

    f = root / "s.py"
    f.write_text("def initialize(context):\n    pass\n", encoding="utf-8")

    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: True)
    monkeypatch.setattr(bw, "wait_daemon_exit", lambda *a, **k: 0.0)
    monkeypatch.setattr(rps, "_precheck_db_available", lambda db_path=None: None)
    monkeypatch.setattr(rps, "_check_data_readiness", lambda db=None: False)  # 复检失败

    with _pytest.raises(RuntimeError, match="数据未就绪"):
        rps.run_backtest(str(f), "2020-01-01", "2020-12-31")


def test_ctx_recheck_passes_when_ready(root, monkeypatch):
    """复检就绪 ⇒ 正常进入引擎（与既有 3 用例等价，此处锁定 F-6 语义）。"""
    from quantstudio.backtest import run_ptrade_strategy as rps

    calls = {"ready": 0}

    def _fake_ready(db=None):
        calls["ready"] += 1
        return True

    f = root / "s.py"
    f.write_text("def initialize(context):\n    pass\n", encoding="utf-8")

    class _R:
        nav_history = [{"date": "2026-01-05", "nav": 1.0}]

    monkeypatch.setattr(bw, "request_stop", lambda reason="backtest", owner_pid=None: True)
    monkeypatch.setattr(bw, "wait_daemon_exit", lambda *a, **k: 0.0)
    monkeypatch.setattr(rps, "_precheck_db_available", lambda db_path=None: None)
    monkeypatch.setattr(rps, "_check_data_readiness", _fake_ready)
    monkeypatch.setattr(rps, "_run_backtest_impl", lambda *a, **k: (_R(), "out", "eng"))

    rps.run_backtest(str(f), "2020-01-01", "2020-12-31")
    assert calls["ready"] == 1, "停采集后必须恰好复检一次数据就绪"
