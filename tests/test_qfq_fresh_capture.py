"""qfq_fresh_capture 单元测试（确定性 FakeFreshFetcher，不依赖 xtquant / 不写正式库）。

测试使用临时内存 DuckDB（init_duckdb_schema 建表），全部为确定性构造数据。
覆盖：列完整性 / hash 稳定性 / metadata 64-hex 合法性 / 落库可读回 / time 为 int epoch-ms
/ 空数据不崩。
"""
from __future__ import annotations

import re
import tempfile
import os

import pandas as pd
import duckdb
import pytest

from quantstudio.pipeline.qfq_fresh_capture import (
    FreshCapture,
    FakeFreshFetcher,
    XtquantFreshFetcher,
    raw_to_xtquant,
    _to_fresh_frame,
)
from quantstudio.pipeline.qfq_orchestrator_types import (
    QFQOrchestratorConfig,
    FreshCaptureRecord,
)
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema


# ---------------------------------------------------------------------------
# 构造确定性测试数据
# ---------------------------------------------------------------------------
def _make_ohlc(index_dates, prices):
    """构造以 DatetimeIndex 为 index、含 open/high/low/close 的 DataFrame。"""
    idx = pd.to_datetime(index_dates)
    rows = []
    for i, d in enumerate(idx):
        o, h, l, c = prices[i]
        rows.append({"open": o, "high": h, "low": l, "close": c})
    return pd.DataFrame(rows, index=idx)


DAILY_DATES = ["2026-07-08", "2026-07-09", "2026-07-10"]
MINUTE_DATETIMES = [
    "2026-07-10 09:30:00",
    "2026-07-10 09:31:00",
    "2026-07-10 09:32:00",
]

# none（原始价）与 front（前复权价，简单 ×0.9 演示）
NONE_DAILY = _make_ohlc(DAILY_DATES, [(10, 11, 9, 10), (10.5, 11.5, 10, 11), (11, 12, 10.5, 11.5)])
FRONT_DAILY = _make_ohlc(DAILY_DATES, [(9, 9.9, 8.1, 9), (9.45, 10.35, 9, 9.9), (9.9, 10.8, 9.45, 10.35)])

NONE_MINUTE = _make_ohlc(MINUTE_DATETIMES, [(10, 10.2, 9.9, 10.1), (10.1, 10.3, 10.0, 10.2), (10.2, 10.4, 10.1, 10.3)])
FRONT_MINUTE = _make_ohlc(MINUTE_DATETIMES, [(9, 9.18, 8.91, 9.09), (9.09, 9.27, 9.0, 9.18), (9.18, 9.36, 9.09, 9.27)])


def _make_fetcher() -> FakeFreshFetcher:
    return FakeFreshFetcher({
        # 用 (xt_code, period) 粒度覆盖日线/分钟线
        ("600000.SH", "1d"): (NONE_DAILY, FRONT_DAILY),
        ("600000.SH", "1m"): (NONE_MINUTE, FRONT_MINUTE),
    })


def _new_conn():
    """临时内存 DuckDB + 建表；返回连接（测试内 commit 由 DuckDB 自动事务处理）。"""
    conn = duckdb.connect(":memory:")
    init_duckdb_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def test_raw_to_xtquant():
    assert raw_to_xtquant("600000") == "600000.SH"
    assert raw_to_xtquant("510050") == "510050.SH"
    assert raw_to_xtquant("159919") == "159919.SZ"
    assert raw_to_xtquant("000001") == "000001.SZ"
    assert raw_to_xtquant("830799") == "830799.BJ"


def test_capture_columns_complete():
    conn = _new_conn()
    fc = FreshCapture(QFQOrchestratorConfig(price_source="xtquant"))
    daily_range = (1_789_785_600_000, 1_789_939_200_000)   # 2026-07-08 ~ 07-10
    minute_range = (1_789_862_400_000, 1_789_862_400_000)
    rec, fresh_daily, fresh_minute = fc.capture(
        conn,
        asset_type="STOCK", code="600000", run_id="run1",
        daily_range_ms=daily_range, minute_range_ms=minute_range,
        fetcher=_make_fetcher(),
    )
    conn.close()

    # 日线列
    for c in ["code", "time", "open", "high", "low", "close",
              "open_front", "high_front", "low_front", "close_front"]:
        assert c in fresh_daily.columns, f"日线缺列 {c}"
    # 分钟额外含 freq
    for c in ["code", "time", "open", "high", "low", "close",
              "open_front", "high_front", "low_front", "close_front", "freq"]:
        assert c in fresh_minute.columns, f"分钟缺列 {c}"
    assert (fresh_minute["freq"] == "1min").all()

    # 行数：3 根 bar
    assert len(fresh_daily) == 3
    assert len(fresh_minute) == 3


def test_hash_stability():
    """相同输入产出相同 daily_sha256/minute_sha256/metadata_sha256。"""
    daily_range = (1_789_785_600_000, 1_789_939_200_000)
    minute_range = (1_789_862_400_000, 1_789_862_400_000)
    kw = dict(asset_type="STOCK", code="600000", run_id="run1",
              daily_range_ms=daily_range, minute_range_ms=minute_range)

    recs = []
    for _ in range(2):
        conn = _new_conn()
        fc = FreshCapture(QFQOrchestratorConfig(price_source="xtquant"))
        rec, _, _ = fc.capture(conn, fetcher=_make_fetcher(), **kw)
        conn.close()
        recs.append(rec)

    assert recs[0].daily_sha256 == recs[1].daily_sha256
    assert recs[0].minute_sha256 == recs[1].minute_sha256
    assert recs[0].metadata_sha256 == recs[1].metadata_sha256


