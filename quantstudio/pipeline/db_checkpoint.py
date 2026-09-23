# -*- coding: utf-8 -*-
"""DuckDB 检查点与 WAL 健康巡检（笔3 / 笔4 共享）。

背景（2026-09-23 GUI 启动卡死案）：主库残留 **2.17 GB 未检查点 WAL** → 任何
`duckdb.connect()` 都要先**回放 WAL**（生产实测 **1338.4 s ≈ 22.3 分钟**），
而 GUI 把这次打开放在 `MainWindow` 构造期同步执行 → 窗口永不出现。
只读连接**无法收敛 WAL**（副本实验实证：read_only 打开会回放但不写回），
故收敛责任只能落在**写者**（daemon 轮次收尾）与**巡检/维护脚本**。

本模块提供：
  1) `checkpoint_database(db_path, timeout_s)` —— 库空闲时的**安全检查点**；
  2) `wal_health(db_path, threshold_bytes)` —— WAL 体积巡检（供告警）。

**安全约束（方案 §4.3，必须遵守）**：本机 duckdb 1.4.5 下，`RW open` 遇他人持锁会
**长时间阻塞**（09-23 案即此形态：多个进程卡在 connect 上）。因此：
  - 调用方须先确认 daemon 不在运行（或本进程即写者且已释放连接）；
  - 检查点一律在**工作线程**执行并**超时放弃**（daemon 线程，不阻塞进程退出）；
  - 任何失败都**不抛异常**、不阻断调用方（daemon 收尾路径必须安全）。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# 检查点主调方等待上限（实测 2.33 GB WAL 的检查点本体仅 7.3 s；给足余量）
DEFAULT_CHECKPOINT_TIMEOUT_S = 120.0
# WAL 体积告警阈值（方案 §4.5：超阈值 + 连续命中才告警）
DEFAULT_WAL_THRESHOLD_BYTES = 256 * 1024 * 1024


def wal_path(db_path) -> Path:
    """DuckDB WAL 文件路径（与主库同目录、同名 + `.wal`）。"""
    return Path(str(db_path) + ".wal")


def wal_size_bytes(db_path) -> int:
    """WAL 体积（字节）；不存在/不可读 → 0（巡检不得因权限问题抛错）。"""
    p = wal_path(db_path)
    try:
        return p.stat().st_size if p.exists() else 0
    except OSError:
        return 0


def wal_health(db_path, threshold_bytes: int = DEFAULT_WAL_THRESHOLD_BYTES) -> dict:
    """WAL 健康巡检结果（结构化，供日志/告警/GUI 提示）。"""
    size = wal_size_bytes(db_path)
    return {
        "db_path": str(db_path),
        "wal_path": str(wal_path(db_path)),
        "wal_bytes": size,
        "wal_mb": round(size / 1e6, 1),
        "threshold_bytes": threshold_bytes,
        "threshold_mb": round(threshold_bytes / 1e6, 1),
        "over_threshold": size > threshold_bytes,
    }


def checkpoint_database(
    db_path,
    timeout_s: float = DEFAULT_CHECKPOINT_TIMEOUT_S,
) -> Tuple[bool, str]:
    """在库空闲时执行 `CHECKPOINT`（带超时放弃）。返回 `(ok, detail)`。

    - **无 WAL 时是廉价空操作**（直接返回，不打开库）→ 可安全地每轮调用；
    - 超时/失败一律不抛异常、不阻塞调用方；结果写 INFO/WARNING 日志（可观测）；
    - 线程为 **daemon**：即使永久阻塞（他人持锁）也不拖住进程退出。
    """
    before = wal_size_bytes(db_path)
    if before == 0:
        return True, "wal=0，无需检查点"

    result: dict = {}

    def _run() -> None:
        try:
            import duckdb

            conn = duckdb.connect(str(db_path))
            try:
                conn.execute("CHECKPOINT")
            finally:
                conn.close()
            result["ok"] = True
        except BaseException as e:  # noqa: BLE001 — 收尾路径不得抛出
            result["error"] = e

    t0 = time.time()
    t = threading.Thread(target=_run, daemon=True, name="duckdb-checkpoint")
    t.start()
    t.join(timeout_s)

    if t.is_alive():
        logger.warning(
            f"[db_checkpoint] CHECKPOINT 超时（>{timeout_s}s，疑库被占用）→ 放弃本次检查点，"
            f"不阻塞调用方: {db_path}")
        return False, f"timeout>{timeout_s}s"

    if result.get("ok"):
        after = wal_size_bytes(db_path)
        logger.info(
            f"[db_checkpoint] CHECKPOINT 完成: wal {before / 1e6:.1f}MB → {after / 1e6:.1f}MB，"
            f"耗时 {time.time() - t0:.1f}s")
        return True, f"wal {before}->{after}"

    err = result.get("error")
    logger.warning(f"[db_checkpoint] CHECKPOINT 失败（跳过，不阻断）: {type(err).__name__}: {err}")
    return False, f"{type(err).__name__}: {err}"
