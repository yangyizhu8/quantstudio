# -*- coding: utf-8 -*-
"""REPLAY-SKIP 验收：分钟表按内容去重（混合批 / 全同批 / 开关 / 只读时间窗）。

对应方案 §2b。用户要求：库内最终状态必须不变（纯性能型）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from quantstudio.pipeline.daemon import ResidentCollector

DAY = 86_400_000
T0 = 1_792_373_400_000


class _FakeWriter:
    def __init__(self, existing: pd.DataFrame):
        self._existing = existing
        self.sqls: list[str] = []

    def read_df(self, sql: str):
        self.sqls.append(sql)
        return self._existing


class _FakeAligner:
    schemas = {"stock_minutes": {"primary_key": ["code", "time", "freq"]}}


@pytest.fixture()
def coll():
    c = ResidentCollector.__new__(ResidentCollector)
    c.aligner = _FakeAligner()
    return c


def _df(rows):
    return pd.DataFrame(rows, columns=["code", "time", "freq", "open", "close"])


def _mk():
    return _df([
        ["A", T0, "1min", 10.0, 10.5],
        ["A", T0 + 60_000, "1min", 10.5, 11.0],
        ["B", T0, "1min", 20.0, 20.5],
    ])


def test_identical_batch_is_fully_skipped(coll):
    """库内完全相同 ⇒ 输出 0 行（本批无需写入）。"""
    coll.writer = _FakeWriter(_mk())
    out = coll._filter_unchanged_rows_scoped(_mk(), "stock_minutes")
    assert len(out) == 0, f"全同批应全部跳过，实得 {len(out)} 行"


def test_mixed_batch_keeps_only_changed(coll):
    """一半相同 + 一半变化 ⇒ 只保留变化行。"""
    existing = _mk()
    incoming = _mk()
    incoming.loc[incoming["code"] == "B", "close"] = 99.0     # B 变化
    coll.writer = _FakeWriter(existing)
    out = coll._filter_unchanged_rows_scoped(incoming, "stock_minutes")
    assert len(out) == 1, out
    assert out.iloc[0]["code"] == "B"
    assert out.iloc[0]["close"] == 99.0


def test_new_rows_are_kept(coll):
    """库内没有的行（新增 bar）必须保留。"""
    existing = _mk()
    incoming = pd.concat([_mk(), _df([["C", T0, "1min", 30.0, 30.5]])], ignore_index=True)
    coll.writer = _FakeWriter(existing)
    out = coll._filter_unchanged_rows_scoped(incoming, "stock_minutes")
    assert len(out) == 1 and out.iloc[0]["code"] == "C"


def test_query_is_time_windowed_not_full_scan(coll):
    """核心：SQL 必须带时间窗（不得全表读 2.47 亿行）。"""
    coll.writer = _FakeWriter(_mk())
    coll._filter_unchanged_rows_scoped(_mk(), "stock_minutes")
    sql = coll.writer.sqls[0]
    assert "WHERE time >=" in sql and "time <=" in sql, f"必须限定时间窗: {sql}"
    assert str(T0) in sql


def test_env_switch_disables(coll, monkeypatch):
    monkeypatch.setenv("QS_MINUTE_DEDUPE", "0")
    assert coll._minute_dedupe_enabled("stock_minutes", {}) is False


def test_enabled_by_default_for_minute_tables(coll, monkeypatch):
    monkeypatch.delenv("QS_MINUTE_DEDUPE", raising=False)
    assert coll._minute_dedupe_enabled("stock_minutes", {}) is True
    assert coll._minute_dedupe_enabled("etf_minutes", {}) is True
    assert coll._minute_dedupe_enabled("stock_daily", {}) is False


def test_explicit_false_config_wins(coll, monkeypatch):
    monkeypatch.delenv("QS_MINUTE_DEDUPE", raising=False)
    assert coll._minute_dedupe_enabled("stock_minutes", {"skip_unchanged": False}) is False


def test_read_failure_falls_back_to_full_write(coll):
    """比对读失败 ⇒ 回退全量写（保守，不丢数据）。"""
    class _Boom:
        def read_df(self, sql):
            raise RuntimeError("db busy")
    coll.writer = _Boom()
    df = _mk()
    out = coll._filter_unchanged_rows_scoped(df, "stock_minutes")
    assert len(out) == len(df), "读失败必须回退全量写"

def test_df_extra_columns_not_in_table(coll):
    """回归（2026-11-04 真机首验命中）：df 含 aligner 中间列（adj_factor）而表里没有
    ==> 必须与表实际列取交集，不得让 SQL 报 Binder Error。"""
    class _W(_FakeWriter):
        def _table_columns(self, table):
            return ["code", "time", "freq", "open", "close"]   # 无 adj_factor

    existing = _mk()
    coll.writer = _W(existing)
    df = _mk()
    df["adj_factor"] = 1.5                                   # 表外列
    out = coll._filter_unchanged_rows_scoped(df, "stock_minutes")
    assert len(out) == 0, f"内容相同应全部跳过，实得 {len(out)} 行"
    sql = coll.writer.sqls[0]
    assert "adj_factor" not in sql, f"SELECT 不得含表外列: {sql}"
