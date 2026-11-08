# -*- coding: utf-8 -*-
"""T3.1 索引一致性 canary 单测。

核心判据（issue #23645）：filtered 计数 < full-scan 计数 ⇒ 索引已损坏但未 fatal。
"""

from __future__ import annotations

import duckdb
import pytest

from quantstudio.pipeline.index_canary import (
    CANARY_TABLES,
    CanaryResult,
    any_broken,
    format_report,
    is_consistent,
    judge,
    run_canary,
    sql_for,
)


class TestIsConsistent:
    def test_equal(self):
        assert is_consistent(100, 100) is True

    def test_filtered_short(self):
        """issue #23645 的实况：filtered 返回 51，full scan 84。"""
        assert is_consistent(84, 51) is False

    def test_filtered_more(self):
        assert is_consistent(100, 103) is False

    def test_zero_both(self):
        assert is_consistent(0, 0) is True


class TestJudge:
    def test_ok(self):
        r = judge(1000, 1000, "t", "c")
        assert r.ok is True and r.diff == 0
        assert "一致" in r.detail

    def test_indexed_short_is_broken(self):
        """filtered 少了 ⇒ 索引损坏（#23645 早期态）。"""
        r = judge(84, 51, "t", "stage")
        assert r.ok is False
        assert r.diff == 33
        assert "少 33 行" in r.detail
        assert "ART 索引损坏" in r.detail

    def test_indexed_more_is_broken(self):
        r = judge(100, 103, "t", "c")
        assert r.ok is False
        assert "多 3 行" in r.detail

    def test_result_fields(self):
        r = judge(10, 8, "stock_minutes", "freq")
        assert isinstance(r, CanaryResult)
        assert r.table == "stock_minutes" and r.group_col == "freq"


class TestSql:
    def test_full_and_grouped(self):
        full, grouped = sql_for("stock_minutes", "freq")
        assert "COUNT(*) FROM stock_minutes" in full
        assert "GROUP BY freq" in grouped

    def test_reserved_word_quoted(self):
        _, grouped = sql_for("t", "time")
        assert '"time"' in grouped


class TestRunCanaryRealDB:
    """真库测试：用临时 DuckDB 造两张表（一张健康、一张故意破坏对拍）。"""

    @pytest.fixture()
    def con(self):
        c = duckdb.connect()
        c.execute("CREATE TABLE good(freq VARCHAR, v INT)")
        c.execute("INSERT INTO good VALUES ('1min',1),('1min',2),('5min',3)")
        yield c
        c.close()

    def test_healthy_table_passes(self, con):
        results = run_canary(con, tables=[("good", "freq", "test")])
        assert len(results) == 1
        assert results[0].ok is True
        assert results[0].full_count == 3
        assert results[0].grouped_count == 3

    def test_empty_table_passes(self, con):
        con.execute("CREATE TABLE empty_t(a VARCHAR)")
        results = run_canary(con, tables=[("empty_t", "a", "test")])
        assert results[0].ok is True
        assert results[0].full_count == 0

    def test_missing_table_reported_not_raised(self, con):
        """表不存在 ⇒ 记 failed，但不抛异常（巡检不中断）。"""
        results = run_canary(con, tables=[("nonexistent", "a", "test")])
        assert len(results) == 1
        assert results[0].ok is False
        assert "查询失败" in results[0].detail

    def test_multi_table_mixed(self, con):
        con.execute("CREATE TABLE good2(x VARCHAR)")
        con.execute("INSERT INTO good2 VALUES ('a'),('b')")
        results = run_canary(con, tables=[
            ("good", "freq", ""), ("good2", "x", ""), ("nope", "y", "")])
        assert len(results) == 3
        assert results[0].ok and results[1].ok
        assert results[2].ok is False


class TestAnyBroken:
    def test_all_ok(self):
        rs = [judge(1, 1, "a", "x"), judge(2, 2, "b", "y")]
        assert any_broken(rs) is False

    def test_one_broken(self):
        rs = [judge(1, 1, "a", "x"), judge(10, 8, "b", "y")]
        assert any_broken(rs) is True

    def test_empty_list(self):
        assert any_broken([]) is False


class TestFormatReport:
    def test_all_ok(self):
        s = format_report([judge(100, 100, "stock_minutes", "freq")])
        assert "[T3.1 index-canary]" in s
        assert "✅" in s
        assert "全部一致" in s

    def test_broken_alerts(self):
        s = format_report([judge(84, 51, "stock_minutes", "freq")])
        assert "❌" in s
        assert "告警" in s
        assert "T3.3 重建 SOP" in s

    def test_mixed(self):
        s = format_report([
            judge(100, 100, "a", "x"),
            judge(84, 51, "b", "y")])
        assert "告警" in s and "1 张表" in s


class TestCanaryTableList:
    def test_key_tables_covered(self):
        names = [t[0] for t in CANARY_TABLES]
        for expected in ("stock_minutes", "etf_minutes", "stock_daily", "etf_daily"):
            assert expected in names, f"canary 应覆盖 {expected}"

    def test_each_entry_has_three_fields(self):
        for entry in CANARY_TABLES:
            assert len(entry) == 3
