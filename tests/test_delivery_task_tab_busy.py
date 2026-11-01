# -*- coding: utf-8 -*-
"""收尾改进两项（2026-11-01 用户批）：
① 任务页 busy 占位符（渲染层，不碰 DbHelper）；② trade_calendar 排除出四态。
"""
from __future__ import annotations

import time
import types

from quantstudio.delivery import stats as S
from quantstudio.delivery.constants import TableState


# ── ① busy 占位符（纯函数 + busy 判定）──────────────────────────────────────
def _tab():
    """不实例化 Qt 控件：直接借 TaskTab 的静态/实例方法（渲染层逻辑与控件无关）。"""
    from quantstudio.gui.tabs.task_tab import TaskTab

    return TaskTab


def test_busy_placeholder_when_busy_and_empty():
    Tab = _tab()
    for raw in ("无", "—", "未知", ""):
        assert Tab._busy_placeholder(raw, True) == "读取中（库被采集占用）"


def test_busy_placeholder_keeps_real_value():
    Tab = _tab()
    assert Tab._busy_placeholder("2026-10-30", True) == "2026-10-30"
    assert Tab._busy_placeholder("无", False) == "无"
    assert Tab._busy_placeholder("—", False) == "—"


def test_busy_hint_uses_dbhelper_last_busy_at():
    Tab = _tab()
    from quantstudio.gui.db_helper import BUSY_HINT_WINDOW_S

    fresh = types.SimpleNamespace(_last_busy_at=time.time(), _busy_count=1,
                                  mw=types.SimpleNamespace())
    obj = types.SimpleNamespace(mw=types.SimpleNamespace(db_helper=fresh))
    assert Tab._busy_hint(obj) is True

    stale = types.SimpleNamespace(_last_busy_at=time.time() - BUSY_HINT_WINDOW_S - 5, _busy_count=1)
    obj2 = types.SimpleNamespace(mw=types.SimpleNamespace(db_helper=stale))
    assert Tab._busy_hint(obj2) is False

    obj3 = types.SimpleNamespace(mw=types.SimpleNamespace(db_helper=None))
    assert Tab._busy_hint(obj3) is False


# ── ② trade_calendar 排除出四态 ─────────────────────────────────────────────
def _row(table, state, basis="time"):
    return {"table": table, "state": state, "date_basis_col": basis,
            "row_count": 1, "max_date": "2026-12-31", "watermark_last_date": "2026-10-31"}


def test_calendar_excluded_from_four_states_and_attention():
    rows = [_row("trade_calendar", TableState.BEHIND), _row("etf_daily", TableState.OK)]
    # 日历表在 collect 阶段就被判为排除性标签（这里直接验证下游聚合行为）
    rows[0]["state"] = TableState.CALENDAR_NOT_APPLICABLE
    assert S.can_incremental(rows) is True, "日历表的『落后』不得影响可增量"
    attention = [r["table"] for r in S.attention_tables(rows)]
    assert "trade_calendar" not in attention, "日历表不得进『需关注』稀释信号"


def test_calendar_state_is_exclusionary_constant():
    from quantstudio.delivery import constants as C

    assert TableState.CALENDAR_NOT_APPLICABLE in C.EXCLUSIONARY_STATES
    assert TableState.CALENDAR_NOT_APPLICABLE not in C.DEFECT_STATES
    assert "trade_calendar" in C.CALENDAR_TABLES
