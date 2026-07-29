"""任务2.5：XtquantFreshFetcher 下载行为 + capture() download_trace（fake xtquant，不连真实 miniQMT）。

覆盖（用注入的 fake xtquant 模块）：
- XtquantFreshFetcher.fetch_none_front：download_history_data 在 get_market_data_ex 之前被调用。
- _split_windows(start, end, cap)：daily 365 天/窗、1min 30 天/窗，区间正确切分
  （含端点、跨年、不足一窗等边界），相邻窗口连续、每窗跨度<=cap。
- 任一窗口 download_history_data 抛异常 → 抛 FreshCaptureDownloadError（不静默返回旧缓存）。
- capture() 落库的 download_trace JSON 含全部字段（download_start/download_finish/
  requested_range/window_list/window_status/daily_content_sha/minute_content_sha/metadata_sha），
  且三个 sha 为 64 位 hex。
"""

import sys
import types

import duckdb
import json
import pandas as pd
import pytest

from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema
from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
from quantstudio.pipeline.qfq_fresh_capture import (
    FreshCapture,
    FreshCaptureDownloadError,
    XtquantFreshFetcher,
    _split_windows,
)


def _fake_price_df():
    idx = pd.to_datetime(["2026-01-05 09:31:00"])
    return pd.DataFrame(
        {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5]}, index=idx
    )


class _FakeXt:
    def __init__(self):
        self.calls = []

    def connect(self):
        pass

    def download_history_data(self, code, period, start, end):
        self.calls.append(("download", period))

    def get_market_data_ex(self, stock_list, period, start_time, end_time, dividend_type):
        self.calls.append(("get", period))
        return {stock_list[0]: _fake_price_df()}


class _RaisingXt:
    def connect(self):
        pass

    def download_history_data(self, code, period, start, end):
        raise RuntimeError("simulated download failure")

    def get_market_data_ex(self, stock_list, period, start_time, end_time, dividend_type):
        raise RuntimeError("should not reach")


def _install_fake_xt(fake):
    mod = types.ModuleType("xtquant.xtdata")
    mod.connect = fake.connect
    mod.download_history_data = fake.download_history_data
    mod.get_market_data_ex = fake.get_market_data_ex
    pkg = types.ModuleType("xtquant")
    saved = {}
    for name in ("xtquant", "xtquant.xtdata"):
        if name in sys.modules:
            saved[name] = sys.modules[name]
    sys.modules["xtquant"] = pkg
    sys.modules["xtquant.xtdata"] = mod
    return saved


def _restore_fake_xt(saved):
    for name, val in saved.items():
        sys.modules[name] = val
    if "xtquant.xtdata" not in saved:
        sys.modules.pop("xtquant.xtdata", None)
    if "xtquant" not in saved:
        sys.modules.pop("xtquant", None)


@pytest.fixture
def fake_xtquant():
    fake = _FakeXt()
    saved = _install_fake_xt(fake)
    yield fake
    _restore_fake_xt(saved)


def test_download_called_before_get(fake_xtquant):
    fetcher = XtquantFreshFetcher()
    fetcher.fetch_none_front("STOCK", "600000.SH", "1d", "20260101", "20260131")
    # 同一 period 内所有 download 必须先于所有 get
    dl = [i for i, (op, p) in enumerate(fake_xtquant.calls) if op == "download" and p == "1d"]
    gt = [i for i, (op, p) in enumerate(fake_xtquant.calls) if op == "get" and p == "1d"]
    assert dl and gt
    assert max(dl) < min(gt)


def test_download_failure_raises_fresh_capture_download_error():
    fake = _RaisingXt()
    saved = _install_fake_xt(fake)
    try:
        fetcher = XtquantFreshFetcher()
        with pytest.raises(FreshCaptureDownloadError):
            fetcher.fetch_none_front("STOCK", "600000.SH", "1d", "20260101", "20260131")
    finally:
        _restore_fake_xt(saved)


def _assert_contiguous_and_capped(windows, cap):
    prev_end = None
    for (s, e) in windows:
        if prev_end is not None:
            assert pd.Timestamp(s) == pd.Timestamp(prev_end) + pd.Timedelta(days=1)
        assert (pd.Timestamp(e) - pd.Timestamp(s)).days + 1 <= cap
        prev_end = e


def test_split_windows_daily_single_window():
    assert _split_windows("20260101", "20260131", 365) == [("20260101", "20260131")]


def test_split_windows_daily_cross_year_and_capped():
    w = _split_windows("20261215", "20270115", 365)
    assert w[0][0] == "20261215"
    assert w[-1][1] == "20270115"
    _assert_contiguous_and_capped(w, 365)


def test_split_windows_minute_window():
    w = _split_windows("20260101", "20260131", 30)
    _assert_contiguous_and_capped(w, 30)


def test_capture_writes_download_trace(fake_xtquant):
    dconn = duckdb.connect(":memory:")
    init_duckdb_schema(dconn)
    cfg = QFQOrchestratorConfig()
    fc = FreshCapture(cfg)
    daily_range_ms = (1_767_283_200_000, 1_767_888_000_000)
    minute_range_ms = (1_767_283_200_000, 1_767_289_000_000)
    rec, _daily, _minute = fc.capture(
        dconn,
        asset_type="STOCK",
        code="600000",
        run_id="r1",
        daily_range_ms=daily_range_ms,
        minute_range_ms=minute_range_ms,
        fetcher=XtquantFreshFetcher(),
        source="xtquant",
    )
    row = dconn.execute(
        "SELECT download_trace FROM qfq_fresh_capture WHERE capture_id=?",
        [rec.capture_id],
    ).fetchone()
    assert row is not None
    dt = json.loads(row[0])
    assert set(dt.keys()) >= {
        "download_start",
        "download_finish",
        "requested_range",
        "window_list",
        "window_status",
        "daily_content_sha",
        "minute_content_sha",
        "metadata_sha",
    }
    for key in ("daily_content_sha", "minute_content_sha", "metadata_sha"):
        assert isinstance(dt[key], str) and len(dt[key]) == 64
    assert dt["download_start"] is not None
    assert dt["download_finish"] is not None
