#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WAL 体积巡检 + 安全检查点（笔4，2026-09-23 GUI 启动卡死案）。

用途
----
主库残留大 WAL 会让**下一次任何打开**都先回放 WAL（实测 2.33 GB → 22.3 分钟），
GUI/daemon 启动因此长时间卡住。本脚本做两件事：
  1) **巡检**：报告 WAL 体积，超阈值（默认 256 MB）即告警（退出码 1）；
  2) **--checkpoint**：在**确认 daemon 不在运行**的前提下执行安全检查点，收敛 WAL。

**安全前置（方案 §4.3，关键）**：本机 duckdb 1.4.5 下 `RW open` 遇他人持锁会
**长时间阻塞**——若在 daemon 采集中强行检查点，巡检自己就会变成新的卡死进程。
故 `--checkpoint` 必须先通过 daemon 运行探测；且检查点自带超时放弃。

运行宿主与周期（方案 §4.5）：推荐由 daemon 空闲期附带执行（见 daemon close 的笔3），
本脚本作为**独立兜底**（计划任务每 30 min）与**人工运维入口**。

用法
----
    python scripts/wal_health_check.py                     # 只巡检（默认）
    python scripts/wal_health_check.py --json              # 巡检结果 JSON
    python scripts/wal_health_check.py --checkpoint        # 巡检 + 安全检查点
    python scripts/wal_health_check.py --threshold-mb 128  # 自定义阈值
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wal_health_check")

EXIT_OK = 0
EXIT_ALERT = 1          # WAL 超阈值（或检查点失败）
EXIT_PRECONDITION = 2   # 前置条件不满足（daemon 在运行等）


def _default_db_path() -> Path:
    from quantstudio._paths import db_path

    return Path(db_path())


def _daemon_running() -> tuple[bool, str]:
    """daemon 运行探测（status 文件 + 进程存活双重校验）。

    只用只读手段：读 `data/daemon_status.json` 的 pid，再用 psutil 校验存活；
    psutil 不可用时退化为「status 文件存在即视为可能在运行」（保守，宁可不检查点）。
    """
    from quantstudio._paths import DATA_ROOT

    status_file = Path(DATA_ROOT) / "daemon_status.json"
    if not status_file.exists():
        return False, "无 status 文件"
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception as e:
        return True, f"status 文件不可解析（保守视为运行中）: {e}"
    if str(status.get("status", "")).lower() not in ("running", "stopping", "stop_requested"):
        return False, f"status={status.get('status')}"
    pid = status.get("pid")
    if not pid:
        return True, "status=running 但无 pid（保守视为运行中）"
    try:
        import psutil
    except Exception:
        return True, f"status=running（pid={pid}，无 psutil 无法校验存活 → 保守视为运行中）"
    try:
        alive = psutil.pid_exists(int(pid))
    except Exception:
        return True, f"status=running（pid={pid} 存活校验失败 → 保守视为运行中）"
    if alive:
        return True, f"status=running 且 pid={pid} 存活"
    return False, f"status 残留但 pid={pid} 已不存在"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WAL 体积巡检 + 安全检查点")
    ap.add_argument("--db", default=None, help="主库路径（默认 data/quantstudio.db）")
    ap.add_argument("--threshold-mb", type=float, default=256.0, help="WAL 告警阈值（MB，默认 256）")
    ap.add_argument("--checkpoint", action="store_true", help="巡检后执行安全检查点（需 daemon 不在运行）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出巡检结果")
    args = ap.parse_args(argv)

    from quantstudio.pipeline.db_checkpoint import checkpoint_database, wal_health

    db = Path(args.db) if args.db else _default_db_path()
    threshold = int(args.threshold_mb * 1024 * 1024)
    health = wal_health(db, threshold_bytes=threshold)

    if args.json:
        print(json.dumps(health, ensure_ascii=False, indent=2))
    else:
        print(f"[wal_health] 主库: {health['db_path']}")
        print(f"[wal_health] WAL : {health['wal_mb']} MB（阈值 {health['threshold_mb']} MB）"
              f" → {'**超阈值**' if health['over_threshold'] else '正常'}")
        print(f"[wal_health] 路径: {health['wal_path']}")

    if not args.checkpoint:
        if health["over_threshold"]:
            logger.warning(
                "[wal_health] WAL 超阈值：下一次任何打开都要先回放 WAL，GUI/daemon 启动会长时间卡住。"
                "请用 --checkpoint 在 daemon 停止后收敛（或等待 daemon 轮次收尾的自动检查点）。")
            return EXIT_ALERT
        return EXIT_OK

    running, reason = _daemon_running()
    if running:
        logger.warning(f"[wal_health] 跳过检查点：daemon 疑似在运行（{reason}）。"
                       f"在采集中强行 RW 打开会长时间阻塞（防自卡死），请先停止 daemon。")
        return EXIT_PRECONDITION

    ok, detail = checkpoint_database(db)
    after = wal_health(db, threshold_bytes=threshold)
    print(f"[wal_health] 检查点: ok={ok} ({detail})；WAL 现为 {after['wal_mb']} MB")
    if not ok:
        return EXIT_ALERT
    return EXIT_ALERT if after["over_threshold"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
