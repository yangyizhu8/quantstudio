"""tests/test_qfq_reanchor_batch1.py — QFQ 重锚子系统「第一批基础设施」纯单元测试。

覆盖模块（全部新增、与既有 qfq_maintenance / qfq_revision 并存不合并）：
- quantstudio/pipeline/qfq_reanchor_schema.py   —— DuckDB 6 表 + SQLite 4 表 DDL
- quantstudio/pipeline/qfq_calendar.py          —— CalendarService（复用 intraday_windows）
- quantstudio/pipeline/qfq_observation.py       —— 版本化 observation + revision alert outbox
- quantstudio/pipeline/writers.py               —— 事务感知内部方法（新增，公共方法回归守卫）

全部 hermetic：tmp_path 临时库，不连 live QMT，不碰正式库、不 stage/commit/push。

对抗性测试（修复 4 个阻断 + 可靠性问题后补强）：
- CalendarService 不得把部分缓存当完整日历（未知≠闭市、相邻交易日须证明区间完整）。
- ObservationStore 拒绝非法因子/键，同批冲突整批回滚，容差绝对+相对双分量。
- aux_db_path 任意自定义主库名均派生到同目录 qfq_aux.db。
- pending backfill resolved 不得静默重开；输入校验；热路径不再重复 DDL。
- schema 迁移 fail-fast，init 后校验必需列。
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from quantstudio.pipeline import qfq_reanchor_schema as SCHEMA
from quantstudio.pipeline.qfq_calendar import CalendarService, _at, TZ
from quantstudio.pipeline.qfq_observation import ObservationStore, alert_id_of
from quantstudio.pipeline.writers import DuckDBWriter

FT1 = 1784822400000  # 2026-07-24 00:00:00 +08（实测日线 time 口径）
FT2 = 1784908800000  # 2026-07-25 00:00:00 +08


def _clock(ms: int) -> str:
    return pd.Timestamp(int(ms), unit="ms", tz=TZ).strftime("%H:%M")


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _persist_full_window(svc: CalendarService, open_day_strs):
    """持久化 [open 首日, open 末日] 的完整自然日窗口（open + 闭市），使覆盖完整。"""
    open_ms = sorted(_at(d, 0, 0, 0) for d in open_day_strs)
    lo, hi = open_ms[0], open_ms[-1]
    start = pd.Timestamp(lo, unit="ms", tz=TZ).strftime("%Y-%m-%d")
    end = pd.Timestamp(hi, unit="ms", tz=TZ).strftime("%Y-%m-%d")
    all_days = [int(pd.Timestamp(d, tz=TZ).timestamp() * 1000)
                for d in pd.date_range(start, end, freq="D")]
    open_set = set(open_ms)
    closed = [d for d in all_days if d not in open_set]
    conn = svc._connect()
    svc.persist_trade_days_on_conn(conn, open_ms, closed_ms=closed)
    conn.commit()
    conn.close()


class _FakeCalendarProvider:
    """测试用日历 provider：返回给定 open 日集合内的工作日（date 字符串）。"""
    name = "fake"

    def __init__(self, open_days):
        self._open = set(open_days)

    def get_trade_days(self, start, end):
        days = pd.date_range(start[:10], end[:10], freq="D")
        return [d.strftime("%Y-%m-%d") for d in days
                if d.strftime("%Y-%m-%d") in self._open]


# ===========================================================================
# 1. DDL 初始化模块
# ===========================================================================
class TestSchemaDDL:
    def test_init_all_creates_all_tables(self, tmp_path):
        import duckdb
        main = tmp_path / "quantstudio.db"
        res = SCHEMA.init_all_from_paths(main_db=main)
        assert Path(res["duckdb"]).name == "quantstudio.db"
        assert Path(res["sqlite"]).name == "qfq_aux.db"

        d = duckdb.connect(res["duckdb"])
        duck_tabs = {r[0] for r in d.execute("SHOW TABLES").fetchall()}
        d.close()
        assert set(SCHEMA.DDL_DUCKDB) <= duck_tabs

        s = sqlite3.connect(res["sqlite"])
        sq_tabs = {r[0] for r in s.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        s.close()
        assert set(SCHEMA.DDL_SQLITE) <= sq_tabs

    def test_duckdb_column_order_matches_manifest(self, tmp_path):
        import duckdb
        main = tmp_path / "quantstudio.db"
        SCHEMA.init_all_from_paths(main_db=main)
        d = duckdb.connect(str(main))
        try:
            for table, cols in SCHEMA.DUCKDB_COLS.items():
                actual = [r[0] for r in d.execute(f"DESCRIBE {table}").fetchall()]
                assert actual == cols, f"{table} 列顺序不匹配: {actual} != {cols}"
        finally:
            d.close()

    def test_reanchor_event_has_v5_aggregation_columns(self, tmp_path):
        cols = SCHEMA.DUCKDB_COLS["qfq_reanchor_event"]
        for c in ("event_type", "cycle_business_date", "occurrence_count",
                  "first_seen_at", "last_seen_at"):
            assert c in cols

    def test_anchor_state_has_stale_probe_columns(self, tmp_path):
        cols = SCHEMA.DUCKDB_COLS["qfq_anchor_state"]
        for c in ("last_stale_probe_at", "last_stale_probe_error", "probe_fail_count"):
            assert c in cols

    def test_observation_pk_includes_revision_no(self, tmp_path):
        main = tmp_path / "quantstudio.db"
        res = SCHEMA.init_all_from_paths(main_db=main)
        s = sqlite3.connect(res["sqlite"])
        try:
            info = {r[1]: r[5] for r in s.execute(
                "PRAGMA table_info(qfq_factor_observation)").fetchall()}
        finally:
            s.close()
        # PK 四列含 revision_no（版本化保留旧行的关键）
        assert info["revision_no"] > 0
        assert set(k for k, v in info.items() if v > 0) == {
            "asset_type", "code", "factor_time", "revision_no"}

    def test_idempotent_reinit(self, tmp_path):
        main = tmp_path / "quantstudio.db"
        r1 = SCHEMA.init_all_from_paths(main_db=main)
        r2 = SCHEMA.init_all_from_paths(main_db=main)  # 不应抛错
        assert r1 == r2

    def test_aux_db_path_derivation(self, tmp_path):
        # 默认主库 → 同目录 qfq_aux.db
        assert SCHEMA.aux_db_path(tmp_path / "quantstudio.db").name == "qfq_aux.db"
        # 已是 qfq_aux.db → 原样返回
        assert SCHEMA.aux_db_path(tmp_path / "qfq_aux.db").name == "qfq_aux.db"
        # 阻断 3 修复：任意自定义主库名 → 同目录 qfq_aux.db（不再按文件名特判）
        assert SCHEMA.aux_db_path(tmp_path / "other.db").name == "qfq_aux.db"

    def test_migrate_adds_missing_column(self, tmp_path):
        """``_migrate_duckdb_columns`` 工具对缺列旧表自动 ALTER 补全（幂等迁移）。

        v2.4 B-3a 修订：普通 ``init_duckdb_schema`` 不再对版本化/部分 schema 调用
        ``_migrate_duckdb_columns``（写前 fail-fast，提示用显式 migration runner
        B-3b）。但 ``_migrate_duckdb_columns`` 作为**独立的 schema 演进通用补列工具**
        保留其行为（B-3R §4：无非 B-3 的特殊兼容用途）。本测试：
        (1) 用全量 DDL 建全表后 DROP 几列模拟旧表缺列；
        (2) 直接调 ``_migrate_duckdb_columns`` 工具验证补全（不经普通 init 闸门）；
        (3) 断言普通 init 对「只有部分表」的库写前 fail-fast。
        """
        import duckdb
        from quantstudio.pipeline.qfq_schema_status import (
            detect_schema_status, SchemaStatus, QfqSchemaMigrationRequired)
        main = tmp_path / "quantstudio.db"
        d = duckdb.connect(str(main))
        # 先用全量 DDL 建全表
        for ddl in SCHEMA.DDL_DUCKDB.values():
            d.execute(ddl)
        # 模拟旧表缺 v5 新列：DROP anchor_state 的几列
        d.execute("ALTER TABLE qfq_anchor_state DROP COLUMN probe_fail_count")
        d.execute("ALTER TABLE qfq_anchor_state DROP COLUMN last_stale_probe_at")
        d.execute("ALTER TABLE qfq_anchor_state DROP COLUMN anchor_version")
        d.commit()
        # _migrate_duckdb_columns 工具补列（独立工具，fail-fast，合法类型不报错）
        SCHEMA._migrate_duckdb_columns(d)
        d.commit()
        actual = {r[0] for r in d.execute("DESCRIBE qfq_anchor_state").fetchall()}
        for c in ("probe_fail_count", "last_stale_probe_at", "last_stale_probe_error",
                  "anchor_version", "retry_count"):
            assert c in actual, f"迁移未补全 {c}"
        d.close()

        # (3) 普通 init 对「只有部分表」的库写前 fail-fast（B-3a 闸门）
        d2 = duckdb.connect(str(tmp_path / "partial.db"))
        d2.execute("CREATE TABLE qfq_anchor_state (asset_type VARCHAR, code VARCHAR, "
                   "price_source VARCHAR, status VARCHAR, updated_at TIMESTAMP, "
                   "PRIMARY KEY(asset_type, code, price_source))")
        d2.commit()
        assert detect_schema_status(d2) == SchemaStatus.PARTIAL_OR_MIXED
        with pytest.raises(QfqSchemaMigrationRequired):
            SCHEMA.init_duckdb_schema(d2)
        d2.close()

    # ---- 阻断 3 新增测试 ----
    def test_aux_db_path_custom_main_derives_sibling_aux(self, tmp_path):
        for name in ("custom.duckdb", "research.db", "qs_test.db", "mydb.duckdb"):
            p = SCHEMA.aux_db_path(tmp_path / name)
            assert p.name == "qfq_aux.db"
            assert p.parent == tmp_path

    def test_init_all_custom_main_uses_separate_sqlite_file(self, tmp_path):
        import duckdb
        main = tmp_path / "custom.duckdb"
        res = SCHEMA.init_all_from_paths(main_db=main)
        assert Path(res["duckdb"]).name == "custom.duckdb"
        assert Path(res["sqlite"]).name == "qfq_aux.db"
        assert res["duckdb"] != res["sqlite"]
        # 两个文件均真实存在且为不同引擎可读
        d = duckdb.connect(res["duckdb"])
        assert "qfq_pending_backfill" in {r[0] for r in d.execute("SHOW TABLES").fetchall()}
        d.close()
        s = sqlite3.connect(res["sqlite"])
        assert "qfq_factor_observation" in {r[0] for r in s.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        s.close()

    def test_main_and_aux_paths_must_not_resolve_equal(self, tmp_path):
        # 主库名本身就是 qfq_aux.db → 派生后解析相同 → 必须 fail-fast
        with pytest.raises(ValueError):
            SCHEMA.init_all_from_paths(main_db=tmp_path / "qfq_aux.db")

    # ---- 可靠性（阻断 5）新增测试 ----
    def test_schema_migration_failure_is_not_silently_ignored(self, tmp_path):
        import duckdb
        from quantstudio.pipeline.qfq_schema_status import (
            detect_schema_status, SchemaStatus, QfqSchemaMigrationRequired)
        main = tmp_path / "quantstudio.db"
        d = duckdb.connect(str(main))
        d.execute("CREATE TABLE qfq_anchor_state (asset_type VARCHAR, code VARCHAR, "
                  "price_source VARCHAR, status VARCHAR, updated_at TIMESTAMP, "
                  "PRIMARY KEY(asset_type, code, price_source))")
        d.commit()
        # v2.4 B-3a：普通 init 对该部分库写前 fail-fast（不再走 _migrate 路径）
        assert detect_schema_status(d) == SchemaStatus.PARTIAL_OR_MIXED
        with pytest.raises(QfqSchemaMigrationRequired):
            SCHEMA.init_duckdb_schema(d)
        # 直接调 _migrate_duckdb_columns 工具：ALTER 类型不可解析 → 抛 RuntimeError
        # （不再静默吞掉；独立工具行为保留，B-3R §4）
        with mock.patch.object(SCHEMA, "_infer_col_type", return_value="NONSENSE_TYPE"):
            with pytest.raises(RuntimeError):
                SCHEMA._migrate_duckdb_columns(d)
        d.close()

    def test_schema_init_verifies_required_columns(self, tmp_path):
        import duckdb
        main = tmp_path / "quantstudio.db"
        res = SCHEMA.init_all_from_paths(main_db=main)
        d = duckdb.connect(res["duckdb"])
        try:
            for table, cols in SCHEMA.DUCKDB_COLS.items():
                actual = {r[0] for r in d.execute(f"DESCRIBE {table}").fetchall()}
                assert set(cols) <= actual, f"{table} 缺必需列: {set(cols) - actual}"
        finally:
            d.close()

    # ---- 阻断 1：完整 schema 契约校验（列/类型/NOT NULL/PK）----
    def _rebuild_duckdb_table(self, tmp_path, ddl):
        import duckdb
        main = tmp_path / "quantstudio.db"
        d = duckdb.connect(str(main))
        SCHEMA.init_duckdb_schema(d); d.commit()  # 先建全正确表
        d.execute(ddl)                            # 用错误契约覆盖其中一张
        d.commit()
        return d

    def test_schema_rejects_missing_duckdb_primary_key(self, tmp_path):
        d = self._rebuild_duckdb_table(tmp_path,
            "DROP TABLE qfq_pending_backfill")
        d.execute("""CREATE TABLE qfq_pending_backfill (
            asset_type VARCHAR NOT NULL, code VARCHAR NOT NULL, table_name VARCHAR NOT NULL,
            freq VARCHAR NOT NULL, range_start BIGINT NOT NULL, range_end BIGINT NOT NULL,
            reason VARCHAR NOT NULL, anchor_version BIGINT, status VARCHAR NOT NULL,
            attempt_count INTEGER DEFAULT 0, last_error VARCHAR, created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL, resolved_at TIMESTAMP)""")  # 列齐全但无主键
        d.commit()
        with pytest.raises(RuntimeError):
            SCHEMA._verify_duckdb_contract(d)
        d.close()

    def test_schema_rejects_wrong_duckdb_primary_key_order(self, tmp_path):
        # 契约问题 1 修正：DuckDB duckdb_constraints() 暴露 constraint_column_names，
        # 可验证 PK **顺序**。此处构造相同列集合、完全逆序的 PK，应被拒绝。
        d = self._rebuild_duckdb_table(tmp_path,
            "DROP TABLE qfq_pending_backfill")
        d.execute("""CREATE TABLE qfq_pending_backfill (
            asset_type VARCHAR NOT NULL, code VARCHAR NOT NULL, table_name VARCHAR NOT NULL,
            freq VARCHAR NOT NULL, range_start BIGINT NOT NULL, range_end BIGINT NOT NULL,
            reason VARCHAR NOT NULL, anchor_version BIGINT, status VARCHAR NOT NULL,
            attempt_count INTEGER DEFAULT 0, last_error VARCHAR, created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL, resolved_at TIMESTAMP,
            PRIMARY KEY (range_end, range_start, freq, table_name, code, asset_type))""")  # 逆序
        d.commit()
        with pytest.raises(RuntimeError):
            SCHEMA._verify_duckdb_contract(d)
        d.close()

    def test_schema_rejects_nullable_required_column(self, tmp_path):
        # 注：DuckDB 对主键列隐式 NOT NULL，故降级非主键的 NOT NULL 列（status）来验证。
        d = self._rebuild_duckdb_table(tmp_path,
            "DROP TABLE qfq_pending_backfill")
        d.execute("""CREATE TABLE qfq_pending_backfill (
            asset_type VARCHAR NOT NULL, code VARCHAR NOT NULL, table_name VARCHAR NOT NULL,
            freq VARCHAR NOT NULL, range_start BIGINT NOT NULL, range_end BIGINT NOT NULL,
            reason VARCHAR NOT NULL, anchor_version BIGINT, status VARCHAR,
            attempt_count INTEGER DEFAULT 0, last_error VARCHAR, created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL, resolved_at TIMESTAMP,
            PRIMARY KEY (asset_type, code, table_name, freq, range_start, range_end))""")
        # status 应为 NOT NULL 但被降级为可空 → 拒绝
        d.commit()
        with pytest.raises(RuntimeError):
            SCHEMA._verify_duckdb_contract(d)
        d.close()

    def test_schema_rejects_wrong_column_type(self, tmp_path):
        d = self._rebuild_duckdb_table(tmp_path,
            "DROP TABLE qfq_pending_backfill")
        d.execute("""CREATE TABLE qfq_pending_backfill (
            asset_type INTEGER NOT NULL, code VARCHAR NOT NULL, table_name VARCHAR NOT NULL,
            freq VARCHAR NOT NULL, range_start BIGINT NOT NULL, range_end BIGINT NOT NULL,
            reason VARCHAR NOT NULL, anchor_version BIGINT, status VARCHAR NOT NULL,
            attempt_count INTEGER DEFAULT 0, last_error VARCHAR, created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL, resolved_at TIMESTAMP,
            PRIMARY KEY (asset_type, code, table_name, freq, range_start, range_end))""")
        # asset_type 实际 INTEGER，契约要求 VARCHAR → 拒绝
        d.commit()
        with pytest.raises(RuntimeError):
            SCHEMA._verify_duckdb_contract(d)
        d.close()

    def test_schema_rejects_missing_sqlite_primary_key(self, tmp_path):
        s = sqlite3.connect(str(tmp_path / "qfq_aux.db"))
        SCHEMA.init_sqlite_schema(s); s.commit()
        s.execute("DROP TABLE qfq_factor_observation")
        s.execute("""CREATE TABLE qfq_factor_observation (
            asset_type TEXT NOT NULL, code TEXT NOT NULL, factor_time INTEGER NOT NULL,
            factor_value REAL NOT NULL, revision_no INTEGER NOT NULL,
            first_seen_run_id TEXT NOT NULL, last_seen_run_id TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL)""")  # 无主键
        s.commit()
        with pytest.raises(RuntimeError):
            SCHEMA._verify_sqlite_contract(s)
        s.close()

    def test_schema_verifies_full_contract_after_init(self, tmp_path):
        import duckdb
        res = SCHEMA.init_all_from_paths(main_db=tmp_path / "quantstudio.db")
        d = duckdb.connect(res["duckdb"])
        SCHEMA._verify_duckdb_contract(d)   # 不应抛
        d.close()
        s = sqlite3.connect(res["sqlite"])
        SCHEMA._verify_sqlite_contract(s)   # 不应抛（含 PK 顺序）
        s.close()


# ===========================================================================
# 2. CalendarService
# ===========================================================================
class TestCalendarService:
    def _svc(self, tmp_path, provider=None):
        return CalendarService(main_db=tmp_path / "quantstudio.db",
                               calendar_provider=provider)

    def test_expected_minute_times_241_bars(self, tmp_path):
        svc = self._svc(tmp_path)
        ts = svc.expected_minute_times("2026-07-24")
        assert len(ts) == 241
        assert _clock(ts[0]) == "09:30"   # 集合竞价 bar
        assert _clock(ts[1]) == "09:31"
        assert _clock(ts[-1]) == "15:00"

    def test_lunch_break_boundaries(self, tmp_path):
        svc = self._svc(tmp_path)
        clk = {_clock(t) for t in svc.expected_minute_times("2026-07-24")}
        assert {"11:30", "13:01"} <= clk
        assert clk.isdisjoint({"11:31", "13:00", "12:00", "09:29"})

    @pytest.mark.parametrize("hh,mm,exp", [
        (9, 30, "09:31"),   # 集合竞价 → 首根连续竞价
        (9, 31, "09:32"),   # +1
        (11, 29, "11:30"),  # +1 到上午末
        (11, 30, "13:01"),  # 午休跨越
        (13, 1, "13:02"),   # +1
        (14, 59, "15:00"),  # +1 到全日末
    ])
    def test_next_expected_time_intraday(self, tmp_path, hh, mm, exp):
        svc = self._svc(tmp_path)
        assert _clock(svc.next_expected_time(_at("2026-07-24", hh, mm))) == exp

    @pytest.mark.parametrize("hh,mm,ss", [(10, 0, 30), (12, 0, 0), (9, 29, 0), (15, 1, 0)])
    def test_next_expected_time_rejects_invalid_bar(self, tmp_path, hh, mm, ss):
        svc = self._svc(tmp_path)
        with pytest.raises(ValueError):
            svc.next_expected_time(_at("2026-07-24", hh, mm, ss))

    def test_cross_day_close_to_next_auction(self, tmp_path):
        svc = self._svc(tmp_path)
        _persist_full_window(svc, ["2026-07-24", "2026-07-27"])  # 周五 → 周一（跳周末）
        nxt = svc.next_expected_time(_at("2026-07-24", 15, 0))
        assert pd.Timestamp(nxt, unit="ms", tz=TZ).strftime("%Y-%m-%d %H:%M") == \
            "2026-07-27 09:30"

    def test_trading_day_navigation(self, tmp_path):
        svc = self._svc(tmp_path)
        # 完整窗口：07-22~07-27（07-25/26 周末为闭市，已持久化）
        _persist_full_window(svc, ["2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27"])
        assert svc.next_trading_day(_at("2026-07-24", 0, 0, 0)) == _at("2026-07-27", 0, 0, 0)
        assert svc.prev_trading_day(_at("2026-07-27", 0, 0, 0)) == _at("2026-07-24", 0, 0, 0)
        assert svc.is_trading_day(_at("2026-07-24", 10, 0, 0)) is True
        # 阻断 1 修复：周六已明确持久化为 is_open=False（不再是“未知当闭市”）
        assert svc.is_trading_day(_at("2026-07-25", 10, 0, 0)) is False

    def test_daily_next_expected_time(self, tmp_path):
        svc = self._svc(tmp_path)
        _persist_full_window(svc, ["2026-07-24", "2026-07-27"])
        dnext = svc.next_expected_time(_at("2026-07-24", 0, 0, 0), freq="1d")
        assert dnext == _at("2026-07-27", 0, 0, 0)
        assert _clock(dnext) == "00:00"

    def test_get_trade_days_range(self, tmp_path):
        svc = self._svc(tmp_path)
        _persist_full_window(
            svc, ["2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27"])
        days_ms = sorted(_at(d, 0, 0, 0) for d in
                         ["2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27"])
        assert svc.get_trade_days("2026-07-22", "2026-07-27") == days_ms
        assert svc.get_trade_days("2026-07-23", "2026-07-24") == days_ms[1:3]

    def test_reuses_intraday_windows_constants(self):
        """铁律：直接复用 intraday_windows，不建第二套常量。"""
        from quantstudio.backtest.providers import intraday_windows as iw
        import quantstudio.pipeline.qfq_calendar as cal
        assert cal.MORNING_START is iw.MORNING_START
        assert cal.AFTERNOON_END is iw.AFTERNOON_END

    # ---- 阻断 1 新增测试 ----
    def test_calendar_partial_cache_does_not_skip_open_days(self, tmp_path):
        """部分缓存（只缓存远处某开市日）不得跳过中间真实开市日。"""
        provider = _FakeCalendarProvider(
            ["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"])  # 周一~周四
        svc = self._svc(tmp_path, provider=provider)
        # 仅手动缓存 07-30（模拟部分缓存）
        conn = svc._connect()
        conn.execute("INSERT OR REPLACE INTO trade_calendar (cal_date, is_open, source, updated_at) "
                     "VALUES (?,?,?,?)", [_at("2026-07-30", 0, 0, 0), True, "direct", _now_iso()])
        conn.commit(); conn.close()
        # 期望返回最近开市日 07-27，而非跳到 07-30
        assert svc.next_trading_day(_at("2026-07-24", 0, 0, 0)) == _at("2026-07-27", 0, 0, 0)

    def test_get_trade_days_partial_cache_refreshes_missing_range(self, tmp_path):
        provider = _FakeCalendarProvider(
            ["2026-07-24", "2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"])
        svc = self._svc(tmp_path, provider=provider)
        # 只缓存 07-24 与 07-30（中间缺失）
        conn = svc._connect()
        for d in ("2026-07-24", "2026-07-30"):
            conn.execute("INSERT OR REPLACE INTO trade_calendar (cal_date, is_open, source, updated_at) "
                         "VALUES (?,?,?,?)", [_at(d, 0, 0, 0), True, "direct", _now_iso()])
        conn.commit(); conn.close()
        got = svc.get_trade_days("2026-07-24", "2026-07-30")
        assert got == sorted(_at(d, 0, 0, 0) for d in
                             ["2026-07-24", "2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"])

    def test_partial_cache_without_provider_raises(self, tmp_path):
        svc = self._svc(tmp_path)  # 无 provider
        conn = svc._connect()
        conn.execute("INSERT OR REPLACE INTO trade_calendar (cal_date, is_open, source, updated_at) "
                     "VALUES (?,?,?,?)", [_at("2026-07-30", 0, 0, 0), True, "direct", _now_iso()])
        conn.commit(); conn.close()
        with pytest.raises(LookupError):
            svc.get_trade_days("2026-07-24", "2026-07-30")
        with pytest.raises(LookupError):
            svc.next_trading_day(_at("2026-07-24", 0, 0, 0))

    def test_unknown_day_is_not_silently_closed(self, tmp_path):
        # 无 provider + 未缓存日期 → 抛 LookupError（未知≠闭市）
        svc_no = self._svc(tmp_path)
        with pytest.raises(LookupError):
            svc_no.is_trading_day(_at("2026-07-25", 10, 0, 0))
        # 有 provider：先明确持久化该周六为 is_open=False，再断言 False（修复错误断言）
        provider = _FakeCalendarProvider([])  # 07-25 为周六，provider 返回空 → is_open=False
        svc = self._svc(tmp_path, provider=provider)
        svc.refresh_calendar("2026-07-25", "2026-07-25")
        assert svc.is_trading_day(_at("2026-07-25", 10, 0, 0)) is False

    def test_next_trading_day_requires_complete_intervening_calendar(self, tmp_path):
        provider = _FakeCalendarProvider(
            ["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"])
        svc = self._svc(tmp_path, provider=provider)
        conn = svc._connect()
        conn.execute("INSERT OR REPLACE INTO trade_calendar (cal_date, is_open, source, updated_at) "
                     "VALUES (?,?,?,?)", [_at("2026-07-30", 0, 0, 0), True, "direct", _now_iso()])
        conn.commit(); conn.close()
        nxt = svc.next_trading_day(_at("2026-07-24", 0, 0, 0))
        assert nxt == _at("2026-07-27", 0, 0, 0)
        # 缺失区间必须被填充（闭市日也持久化）→ 证明区间完整
        conn = svc._connect()
        for d in ("2026-07-25", "2026-07-26"):
            r = conn.execute("SELECT is_open FROM trade_calendar WHERE cal_date=?",
                             [_at(d, 0, 0, 0)]).fetchone()
            assert r is not None and r[0] is False
        conn.close()

    def test_prev_trading_day_refreshes_backward(self, tmp_path):
        provider = _FakeCalendarProvider(
            ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"])
        svc = self._svc(tmp_path, provider=provider)
        # 只缓存远处的 07-20（模拟部分缓存）
        conn = svc._connect()
        conn.execute("INSERT OR REPLACE INTO trade_calendar (cal_date, is_open, source, updated_at) "
                     "VALUES (?,?,?,?)", [_at("2026-07-20", 0, 0, 0), True, "direct", _now_iso()])
        conn.commit(); conn.close()
        # 期望返回最近的前一开市日 07-23，而非跳到 07-20
        assert svc.prev_trading_day(_at("2026-07-24", 0, 0, 0)) == _at("2026-07-23", 0, 0, 0)

    def test_refresh_persists_open_and_closed_days(self, tmp_path):
        provider = _FakeCalendarProvider(["2026-07-27", "2026-07-28"])  # 07-25/26 周末闭市
        svc = self._svc(tmp_path, provider=provider)
        svc.refresh_calendar("2026-07-25", "2026-07-28")
        assert svc.is_trading_day(_at("2026-07-25", 10, 0, 0)) is False  # 周六
        assert svc.is_trading_day(_at("2026-07-26", 10, 0, 0)) is False  # 周日
        assert svc.is_trading_day(_at("2026-07-27", 10, 0, 0)) is True
        assert svc.is_trading_day(_at("2026-07-28", 10, 0, 0)) is True

    def test_provider_refresh_without_persisted_day_raises(self, tmp_path):
        # 非阻断防御：provider 刷新后仍无该日记录（持久化异常）→ 不得静默当闭市
        provider = _FakeCalendarProvider([])  # 永不开放
        svc = self._svc(tmp_path, provider=provider)
        with mock.patch.object(svc, "_persist_range", return_value=[]):
            with pytest.raises(LookupError):
                svc.is_trading_day(_at("2026-07-27", 10, 0, 0))


# ===========================================================================
# 3. ObservationStore（版本化 observation + outbox）
# ===========================================================================
class TestObservationStore:
    def _store(self, tmp_path):
        return ObservationStore(aux_db=tmp_path / "qfq_aux.db")

    def _rows(self, store, code, ft):
        c = sqlite3.connect(str(store.aux_db))
        r = c.execute("SELECT revision_no, factor_value, first_seen_run_id, last_seen_run_id "
                      "FROM qfq_factor_observation WHERE code=? AND factor_time=? "
                      "ORDER BY revision_no", [code, ft]).fetchall()
        c.close(); return r

    def _alerts(self, store):
        c = sqlite3.connect(str(store.aux_db))
        r = c.execute("SELECT alert_id, revision_no, status FROM qfq_factor_revision_alert "
                      "ORDER BY revision_no").fetchall()
        c.close(); return r

    def test_first_observation_revision_1_no_alert(self, tmp_path):
        store = self._store(tmp_path)
        res = store.record_observations([("STOCK", "600519", FT1, 2.35)], run_id="r1")
        assert (res.new_count, res.unchanged_count, res.revised_count) == (1, 0, 0)
        assert self._rows(store, "600519", FT1) == [(1, 2.35, "r1", "r1")]
        assert self._alerts(store) == []

    def test_unchanged_only_refreshes_last_seen(self, tmp_path):
        store = self._store(tmp_path)
        store.record_observations([("STOCK", "600519", FT1, 2.35)], run_id="r1")
        res = store.record_observations([("STOCK", "600519", FT1, 2.35 + 1e-12)], run_id="r2")
        assert (res.new_count, res.unchanged_count, res.revised_count) == (0, 1, 0)
        assert self._rows(store, "600519", FT1) == [(1, 2.35, "r1", "r2")]
        assert self._alerts(store) == []

    def test_revision_keeps_old_row_and_emits_alert(self, tmp_path):
        store = self._store(tmp_path)
        store.record_observations([("STOCK", "600519", FT1, 2.35)], run_id="r1")
        res = store.record_observations([("STOCK", "600519", FT1, 2.50)], run_id="r3")
        assert (res.new_count, res.unchanged_count, res.revised_count) == (0, 0, 1)
        assert self._rows(store, "600519", FT1) == [
            (1, 2.35, "r1", "r1"), (2, 2.50, "r3", "r3")]  # 旧行 1 完整保留
        al = self._alerts(store)
        assert len(al) == 1 and al[0][1] == 2 and al[0][2] == "pending"
        assert al[0][0] == alert_id_of("STOCK", "600519", FT1, 2)
        assert res.revisions[0].previous_value == 2.35
        assert res.revisions[0].current_value == 2.50

    def test_alert_id_deterministic_and_idempotent(self, tmp_path):
        store = self._store(tmp_path)
        store.record_observations([("STOCK", "600519", FT1, 2.35)], run_id="r1")
        store.record_observations([("STOCK", "600519", FT1, 2.50)], run_id="r3")
        # 再次写入相同 rev2 值 → unchanged，不新增 alert
        store.record_observations([("STOCK", "600519", FT1, 2.50)], run_id="r4")
        assert len(self._alerts(store)) == 1

    def test_future_excluded(self, tmp_path):
        store = self._store(tmp_path)
        res = store.record_observations(
            [("STOCK", "000001", FT2, 9.9)], run_id="r5", as_of_ms=FT1)
        assert res.future_excluded == 1 and res.observed == 0

    def test_non_finite_rejected_and_rolled_back(self, tmp_path):
        store = self._store(tmp_path)
        store._connect().close()  # 确保 schema 存在（拒绝路径不触达 _connect）
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "000002", FT1, float("nan"))], run_id="r6")
        c = sqlite3.connect(str(store.aux_db))
        n = c.execute("SELECT count(*) FROM qfq_factor_observation WHERE code='000002'").fetchone()[0]
        c.close()
        assert n == 0

    def test_external_conn_atomic_rollback(self, tmp_path):
        store = self._store(tmp_path)
        store._connect().close()  # 确保 schema 已建
        c = sqlite3.connect(str(store.aux_db)); c.execute("PRAGMA busy_timeout=30000")
        c.execute("BEGIN IMMEDIATE")
        store.record_observations([("STOCK", "600036", FT1, 5.0)], run_id="tx", conn=c)
        c.rollback()  # 调用方回滚 → observation 一并撤销
        c2 = sqlite3.connect(str(store.aux_db))
        n = c2.execute("SELECT count(*) FROM qfq_factor_observation WHERE code='600036'").fetchone()[0]
        c2.close()
        assert n == 0

    def test_outbox_list_and_acknowledge(self, tmp_path):
        store = self._store(tmp_path)
        store.record_observations([("STOCK", "600519", FT1, 2.35)], run_id="r1")
        store.record_observations([("STOCK", "600519", FT1, 2.50)], run_id="r3")
        pend = store.list_pending_alerts()
        assert len(pend) == 1 and pend[0]["code"] == "600519"
        store.acknowledge_alert(pend[0]["alert_id"])
        assert store.list_pending_alerts() == []

    # ---- 阻断 2 新增测试 ----
    def test_observation_rejects_zero_factor(self, tmp_path):
        store = self._store(tmp_path)
        store._connect().close()
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, 0.0)], run_id="r")
        c = sqlite3.connect(str(store.aux_db))
        assert c.execute("SELECT count(*) FROM qfq_factor_observation").fetchone()[0] == 0
        c.close()

    def test_observation_rejects_negative_factor(self, tmp_path):
        store = self._store(tmp_path)
        store._connect().close()
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, -1.0)], run_id="r")
        c = sqlite3.connect(str(store.aux_db))
        assert c.execute("SELECT count(*) FROM qfq_factor_observation").fetchone()[0] == 0
        c.close()

    def test_observation_rejects_invalid_epoch_ms(self, tmp_path):
        store = self._store(tmp_path)
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", -1, 1.0)], run_id="r")
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", 50, 1.0)], run_id="r")  # 远早于 2000
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", "bad", 1.0)], run_id="r")

    def test_observation_rejects_invalid_asset_type(self, tmp_path):
        store = self._store(tmp_path)
        for bad in ["bond", "index", "", "NONE", None, "stock_etf"]:
            with pytest.raises(ValueError):
                store.record_observations(
                    [(bad if bad is not None else None, "600519", FT1, 1.0)], run_id="r")

    def test_observation_normalizes_and_validates_bare_code(self, tmp_path):
        store = self._store(tmp_path)
        # 合法裸 6 位码
        assert store.record_observations([("STOCK", "600000", FT1, 1.0)], run_id="r").new_count == 1
        # 非法码
        for bad in ["sh600000", "60000", "6000000", "", "NONE", None]:
            with pytest.raises(ValueError):
                store.record_observations(
                    [("STOCK", bad if bad is not None else None, FT1, 1.0)], run_id="r")

    def test_observation_rejects_empty_run_id(self, tmp_path):
        store = self._store(tmp_path)
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, 1.0)], run_id="")

    def test_same_run_duplicate_same_value_deduped(self, tmp_path):
        store = self._store(tmp_path)
        res = store.record_observations(
            [("STOCK", "600000", FT1, 1.0), ("STOCK", "600000", FT1, 1.0)], run_id="r")
        assert res.new_count == 1 and res.observed == 1  # 同键同值去重
        c = sqlite3.connect(str(store.aux_db))
        n = c.execute("SELECT count(*) FROM qfq_factor_observation WHERE code='600000'").fetchone()[0]
        na = c.execute("SELECT count(*) FROM qfq_factor_revision_alert").fetchone()[0]
        c.close()
        assert n == 1 and na == 0

    def test_same_run_duplicate_conflicting_value_rolls_back(self, tmp_path):
        store = self._store(tmp_path)
        store._connect().close()
        with pytest.raises(ValueError):
            store.record_observations(
                [("STOCK", "600000", FT1, 1.0), ("STOCK", "600000", FT1, 1.1)], run_id="r")
        c = sqlite3.connect(str(store.aux_db))
        n = c.execute("SELECT count(*) FROM qfq_factor_observation WHERE code='600000'").fetchone()[0]
        c.close()
        assert n == 0  # 整批拒绝，不写任何行

    def test_tolerance_uses_absolute_and_relative_components(self, tmp_path):
        # 1) 小基数 + 微小抖动：绝对分量兜底 → unchanged
        s1 = ObservationStore(aux_db=tmp_path / "a1.db")
        s1.record_observations([("STOCK", "600001", FT1, 1.0)], run_id="r")
        assert s1.record_observations([("STOCK", "600001", FT1, 1.0 + 2e-12)],
                                      run_id="r2").revised_count == 0
        # 2) 大基数 + 小绝对差：相对分量兜底 → unchanged（0.5 <= 1e-6*1e6=1.0）
        s2 = ObservationStore(aux_db=tmp_path / "a2.db")
        s2.record_observations([("STOCK", "600002", FT1, 1_000_000.0)], run_id="r")
        assert s2.record_observations([("STOCK", "600002", FT1, 1_000_000.0 + 0.5)],
                                      run_id="r2").revised_count == 0
        # 3) 大基数 + 超过双容差：修订（2.0 > 相对容差 1.0）
        s3 = ObservationStore(aux_db=tmp_path / "a3.db")
        s3.record_observations([("STOCK", "600003", FT1, 1_000_000.0)], run_id="r")
        assert s3.record_observations([("STOCK", "600003", FT1, 1_000_000.0 + 2.0)],
                                      run_id="r2").revised_count == 1

    def test_outbox_revision_and_alert_atomic_rollback(self, tmp_path):
        """rev+alert 必须在同一外部事务中一起回滚（原测试只覆盖首次 observation）。"""
        store = self._store(tmp_path)
        store.record_observations([("STOCK", "600036", FT1, 5.0)], run_id="r1")  # rev1 自管
        c = sqlite3.connect(str(store.aux_db)); c.execute("PRAGMA busy_timeout=30000")
        c.execute("BEGIN IMMEDIATE")
        res = store.record_observations([("STOCK", "600036", FT1, 6.0)], run_id="r2", conn=c)
        assert res.revised_count == 1
        c.rollback()  # 调用方回滚 → rev2 与 alert 一并撤销
        c2 = sqlite3.connect(str(store.aux_db))
        n_obs = c2.execute("SELECT count(*) FROM qfq_factor_observation "
                           "WHERE code='600036'").fetchone()[0]
        n_alert = c2.execute("SELECT count(*) FROM qfq_factor_revision_alert "
                             "WHERE code='600036'").fetchone()[0]
        c2.close()
        assert n_obs == 1   # 仅 rev1 保留
        assert n_alert == 0  # alert 随 rev2 回滚

    # ---- 阻断 2：run_id / as_of_ms 审计语义污染 ----
    def test_observation_rejects_whitespace_run_id(self, tmp_path):
        store = self._store(tmp_path)
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, 1.0)], run_id="   ")

    def test_observation_rejects_non_string_run_id(self, tmp_path):
        store = self._store(tmp_path)
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, 1.0)], run_id=123)

    def test_observation_rejects_invalid_as_of_ms(self, tmp_path):
        store = self._store(tmp_path)
        # as_of_ms 非法（负值 / 非整数 / 远早于2000 / 远晚于2100）→ 不得静默全量 future_excluded
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, 1.0)], run_id="r", as_of_ms=-1)
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, 1.0)], run_id="r", as_of_ms="bad")
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, 1.0)], run_id="r", as_of_ms=50)
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, 1.0)], run_id="r",
                                      as_of_ms=9_999_999_999_999)

    def test_observation_rejects_nonpositive_stored_baseline(self, tmp_path):
        store = self._store(tmp_path)
        store._connect().close()  # 确保 schema 存在
        # 直接写入一个非法零基线（绕过输入校验），模拟旧库损坏数据
        c = sqlite3.connect(str(store.aux_db))
        c.execute("INSERT INTO qfq_factor_observation "
                  "(asset_type, code, factor_time, factor_value, revision_no, "
                  "first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
                  "VALUES ('STOCK','600519',?,0.0,1,'r0','r0','t','t')", [FT1])
        c.commit(); c.close()
        # 再次写入不同值 → 基线损坏（≤0）必须拒绝，且不新增 revision 行
        with pytest.raises(ValueError):
            store.record_observations([("STOCK", "600519", FT1, 2.0)], run_id="r1")
        c = sqlite3.connect(str(store.aux_db))
        n = c.execute("SELECT count(*) FROM qfq_factor_observation WHERE code='600519'").fetchone()[0]
        na = c.execute("SELECT count(*) FROM qfq_factor_revision_alert WHERE code='600519'").fetchone()[0]
        c.close()
        assert n == 1 and na == 0  # 仅原始零基线，无新增

    def test_observation_partial_epsilon_override_semantics(self, tmp_path):
        # 仅覆盖 epsilon_abs → epsilon_rel 回退实例默认（1e-6）
        s1 = ObservationStore(aux_db=tmp_path / "e1.db")
        s1.record_observations([("STOCK", "600001", FT1, 1.0)], run_id="r")
        # 绝对差 1e-4 <= max(覆盖 1e-3, 默认 1e-6*1.0)=1e-3 → unchanged
        assert s1.record_observations([("STOCK", "600001", FT1, 1.0 + 1e-4)],
                                      run_id="r2", epsilon_abs=1e-3).revised_count == 0
        # 仅覆盖 epsilon_rel → epsilon_abs 回退实例默认（1e-9）
        s2 = ObservationStore(aux_db=tmp_path / "e2.db")
        s2.record_observations([("STOCK", "600002", FT1, 1_000_000.0)], run_id="r")
        # 绝对差 0.5 <= max(默认 1e-9, 覆盖 1e-2*1e6=1e4)=1e4 → unchanged
        assert s2.record_observations([("STOCK", "600002", FT1, 1_000_000.0 + 0.5)],
                                      run_id="r2", epsilon_rel=1e-2).revised_count == 0

    # ---- 阻断 1：per-call epsilon 覆盖必须作用于批内去重 ----
    def test_duplicate_preprocess_uses_per_call_epsilon_override(self, tmp_path):
        # a=1.0, b=1.05：实例默认(1e-9/1e-6) 会判冲突；调用方传 abs=0,rel=0.1 应合并。
        store = self._store(tmp_path)
        res = store.record_observations(
            [("STOCK", "600000", FT1, 1.00), ("STOCK", "600000", FT1, 1.05)],
            run_id="r", epsilon_abs=0, epsilon_rel=0.1)
        assert (res.new_count, res.observed) == (1, 1)  # 合并，未误判冲突
        c = sqlite3.connect(str(store.aux_db))
        n = c.execute("SELECT count(*) FROM qfq_factor_observation WHERE code='600000'").fetchone()[0]
        na = c.execute("SELECT count(*) FROM qfq_factor_revision_alert").fetchone()[0]
        c.close()
        assert n == 1 and na == 0  # 仅一行、无 alert

    def test_duplicate_preprocess_partial_override_uses_instance_other_component(self, tmp_path):
        # 仅覆盖 abs=1e-3（rel 回退 1e-6）：a=1.0,b=1.0002 默认会冲突，覆盖后应合并。
        store = self._store(tmp_path)
        res = store.record_observations(
            [("STOCK", "600000", FT1, 1.0), ("STOCK", "600000", FT1, 1.0 + 2e-4)],
            run_id="r", epsilon_abs=1e-3)
        assert (res.new_count, res.observed) == (1, 1)
        # 仅覆盖 rel=1e-2（abs 回退 1e-9）：大基数 a=1e6,b=1e6+0.5 默认会冲突，覆盖后应合并。
        s2 = ObservationStore(aux_db=tmp_path / "pp.db")
        res2 = s2.record_observations(
            [("STOCK", "600001", FT1, 1_000_000.0), ("STOCK", "600001", FT1, 1_000_000.0 + 0.5)],
            run_id="r", epsilon_rel=1e-2)
        assert (res2.new_count, res2.observed) == (1, 1)


# ===========================================================================
# 4. 事务感知 writer 内部方法（+ 公共方法回归守卫）
# ===========================================================================
class TestWriterTxMethods:
    def _writer(self, tmp_path):
        return DuckDBWriter({"type": "duckdb", "path": str(tmp_path / "quantstudio.db")})

    def test_public_advance_watermark_unchanged(self, tmp_path):
        """铁律回归守卫：公共 advance_watermark 行为不变。"""
        w = self._writer(tmp_path)
        w.advance_watermark("xtquant", "stock_minutes", "1min", FT1, "batchP")
        assert w.get_last_date("xtquant", "stock_minutes", "1min") == str(FT1)

    def test_tx_atomic_rollback(self, tmp_path):
        w = self._writer(tmp_path)
        w.advance_watermark("xtquant", "stock_minutes", "1min", FT1, "batchP")
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        conn.execute("BEGIN TRANSACTION")
        w._advance_watermark_on_conn(conn, "xtquant", "stock_minutes", "1min", FT2, "tx")
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision",
            anchor_version=7)
        conn.execute("ROLLBACK")
        wm = conn.execute("SELECT last_date FROM source_watermark "
                          "WHERE table_name='stock_minutes' AND freq='1min'").fetchone()[0]
        n = conn.execute("SELECT count(*) FROM qfq_pending_backfill").fetchone()[0]
        conn.close()
        assert wm == FT1 and n == 0  # 原子性：一起回滚

    def test_tx_commit_persists_both(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        conn.execute("BEGIN TRANSACTION")
        w._advance_watermark_on_conn(conn, "xtquant", "stock_minutes", "1min", FT2, "tx2")
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision",
            anchor_version=7)
        conn.commit()
        assert w.get_last_date("xtquant", "stock_minutes", "1min") == str(FT2)
        r = conn.execute("SELECT reason, anchor_version, status, attempt_count "
                         "FROM qfq_pending_backfill").fetchone()
        conn.close()
        assert r == ("blocked_revision", 7, "pending", 0)

    def test_pending_backfill_conflict_idempotent(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        conn.execute("BEGIN TRANSACTION")
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision",
            anchor_version=7)
        conn.commit()
        created0 = conn.execute("SELECT created_at FROM qfq_pending_backfill").fetchone()[0]
        conn.execute("BEGIN TRANSACTION")
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="bootstrap_required",
            anchor_version=8)
        conn.commit()
        cnt = conn.execute("SELECT count(*) FROM qfq_pending_backfill").fetchone()[0]
        r = conn.execute("SELECT reason, anchor_version, attempt_count, created_at "
                         "FROM qfq_pending_backfill").fetchone()
        conn.close()
        assert cnt == 1
        assert r[0] == "bootstrap_required" and r[1] == 8
        assert r[2] == 0 and r[3] == created0  # attempt_count / created_at 保留

    # ---- 阻断 4 新增测试 ----
    def test_resolved_backfill_idempotent_upsert_stays_resolved(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        conn.execute("BEGIN TRANSACTION")
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision")
        conn.commit()
        # 标记为 resolved
        conn.execute("UPDATE qfq_pending_backfill SET status='resolved', resolved_at=? "
                     "WHERE code='600875'", [_now_iso()])
        conn.commit()
        # 普通幂等 upsert（不重开）→ 应保持 resolved
        conn.execute("BEGIN TRANSACTION")
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision")
        conn.commit()
        r = conn.execute("SELECT status, resolved_at FROM qfq_pending_backfill").fetchone()
        conn.close()
        assert r[0] == "resolved" and r[1] is not None

    def test_explicit_reopen_clears_resolved_at(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        conn.execute("BEGIN TRANSACTION")
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision")
        conn.commit()
        conn.execute("UPDATE qfq_pending_backfill SET status='resolved', resolved_at=?, "
                     "last_error='boom', attempt_count=3 WHERE code='600875'", [_now_iso()])
        conn.commit()
        conn.execute("BEGIN TRANSACTION")
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="bootstrap_required",
            reopen=True)
        conn.commit()
        r = conn.execute("SELECT status, resolved_at, last_error, attempt_count "
                         "FROM qfq_pending_backfill").fetchone()
        conn.close()
        assert r == ("pending", None, None, 0)

    def test_pending_backfill_rejects_inverted_range(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        with pytest.raises(ValueError):
            w._upsert_pending_backfill_on_conn(
                conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
                freq="1min", range_start=FT2, range_end=FT1, reason="blocked_revision")
        conn.close()

    def test_pending_backfill_rejects_invalid_table(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        with pytest.raises(ValueError):
            w._upsert_pending_backfill_on_conn(
                conn, asset_type="STOCK", code="600875", table_name="tick",
                freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision")
        conn.close()

    # ---- 阻断 3：pending backfill 关联契约校验 ----
    def test_pending_backfill_rejects_invalid_code(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        for bad in ["bad", "sh600875", "60087", "6008750", None]:
            with pytest.raises(ValueError):
                w._upsert_pending_backfill_on_conn(
                    conn, asset_type="STOCK", code=bad if bad is not None else None,
                    table_name="stock_minutes", freq="1min",
                    range_start=FT1, range_end=FT2, reason="blocked_revision")
        conn.close()

    def test_pending_backfill_rejects_invalid_epoch_ms(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        for bad in [-1, 2, 50]:
            with pytest.raises(ValueError):
                w._upsert_pending_backfill_on_conn(
                    conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
                    freq="1min", range_start=bad, range_end=FT2, reason="blocked_revision")
        # range_end 非法（range_start 合法）
        with pytest.raises(ValueError):
            w._upsert_pending_backfill_on_conn(
                conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
                freq="1min", range_start=FT1, range_end=2, reason="blocked_revision")
        # 合法范围应成功
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision")
        conn.close()

    def test_pending_backfill_rejects_asset_table_mismatch(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        for at, tbl in [("STOCK", "etf_daily"), ("ETF", "stock_minutes"),
                        ("STOCK", "etf_minutes"), ("ETF", "stock_daily")]:
            with pytest.raises(ValueError):
                w._upsert_pending_backfill_on_conn(
                    conn, asset_type=at, code="600875", table_name=tbl,
                    freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision")
        conn.close()

    def test_pending_backfill_rejects_daily_minute_freq_mismatch(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        # 日线表配分钟 freq
        with pytest.raises(ValueError):
            w._upsert_pending_backfill_on_conn(
                conn, asset_type="STOCK", code="600875", table_name="stock_daily",
                freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision")
        # 分钟表配日线 freq
        with pytest.raises(ValueError):
            w._upsert_pending_backfill_on_conn(
                conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
                freq="daily", range_start=FT1, range_end=FT2, reason="blocked_revision")
        conn.close()

    def test_pending_backfill_rejects_empty_reason(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        for bad in ["", "   "]:
            with pytest.raises(ValueError):
                w._upsert_pending_backfill_on_conn(
                    conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
                    freq="1min", range_start=FT1, range_end=FT2, reason=bad)
        conn.close()

    def test_pending_backfill_rejects_non_bool_reopen(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        for bad in ["false", "true", 0, 1]:
            with pytest.raises(ValueError):
                w._upsert_pending_backfill_on_conn(
                    conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
                    freq="1min", range_start=FT1, range_end=FT2,
                    reason="blocked_revision", reopen=bad)
        # 合法 bool 不误伤
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2,
            reason="blocked_revision", reopen=True)
        conn.close()

    # ---- 阻断 2：freq 别名必须规范为 storage canonical ----
    def test_pending_backfill_freq_1m_stored_as_1min(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1m", range_start=FT1, range_end=FT2, reason="blocked_revision")
        r = conn.execute("SELECT freq FROM qfq_pending_backfill").fetchone()
        conn.close()
        assert r[0] == "1min"  # 别名 1m → canonical 1min

    def test_pending_backfill_freq_1d_stored_as_daily(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_daily",
            freq="1d", range_start=FT1, range_end=FT2, reason="blocked_revision")
        r = conn.execute("SELECT freq FROM qfq_pending_backfill").fetchone()
        conn.close()
        assert r[0] == "daily"  # 别名 1d → canonical daily

    def test_freq_aliases_conflict_to_same_pending_primary_key(self, tmp_path):
        # 1m 与 1min 同源 → 规范后同一主键（idempotent，不新增行）。
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1m", range_start=FT1, range_end=FT2, reason="blocked_revision")
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="bootstrap_required")
        cnt = conn.execute("SELECT count(*) FROM qfq_pending_backfill").fetchone()[0]
        r = conn.execute("SELECT freq, reason FROM qfq_pending_backfill").fetchone()
        conn.close()
        assert cnt == 1
        assert r[0] == "1min" and r[1] == "bootstrap_required"

    # ---- 阻断 3：普通 upsert 禁止创建/变更终态 ----
    def test_pending_upsert_cannot_create_resolved_without_timestamp(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        # 新建行直接声明 status='resolved' → 禁止（需 _resolve_backfill_on_conn）
        with pytest.raises(ValueError):
            w._upsert_pending_backfill_on_conn(
                conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
                freq="1min", range_start=FT1, range_end=FT2,
                reason="done", status="resolved")
        n = conn.execute("SELECT count(*) FROM qfq_pending_backfill").fetchone()[0]
        conn.close()
        assert n == 0  # 未写入矛盾行

    def test_pending_upsert_cannot_create_in_progress_directly(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        with pytest.raises(ValueError):
            w._upsert_pending_backfill_on_conn(
                conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
                freq="1min", range_start=FT1, range_end=FT2,
                reason="running", status="in_progress")
        n = conn.execute("SELECT count(*) FROM qfq_pending_backfill").fetchone()[0]
        conn.close()
        assert n == 0

    def test_resolved_transition_requires_dedicated_method(self, tmp_path):
        w = self._writer(tmp_path)
        conn = w._conn()
        SCHEMA.init_duckdb_schema(conn); conn.commit()
        w._upsert_pending_backfill_on_conn(
            conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
            freq="1min", range_start=FT1, range_end=FT2, reason="blocked_revision")
        conn.commit()
        # 普通 upsert 把 pending 直接改 resolved → 禁止（需专用状态机方法）
        with pytest.raises(ValueError):
            w._upsert_pending_backfill_on_conn(
                conn, asset_type="STOCK", code="600875", table_name="stock_minutes",
                freq="1min", range_start=FT1, range_end=FT2,
                reason="blocked_revision", status="resolved")
        r = conn.execute("SELECT status FROM qfq_pending_backfill").fetchone()
        conn.close()
        assert r[0] == "pending"  # 仍 pending，未被非法转移
