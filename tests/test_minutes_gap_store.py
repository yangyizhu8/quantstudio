# -*- coding: utf-8 -*-
"""账本持久化层：真 SQLite 文件测（不 mock），含幂等/销账/union 联动。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quantstudio.pipeline.minute_gap_ledger import merge_forced_days
from quantstudio.pipeline.minutes_gap_store import (
    close_days,
    close_gap,
    open_days,
    open_records,
    record_gap,
    stats,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "minutes_gap_test.db"


class TestRecordAndRead:
    def test_record_then_open(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24,
                   "defer", "服务端未就绪", db_path=db)
        recs = open_records(db_path=db)
        assert len(recs) == 1
        assert recs[0]["action"] == "defer"
        assert recs[0]["shards_actual"] == 8

    def test_idempotent_upsert(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "", db_path=db)
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 20, 24, "defer", "重试后仍不足", db_path=db)
        recs = open_records(db_path=db)
        assert len(recs) == 1, "同 key 应覆盖而非新增"
        assert recs[0]["shards_actual"] == 20

    def test_filter_by_table(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "", db_path=db)
        record_gap("etf_minutes", "2026-11-02", "2026-11-03", 5, 24, "fail", "", db_path=db)
        assert len(open_records("stock_minutes", db_path=db)) == 1
        assert len(open_records("etf_minutes", db_path=db)) == 1
        assert len(open_records(db_path=db)) == 2

    def test_invalid_action_rejected(self, db):
        with pytest.raises(ValueError):
            record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "skip", db_path=db)


class TestClose:
    def test_close_gap(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "", db_path=db)
        assert close_gap("stock_minutes", "2026-11-04", "2026-11-05", db_path=db) is True
        assert open_records(db_path=db) == []

    def test_close_twice_returns_false(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "", db_path=db)
        close_gap("stock_minutes", "2026-11-04", "2026-11-05", db_path=db)
        assert close_gap("stock_minutes", "2026-11-04", "2026-11-05", db_path=db) is False

    def test_close_days_by_endpoint(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "", db_path=db)
        record_gap("stock_minutes", "2026-11-02", "2026-11-03", 5, 24, "fail", "", db_path=db)
        n = close_days("stock_minutes", ["2026-11-05"], db_path=db)
        assert n == 1
        remaining = open_records(db_path=db)
        assert len(remaining) == 1
        assert remaining[0]["batch_start"] == "2026-11-02"


class TestOpenDaysAndUnion:
    def test_open_days(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "", db_path=db)
        record_gap("stock_minutes", "2026-10-20", "2026-10-21", 5, 47, "fail", "", db_path=db)
        assert open_days("stock_minutes", db_path=db) == {
            "2026-11-04", "2026-11-05", "2026-10-20", "2026-10-21"}

    def test_union_with_next_window(self, db):
        """自愈闭环：下轮重拉日集 = 声明窗口 ∪ 账本未清日。"""
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "", db_path=db)
        next_window = ["2026-11-06", "2026-11-09"]
        forced = merge_forced_days(next_window, open_days("stock_minutes", db_path=db))
        assert forced == ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]

    def test_after_close_not_forced(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "", db_path=db)
        close_gap("stock_minutes", "2026-11-04", "2026-11-05", db_path=db)
        forced = merge_forced_days(["2026-11-06"], open_days("stock_minutes", db_path=db))
        assert forced == ["2026-11-06"], "销账后不应再强制重含"


class TestStats:
    def test_stats(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "", db_path=db)
        record_gap("stock_minutes", "2026-11-02", "2026-11-03", 5, 24, "fail", "", db_path=db)
        close_gap("stock_minutes", "2026-11-02", "2026-11-03", db_path=db)
        s = stats(db_path=db)
        assert s.get("open") == 1
        assert s.get("closed") == 1


class TestRealFilePersistence:
    """真文件：重连后数据仍在（证明落盘可靠，非内存 mock）。"""

    def test_survives_reconnect(self, db):
        record_gap("stock_minutes", "2026-11-04", "2026-11-05", 8, 24, "defer", "reconnect", db_path=db)
        assert db.exists(), "账本文件应真实落盘"
        recs = open_records(db_path=db)
        assert len(recs) == 1 and recs[0]["reason"] == "reconnect"
