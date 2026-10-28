# -*- coding: utf-8 -*-
"""daemon 身份判定：`denied` 必须按「在运行」对待（2026-10-28 01:41 实证缺陷）。

背景：daemon 由提权/守护包装启动时，普通权限的 GUI 读其 cmdline 会 AccessDenied；
`verify_daemon_identity` 返回 `denied`。原实现只认 ("alive","unknown")，于是：
  · `is_daemon_running()` → False → GUI 放行启动；
  · `find_running_daemon_pids()` 遇 AccessDenied 直接跳过 → 第二源也空；
  → 新实例撞 `.daemon.lock`，用户只看到「启动后立即退出（另一个 daemon 已在运行）」。
"""
from __future__ import annotations

import psutil
import pytest

from quantstudio.gui import daemon_process as dp


# ── is_daemon_running ──────────────────────────────────────────────────────
@pytest.mark.parametrize("verdict,expected", [
    ("alive", True),
    ("unknown", True),     # D2：查不出 ≠ 死亡
    ("denied", True),      # 本次修复：无权读 ≠ 不存在
    ("stale", False),
])
def test_is_daemon_running_verdict_matrix(monkeypatch, verdict, expected):
    monkeypatch.setattr(dp, "read_daemon_status", lambda: {"pid": 4321})
    monkeypatch.setattr(dp, "verify_daemon_identity", lambda st: verdict)
    assert dp.is_daemon_running() is expected


def test_is_daemon_running_without_status(monkeypatch):
    monkeypatch.setattr(dp, "read_daemon_status", lambda: None)
    assert dp.is_daemon_running() is False


# ── find_running_daemon_pids：AccessDenied 的第二源信号 ──────────────────
class _DeniedProc:
    """模拟 psutil 进程对象：info 访问被拒（权限不匹配）。"""

    def __init__(self, pid: int):
        self.pid = pid

    @property
    def info(self):
        raise psutil.AccessDenied(pid=self.pid)


def test_find_pids_keeps_signal_when_access_denied(monkeypatch):
    monkeypatch.setattr(dp, "read_daemon_status", lambda: {"pid": 4321})
    monkeypatch.setattr(dp.psutil, "process_iter", lambda attrs: [_DeniedProc(4321)])

    hits = dp.find_running_daemon_pids()
    assert hits == [{"pid": 4321, "started_at": "unknown"}], \
        "与 status 同 PID 的无权进程必须作为第二源信号保留"


def test_find_pids_ignores_denied_unrelated_pid(monkeypatch):
    monkeypatch.setattr(dp, "read_daemon_status", lambda: {"pid": 9999})
    monkeypatch.setattr(dp.psutil, "process_iter", lambda attrs: [_DeniedProc(4321)])

    assert dp.find_running_daemon_pids() == [], "与 status 无关的无权进程不应误报"
