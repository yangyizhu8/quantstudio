"""分红事件 payload 归一化与 hash（中立模块，无循环依赖）。

本模块从 ``qfq_event_discovery`` 抽取分红字段的归一化（``norm_div_val``，原
``_norm_div_val``）与 payload hash（``dividend_payload_hash``），供 discovery 的
scan/establish_baseline 共用，**避免两处各自重列字段导致 hash 口径漂移**。

依赖方向（铁律，单向，禁止反向）：
    qfq_event_discovery  →  本模块  →  qfq_orchestrator_types.payload_hash_of
本模块**只**依赖底层 ``payload_hash_of``，不依赖任何上层编排/发现模块，杜绝循环 import。

hash 字段口径与历史 ``scan_stock_dividend``（``qfq_event_discovery.py:175-187``）逐字一致，
确保历史 trigger 的 ``payload_hash`` 可比对的语义不变（v2.4 MCP cutover 设计 §3.2.3）。
"""
from __future__ import annotations

import math
from typing import Any

from quantstudio.pipeline.qfq_orchestrator_types import payload_hash_of

__all__ = ["norm_div_val", "dividend_payload_hash"]


def norm_div_val(v: Any) -> str:
    """统一规范化分红字段——None/NaN → ''；字符串数值 → strip。保证 hash 稳定。

    从 ``qfq_event_discovery._norm_div_val`` 逐字搬迁（改名去下划线前缀，作中立模块公共 API）。
    """
    if v is None:
        return ""
    try:
        if isinstance(v, float) and math.isnan(v):
            return ""
    except TypeError:
        pass
    return str(v).strip()


def dividend_payload_hash(code: Any, ex_date: Any, record_date: Any,
                          ann_date: Any, end_date: Any,
                          cash_div_before_tax: Any, cash_div_after_tax: Any,
                          cash_div: Any, stk_div: Any,
                          stk_bo_rate: Any, stk_co_rate: Any,
                          div_rat: Any, div_proc: Any) -> str:
    """分红事件 payload hash（与 ``scan_stock_dividend:175-187`` 逐字一致）。

    字段顺序、归一化（``norm_div_val``）、ann_date/end_date 的 int 转换必须与历史实现
    一致，否则跨批次 payload_hash 漂移会误判为 revision。供 discover 的 scan 与
    establish_discovery_baseline 共用，绝不各自重列字段。
    """
    return payload_hash_of([
        code, ex_date, record_date,
        int(ann_date) if ann_date is not None else None,
        int(end_date) if end_date is not None else None,
        norm_div_val(cash_div_before_tax),
        norm_div_val(cash_div_after_tax),
        norm_div_val(cash_div),
        norm_div_val(stk_div),
        norm_div_val(stk_bo_rate),
        norm_div_val(stk_co_rate),
        norm_div_val(div_rat),
        norm_div_val(div_proc),
    ])
