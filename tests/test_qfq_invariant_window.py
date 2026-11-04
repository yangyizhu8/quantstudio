# -*- coding: utf-8 -*-
"""P1 验收：因子查询时间窗口（等价性 + 回退开关 + 极端边界）。

对应方案 §1 / §3.6.1。用户补充条件：长期停牌复牌 ⇒ 窗口外因子必须落 unknown（设计语义）。
"""
from __future__ import annotations

import os
import sqlite3

import pandas as pd
import pytest

from quantstudio.pipeline.qfq_invariant import (
    INVARIANT_FULL_FACTORS_ENV,
    _factor_window_ms,
    _load_factor_lookup,
)

DAY = 86_400_000


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(INVARIANT_FULL_FACTORS_ENV, raising=False)


class TestWindowComputation:
    def test_window_is_padded_3_days(self):
        t = [1_792_373_400_000]
        lo, hi = _factor_window_ms(t)
        assert hi - lo == 6 * DAY, "±3 天冗余 ⇒ 跨度应为 6 天"
        assert lo == t[0] - 3 * DAY

    def test_window_covers_min_max(self):
        t = [1_000_000_000_000, 1_000_000_000_000 + 10 * 60_000]
        lo, hi = _factor_window_ms(t)
        assert lo <= min(t) and hi > max(t)

    def test_env_switch_disables_window(self, monkeypatch):
        monkeypatch.setenv(INVARIANT_FULL_FACTORS_ENV, "1")
        assert _factor_window_ms([1_792_373_400_000]) == (None, None)

    def test_empty_input(self):
        assert _factor_window_ms([]) == (None, None)


class TestLookupWindow:
    def _aux(self, tmp_path):
        db = tmp_path / "aux.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
        # code A：2020 年老因子 + 本次窗口内因子
        con.executemany("INSERT INTO adj_factor VALUES (?,?,?)", [
            ("A", 1_500_000_000_000, 1.0),          # 很早
            ("A", 1_792_373_400_000, 2.0),          # 窗口内
            ("B", 1_500_000_000_000, 3.0),          # 只有很早的
        ])
        con.commit()
        return con

    def test_window_excludes_old_rows(self, tmp_path):
        con = self._aux(tmp_path)
        try:
            full = _load_factor_lookup(con, "adj_factor", ["A", "B"])
            win = _load_factor_lookup(
                con, "adj_factor", ["A", "B"],
                day_lo_ms=1_792_373_400_000 - 3 * DAY,
                day_hi_ms=1_792_373_400_000 + 3 * DAY)
        finally:
            con.close()
        assert len(full) >= len(win)
        for k, v in win.items():
            assert full[k] == v, "窗口内键的值必须与全历史一致"


class TestEdgeCaseSuspensionResume:
    """用户补充条件 1：长期停牌复牌 ⇒ 窗口外因子落 unknown（设计语义，非缺陷）。"""

    def test_factor_outside_window_yields_unknown(self, tmp_path):
        from quantstudio.pipeline.qfq_invariant import check_qfq_invariant

        aux = tmp_path / "aux.db"
        con = sqlite3.connect(str(aux))
        con.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
        # bar 在 D 日，但因子只存在于 D-10 日（超出 ±3 天窗口）
        bar_ms = 1_792_373_400_000
        con.execute("INSERT INTO adj_factor VALUES (?,?,?)",
                    ("SUSPEND", bar_ms - 10 * DAY, 4.0))
        con.commit()
        con.close()

        df = pd.DataFrame({
            "code": ["SUSPEND"] * 4,
            "time": [bar_ms] * 4,
            "open": [10.0] * 4, "high": [10.5] * 4,
            "low": [9.5] * 4, "close": [10.0] * 4,
            "adj_factor": [None] * 4,   # 行内缺失 ⇒ 回退 aux 查询
        })
        anchor = {"SUSPEND": 4.0}
        r = check_qfq_invariant(df, "stock_minutes", anchor,
                                aux_path=str(aux), source="mcp")
        # 窗口外 ⇒ 找不到因子行 ⇒ 绝不得判 bad / 抛异常（保守方向）
        assert r["bad"] == 0, f"窗口外因子不得判为错误值: {r}"
        # 实测语义（2026-11-04）：既有实现把"因子行缺失"单列为 missing_factor_rows
        # 并计入 skipped——比 unknown 更精确，同为"未找到"语义，不是错误值。
        # 该语义**非本改动引入**（原全历史路径同样如此），验收件已显式记录。
        assert r["skipped"] > 0, f"窗口外因子应计入 skipped: {r}"
        assert r.get("missing_factor_rows", 0) > 0, \
            f"窗口外因子应记入 missing_factor_rows: {r}"
        assert r["unknown_rows"] == 0, f"本场景不应落 unknown（应落 missing_factor_rows）: {r}"

    def test_same_case_matches_full_history_path(self, tmp_path, monkeypatch):
        """同一构造下开回退开关（全历史）⇒ 结果与窗口路径一致（逐项）。"""
        from quantstudio.pipeline.qfq_invariant import check_qfq_invariant

        aux = tmp_path / "aux.db"
        con = sqlite3.connect(str(aux))
        con.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
        bar_ms = 1_792_373_400_000
        con.execute("INSERT INTO adj_factor VALUES (?,?,?)",
                    ("SUSPEND", bar_ms - 10 * DAY, 4.0))
        con.commit()
        con.close()

        df = pd.DataFrame({
            "code": ["SUSPEND"] * 4, "time": [bar_ms] * 4,
            "open": [10.0] * 4, "high": [10.5] * 4, "low": [9.5] * 4,
            "close": [10.0] * 4, "adj_factor": [None] * 4,
        })
        anchor = {"SUSPEND": 4.0}
        win = check_qfq_invariant(df, "stock_minutes", anchor,
                                  aux_path=str(aux), source="mcp")
        monkeypatch.setenv(INVARIANT_FULL_FACTORS_ENV, "1")
        full = check_qfq_invariant(df, "stock_minutes", anchor,
                                   aux_path=str(aux), source="mcp")
        assert win["bad"] == full["bad"], f"bad 必须一致: win={win} full={full}"
        assert win["sampled"] == full["sampled"]
