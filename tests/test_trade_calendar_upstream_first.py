# -*- coding: utf-8 -*-
"""案二：trade_calendar 前置上游化——启动期 preflight + per-date local-first 测试。

设计件：docs/design-trade-calendar-upstream-first-20261105.md（②审裁：视窗
[-7d,+30d] 参数化可 env 覆盖；strict 默认关；命中留痕 `calendar local-first
hit rows=N`）。

测试面（委派件四项）：
- 等价性：覆盖完整时 local_first_trade_days 与**冻结参考基准**（现实现 tushare
  trade_cal 过滤语义 cal[cal["is_open"]==1]["cal_date"].tolist()）逐位一致；
  覆盖不完整/未建表/开关关闭 → None（调用方回落现拉 ⇒ 现行为保留）。
- local-first 命中日志含 `calendar local-first hit rows=N`。
- strict：关 ⇒ 不阻断（默认仅告警放行）；开 ⇒ 缺日历显式失败（RuntimeError）。
- is_open 口径：凡判交易日处必带 AND is_open（区间含周末 ⇒ 交易日不含周末）。

隔离：全部用临时 DuckDB（tmp_path），不触碰正式库；env 经 monkeypatch 清理。
"""
from __future__ import annotations

import inspect
import logging
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from quantstudio.pipeline.qfq_calendar import (  # noqa: E402
    CALENDAR_LOCAL_FIRST_ENV,
    CALENDAR_PREFLIGHT_FUTURE_DAYS_ENV,
    CALENDAR_PREFLIGHT_PAST_DAYS_ENV,
    CalendarService,
    _at,
    calendar_preflight,
    local_first_trade_days,
)

# ---------------------------------------------------------------------------
# fixture 数据：2026-01-05(周一) ~ 2026-01-25(周日) = 21 自然日
# 周末（01-10/11、01-17/18、01-24/25）+ 节假日（01-20 周二）闭市 → open 14 / closed 7
# ---------------------------------------------------------------------------
WINDOW_START, WINDOW_END = "2026-01-05", "2026-01-25"
_HOLIDAY = "2026-01-20"
_ALL_DAYS = [d.strftime("%Y-%m-%d")
             for d in pd.date_range(WINDOW_START, WINDOW_END, freq="D")]
OPEN_DAYS = [d for d in _ALL_DAYS
             if pd.Timestamp(d).weekday() < 5 and d != _HOLIDAY]
CLOSED_DAYS = [d for d in _ALL_DAYS if d not in set(OPEN_DAYS)]


def _build_calendar_db(tmp_path, open_days, closed_days=()):
    """临时主库 + 完整自然日日历（复用生产写入路径 persist_trade_days_on_conn）。"""
    db = tmp_path / "quantstudio.db"
    svc = CalendarService(main_db=db)
    conn = svc._connect()  # 幂等 DDL 建表（含 3A 写锁，与生产一致）
    try:
        svc.persist_trade_days_on_conn(
            conn, [_at(d, 0, 0, 0) for d in open_days],
            closed_ms=[_at(d, 0, 0, 0) for d in closed_days], source="test")
        conn.commit()
    finally:
        conn.close()
    return db


def _build_calendar_db_around_today(tmp_path):
    """以**真实今日**为锚建 [−10, +40] 自然日日历（工作日开市/周末闭市）。

    daemon `_calendar_preflight` 缺省锚定当日（无 today 入参），故 OK 路径测试
    必须用真实日期锚定的 fixture；±10/40 比缺省视窗 ±7/30 留有余量。
    """
    today = pd.Timestamp.now(tz="Asia/Shanghai").normalize()
    rng = pd.date_range(today - pd.Timedelta(days=10),
                        today + pd.Timedelta(days=40), freq="D")
    days = [d.strftime("%Y-%m-%d") for d in rng]
    open_days = [d for d in days if pd.Timestamp(d).weekday() < 5]
    closed = [d for d in days if d not in set(open_days)]
    return _build_calendar_db(tmp_path, open_days, closed)


def _reference_tushare_filter(open_days, start, end):
    """冻结参考基准（**现实现**语义，daemon per-date 现拉路径）：
    tushare trade_cal 返回 [start,end] 行 → cal[cal["is_open"]==1]["cal_date"]
    .tolist() → 'YYYYMMDD' 升序列表。"""
    s = str(start).replace("-", "")
    e = str(end).replace("-", "")
    return sorted(d.replace("-", "") for d in open_days
                  if s <= d.replace("-", "") <= e)


