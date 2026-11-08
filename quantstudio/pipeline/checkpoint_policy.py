# -*- coding: utf-8 -*-
"""周期 CHECKPOINT（T2.1）与入口 CHECKPOINT（T2.3）的纯判定逻辑。

设计依据：`docs/t21-t23-implementation-plan-20261108.md`

- T2.1：daemon 轻量调度循环内、**空闲期**（不持连接）每 N 秒 CHECKPOINT 一次
- T2.3：任何进程打开库后先收敛遗留 WAL 一次
- 两者都受 `QS_PERIODIC_CHECKPOINT=0` 一键关闭（逐位回退旧行为）

本模块只放**纯函数**（便于真单测，不依赖 duckdb 连接）。
"""

from __future__ import annotations

import os
import time
from typing import Optional

# 默认周期：30 分钟（消除 #23788 的"pending WAL 长期存在"条件）
DEFAULT_CHECKPOINT_INTERVAL_S = 1800

# 环境开关（逐位回退）
ENV_SWITCH = "QS_PERIODIC_CHECKPOINT"


def periodic_checkpoint_enabled() -> bool:
    """总开关：默认开；`QS_PERIODIC_CHECKPOINT=0` 关闭。"""
    v = os.environ.get(ENV_SWITCH)
    if v is None:
        return True
    return str(v).strip() not in ("0", "false", "False", "off", "no")


def resolve_interval(config: Optional[dict], default: int = DEFAULT_CHECKPOINT_INTERVAL_S) -> int:
    """从调度配置解析周期（秒）。

    `daemon_schedule.checkpoint_interval_sec`：
      - 缺省 / None / 非法 ⇒ default（1800）
      - 0 ⇒ 关闭（返回 0，调用方据此跳过）
      - 正数 ⇒ 采用（下限 60，避免抖动）
    """
    if not isinstance(config, dict):
        return default
    raw = config.get("checkpoint_interval_sec", default)
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    if v <= 0:
        return 0
    return max(60, v)


def should_run_periodic_checkpoint(
    now_ts: float,
    last_ts: float,
    interval_s: int,
) -> bool:
    """是否该执行一次周期 CHECKPOINT。

    - 开关关闭 / interval<=0 ⇒ False
    - last_ts<=0（首次 tick）⇒ True（启动即收一次遗留 WAL，即 T2.3 的首 tick 形态）
    - 否则：now - last >= interval ⇒ True
    """
    if not periodic_checkpoint_enabled():
        return False
    if interval_s <= 0:
        return False
    if last_ts <= 0:
        return True
    return (now_ts - last_ts) >= interval_s


def next_last_ts(now_ts: float, ok: bool, last_ts: float) -> float:
    """推进 last_ts 的规则。

    - 成功 ⇒ 记为本次时间戳（正常节奏）
    - 失败 ⇒ **也推进**（避免每 tick 反复重试一个被占用/损坏的库，把失败率刷成噪声）；
      下一次仍按 interval 重试。
    """
    return float(now_ts)


def format_periodic_log(
    entry: str,
    ok: bool,
    detail: str,
    elapsed_s: float,
) -> str:
    """统一的周期 CHECKPOINT 日志行（供审计 grep `[T2.1]`）。"""
    status = "OK" if ok else "SKIP"
    return f"[T2.1] 周期 CHECKPOINT {status} entry={entry} elapsed={elapsed_s:.1f}s detail={detail}"


def format_on_open_log(entry: str, ok: bool, detail: str) -> str:
    """入口 CHECKPOINT 日志行（供审计 grep `[T2.3]`）。"""
    status = "OK" if ok else "SKIP"
    return f"[T2.3] 入口 CHECKPOINT {status} entry={entry} detail={detail}"
