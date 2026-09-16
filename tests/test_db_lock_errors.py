"""T1 判据契约测试：db_lock_errors（2026-09-17）。

红/绿说明（诚实标注）：本模块为**新建**，其"红态"即"模块不存在"（此前两侧各自维护串表，
策略研发侧曾因缺 POSIX 串而在 macOS 上把锁冲突判为非 busy）。模块落地后本文件即为**契约锁**——
真正的红→绿在 **T1 重试层**（writers._open_rw_with_backoff，见 tests/test_writers_rw_backoff.py），
那里"重试层只认 Windows 串 → macOS 永不重试"必须能被红态用例捕获。

参数化遍历 LOCK_CONFLICT_PATTERNS：**每条串一条用例**——将来任何人删改串表，
对应用例立刻红，避免"漏一条串"这种静默缺陷。
"""
from __future__ import annotations

import pytest

from quantstudio.pipeline.db_lock_errors import (
    LOCK_CONFLICT_PATTERNS,
    is_db_lock_conflict,
)

# 每条串对应一条**真实形态**的 DuckDB 报错文本（A2 实证来源见模块 docstring）
PATTERN_SAMPLES = {
    "另一个程序正在使用此文件":
        'IO Error: Cannot open file "data/quantstudio.db": 另一个程序正在使用此文件，进程无法访问。',
    "file is already open in":
        "IO Error: File is already open in C:\\Python311\\python.exe (PID 37040)",
    "could not lock":
        'IO Error: Could not lock file "/Users/x/data/quantstudio.db"',
    "could not set lock":
        'IO Error: Could not set lock on file "/Users/x/data/quantstudio.db"',
    "conflicting lock":
        "IO Error: Conflicting lock is held by PID 37040",
}

# 非冲突：**不得**判为 busy（否则被送入 30s 退避 = 把响亮失败磨成哑失败）
NON_CONFLICT_SAMPLES = {
    "path_not_found": 'IO Error: Cannot open file "x": No such file or directory',
    "permission_denied": "IO Error: Permission denied opening database",
    "corrupted": "Invalid Input Error: database file is corrupted",
}


def test_pattern_table_is_complete_and_mapped():
    """串表与样例表必须一一对应——防止新增串却漏写用例（静默缺口）。"""
    missing = [p for p in LOCK_CONFLICT_PATTERNS if p not in PATTERN_SAMPLES]
    assert not missing, f"新增串未配样例（用例会漏）：{missing}"
    assert len(LOCK_CONFLICT_PATTERNS) == len(set(LOCK_CONFLICT_PATTERNS)), "串表存在重复项"


@pytest.mark.parametrize("pattern", LOCK_CONFLICT_PATTERNS)
def test_each_pattern_is_recognized(pattern):
    """每条串都必须被判为锁冲突（含 Windows 中文串与 POSIX 两条易漏形态）。"""
    sample = PATTERN_SAMPLES[pattern]
    assert pattern in sample.lower(), "样例文本未包含该串（样例写错）"
    assert is_db_lock_conflict(sample) is True, f"串未被识别：{pattern!r}"


@pytest.mark.parametrize("name", sorted(NON_CONFLICT_SAMPLES))
def test_non_conflict_errors_are_not_busy(name):
    """路径不存在/权限/库损坏 → False，调用方须**立即抛**，不得进退避。"""
    assert is_db_lock_conflict(NON_CONFLICT_SAMPLES[name]) is False


def test_accepts_exception_object_and_rejects_none():
    """双入参契约：异常对象与字符串等效；None/空串安全返回 False（不得抛）。"""
    assert is_db_lock_conflict(RuntimeError('Could not set lock on file "/x"')) is True
    assert is_db_lock_conflict(None) is False
    assert is_db_lock_conflict("") is False


def test_posix_could_not_lock_is_not_shadowed_by_set_variant():
    """回归钉子：'could not lock' 与 'could not set lock' 非子串关系，
    仅前者出现的文本也必须命中（漏它则该形态永不重试）。"""
    text = 'IO Error: Could not lock file "/x.db"'
    assert "could not set lock" not in text.lower()
    assert is_db_lock_conflict(text) is True
