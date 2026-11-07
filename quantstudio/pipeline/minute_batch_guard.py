# -*- coding: utf-8 -*-
"""分钟批次完整性守卫：1.5 动态分片下限 + 1.4 逐日覆盖对账（纯函数，无 IO）。

设计依据：docs/minute-newday-truncation-fix-design-20261108.md v4 §3 腿1
- 1.4 执行级逐日覆盖对账：实际拉取分片日集 ⊇ 声明窗口内全部交易日
- 1.5 批次分片数下限（动态）：下限 = 批次内交易日数 × 26 × 0.9

实证背景（2026-11-05 轮）：
- 批次 10（11-04→11-05）只返回 8 shards / 397,895 行（完整应 26 shards / 约 126 万行）
- 被静默接受 ⇒ 11-04 缺 219 只证券
- 固定下限 23 会漏判双日批（51 片只到 25 也不报）⇒ 必须按批次交易日数动态算

本模块刻意保持**纯函数 + 零依赖**（不 import duckdb/pandas），便于单测与复用。
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Set, Tuple

# 每分钟表单个交易日的预期分片数（实证：11-05 轮历史批稳定为 26 片/日）
SHARDS_PER_TRADING_DAY = 26

# 完整性容忍比例（90%：允许少量分片缺失，但 8/26=31% 会被判不完整）
DEFAULT_COMPLETENESS_RATIO = 0.9


def min_shards_required(
    trading_days: int,
    shards_per_day: int = SHARDS_PER_TRADING_DAY,
    ratio: float = DEFAULT_COMPLETENESS_RATIO,
) -> int:
    """1.5 批次分片数下限（动态）。

    下限 = ceil(批次内交易日数 × 每日片数 × ratio)。

    - 单日批：26 × 0.9 = 23.4 → 24
    - 双日批：52 × 0.9 = 46.8 → 47

    Args:
        trading_days: 本批次覆盖的交易日数（>0）
        shards_per_day: 单个交易日的预期分片数
        ratio: 容忍比例（0<ratio<=1）

    Returns:
        该批次至少应获得的分片数

    Raises:
        ValueError: 参数非法
    """
    if trading_days <= 0:
        raise ValueError(f"trading_days 必须 > 0，实际 {trading_days}")
    if shards_per_day <= 0:
        raise ValueError(f"shards_per_day 必须 > 0，实际 {shards_per_day}")
    if not (0 < ratio <= 1):
        raise ValueError(f"ratio 必须在 (0, 1]，实际 {ratio}")

    raw = trading_days * shards_per_day * ratio
    # ceil：宁可多要求一点，也不放过边界
    required = int(raw)
    if required < raw:
        required += 1
    return required


def is_shard_complete(
    actual_shards: int,
    trading_days: int,
    shards_per_day: int = SHARDS_PER_TRADING_DAY,
    ratio: float = DEFAULT_COMPLETENESS_RATIO,
) -> bool:
    """1.5 判定：本批次分片数是否达标。"""
    if actual_shards < 0:
        raise ValueError(f"actual_shards 不能为负，实际 {actual_shards}")
    return actual_shards >= min_shards_required(trading_days, shards_per_day, ratio)


def expected_trading_days(
    start: str,
    end: str,
    calendar_days: Iterable[str],
) -> List[str]:
    """1.4 对账用：声明窗口 [start, end] 内应覆盖的交易日列表（升序去重）。

    Args:
        start: 'YYYY-MM-DD'（含）
        end: 'YYYY-MM-DD'（含）
        calendar_days: 交易日历（'YYYY-MM-DD' 字符串集合，已剔除周末/节假日）

    Returns:
        窗口内交易日列表（升序、去重）
    """
    normalized = {str(d)[:10] for d in calendar_days}
    picked = sorted(d for d in normalized if start <= d <= end)
    return picked


def reconcile_daily_coverage(
    declared_start: str,
    declared_end: str,
    calendar_days: Iterable[str],
    fetched_days: Set[str],
) -> Tuple[bool, List[str]]:
    """1.4 执行级逐日覆盖对账：实拉分片日集是否覆盖声明窗口全部交易日。

    Args:
        declared_start / declared_end: 本轮声明的增量窗口（含两端）
        calendar_days: 交易日历（'YYYY-MM-DD'）
        fetched_days: 实际拿到分片的日期集合

    Returns:
        (是否完整, 缺失交易日列表)

    Note:
        缺失日列表是「缺口账本」的输入；下一轮 union 必须重含这些日期。
    """
    expected = expected_trading_days(declared_start, declared_end, calendar_days)
    got = {str(d)[:10] for d in fetched_days}
    missing = [d for d in expected if d not in got]
    return (len(missing) == 0, missing)


def classify_batch_days(
    batch_start: str,
    batch_end: str,
    calendar_days: Iterable[str],
) -> List[str]:
    """把批次窗口展开为交易日列表（供 min_shards_required 计算 trading_days）。

    Examples:
        单日批 ('2026-11-04','2026-11-04') → ['2026-11-04']
        双日批 ('2026-10-19','2026-10-20') → ['2026-10-19','2026-10-20']
        跨周末 ('2026-10-23','2026-10-24') → ['2026-10-23']（10-24 周六不在日历）
    """
    return expected_trading_days(batch_start, batch_end, calendar_days)