@pytest.fixture(autouse=True)
def _clean_calendar_envs(monkeypatch):
    """清掉本特性全部 env，保证缺省行为测试确定性。"""
    for k in (CALENDAR_LOCAL_FIRST_ENV,
              CALENDAR_PREFLIGHT_PAST_DAYS_ENV,
              CALENDAR_PREFLIGHT_FUTURE_DAYS_ENV,
              "QS_CALENDAR_PREFLIGHT",
              "QS_CALENDAR_PREFLIGHT_STRICT"):
        monkeypatch.delenv(k, raising=False)


# ===========================================================================
# 1. local-first 等价性 + is_open 口径 + 命中日志
# ===========================================================================
class TestLocalFirstEquivalence:

    def test_hit_equals_frozen_reference_bitwise(self, tmp_path):
        """覆盖完整 ⇒ 与冻结参考基准（现实现）逐位一致（等价硬门）。"""
        db = _build_calendar_db(tmp_path, OPEN_DAYS, CLOSED_DAYS)
        got = local_first_trade_days(db, WINDOW_START, WINDOW_END)
        assert got == _reference_tushare_filter(OPEN_DAYS, WINDOW_START, WINDOW_END)
        assert len(got) == 14  # 21 自然日 − 6 周末 − 1 节假日

    def test_hit_log_line(self, tmp_path, caplog):
        """命中路径留痕一行 `calendar local-first hit rows=N`（②审要点 4）。"""
        db = _build_calendar_db(tmp_path, OPEN_DAYS, CLOSED_DAYS)
        with caplog.at_level(logging.INFO,
                             logger="quantstudio.pipeline.qfq_calendar"):
            local_first_trade_days(db, WINDOW_START, WINDOW_END, tag="batch-1")
        joined = " | ".join(r.getMessage() for r in caplog.records)
        assert "calendar local-first hit rows=14" in joined
        assert "[batch-1]" in joined  # tag 前缀（batch_id 透传）

    def test_is_open_criterion_excludes_weekends(self, tmp_path):
        """is_open 口径：判交易日必带 AND is_open——区间含周末 ⇒ 计数不含周末。"""
        db = _build_calendar_db(tmp_path, OPEN_DAYS, CLOSED_DAYS)
        # 纯周末区间：覆盖完整但全闭市 → 空（与 tushare 现拉过滤一致，非 None）
        assert local_first_trade_days(db, "2026-01-10", "2026-01-11") == []
        # 跨周末区间：周五+周六+周日+周一 → 只返回周五/周一
        assert local_first_trade_days(db, "2026-01-09", "2026-01-12") \
            == ["20260109", "20260112"]
        # 节假日（周中闭市日）同样被 AND is_open 排除
        assert "20260120" not in local_first_trade_days(db, "2026-01-19",
                                                        "2026-01-21")

    def test_subrange_hit_and_gap_miss(self, tmp_path):
        """部分覆盖库：请求落在已覆盖子区间 ⇒ 命中；跨缺口 ⇒ None 回落现拉。"""
        part_open = [d for d in OPEN_DAYS if d <= "2026-01-15"]
        part_closed = [d for d in CLOSED_DAYS if d <= "2026-01-15"]
        db = _build_calendar_db(tmp_path, part_open, part_closed)  # 仅覆盖到 01-15
        got = local_first_trade_days(db, "2026-01-05", "2026-01-12")
        assert got == _reference_tushare_filter(part_open, "2026-01-05",
                                                "2026-01-12")
        assert local_first_trade_days(db, WINDOW_START, WINDOW_END) is None

    def test_missing_table_returns_none_creates_nothing(self, tmp_path):
        """未建表 ⇒ None（回落现拉），且只读探测不得顺手建表。"""
        import duckdb
        db = tmp_path / "empty.db"
        duckdb.connect(str(db)).close()  # 建空库文件（无表）
        assert local_first_trade_days(db, WINDOW_START, WINDOW_END) is None
        conn = duckdb.connect(str(db))
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = 'trade_calendar'").fetchone()[0]
        finally:
            conn.close()
        assert n == 0

    def test_env_disable_returns_none_even_when_covered(self, tmp_path,
                                                        monkeypatch):
        """QS_CALENDAR_LOCAL_FIRST=0 ⇒ 整体关闭（覆盖完整也返回 None）。"""
        db = _build_calendar_db(tmp_path, OPEN_DAYS, CLOSED_DAYS)
        monkeypatch.setenv(CALENDAR_LOCAL_FIRST_ENV, "0")
        assert local_first_trade_days(db, WINDOW_START, WINDOW_END) is None

    def test_accepts_compact_date_format(self, tmp_path):
        """daemon 现拉入参为 YYYYMMDD 口径——YYYY-MM-DD / YYYYMMDD 两格式等价。"""
        db = _build_calendar_db(tmp_path, OPEN_DAYS, CLOSED_DAYS)
        a = local_first_trade_days(db, "2026-01-05", "2026-01-12")
        b = local_first_trade_days(db, "20260105", "20260112")
        assert a == b == ["20260105", "20260106", "20260107", "20260108",
                          "20260109", "20260112"]


