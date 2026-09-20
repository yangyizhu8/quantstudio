"""T2 委托形态等价契约测试（A 案替换件，2026-09-20）。

替换 7 条旧直跑契约测试（test_gui_task_audit_separation ×5 +
test_gui_task_stop ×2）——旧断言对象（进程内 ResidentCollector 调用链）在
委托化后不存在，本文件按**迁移对照表四项**重建等价保证：

  1. 终信号前无悬挂（旧 close-before-signal）→ 终信号前子进程已退出
  2. 成败判定走 manifest+nonce（旧 task_ok 语义）→ 审计结果独立字段，不转任务成败
  3. 取消链（旧 cancel_check 协作式透传）→ cancel 触发 terminate + 停止文案
     【语义差·如实标注】协作式取消 → 子进程硬停（Windows TerminateProcess）；
     锁残留由批一 P0 写锁死亡自愈 + 写事务原子性兜底（总调度 2026-09-20 裁定）
  4. RunAll 逐任务结果传播 + 批间停止不派发

替身纪律：被测为真实 LockedTaskWorker/LockedRunAllWorker.run()，替身仅止于
委托链入口（start_once_subprocess 三函数）与子进程对象——不复制实现。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication

import quantstudio.gui.workers as workers


@pytest.fixture(scope="module", autouse=True)
def qcore_app():
    return QCoreApplication.instance() or QCoreApplication([])


class FakeProc:
    """子进程替身：默认 poll 两次后自行退出（模拟子进程很快跑完）。

    剧本缺陷教训（第 7 例）：首版 poll 恒 None → Worker 的 while True 轮询
    永不 break → 测试挂死被杀且零输出。替身必须有「退出剧本」。
    self_exit_polls=None 表示永不自退（专供 cancel 用例，等 terminate）。
    """

    def __init__(self, already_exited=False, self_exit_polls=2):
        self.pid = 424242
        self._exited = already_exited
        self._polls = 0
        self._self_exit = self_exit_polls
        self.terminated = False

    def poll(self):
        if not self._exited and self._self_exit is not None:
            self._polls += 1
            if self._polls >= self._self_exit:
                self._exited = True
        return 0 if self._exited else None

    def terminate(self):
        self.terminated = True
        self._exited = True

    @property
    def returncode(self):
        # Popen 契约：未退出为 None；退出后给非零（错误路径用例会把它拼进错误文案）
        return None if not self._exited else 1


class DelegationHarness:
    """monkeypatch 委托链入口；记录调用；按剧本回放结果。"""

    def __init__(self, monkeypatch, tmp_path, *, once_done=True, busy=False,
                 cancel_mode=False):
        self.calls = []
        self.tmp = tmp_path
        self.once_done = once_done
        self.busy = busy
        # cancel_mode：子进程不自退（self_exit_polls=None），专等 terminate
        self.cancel_mode = cancel_mode
        # 挂点=源模块：两 Worker 的 run() 内部是函数级 import（名字不在 workers 模块
        # 属性上），每次调用都从 daemon_process 解析——故 patch 源头即生效。
        import quantstudio.gui.daemon_process as dp
        monkeypatch.setattr(dp, "start_once_subprocess", self._fake_start)
        monkeypatch.setattr(dp, "read_once_manifest", self._fake_manifest)
        monkeypatch.setattr(dp, "once_busy_hint", self._fake_busy)

    def _fake_start(self, name, config_dir, mode="incremental",
                    run_quality_audit=True, pull_mode=None):
        proc = FakeProc(self_exit_polls=None) if self.cancel_mode else FakeProc()
        log = self.tmp / ("once_%s.log" % name)
        manifest = self.tmp / ("once_%s.manifest.json" % name)
        body = "once 完成（task + audit 全部通过）" if self.once_done else "ERROR something failed"
        log.write_text(body, encoding="utf-8")
        manifest.write_text('{"nonce": "n/a", "task": "%s"}' % name, encoding="utf-8")
        self.calls.append({"name": name, "proc": proc, "manifest": manifest, "log": log})
        return "n/a", proc, manifest, log

    def _fake_manifest(self, path):
        import json
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _fake_busy(self, log_path):
        return "数据库采集中（守护进程正在写入），请稍后重试" if self.busy else None


def _collect(worker):
    ok, err, prog = [], [], []
    worker.finished_ok.connect(lambda r: ok.append(r))
    worker.finished_err.connect(lambda e: err.append(e))
    worker.progress.connect(lambda m: prog.append(m))
    worker.run()
    return ok, err, prog


# ── 对照项 2+1：成功判定走 manifest；审计独立；终信号前子进程已退出 ──
def test_task_success_via_manifest_and_no_dangling(monkeypatch, tmp_path):
    h = DelegationHarness(monkeypatch, tmp_path, once_done=True)
    w = workers.LockedTaskWorker({"name": "t1"}, tmp_path)
    ok, err, prog = _collect(w)
    assert err == [] and len(ok) == 1
    r = ok[0]
    assert r["task_ok"] is True
    assert r["delegated"] is True
    # 终信号前子进程已退出（poll 非 None）——无悬挂（旧 close-before-signal 等价）
    assert h.calls[0]["proc"].poll() is not None


def test_task_failure_stays_failure_with_log_tail(monkeypatch, tmp_path):
    h = DelegationHarness(monkeypatch, tmp_path, once_done=False)
    w = workers.LockedTaskWorker({"name": "t2"}, tmp_path)
    ok, err, prog = _collect(w)
    assert ok == [] and len(err) == 1
    assert "任务拉取失败" in err[0]
    assert "ERROR something failed" in err[0]      # 日志尾随错误返回，不吞现场
    assert h.calls[0]["proc"].poll() is not None


def test_task_busy_returns_bounded_hint(monkeypatch, tmp_path):
    DelegationHarness(monkeypatch, tmp_path, busy=True)
    w = workers.LockedTaskWorker({"name": "t3"}, tmp_path)
    ok, err, prog = _collect(w)
    assert ok == []
    assert "采集中" in err[0]                        # 有界提示，不无限等待


# ── 对照项 3：取消链（语义差如实标注，见 module docstring）──
def test_task_cancel_terminates_subprocess_and_reports_stop(monkeypatch, tmp_path):
    h = DelegationHarness(monkeypatch, tmp_path, cancel_mode=True)
    w = workers.LockedTaskWorker({"name": "t4"}, tmp_path, cancel_check=lambda: True)
    ok, err, prog = _collect(w)
    assert ok == []
    assert "已停止" in err[0]
    assert h.calls[0]["proc"].terminated is True     # terminate 被调（协作式→硬停，裁定兜底）


# ── 对照项 4：RunAll 逐任务传播 + 批间停止不派发 ──
def test_run_all_propagates_each_result(monkeypatch, tmp_path):
    h = DelegationHarness(monkeypatch, tmp_path, once_done=True)
    w = workers.LockedRunAllWorker([{"name": "a"}, {"name": "b"}, {"name": "c"}], tmp_path)
    ok, err, prog = _collect(w)
    assert err == [] and len(ok) == 1
    payload = ok[0]
    assert payload["total"] == 3 and payload["ok_count"] == 3
    assert [r["name"] for r in payload["results"]] == ["a", "b", "c"]
    assert all(r["ok"] for r in payload["results"])
    assert payload["quality_audit_ok"] is True
    assert len(h.calls) == 3                          # 逐任务串行三子进程


def test_run_all_batch_stop_skips_remaining(monkeypatch, tmp_path):
    h = DelegationHarness(monkeypatch, tmp_path)
    flags = {"n": 0}

    def cancel_after_first():
        flags["n"] += 1
        return flags["n"] > 1                          # 第二次轮询起请求停止

    w = workers.LockedRunAllWorker([{"name": "a"}, {"name": "b"}], tmp_path,
                                   cancel_check=cancel_after_first)
    ok, err, prog = _collect(w)
    assert len(ok) == 1
    payload = ok[0]
    assert payload["stopped"] is True
    assert len(h.calls) <= 2                           # 批间停止后不再派发
