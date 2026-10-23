# -*- coding: utf-8 -*-
"""GUI「进程常驻增量拉取」启动失败修复 —— 验收固件（A1–A15）

方案件：docs/daemon-gui-start-failure-design-20261023.md
证据：  docs/evidence/daemon-gui-start-failure-20261023.md

覆盖：
  FP-1（D-B）read_bootstrap_log_tail 回退链 ···················· A1/A2/A3
  FP-2（D-A）抢锁失败补写一行到 bootstrap ······················ A4/A5/A6
  FP-3（D-C）双源前置判据 + 第三态文案 ························· A7/A8/A9/A10
  FP-4（R-1）按钮状态实时化 + stopped 态探测 ··················· A11/A12/A13
  FP-5（R-2）诊断留痕（env 控制） ···························· A14/A15

设计原则：
  · 全部使用**临时 data_root**（monkeypatch `_data_root`），绝不触碰生产 data/；
  · A4 的真实撞锁用例自建占锁 dummy + 子进程，跑完即清理；
  · 不启停生产 daemon、不写生产库。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import quantstudio.gui.daemon_process as dp

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture()
def tmp_root(tmp_path, monkeypatch):
    """隔离 data_root：所有落盘/读取都指向 tmp_path。"""
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dp, "_data_root", lambda: tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# FP-1 · A1/A2/A3
# --------------------------------------------------------------------------- #
def test_a1_empty_bootstrap_falls_back_to_daemon_log(tmp_root):
    token = "abc123def456"
    bs = tmp_root / "logs" / f"daemon_bootstrap_{token}.log"
    bs.write_bytes(b"")  # 0B：本次故障形态
    (tmp_root / "logs" / "daemon.log").write_text(
        "01:56:18 WARNING [DaemonLifecycle] .daemon.lock 已被占用（另一个 daemon 运行中），退出\n"
        "01:56:18 ERROR   [CLI] 另一个 daemon 已在运行，退出\n",
        encoding="utf-8")
    out = dp.read_bootstrap_log_tail(token)
    assert out.strip()
    assert "另一个 daemon 已在运行" in out
    assert "以下为 daemon.log 尾部" in out and "可能含其他实例日志" in out


def test_a2_nonempty_bootstrap_not_overridden(tmp_root):
    """负向对照：bootstrap 有内容时不得越界读 daemon.log（保持原语义）。"""
    token = "tok2"
    (tmp_root / "logs" / f"daemon_bootstrap_{token}.log").write_text(
        "Traceback: import error X\n", encoding="utf-8")
    (tmp_root / "logs" / "daemon.log").write_text("另一个 daemon 已在运行\n", encoding="utf-8")
    out = dp.read_bootstrap_log_tail(token)
    assert "Traceback: import error X" in out
    assert "以下为 daemon.log 尾部" not in out
    assert "另一个 daemon 已在运行" not in out


def test_a3_both_missing_returns_hint_without_raise(tmp_root):
    token = "tok3"
    out = dp.read_bootstrap_log_tail(token)          # 不抛
    assert f"daemon_bootstrap_{token}.log" in out and "daemon.log" in out


# --------------------------------------------------------------------------- #
# FP-2 · A4/A5/A6
# --------------------------------------------------------------------------- #
def _daemon_src() -> str:
    return (ROOT / "quantstudio" / "pipeline" / "daemon.py").read_text(encoding="utf-8")


def test_a6_print_only_in_lock_failure_branch():
    """A6（回归）：打印语句必须位于抢锁失败分支内、且在 try 中兜住。"""
    src = _daemon_src()
    i_guard = src.find("if not lifecycle.acquire_instance_lock():")
    i_print = src.find('print(f"[bootstrap] 启动失败')
    i_err = src.find('logger.error("[CLI] 另一个 daemon 已在运行，退出")')
    assert 0 < i_guard < i_print < i_err
    assert "except Exception:  # 打印失败绝不影响退出语义" in src


def test_a4_real_lock_conflict_writes_bootstrap(tmp_path):
    """A4（真实撞锁）：GUI 场景（带 token）撞 .daemon.lock ⇒ bootstrap 非 0B 且含原因。"""
    from filelock import FileLock

    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "QUANTSTUDIO_DATA_ROOT": str(tmp_path),
           "PYTHONIOENCODING": "utf-8"}
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import time\nfrom filelock import FileLock\n"
         f"lk=FileLock(r'{tmp_path / '.daemon.lock'}')\nlk.acquire(timeout=0)\n"
         "print('HELD', flush=True)\ntime.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(ROOT))
    try:
        assert holder.stdout.readline().decode("utf-8", "replace").strip() == "HELD"
        token = "guitoken1234567"
        boot = tmp_path / "logs" / f"daemon_bootstrap_{token}.log"
        cmd = [sys.executable, "-m", "quantstudio.pipeline.daemon", "--mode", "forever",
               "--config-dir", "config/profiles/mcp_only", "--instance-token", token]
        with boot.open("wb") as f:
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                 env=env, cwd=str(ROOT))
            assert p.wait(timeout=180) == 1
        txt = boot.read_text(encoding="utf-8", errors="replace")
        assert txt.strip(), "bootstrap 仍为 0B（FP-2 未生效）"
        assert "另一个 daemon 已在运行" in txt
        assert token[:8] in txt
        assert txt.count("[bootstrap]") == 1     # 只补一行
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_a5_cli_start_without_token_does_not_print(tmp_path):
    """A5（负向）：无 instance_token（CLI 手动启动形态）时不得打屏，但仍写 daemon.log。"""
    from filelock import FileLock

    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "QUANTSTUDIO_DATA_ROOT": str(tmp_path),
           "PYTHONIOENCODING": "utf-8"}
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import time\nfrom filelock import FileLock\n"
         f"lk=FileLock(r'{tmp_path / '.daemon.lock'}')\nlk.acquire(timeout=0)\n"
         "print('HELD', flush=True)\ntime.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(ROOT))
    try:
        assert holder.stdout.readline().decode("utf-8", "replace").strip() == "HELD"
        out_file = tmp_path / "cli_stdout.log"
        cmd = [sys.executable, "-m", "quantstudio.pipeline.daemon", "--mode", "forever",
               "--config-dir", "config/profiles/mcp_only"]
        with out_file.open("wb") as f:
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                 env=env, cwd=str(ROOT))
            assert p.wait(timeout=180) == 1
        assert "[bootstrap] 启动失败" not in out_file.read_text(encoding="utf-8", errors="replace")
        assert "另一个 daemon 已在运行" in (tmp_path / "logs" / "daemon.log").read_text(
            encoding="utf-8", errors="replace")
    finally:
        holder.kill()
        holder.wait(timeout=10)


# --------------------------------------------------------------------------- #
# FP-3 · A7/A8/A9/A10
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cl,want", [
    (["C:/x/python.exe", "-m", "quantstudio.pipeline.daemon", "--mode", "forever"], True),
    (["python.exe", "-m", "quantstudio.pipeline.daemon"], True),
    (["C:/x/python.exe", "D:\\a\\QuantStudio\\quantstudio\\pipeline\\daemon.py"], True),
    # 误判回归（实测暴露）：命令行**提及**该串但不是 daemon
    (["C:/nvm4w/nodejs/node.exe", "--", "powershell.exe", "-Command",
      "python -m quantstudio.pipeline.daemon"], False),
    (["C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-Command",
      "Get-Content quantstudio.pipeline.daemon"], False),
    (["C:/node.exe", "-m", "quantstudio.pipeline.daemon"], False),
    ([], False),
])
def test_a8_cmdline_structured_detection(cl, want):
    assert dp._is_daemon_cmdline(cl) is want


def test_a7_a9_gate_order_in_start_daemon():
    """A7/A8/A9 结构判据：alive 闸 → 第二源 → 真启动，且第三态文案四要素齐。"""
    src = (ROOT / "quantstudio" / "gui" / "tabs" / "task_tab.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    seg = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_start_daemon":
            seg = "\n".join(src.splitlines()[node.lineno - 1: node.end_lineno])
            break
    assert seg
    i_alive = seg.find("if is_daemon_running():")
    i_second = seg.find("find_running_daemon_pids()")
    i_start = seg.find("start_daemon_subprocess(")
    assert 0 <= i_alive < i_second < i_start
    for k in ("PID {first['pid']}", "启动于 {first['started_at']}", "「刷新」", "「停止」"):
        assert k in seg


def test_a9_enumeration_fails_soft(monkeypatch):
    class Boom:
        NoSuchProcess = Exception
        AccessDenied = Exception

        @staticmethod
        def process_iter(attrs=None):
            raise RuntimeError("psutil boom")

    monkeypatch.setattr(dp, "psutil", Boom)
    assert dp.find_running_daemon_pids() == []


def test_a10_registry_excludes_once_and_self(monkeypatch):
    """once（非常驻）与本进程不得被判为常驻实例（A8 的第二源不得误拦启动）。"""
    class FakeP:
        def __init__(self, pid, cl):
            self.info = {"pid": pid, "cmdline": cl, "create_time": 1700000000.0}

    fakes = [
        FakeP(1111, ["python.exe", "-m", "quantstudio.pipeline.daemon", "--mode", "forever"]),
        FakeP(2222, ["python.exe", "-m", "quantstudio.pipeline.daemon", "--mode", "once"]),
        FakeP(os.getpid(), ["python.exe", "-m", "quantstudio.pipeline.daemon"]),
    ]

    class FakePs:
        NoSuchProcess = Exception
        AccessDenied = Exception

        @staticmethod
        def process_iter(attrs=None):
            return iter(fakes)

    monkeypatch.setattr(dp, "psutil", FakePs)
    pids = [d["pid"] for d in dp.find_running_daemon_pids()]
    assert pids == [1111]


# --------------------------------------------------------------------------- #
# FP-4 · A11/A12/A13（静态；真实启停演练见证据文档「待补验」）
# --------------------------------------------------------------------------- #
def _tab_src() -> str:
    return (ROOT / "quantstudio" / "gui" / "tabs" / "task_tab.py").read_text(encoding="utf-8")


def _func_src(name: str) -> str:
    src = _tab_src()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(src.splitlines()[node.lineno - 1: node.end_lineno])
    return ""


def test_a11_stopped_state_polls_and_adapts_interval():
    upd, poll, tog = _func_src("_update_daemon_btn"), _func_src("_on_daemon_poll"), _func_src("_toggle_daemon")
    assert 'want = 3000 if state != "stopped" else 5000' in upd          # A11/A13 间隔
    assert "if not self._daemon_poll_timer.isActive():" in upd           # 定时器保持激活
    assert 'elif self._daemon_state == "stopped":' in poll               # A11/A12 探测外部启动
    assert "is_daemon_running() or bool(find_running_daemon_pids())" in poll
    assert "_sync_daemon_state()" in poll
    assert 'if self._daemon_state == "stopped":\n            self._sync_daemon_state()' in tog


def test_a12_no_self_stop_left():
    """A11/A12 关键：stopped 路径不得再自停轮询（否则外部启动永不被发现 = R-1 原缺陷）。"""
    assert "_daemon_poll_timer.stop()" not in _tab_src()


# --------------------------------------------------------------------------- #
# FP-5 · A14/A15
# --------------------------------------------------------------------------- #
def test_a14_gate_diagnosis_written(tmp_root, monkeypatch):
    monkeypatch.delenv("QS_GUI_DAEMON_DIAG", raising=False)   # 默认开
    dp.diagnose_start_gate(others=[53256], note="unit-test")
    f = tmp_root / "logs" / "gui_gate_diag.log"
    assert f.exists()
    lines = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lines and "verdict" in lines[-1]
    assert lines[-1]["proc_pids"] == [53256]
    assert lines[-1]["note"] == "unit-test"


def test_a15_gate_diagnosis_disabled_by_env(tmp_root, monkeypatch):
    monkeypatch.setenv("QS_GUI_DAEMON_DIAG", "0")
    dp.diagnose_start_gate(others=[1], note="should-not-write")
    assert not (tmp_root / "logs" / "gui_gate_diag.log").exists()