# ===========================================================================
# 2. 启动期 preflight（新增检测型：缺失从下游迟发 → 启动即明确）
# ===========================================================================
class TestCalendarPreflight:

    def test_ok_when_window_covered(self, tmp_path):
        db = _build_calendar_db(tmp_path, OPEN_DAYS, CLOSED_DAYS)
        info = calendar_preflight(db, today="2026-01-15",
                                  past_days=7, future_days=10)  # 视窗 ⊆ 覆盖
        assert info["ok"] is True
        assert info["window_covered"] is True
        assert info["rows"] == 21
        assert info["detail"]

    def test_default_window_7d_30d(self, tmp_path):
        """缺省视窗 = [今日−7, 今日+30]（方案件 §2.1；②审要点 1）。"""
        info = calendar_preflight(tmp_path / "no.db", today="2026-01-15")
        assert info["past_days"] == 7 and info["future_days"] == 30
        assert info["window_lo"] == _at("2026-01-08", 0, 0, 0)
        assert info["window_hi"] == _at("2026-02-14", 0, 0, 0)
        assert info["ok"] is False  # 库/表不存在 → 不可用（启动即知）

    def test_env_window_override(self, tmp_path, monkeypatch):
        """env 可覆盖视窗参数（QS_CALENDAR_PREFLIGHT_PAST/_FUTURE_DAYS）。"""
        db = _build_calendar_db(tmp_path, OPEN_DAYS, CLOSED_DAYS)
        monkeypatch.setenv(CALENDAR_PREFLIGHT_PAST_DAYS_ENV, "0")
        monkeypatch.setenv(CALENDAR_PREFLIGHT_FUTURE_DAYS_ENV, "0")
        info = calendar_preflight(db, today="2026-01-15")
        assert info["window_lo"] == info["window_hi"] == _at("2026-01-15", 0, 0, 0)
        assert info["ok"] is True  # 单日视窗在覆盖内

    def test_empty_table_not_ok(self, tmp_path):
        db = tmp_path / "t.db"
        svc = CalendarService(main_db=db)
        conn = svc._connect()
        conn.commit()
        conn.close()  # 只建空表
        info = calendar_preflight(db, today="2026-01-15")
        assert info["ok"] is False and info["rows"] == 0
        assert "空表" in info["detail"]

    def test_missing_table_not_ok_and_no_side_create(self, tmp_path):
        """表不存在 ⇒ ok=False；探测不得建表（不引入 schema 副作用）。"""
        import duckdb
        db = tmp_path / "notable.db"
        duckdb.connect(str(db)).close()
        info = calendar_preflight(db, today="2026-01-15")
        assert info["ok"] is False
        assert "不可读" in info["detail"]
        conn = duckdb.connect(str(db))
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = 'trade_calendar'").fetchone()[0]
        finally:
            conn.close()
        assert n == 0

    def test_window_gap_not_ok(self, tmp_path):
        """表非空但视窗越界（cal_max < 今日+future）⇒ ok=False（不再等下游迟发）。"""
        part_open = [d for d in OPEN_DAYS if d <= "2026-01-15"]
        part_closed = [d for d in CLOSED_DAYS if d <= "2026-01-15"]
        db = _build_calendar_db(tmp_path, part_open, part_closed)
        info = calendar_preflight(db, today="2026-01-15",
                                  past_days=7, future_days=10)  # 视窗至 01-25
        assert info["ok"] is False and info["window_covered"] is False
        assert "未缓存" in info["detail"]


