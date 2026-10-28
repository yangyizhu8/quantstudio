# -*- coding: utf-8 -*-
"""停止原因（reason）与留痕单测。

口径：回测前停采集复用既有 `daemon_stop.request`，只加 `reason` 字段；
daemon 消费时记录 `stop_reason`，`backtest` 额外写 `yield_reason="backtest_stop"`，
界面据此区分「因回测停止」与「用户手动停/故障」。
"""
from __future__ import annotations

import json
import types

import pytest

from quantstudio.pipeline import daemon_lifecycle as dl


class FakeCollector:
    def __init__(self):
        self.writer = types.SimpleNamespace(shared_conn=lambda: object())
        self._last_quality_audit = {}
        self.qfq_aux_paths_config = None
        self.closed = False
        self.executed = 0

    def qfq_begin_cycle(self):
        return None

    def execute_task(self, task, mode=None, run_quality_audit=False, **kw):
        self.executed += 1
        return True

    def _run_full_quality_audit(self, should_stop=None):
        return True

    def _audit_qfq_factor_integrity(self):
        return None

    def close(self):
        self.closed = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(dl, "write_qfq_cycle_terminal", lambda **kw: None)
    return tmp_path


TASKS_CFG = {"tasks": [{"name": "t1", "enabled": True, "mode": "incremental"}]}


def _write_stop(tmp_path, token="tok-test", reason=None):
    doc = {"instance_token": token}
    if reason is not None:
        doc["reason"] = reason
    (tmp_path / "daemon_stop.request").write_text(
        json.dumps(doc), encoding="utf-8")


# ── 消费语义 ───────────────────────────────────────────────────────────────
def test_consume_returns_reason_and_deletes(env):
    _write_stop(env, reason="backtest")
    got = dl.consume_stop_request_with_reason("tok-test")
    assert got is not None and got["reason"] == "backtest"
    assert dl.read_stop_request() is None


def test_consume_ignores_other_token(env):
    _write_stop(env, token="someone-else", reason="backtest")
    assert dl.consume_stop_request_with_reason("tok-test") is None
    assert dl.read_stop_request() is not None, "不是给我的不得消费"


def test_reason_defaults_to_user(env):
    _write_stop(env)                    # 旧 GUI 不写 reason
    got = dl.consume_stop_request_with_reason("tok-test")
    assert got is not None and got["reason"] == "user"


def test_if_matched_wrapper_still_works(env):
    _write_stop(env, reason="user")
    assert dl.consume_stop_request_if_matched("tok-test") is True
    assert dl.consume_stop_request_if_matched("tok-test") is False


# ── 轮末留痕 ───────────────────────────────────────────────────────────────
def _patch_collector(monkeypatch, fake):
    import quantstudio.pipeline.daemon as daemon_mod
    monkeypatch.setattr(daemon_mod.ResidentCollector, "from_configs",
                        classmethod(lambda cls, *a, **k: fake))


def test_backtest_stop_writes_reason_and_yield(env, monkeypatch):
    """pre_cycle 就命中 backtest 停止 ⇒ 轮不跑任务，留痕 stop_reason/yield_reason。"""
    _write_stop(env, reason="backtest")
    fake = FakeCollector()
    _patch_collector(monkeypatch, fake)

    lc = dl.DaemonLifecycle(config_dir=env, instance_token="tok-test")
    lc.run_one_cycle(TASKS_CFG)

    st = dl.read_run_state() or {}
    assert st.get("stop_reason") == "backtest"
    assert st.get("yield_reason") == "backtest_stop"
    assert st.get("stop_requested") is True
    assert fake.executed == 0, "停止请求在 pre_cycle 命中，不应再跑任务"


def test_user_stop_does_not_mark_backtest(env, monkeypatch):
    _write_stop(env, reason="user")
    fake = FakeCollector()
    _patch_collector(monkeypatch, fake)

    lc = dl.DaemonLifecycle(config_dir=env, instance_token="tok-test")
    lc.run_one_cycle(TASKS_CFG)

    st = dl.read_run_state() or {}
    assert st.get("stop_reason") == "user"
    assert st.get("yield_reason") is None, "用户手动停不得冒充'因回测停止'"


def test_no_stop_runs_normally(env, monkeypatch):
    fake = FakeCollector()
    _patch_collector(monkeypatch, fake)

    lc = dl.DaemonLifecycle(config_dir=env, instance_token="tok-test")
    lc.run_one_cycle(TASKS_CFG)

    st = dl.read_run_state() or {}
    assert st.get("status") == "completed"
    assert st.get("stop_requested") is False
    assert st.get("yield_reason") is None
    assert fake.executed == 1
