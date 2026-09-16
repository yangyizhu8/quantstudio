"""T1 重试层契约测试：writers._open_rw_with_backoff（2026-09-17）。

**本文件是 T1 的真红态所在**（模块级串表测试在 test_db_lock_errors.py，那是绿态契约锁）：
  · 若重试层只认 Windows 串   → POSIX 冲突两例红（macOS 上永不重试）；
  · 若非冲突错误被误判为 busy → 「立即抛」例红（响亮失败被 30s 退避磨成哑失败）。
两条都是 A2 实证过的真实形态，故在此钉死。

退避序列在测试内替换为全 0（不睡 30s），但**尝试次数与语义按真实序列**断言。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from quantstudio.pipeline.writers import DuckDBWriter

WIN_CN = 'IO Error: Cannot open file "x": 另一个程序正在使用此文件，进程无法访问。'
WIN_EN = "IO Error: File is already open in C:\\py\\python.exe (PID 37040)"
POSIX_LOCK = 'IO Error: Could not lock file "/x/quantstudio.db"'
POSIX_SET = 'IO Error: Could not set lock on file "/x/quantstudio.db"'
NON_CONFLICT = 'IO Error: Cannot open file "x": No such file or directory'


def _writer(errors):
    """构造最小宿主：只装 _duckdb.connect 与 db_path（其余方法不参与本测）。"""
    w = DuckDBWriter.__new__(DuckDBWriter)
    w.db_path = Path("X:/fake/quantstudio.db")
    w._RW_BACKOFF_SECONDS = (0, 0, 0, 0, 0)          # 免睡；次数语义不变
    state = {"n": 0}

    def fake_connect(_path):
        i = state["n"]
        state["n"] += 1
        if i < len(errors):
            raise errors[i]
        return "CONN"

    w._duckdb = type("_D", (), {"connect": staticmethod(fake_connect)})()
    w._describe_lock_holder = lambda: None            # 归因另测，不扰本测
    return w, state


@pytest.mark.parametrize("err", [WIN_CN, WIN_EN, POSIX_LOCK, POSIX_SET,
                                 "IO Error: Conflicting lock is held by PID 9"])
def test_lock_conflict_is_retried(err):
    """锁冲突（含 Windows 中文/英文 与 POSIX 三串）→ 必须重试至成功。"""
    w, state = _writer([RuntimeError(err)])
    assert w._open_rw_with_backoff() == "CONN"
    assert state["n"] == 2, "冲突应重试一次后成功"


def test_non_conflict_raises_immediately():
    """非锁冲突（路径不存在/权限/损坏类）→ **立即抛**，绝不进退避。"""
    w, state = _writer([OSError(NON_CONFLICT), OSError(NON_CONFLICT)])
    with pytest.raises(OSError):
        w._open_rw_with_backoff()
    assert state["n"] == 1, "非冲突错误不得重试（首次即抛）"


def test_exhaustion_message_carries_four_anchors():
    """耗尽异常文本必须含四锚点（供跨线 ATTRIB_PATTERNS 解析，口径见 32eadc7）。"""
    w, state = _writer([RuntimeError(POSIX_SET)] * 6)
    with pytest.raises(RuntimeError) as ei:
        w._open_rw_with_backoff()
    msg = str(ei.value)
    for anchor in ("db_path :", "轨迹    :", "持有者  :", "原始文本:"):
        assert anchor in msg, f"耗尽文本缺锚点：{anchor}"
    assert str(w.db_path) in msg
    assert POSIX_SET in msg, "原始文本必须原样保留"


def test_exhaustion_attempts_match_backoff_sequence():
    """尝试次数 = 1（首试）+ 退避序列长度；轨迹逐次留痕。"""
    w, state = _writer([RuntimeError(POSIX_LOCK)] * 6)
    with pytest.raises(RuntimeError):
        w._open_rw_with_backoff()
    assert state["n"] == 6, "1 首试 + 5 次退避 = 6 次尝试"
