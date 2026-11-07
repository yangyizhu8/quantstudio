# -*- coding: utf-8 -*-
"""1.6 最新交易日批次「挂账 + 下轮重拉」纯函数（零 IO，可单测）。

设计依据：docs/minute-newday-truncation-fix-design-20261108.md v4 §3 腿1 的 1.6

语义（关键：按【批次窗口】判定，不是按单个交易日）：
- **批次窗口含「最近交易日」且分片不足** ⇒ 不在本轮判失败，而是**挂账**
  （晨间轮与云端入库节奏赛跑：07:44 拉 11-04→11-05 时，11-05 尚未收盘、11-04 可能仍在入库）
- **历史批次（不含最近交易日）分片不足** ⇒ **本轮记失败**（真的坏，不许延后）
- 下一轮（数据就绪后）**强制重拉账本未清日**，补齐后销账

实证锚点（2026-11-05 轮）：批次 10 = `11-04 → 11-05` 只得 8 shards（需 24+）
⇒ 该批次窗口**含** 11-05（当时最新交易日）⇒ 应挂账而非失败。

与 1.5 的分工：
- 1.5 回答「这片数够不够」
- 1.6 回答「不够时该判失败还是挂账」
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Set

ACTION_NONE = "none"          # 已完整，无需动作
ACTION_DEFER = "defer"        # 批次含最新交易日且不足 ⇒ 挂账，下轮重拉
ACTION_FAIL = "fail"          # 历史批次不足 ⇒ 本轮记失败


def batch_contains_day(batch_start: str, batch_end: str, day: str) -> bool:
    """批次窗口 [batch_start, batch_end] 是否包含某日（ISO 字符串比较）。"""
    s, e, d = str(batch_start)[:10], str(batch_end)[:10], str(day)[:10]
    return s <= d <= e


def should_defer_gap(
    batch_start: str,
    batch_end: str,
    latest_trading_day: str,
    shards_actual: int,
    shards_required: int,
) -> bool:
    """该批次是否应「挂账延后」而非「本轮判失败」。

    规则：
    - 分片已达标 ⇒ False（无需动作）
    - 未达标 且 批次窗口**含** latest_trading_day ⇒ True（挂账）
    - 未达标 且 批次窗口**不含** latest_trading_day ⇒ False（真失败）

    Args:
        batch_start / batch_end: 本批次窗口 'YYYY-MM-DD'
        latest_trading_day: 本轮「最新已收盘交易日」
        shards_actual: 实际分片数
        shards_required: 1.5 算出的下限

    Note:
        用 ISO 字符串比较日期是安全的（字典序 == 时间序）。
    """
    if shards_actual >= shards_required:
        return False
    return batch_contains_day(batch_start, batch_end, latest_trading_day)


def classify_gap_action(
    batch_start: str,
    batch_end: str,
    latest_trading_day: str,
    shards_actual: int,
    shards_required: int,
) -> str:
    """三态分类：none / defer / fail（便于日志与落库）。"""
    if shards_actual >= shards_required:
        return ACTION_NONE
    if batch_contains_day(batch_start, batch_end, latest_trading_day):
        return ACTION_DEFER
    return ACTION_FAIL


def merge_forced_days(
    window_days: Iterable[str],
    ledger_days: Iterable[str],
) -> List[str]:
    """下一轮重拉日集 = 声明窗口日集 ∪ 账本未清日集（升序去重）。

    这是「自愈闭环」的核心：只要账本未清，该日必被重含。
    """
    merged = {str(d)[:10] for d in window_days} | {str(d)[:10] for d in ledger_days}
    return sorted(merged)


def build_gap_record(
    table: str,
    batch_start: str,
    batch_end: str,
    shards_actual: int,
    shards_required: int,
    action: str,
    reason: str = "",
) -> Dict[str, object]:
    """构造一条账本记录（供调用方写入 minutes_gap_ledger）。

    返回纯 dict，不触库。账本以**交易日**为粒度（批次区间内的交易日由调用方展开）。
    """
    return {
        "table_name": table,
        "batch_start": str(batch_start)[:10],
        "batch_end": str(batch_end)[:10],
        "shards_actual": int(shards_actual),
        "shards_required": int(shards_required),
        "action": action,
        "reason": reason,
    }


def ledger_days_to_retry(records: Iterable[Dict[str, object]]) -> Set[str]:
    """从账本记录提取需重拉的日期（defer / fail 均需重拉）。

    批次区间会展开为端点（保守：两端都重含，由日集 union 去重）。
    """
    out: Set[str] = set()
    for r in records:
        act = str(r.get("action", ""))
        if act in (ACTION_DEFER, ACTION_FAIL):
            for k in ("batch_start", "batch_end", "trade_date"):
                v = r.get(k)
                if v:
                    out.add(str(v)[:10])
    return out
