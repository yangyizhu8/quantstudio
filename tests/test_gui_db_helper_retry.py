# -*- coding: utf-8 -*-
"""A-prime(T3) / A4 自验：DbHelper 只读快速重试 + 忙态可观测（跨平台，纯 Python）。

红态契约：重试只针对跨进程锁冲突（busy），不得吞掉其它异常；返回契约不得变化。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from quantstudio.gui.db_helper import (
    DbHelper,
    READ_ONLY_RETRY_ATTEMPTS,
    _is_db_busy_error,
)

BUSY_MSG = ('IO Error: Cannot open file "x.db": 另一个程序正在使用此文件，进程无法访问。'
            ' File is already open in python.exe')


class _FakeConn:
    def __init__(self, df=None):
        self._df = df if df is not None else pd.DataFrame({"n": [1]})

    def execute(self, sql):
        return self

    def fetchdf(self):
        return self._df

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _helper(tmp_path: Path) -> DbHelper:
    return DbHelper(tmp_path / "a.db", tmp_path / "q.db", tmp_path / "b.db")


def test_is_db_busy_error_recognizes_cn_and_en():
    assert _is_db_busy_error(Exception(BUSY_MSG))
    assert _is_db_busy_error(Exception("Could not set lock on file"))
    assert not _is_db_busy_error(Exception("Binder Error: column nope not found"))


def test_retry_succeeds_within_budget(tmp_path):
    """前 N 次抛 busy，最后一次成功 -> 返回数据且不计入忙态。"""
    calls = {"n": 0}
    def fake_connect(*a, **k):
        calls["n"] += 1
        if calls["n"] <= READ_ONLY_RETRY_ATTEMPTS:
            raise Exception(BUSY_MSG)
        return _FakeConn()
    h = _helper(tmp_path)
    with patch("duckdb.connect", side_effect=fake_connect):
        df = h.query_duckdb("select 1")
    assert len(df) == 1, "重试后应成功返回数据"
    assert calls["n"] == READ_ONLY_RETRY_ATTEMPTS + 1
    assert h.busy_count == 0, "重试成功不应记忙态"
    assert h.busy_hint() == "", "无忙态时不显示降级提示"


def test_retry_exhausted_returns_empty_and_marks_busy(tmp_path):
    """全部重试失败 -> 返回空 DataFrame 契约不变 + 忙态可观测（含预计等待来源）。"""
    h = _helper(tmp_path)
    with patch("duckdb.connect", side_effect=Exception(BUSY_MSG)):
        df = h.query_duckdb("select 1")
    assert isinstance(df, pd.DataFrame) and len(df) == 0, "契约：忙时返回空 DataFrame"
    assert h.busy_count == 1
    assert h.last_busy_at is not None
    assert h.is_busy_recent()
    hint = h.busy_hint()
    assert "采集中" in hint and hint.endswith("。"), "提示文案应说明采集中"
    assert "300" in hint or "秒" in hint, "预计等待须标明来源（check_interval_sec）或不编造数字"


def test_non_busy_exception_propagates(tmp_path):
    """非锁冲突异常必须上抛（重试不得掩盖真故障）。"""
    h = _helper(tmp_path)
    with patch("duckdb.connect", side_effect=RuntimeError("Binder Error: nope")):
        with pytest.raises(Exception):
            h.query_duckdb("select nope")
    assert h.busy_count == 0, "非 busy 异常不应记忙态"


def test_return_contracts_unchanged_when_busy(tmp_path):
    """三个曾裸连接的方法在忙时返回契约不变：[] / 0 / []。"""
    h = _helper(tmp_path)
    with patch("duckdb.connect", side_effect=Exception(BUSY_MSG)):
        assert h.list_tables() == []
        assert h.table_rowcount("t") == 0
        assert h.get_table_columns("t") == []


def test_return_contracts_normal_paths(tmp_path):
    """正常路径返回类型不变：list[str] / int / list[tuple]。"""
    h = _helper(tmp_path)
    with patch("duckdb.connect", return_value=_FakeConn(pd.DataFrame({"name": ["t1", "t2"]}))):
        assert h.list_tables() == ["t1", "t2"]
    with patch("duckdb.connect", return_value=_FakeConn(pd.DataFrame({"n": [42]}))):
        assert h.table_rowcount("t") == 42
    with patch("duckdb.connect", return_value=_FakeConn(
            pd.DataFrame({"column_name": ["c1"], "column_type": ["INTEGER"]}))):
        assert h.get_table_columns("t") == [("c1", "INTEGER")]
