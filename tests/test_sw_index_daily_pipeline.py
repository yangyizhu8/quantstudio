"""F5: 申万行业指数日线通用管线测试（任务书 §7.7）

契约：
- 普通指数 → tushare index_daily 接口；申万指数（SW2021 分类宇宙 / .SI 后缀）
  → tushare sw_daily 正式接口；输出同一 canonical schema 写入 index_daily；
- 单位：sw_daily vol=万股 / amount=万元 → adapter 换算为 index_daily 接口单位
  （手/千元），aligner 统一映射后为 股/元（与现有 index_daily 一致）；
- 行业指数范围来自 SW2021 分类（probe），允许显式代码列表，不写策略白名单；
- 源端字段漂移 → fail-closed；空数据代码跳过（不伪造行）。
"""
from __future__ import annotations

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from quantstudio.pipeline.sources.tushare_adapter import TushareAdapter
from quantstudio.pipeline.aligner import FieldAligner


def _make_adapter():
    adapter = object.__new__(TushareAdapter)
    adapter.token = "test-token"

    class _NoopLimiter:
        def acquire(self):
            return None

    adapter.rate_limiter = _NoopLimiter()
    adapter._base_rate_limit = {"calls_per_min": 60, "wait_on_429": False}
    return adapter


_CLASSIFY_L1 = pd.DataFrame([
    {"index_code": "801010.SI", "industry_name": "农林牧渔", "level": "L1",
     "industry_code": "110000", "is_pub": 1, "parent_code": "0", "src": "SW2021"},
    {"index_code": "801020.SI", "industry_name": "基础化工", "level": "L1",
     "industry_code": "220000", "is_pub": 1, "parent_code": "0", "src": "SW2021"},
])

_SW_DAILY = pd.DataFrame([
    {"ts_code": "801010.SI", "trade_date": "20260723", "name": "农林牧渔",
     "open": 2339.77, "low": 2328.25, "high": 2384.79, "close": 2379.45,
     "change": 24.57, "pct_change": 1.04, "vol": 173375.0, "amount": 1404515.0,
     "pe": 38.5, "pb": 2.12, "float_mv": 1.0, "total_mv": 2.0},
    {"ts_code": "801010.SI", "trade_date": "20260724", "name": "农林牧渔",
     "open": 2368.43, "low": 2312.71, "high": 2377.55, "close": 2312.71,
     "change": -66.74, "pct_change": -2.80, "vol": 176883.0, "amount": 1315549.0,
     "pe": 37.83, "pb": 2.08, "float_mv": 1.0, "total_mv": 2.0},
])

_CSI_DAILY = pd.DataFrame([
    {"ts_code": "000300.SH", "trade_date": "20260724", "open": 4728.0,
     "high": 4750.0, "low": 4640.0, "close": 4649.19, "pre_close": 4728.0,
     "change": -78.81, "pct_chg": -1.6668, "vol": 221261212.0, "amount": 626378644.37},
])


def _mock_pro(monkeypatch, sw_daily=None, csi_daily=None, classify=None,
              sw_fail=False, classify_fail=False):
    import tushare as ts

    calls = {"index_daily": [], "sw_daily": []}

    class FakePro:
        def index_classify(self, level=None, src=None):
            if classify_fail:
                raise RuntimeError("classification probe failed")
            return classify if classify is not None else _CLASSIFY_L1

        def index_daily(self, ts_code=None, start_date=None, end_date=None):
            calls["index_daily"].append(ts_code)
            return csi_daily if csi_daily is not None else _CSI_DAILY

        def sw_daily(self, ts_code=None, start_date=None, end_date=None):
            calls["sw_daily"].append(ts_code)
            if sw_fail:
                raise RuntimeError("sw_daily source failure")
            return sw_daily if sw_daily is not None else _SW_DAILY

    monkeypatch.setattr(ts, "pro_api", lambda *a, **k: FakePro())
    return calls


def test_adapter_routes_sw_codes_to_sw_daily(monkeypatch):
    """混合代码：普通指数走 index_daily，申万 801xxx 走 sw_daily。"""
    calls = _mock_pro(monkeypatch)
    adapter = _make_adapter()
    df, meta = adapter._fetch_index_daily("2026-07-01", "2026-07-24",
                                          ["000300.SH", "801010"])
    assert calls["index_daily"] == ["000300.SH"]
    assert calls["sw_daily"] == ["801010.SI"]
    assert meta["table"] == "index_daily"


def test_adapter_si_suffix_always_sw(monkeypatch):
    calls = _mock_pro(monkeypatch)
    adapter = _make_adapter()
    adapter._fetch_index_daily("2026-07-01", "2026-07-24", ["801010.SI"])
    assert calls["sw_daily"] == ["801010.SI"]
    assert calls["index_daily"] == []


def test_adapter_sw_output_canonical_units(monkeypatch):
    """sw_daily（万股/万元）→ adapter 输出 index_daily 接口单位（手/千元）。"""
    _mock_pro(monkeypatch)
    adapter = _make_adapter()
    df, meta = adapter._fetch_index_daily("2026-07-01", "2026-07-24", ["801010"])
    sw_rows = df[df["ts_code"] == "801010.SI"]
    assert len(sw_rows) == 2
    first = sw_rows.iloc[0]
    assert first["vol"] == 173375.0 * 100        # 万股 → 手
    assert first["amount"] == 1404515.0 * 10     # 万元 → 千元
    assert "pct_chg" in sw_rows.columns          # pct_change 已改名
    assert set(sw_rows.columns) == {"ts_code", "trade_date", "open", "high",
                                    "low", "close", "pct_chg", "vol", "amount"}


