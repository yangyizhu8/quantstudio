"""客户 macOS 案（A-3）可复现单测——红态即缺陷在场（2026-09-14）。

客户事实链：macOS/外挂盘，GUI 判 daemon「异常退出」，但同一 pid 仍持主库写锁
⇒ 进程活着 ⇒ 误报。走查定名两处缺陷：

  D1 exe 双侧不 realpath：p.exe() 取内核真路径（macOS/Linux 经 proc_pidpath//proc/pid/exe，
     **解引用 symlink**），而 status["exe"] 记的是**启动调用路径**；客户 /Volumes/ssd/python311
     若为符号链接则二者必然不等 → 活进程被判 'stale' → 死亡误报。
  D2 「查不出来」塌缩成「它死了」：except (psutil.Error, OSError) → return "stale"，
     即任何 psutil/OS 层瞬时异常都被当成"进程已死"，而没有任何第三态。

替身纪律（GUI P0 自愈教训）：**被测函数 verify_daemon_identity 用真实实现**；
仅替身外部依赖 psutil（跨平台不可控的内核查询层），不做任何实现复制。

平台说明：真实 symlink 漂移在 Windows 上不会发生（Windows 的 p.exe() 返回调用路径），
故本测不依赖平台特性，而是以受控 psutil 直接构造两处缺陷的输入条件——
**在任意平台可复现，且锁定修复后的契约**。

红→绿：本文件当前应 FAIL（缺陷在场）；修复后应 PASS。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

import quantstudio.pipeline.daemon_lifecycle as dl


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


class _FakeProc:
    def __init__(self, *, exe=None, cmdline=None, create_time=1000.0, running=True,
                 raise_on_cmdline=None):
        self._exe = exe
        self._cmdline = cmdline or []
        self._create_time = create_time
        self._running = running
        self._raise_on_cmdline = raise_on_cmdline

    def is_running(self):
        return self._running

    def create_time(self):
        return self._create_time

    def exe(self):
        return self._exe

    def cmdline(self):
        if self._raise_on_cmdline is not None:
            raise self._raise_on_cmdline
        return self._cmdline


class _FakePsutil:
    """仅替身外部依赖：内核查询层（Process/异常类）。"""

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    class Error(Exception):
        pass

    def __init__(self, proc):
        self._proc = proc

    def Process(self, pid):  # noqa: N802 - 与 psutil 同名
        if self._proc is None:
            raise _FakePsutil.NoSuchProcess(pid)
        return self._proc


def _install(monkeypatch, proc):
    monkeypatch.setattr(dl, "psutil", _FakePsutil(proc))


def _status(**over):
    base = {
        "pid": 36456,
        "create_time": 1000.0,
        "exe": "/Volumes/ssd/python311/bin/python3.11",   # 启动调用路径（symlink）
        "cmdline": ["/Volumes/ssd/python311/bin/python3.11", "-m",
                    "quantstudio.pipeline.daemon"],
    }
    base.update(over)
    return base


def test_alive_baseline(monkeypatch):
    """基线：五项全过应判 alive（保证本测其余用例的差异只来自被考校验项）。"""
    _install(monkeypatch, _FakeProc(
        exe="/Volumes/ssd/python311/bin/python3.11",
        cmdline=["python3.11", "-m", "quantstudio.pipeline.daemon"],
        create_time=1000.0))
    assert dl.verify_daemon_identity(_status()) == "alive"


def test_d1_exe_realpath_drift_must_not_mean_death(monkeypatch):
    """D1：内核真路径（symlink 已解）vs status 记录调用路径 → 现在判 stale（=死亡误报）。

    修复方向：exe 双侧 realpath 容错——两侧都取 realpath 后相等即通过。
    期望（绿）：alive；当前（红）：stale。
    """
    real = "/Volumes/ssd/python311-real/bin/python3.11"      # 内核真路径（symlink 目标）
    _install(monkeypatch, _FakeProc(
        exe=real,
        cmdline=["python3.11", "-m", "quantstudio.pipeline.daemon"],
        create_time=1000.0))
    assert dl.verify_daemon_identity(_status()) == "alive", (
        "活进程因 symlink 真路径漂移被判 stale —— 这正是客户案的死亡误报路径")


def test_d2_unknown_query_failure_must_not_mean_death(monkeypatch):
    """D2：内核查询瞬时失败（psutil.Error）→ 现在塌缩成 stale（=死亡）。

    修复方向：identity 失败三态化——查询不能确证时返回独立态（如 'unknown'），
    由调用方按"不确定"处置（去抖/不摘 token/不停轮询），不得等同死亡。
    期望（绿）：非 'stale'（独立第三态）；当前（红）：'stale'。
    """
    _install(monkeypatch, _FakeProc(
        exe="/Volumes/ssd/python311/bin/python3.11",
        cmdline=["python3.11", "-m", "quantstudio.pipeline.daemon"],
        create_time=1000.0,
        raise_on_cmdline=_FakePsutil.Error("transient kernel query failure")))
    verdict = dl.verify_daemon_identity(_status())
    assert verdict != "stale", (
        "「查不出来」被塌缩成「死了」——未知必须自成一态（族谱第四例）")


def test_access_denied_still_distinct(monkeypatch):
    """回归：AccessDenied 已有第三态 denied，修复不得把它并回 stale。"""
    _install(monkeypatch, None)
    monkeypatch.setattr(dl.psutil, "Process",
                        lambda pid: (_ for _ in ()).throw(_FakePsutil.AccessDenied(pid)))
    assert dl.verify_daemon_identity(_status()) == "denied"


# ==================== 方案 V4/V5 验收 ====================
def test_v4_is_daemon_running_unknown_is_not_death(monkeypatch):
    """V4：unknown 经 is_daemon_running **不得**收敛为 False（调用侧不被吃掉）。"""
    import quantstudio.gui.daemon_process as dp
    monkeypatch.setattr(dp, "read_daemon_status", lambda: {"pid": 36456})
    monkeypatch.setattr(dp, "verify_daemon_identity", lambda s: "unknown")
    assert dp.is_daemon_running() is True, (
        "D2 三态若在 is_daemon_running 收敛为 False，D2 修复即被调用侧完全吃掉")


def test_v5_debounce_requires_three_consecutive(app, monkeypatch):
    """V5：去抖 N=3——前 2 次不清理且状态栏可见进度，第 3 次才判死。"""
    import pandas as pd
    from quantstudio._paths import DATA_ROOT
    from quantstudio.gui.tabs.task_tab import TaskTab as _TaskTab

    class _Db:
        def get_watermarks(self):
            return pd.DataFrame(columns=["table_name", "freq", "source", "watermark"])

    class _MW:
        def __init__(self):
            self.app_root = DATA_ROOT.parent
            self.config_dir = DATA_ROOT.parent / "config" / "profiles" / "mcp_only"
            self.current_profile = "mcp_only"
            self.db_helper = _Db()
            self.workers = []

        def profile_options(self):
            return [("mcp_only", "MCP-only")]

        def hold_worker(self, w):
            self.workers.append(w)

    tab = _TaskTab(_MW())
    tab._daemon_state = "running"
    monkeypatch.setattr("quantstudio.gui.tabs.task_tab.is_daemon_running", lambda: False)
    tab._on_daemon_poll()
    assert tab._daemon_state == "running"
    assert tab._daemon_death_streak == 1
    assert "1/3" in tab.status_label.text()
    tab._on_daemon_poll()
    assert tab._daemon_state == "running"
    assert tab._daemon_death_streak == 2
    assert "2/3" in tab.status_label.text()
    tab._on_daemon_poll()
    assert tab._daemon_state == "stopped"
    assert tab.status_label.text() == "⚠ 常驻进程异常退出"
