# -*- coding: utf-8 -*-
"""Windows 环境探测（plan §7 / FR-003①②、FR-013②）。

每项探测都返回 ``(ok, detail)``，**不抛异常**（探测本身失败也是一种探测结果，
由调用方 fail-closed）。非 Windows 平台自动降级为等价的可移植做法，便于在
CI / 非 Windows 上跑单测。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple

# 同步盘特征（路径或环境变量前缀命中即警示，不阻断）
SYNC_HINTS = ("onedrive", "dropbox", "nutstore", "坚果云", "百度网盘", "baidunetdisk", "google drive")
SYNC_ENV_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer", "DROPBOX_PATH")

MAX_PATH_SOFT = 255   # 超过即警示（NTFS 单路径上限 260，留余量给临时名后缀）


def is_windows() -> bool:
    return os.name == "nt"


def probe_dir_writable(directory: Path) -> Tuple[bool, str]:
    """目录可写探测：create + delete 临时文件（FR-003① 的目录侧）。"""
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".qs_delivery_probe_", dir=str(directory))
        os.close(fd)
        os.unlink(name)
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def probe_file_handle(path: Path) -> Tuple[bool, str]:
    """文件句柄探测：以读写方式打开并尝试非阻塞独占锁（Windows 才做字节锁）。

    失败通常意味着杀软实时扫描 / Explorer 预览窗格 / 同步客户端持有句柄。
    """
    path = Path(path)
    if not path.exists():
        return False, "文件不存在"
    try:
        fd = os.open(str(path), os.O_RDWR | getattr(os, "O_BINARY", 0))
    except Exception as exc:
        return False, f"无法以读写方式打开（{type(exc).__name__}: {exc}）"
    try:
        if is_windows():
            import msvcrt

            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                return False, f"被其他进程占用（Windows 字节锁失败：{exc}）"
            finally:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    import msvcrt as _m

                    _m.locking(fd, _m.LK_UNLCK, 1)
                except Exception:
                    pass
        return True, "ok"
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


def detect_sync_dir(path: Path) -> Tuple[bool, str]:
    """同步盘特征检测（路径特征 / 环境变量前缀 / reparse 属性）。仅警示，不阻断。"""
    text = str(Path(path)).lower()
    for hint in SYNC_HINTS:
        if hint in text:
            return True, f"路径含同步盘特征「{hint}」"
    for var in SYNC_ENV_VARS:
        prefix = os.environ.get(var)
        if prefix and text.startswith(str(Path(prefix)).lower()):
            return True, f"路径位于环境变量 {var} 指向的同步目录"
    try:
        attrs = os.stat(path).st_file_attributes  # type: ignore[attr-defined]
        if attrs & getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return True, "路径含 reparse point（可能是同步盘/挂载点）"
    except Exception:
        pass
    return False, "ok"


def check_path_length(path: Path, reserve: int = 32) -> Tuple[bool, str]:
    """路径长度预算检查：加上临时名后缀仍须 < 260。"""
    length = len(str(Path(path)))
    if length + reserve > MAX_PATH_SOFT + 5:
        return False, f"路径长度 {length} 字符，加上临时后缀可能超 MAX_PATH（建议缩短）"
    return True, f"{length} 字符"


def extended_path(path: Path) -> str:
    """内部使用的 `\\\\?\\` 长路径前缀（仅 Windows；manifest 仍写相对路径）。"""
    if not is_windows():
        return str(path)
    resolved = str(Path(path).resolve())
    if resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def platform_tag() -> str:
    """manifest.producer.platform 的取值（诊断用，稳定可读）。"""
    return f"{sys.platform}-{os.name}"