def test_adapter_universe_from_classification_probe(monkeypatch):
    """codes=None：CSI 核心 + SW2021 L1 宇宙（probe 自分类接口）。"""
    calls = _mock_pro(monkeypatch)
    adapter = _make_adapter()
    df, meta = adapter._fetch_index_daily("2026-07-01", "2026-07-24", None)
    assert set(calls["sw_daily"]) == {"801010.SI", "801020.SI"}
    assert "000300.SH" in calls["index_daily"]


def test_adapter_sw_field_drift_fail_closed(monkeypatch):
    """sw_daily 字段漂移（缺 pct_change）→ fail-closed 抛错，不写可疑数据。"""
    drifted = _SW_DAILY.drop(columns=["pct_change"])
    _mock_pro(monkeypatch, sw_daily=drifted)
    adapter = _make_adapter()
    with pytest.raises(RuntimeError, match="sw_daily"):
        adapter._fetch_index_daily("2026-07-01", "2026-07-24", ["801010"])


def test_adapter_classify_probe_failure_fail_closed_for_sw(monkeypatch):
    """分类 probe 失败且含 SW 代码 → fail-closed 抛错（不误路由接口）。"""
    _mock_pro(monkeypatch, classify_fail=True)
    adapter = _make_adapter()
    with pytest.raises(RuntimeError, match="index_classify"):
        adapter._fetch_index_daily("2026-07-01", "2026-07-24", ["801010"])


def test_adapter_sw_empty_data_code_skipped(monkeypatch):
    """源端空数据的 SW 代码跳过（不伪造行），其他代码不受影响。"""
    _mock_pro(monkeypatch, sw_daily=pd.DataFrame())
    adapter = _make_adapter()
    df, meta = adapter._fetch_index_daily("2026-07-01", "2026-07-24",
                                          ["000300.SH", "801010"])
    assert (df["ts_code"] == "000300.SH").any()
    assert not (df["ts_code"] == "801010.SI").any()


def test_aligner_sw_rows_to_canonical_index_daily(monkeypatch):
    """端到端：adapter SW 输出经 aligner → canonical index_daily（裸码/ms/股/元）。"""
    _mock_pro(monkeypatch)
    adapter = _make_adapter()
    df, _ = adapter._fetch_index_daily("2026-07-01", "2026-07-24", ["801010"])
    aligner = FieldAligner.from_config("config/alignment_rules.json")
    std, meta = aligner.align(df, "index_daily", "tushare")
    row = std.sort_values("time").iloc[0]
    assert row["code"] == "801010"
    expected_ms = int(pd.Timestamp("2026-07-23", tz="Asia/Shanghai").timestamp() * 1000)
    assert int(row["time"]) == expected_ms
    assert row["volume"] == 173375.0 * 10000      # 万股 → 股（净 ×10000）
    assert row["amount"] == 1404515.0 * 10000     # 万元 → 元（净 ×10000）
    assert abs(row["pctChg"] - 1.04) < 1e-9


def test_writer_index_daily_sw_upsert(tmp_path):
    """801xxx 写入统一 index_daily：幂等 upsert，重放不重复。"""
    from quantstudio.pipeline.writers import DuckDBWriter

    writer = DuckDBWriter({"type": "duckdb", "path": str(tmp_path / "w.duckdb")})
    ms = int(pd.Timestamp("2026-07-24", tz="Asia/Shanghai").timestamp() * 1000)
    df = pd.DataFrame([{
        "code": "801010", "time": ms, "open": 2368.43, "high": 2377.55,
        "low": 2312.71, "close": 2312.71, "pctChg": -2.8,
        "volume": 1768830000.0, "amount": 13155490000.0, "data_source": "tushare"}])
    writer.write(df, "index_daily", "b1")
    writer.write(df, "index_daily", "b1-replay")
    rows = writer.execute_read("SELECT COUNT(*) FROM index_daily WHERE code='801010'")
    assert rows[0][0] == 1


def test_sw_index_daily_coverage_report(tmp_path):
    """覆盖报告：对照 industry_classification SW2021 L1 全集标记缺失行业指数。"""
    from quantstudio.pipeline.writers import DuckDBWriter
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess

    db = tmp_path / "cov.duckdb"
    writer = DuckDBWriter({"type": "duckdb", "path": str(db)})
    ms = int(pd.Timestamp("2026-07-24", tz="Asia/Shanghai").timestamp() * 1000)
    writer.write(pd.DataFrame([
        {"classification_system": "SW", "classification_version": "SW2021",
         "industry_code": "801010", "industry_name": "农林牧渔",
         "industry_level": "L1", "parent_industry_code": None,
         "effective_from": 0, "effective_to": None,
         "update_time": "2026-01-01", "data_source": "tushare"},
        {"classification_system": "SW", "classification_version": "SW2021",
         "industry_code": "801020", "industry_name": "基础化工",
         "industry_level": "L1", "parent_industry_code": None,
         "effective_from": 0, "effective_to": None,
         "update_time": "2026-01-01", "data_source": "tushare"},
    ]), "industry_classification", "b1")
    writer.write(pd.DataFrame([{
        "code": "801010", "time": ms, "open": 1.0, "high": 2.0, "low": 0.5,
        "close": 1.5, "pctChg": 1.0, "volume": 100.0, "amount": 1000.0,
        "data_source": "tushare"}]), "index_daily", "b1")
    cov = DuckDBDataAccess(db).query_sw_index_daily_coverage()
    by_code = cov.set_index("industry_code")
    assert bool(by_code.loc["801010", "has_daily"]) is True
    assert bool(by_code.loc["801020", "has_daily"]) is False
    assert by_code.loc["801010", "max_time"] == ms