def test_metadata_sha256_valid_64hex():
    conn = _new_conn()
    fc = FreshCapture(QFQOrchestratorConfig(price_source="xtquant"))
    daily_range = (1_789_785_600_000, 1_789_939_200_000)
    minute_range = (1_789_862_400_000, 1_789_862_400_000)
    rec, _, _ = fc.capture(
        conn,
        asset_type="STOCK", code="600000", run_id="run1",
        daily_range_ms=daily_range, minute_range_ms=minute_range,
        fetcher=_make_fetcher(),
    )
    conn.close()
    assert re.fullmatch(r"[0-9a-f]{64}", rec.metadata_sha256) is not None
    assert re.fullmatch(r"[0-9a-f]{64}", rec.daily_sha256) is not None
    assert re.fullmatch(r"[0-9a-f]{64}", rec.minute_sha256) is not None


def test_capture_persisted_and_readback():
    conn = _new_conn()
    fc = FreshCapture(QFQOrchestratorConfig(price_source="xtquant"))
    daily_range = (1_789_785_600_000, 1_789_939_200_000)
    minute_range = (1_789_862_400_000, 1_789_862_400_000)
    rec, _, _ = fc.capture(
        conn,
        asset_type="STOCK", code="600000", run_id="run1",
        daily_range_ms=daily_range, minute_range_ms=minute_range,
        fetcher=_make_fetcher(),
    )
    # get_capture 可读回
    got = fc.get_capture(conn, rec.capture_id)
    assert got is not None
    assert got.capture_id == rec.capture_id
    assert got.code == "600000"
    assert got.asset_type == "STOCK"
    assert got.status == "captured"
    assert got.daily_row_count == 3
    assert got.minute_row_count == 3

    # mark_applied
    fc.mark_applied(conn, rec.capture_id)
    got2 = fc.get_capture(conn, rec.capture_id)
    assert got2.status == "applied"
    conn.close()


def test_time_is_int_epoch_ms():
    conn = _new_conn()
    fc = FreshCapture(QFQOrchestratorConfig(price_source="xtquant"))
    daily_range = (1_789_785_600_000, 1_789_939_200_000)
    minute_range = (1_789_862_400_000, 1_789_862_400_000)
    rec, fresh_daily, fresh_minute = fc.capture(
        conn,
        asset_type="STOCK", code="600000", run_id="run1",
        daily_range_ms=daily_range, minute_range_ms=minute_range,
        fetcher=_make_fetcher(),
    )
    conn.close()
    # time 必须为 int（epoch-ms BIGINT），非 datetime
    assert fresh_daily["time"].dtype.kind in ("i", "u")  # 整数
    assert fresh_minute["time"].dtype.kind in ("i", "u")
    assert isinstance(fresh_daily["time"].iloc[0], (int,)) or \
        isinstance(fresh_daily["time"].iloc[0], __import__("numpy").integer)
    # 日线首根 = 2026-07-08 00:00:00 +08（与 to_ms_timestamp 口径一致）
    from quantstudio.pipeline.aligner import to_ms_timestamp
    import pandas as pd
    assert fresh_daily["time"].iloc[0] == to_ms_timestamp(pd.Timestamp("2026-07-08"))
    # 分钟首根 = 2026-07-10 09:30:00 +08
    assert fresh_minute["time"].iloc[0] == to_ms_timestamp(pd.Timestamp("2026-07-10 09:30:00"))


def test_empty_data_no_crash():
    """空数据（FakeFetcher 返回空 df）不崩，row_count=0，hash 仍可计算。"""
    empty = pd.DataFrame(columns=["open", "high", "low", "close"])
    fetcher = FakeFreshFetcher({
        ("600000.SH", "1d"): (empty, empty),
        ("600000.SH", "1m"): (empty, empty),
    })
    conn = _new_conn()
    fc = FreshCapture(QFQOrchestratorConfig(price_source="xtquant"))
    daily_range = (1_789_785_600_000, 1_789_939_200_000)
    minute_range = (1_789_862_400_000, 1_789_862_400_000)
    rec, fresh_daily, fresh_minute = fc.capture(
        conn,
        asset_type="STOCK", code="600000", run_id="run1",
        daily_range_ms=daily_range, minute_range_ms=minute_range,
        fetcher=fetcher,
    )
    conn.close()
    assert rec.daily_row_count == 0
    assert rec.minute_row_count == 0
    assert len(fresh_daily) == 0
    assert len(fresh_minute) == 0
    # hash 仍可计算（空 csv 的 sha256）
    assert re.fullmatch(r"[0-9a-f]{64}", rec.daily_sha256) is not None
    assert re.fullmatch(r"[0-9a-f]{64}", rec.minute_sha256) is not None
    assert re.fullmatch(r"[0-9a-f]{64}", rec.metadata_sha256) is not None
    assert rec.daily_min_time is None
    assert rec.minute_max_time is None


def test_capture_id_deterministic():
    """同券同轮 capture_id 稳定（FreshCaptureRecord 由 capture_id_of 推导）。"""
    from quantstudio.pipeline.qfq_orchestrator_types import capture_id_of
    a = capture_id_of("STOCK", "600000", "run1")
    b = capture_id_of("STOCK", "600000", "run1")
    c = capture_id_of("STOCK", "600000", "run2")
    assert a == b
    assert a != c


def test_xtquant_fetcher_lazy_import():
    """模块加载不触发网络连接；构造 XtquantFreshFetcher 不应尝试 import xtquant 连接。"""
    # 仅构造（不调用 fetch_none_front），不抛异常、不触发网络
    f = XtquantFreshFetcher()
    assert f is not None
    # 抽象基类契约
    assert issubclass(XtquantFreshFetcher, __import__(
        "quantstudio.pipeline.qfq_fresh_capture", fromlist=["FreshFetcher"]).FreshFetcher)