# ===========================================================================
# 3. daemon 装配：strict 默认关（纯增益）+ 入口/per-date 布线在位
# ===========================================================================
def _resident_cls():
    from quantstudio.pipeline.daemon import ResidentCollector
    return ResidentCollector


class _PreflightHost:
    """最小宿主：借用 ResidentCollector 的方法（免完整构造 collector）。

    方法在首次实例化时于**类级**借用——实例属性存函数不会触发描述符绑定
    （host._calendar_preflight() 将缺 self），类级赋值才能得到正确 bound method。
    """

    _borrowed = False

    def __init__(self, db_path):
        if not _PreflightHost._borrowed:
            rc = _resident_cls()
            _PreflightHost._calendar_preflight = rc._calendar_preflight
            _PreflightHost._note_qfq_degraded = rc._note_qfq_degraded
            _PreflightHost._borrowed = True
        self.writer = types.SimpleNamespace(db_path=str(db_path))


class TestDaemonPreflightWiring:

    def test_default_warn_only_no_raise(self, tmp_path, caplog):
        """strict 关（缺省）⇒ 缺日历不阻断：ERROR 留痕 + degraded 计数，不抛。"""
        import duckdb
        db = tmp_path / "t.db"
        duckdb.connect(str(db)).close()  # 无表 ⇒ preflight FAIL
        host = _PreflightHost(db)
        with caplog.at_level(logging.ERROR,
                             logger="quantstudio.pipeline.daemon"):
            host._calendar_preflight()  # 不得抛
        joined = " | ".join(r.getMessage() for r in caplog.records)
        assert "preflight FAIL" in joined
        assert host._qfq_degraded_streak.get("calendar_preflight") == 1

    def test_strict_on_raises(self, tmp_path, monkeypatch):
        """strict 开 ⇒ 缺日历显式失败（负向对照：RuntimeError 拒启）。"""
        import duckdb
        db = tmp_path / "t.db"
        duckdb.connect(str(db)).close()
        monkeypatch.setenv("QS_CALENDAR_PREFLIGHT_STRICT", "1")
        host = _PreflightHost(db)
        with pytest.raises(RuntimeError, match="STRICT"):
            host._calendar_preflight()

    def test_ok_path_single_info_line(self, tmp_path, caplog):
        """日历可用 ⇒ 恰一行 INFO（preflight OK），不抛（真实今日锚定 fixture）。"""
        db = _build_calendar_db_around_today(tmp_path)
        host = _PreflightHost(db)
        with caplog.at_level(logging.INFO,
                             logger="quantstudio.pipeline.daemon"):
            host._calendar_preflight()
        infos = [r for r in caplog.records if "preflight OK" in r.getMessage()]
        assert len(infos) == 1

    def test_switch_off_disables(self, tmp_path, caplog, monkeypatch):
        """QS_CALENDAR_PREFLIGHT=0 ⇒ 整体关闭（静默早退，回退开关）。"""
        import duckdb
        db = tmp_path / "t.db"
        duckdb.connect(str(db)).close()
        monkeypatch.setenv("QS_CALENDAR_PREFLIGHT", "0")
        host = _PreflightHost(db)
        with caplog.at_level(logging.INFO,
                             logger="quantstudio.pipeline.daemon"):
            host._calendar_preflight()
        # 整体关闭 ⇒ 校验静默早退，不得产出任何 preflight 相关日志
        assert not [r for r in caplog.records
                    if "preflight" in r.getMessage()]


class TestDaemonWiringInPlace:

    def test_run_once_and_cycle_call_preflight(self):
        rc = _resident_cls()
        assert "self._calendar_preflight()" in inspect.getsource(rc.run_once)
        assert "self._calendar_preflight()" in inspect.getsource(
            rc._run_incremental_cycle)

    def test_per_date_local_first_with_fallback_preserved(self):
        """per-date 装配：本地优先在位 + tushare 现拉回落逐字保留（仅 None 才回落）。"""
        rc = _resident_cls()
        src = inspect.getsource(rc._execute_task_per_trade_date)
        assert "local_first_trade_days" in src
        assert "pro.trade_cal" in src
        assert "trade_days is None" in src
