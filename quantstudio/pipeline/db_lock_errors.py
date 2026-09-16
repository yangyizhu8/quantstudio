"""DuckDB 跨进程写锁冲突判据 —— 中立模块（T1，2026-09-17）。

**为什么中立**：判据需被两侧共用——
  pipeline 侧：writers._open_rw_with_backoff（本线 T1，重试层）
  gui 侧：db_helper._is_db_busy_error（策略研发线 A′/T3）
放任一侧都会出问题：放 gui → pipeline 依赖 gui（分层倒挂）；各写一份 → 串表分叉，
典型后果就是「重试层只认 Windows 串 → macOS 上永不重试」。故置中立位置，双方各 import 一处。

**串表两源合一**（逐条标注出处；口径的出处写在表里，不留在文档里）。

匹配规则：**小写化后子串匹配**（表内已按小写书写；短形态优先以抗变体）。
"""
from __future__ import annotations

from typing import Union

__all__ = ["LOCK_CONFLICT_PATTERNS", "is_db_lock_conflict"]

LOCK_CONFLICT_PATTERNS = (
    # ── Windows（A2 实证 · 同步件一）──
    "另一个程序正在使用此文件",          # 中文报错全文尾部
    "file is already open in",           # File is already open in <exe> (PID n)
    # ── POSIX（A2 实证 · 同步件二 §四）──
    "could not lock",                    # Could not lock <path>
    #                                      ↑ 独立形态：非 "could not set lock" 的子串
    #                                        （中间隔着 set），漏则永不重试
    "could not set lock",                # Could not set lock on file <path>
    "conflicting lock",                  # Conflicting lock is held ...
)

# 预规范化：一次小写化，避免每次热路径重复 lower()
_NORMALIZED = tuple(p.lower() for p in LOCK_CONFLICT_PATTERNS)


def is_db_lock_conflict(err: Union[BaseException, str, None]) -> bool:
    """是否为 DuckDB **跨进程写锁冲突**（busy）——应进入退避重试。

    入参同时接受异常对象与字符串（调用方两侧现有形态不一，避免为适配改动签名）。

    返回 False 的情形**明确包含**：路径不存在 / 权限不足 / 库损坏 / 参数错误。
    这些是**响亮失败**，调用方必须**立即抛**——若误判为 busy 送入 30s 退避，
    等于把响亮失败磨成哑失败，与质量判据直接冲突。
    """
    if err is None:
        return False
    text = err if isinstance(err, str) else str(err)
    if not text:
        return False
    low = text.lower()
    return any(p in low for p in _NORMALIZED)
