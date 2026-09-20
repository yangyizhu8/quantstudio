# -*- coding: utf-8 -*-
"""同 candidate 幂等短路单测（2026-09-20 批准，建议 1）。

纯函数 should_skip_deferred_replay：四条判据 + 两条保守分支 + 环境回退开关。
零主库依赖：内存 DuckDB 合成 qfq_watermark_intent。
"""
from __future__ import annotations
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.daemon import (  # noqa: E402
    DEFERRED_SHORTCIRCUIT_ENV,
    FORCED_REPLAY_DAYS,
    deferred_shortcircuit_enabled,
    should_skip_deferred_replay,
)

NOW = 1_800_000_000.0
WM = 1_789_023_600_000          # 2026-09-11（实测存储水位）
CAND = 1_789_455_600_000        # 2026-09-16（实测 candidate）


def _conn(with_intent=True, status="pending", cand=CAND):
    c = duckdb.connect(":memory:")
    if with_intent:
        c.execute("CREATE TABLE qfq_watermark_intent (cycle_id VARCHAR, source VARCHAR,"
                  " table_name VARCHAR, freq VARCHAR, candidate_watermark VARCHAR,"
                  " status VARCHAR)")
        c.execute("INSERT INTO qfq_watermark_intent VALUES ('cyc_1','mcp','etf_minutes','1min',?,?)",
                  [str(cand), status])
    return c


def _call(conn, *, a4=0, last_pull=NOW - 3600, wm=str(WM), table="etf_minutes"):
    return should_skip_deferred_replay(conn, wm, source="mcp", table=table, freq="1min",
                                       a4_verdict=a4, last_pull_ts=last_pull, now_ts=NOW)


# ── 判据① A4 裁决 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("a4", [1, -1, 5])
def test_a4_nonzero_never_skips(a4):
    """A4 报 repair/full（>0）或熔断（-1）⇒ 绝不短路（漏上游修订的防线）。"""
    skip, why = _call(_conn(), a4=a4)
    assert skip is False and "a4_verdict=" in why


def test_a4_zero_allows_skip_when_all_conditions_hold():
    """四条全满足 ⇒ 短路。"""
    skip, why = _call(_conn(), a4=0)
    assert skip is True and "pending candidate" in why


# ── 判据② 锚 ────────────────────────────────────────────────────────────
def test_no_anchor_never_skips():
    """本进程内未真实拉取过（重启后首轮）⇒ 不短路 ⇒ 重启即刷新。"""
    skip, why = _call(_conn(), last_pull=None)
    assert skip is False and "no-anchor" in why


# ── 判据③ 30 天强放旁路 ─────────────────────────────────────────────────
def test_forced_replay_due_after_30_days():
    skip, why = _call(_conn(), last_pull=NOW - FORCED_REPLAY_DAYS * 86400 - 1)
    assert skip is False and "forced-replay-due" in why


def test_just_below_forced_replay_boundary_still_skips():
    skip, why = _call(_conn(), last_pull=NOW - FORCED_REPLAY_DAYS * 86400 + 60)
    assert skip is True, why


# ── 判据④ intent / 水位 ─────────────────────────────────────────────────
def test_no_pending_intent_never_skips():
    skip, why = _call(_conn(with_intent=False))
    assert skip is False and "intent 查询失败" in why        # 表缺失 ⇒ 保守


def test_pending_intent_of_other_status_not_counted():
    skip, why = _call(_conn(status="superseded"))
    assert skip is False and "no-pending-intent" in why


def test_candidate_not_ahead_never_skips():
    skip, why = _call(_conn(cand=WM))                        # candidate == 水位
    assert skip is False and "candidate-not-ahead" in why
    skip2, why2 = _call(_conn(cand=WM - 1))
    assert skip2 is False and "candidate-not-ahead" in why2


def test_missing_stored_watermark_never_skips():
    skip, why = _call(_conn(), wm=None)
    assert skip is False and "no-stored-watermark" in why


# ── 环境回退开关 ────────────────────────────────────────────────────────
def test_env_kill_switch(monkeypatch):
    monkeypatch.delenv(DEFERRED_SHORTCIRCUIT_ENV, raising=False)
    assert deferred_shortcircuit_enabled() is True           # 默认开
    for off in ("0", "false", "OFF", "off"):
        monkeypatch.setenv(DEFERRED_SHORTCIRCUIT_ENV, off)
        assert deferred_shortcircuit_enabled() is False, off
    monkeypatch.setenv(DEFERRED_SHORTCIRCUIT_ENV, "1")
    assert deferred_shortcircuit_enabled() is True
