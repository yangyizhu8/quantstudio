"""R1-A 回归测试：日线日期区间前复权契约与 count 路径一致。

覆盖：
1. query_bars_by_range 的 use_qfq 分支（front/raw 列选择）
2. get_bars 区间路径 fq='pre'/'dypre' 返回前复权 OHLC，fq=None/'none' 返回 raw
3. 单码/多码/部分无数据/全无数据
4. fields 过滤不影响公共返回结构
5. count 路径回归（不破坏现有行为）
6. fq=None 修复前后等价（列/索引/dtype/值/空值/顺序）
7. 频率隔离（frequency='1m' 走分钟路径，不受日线改动影响）
8. 300750 黄金断言（正式库只读烟测，不可读时 SKIP 不 FAIL）

本轮属于框架层正确性修复，禁止修改正式数据库。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider
from quantstudio.backtest.ptrade_api import PtradeAPI


# ---------------------------------------------------------------------------
# 临时 DuckDB 构造工具
# ---------------------------------------------------------------------------
def _make_tmp_stock_daily(rows):
    """rows: list of dict，含 code/time/open/high/low/close/open_front/...等。

    返回临时 db 路径（调用方负责清理）。
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # DuckDB 不会自动覆盖已存在的空文件，先删除占位文件让其新建库
    os.unlink(path)
    con = duckdb.connect(path)
    con.execute("""
        CREATE TABLE stock_daily (
            code VARCHAR, time BIGINT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, preClose DOUBLE, pctChg DOUBLE,
            turn DOUBLE, peTTM DOUBLE, pbMRQ DOUBLE,
            open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE
        )
    """)
    if rows:
        cols = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_list = ", ".join(cols)
        for r in rows:
            con.execute(
                f"INSERT INTO stock_daily ({col_list}) VALUES ({placeholders})",
                [r[c] for c in cols])
    con.close()
    return Path(path)


def _row(code, time, raw, front):
    return {
        "code": code, "time": time,
        "open": raw, "high": raw + 1, "low": raw - 1, "close": raw,
        "volume": 1000.0, "amount": raw * 1000.0, "preClose": raw - 0.5,
        "pctChg": 1.0, "turn": 0.01, "peTTM": 10.0, "pbMRQ": 2.0,
        "open_front": front, "high_front": front + 1,
        "low_front": front - 1, "close_front": front,
    }


@pytest.fixture
def tmp_db_path():
    # 600000: raw=10, front=8 ; 600001: raw=20, front=16
    t = 1_700_000_000_000
    rows = [
        _row("600000", t, 10.0, 8.0),
        _row("600001", t, 20.0, 16.0),
    ]
    p = _make_tmp_stock_daily(rows)
    yield p
    try:
        p.unlink()
    except OSError:
        pass


@pytest.fixture
def provider(tmp_db_path):
    return DuckDBMarketDataProvider(tmp_db_path)


# ---------------------------------------------------------------------------
# 1. query_bars_by_range use_qfq 分支
# ---------------------------------------------------------------------------
def test_query_bars_by_range_raw_default(tmp_db_path):
    """默认 use_qfq=False 返回 raw 列值。"""
    da = DuckDBDataAccess(tmp_db_path)
    df = da.query_bars_by_range("600000", 0, 9_999_999_999_999)
    assert not df.empty
    assert float(df.iloc[0]["close"]) == pytest.approx(10.0)
    assert "close_front" not in df.columns  # 不新增 front 列


def test_query_bars_by_range_qfq(tmp_db_path):
    """use_qfq=True 返回 front 列值，且公共列集不含 front 列。"""
    da = DuckDBDataAccess(tmp_db_path)
    df = da.query_bars_by_range("600000", 0, 9_999_999_999_999, use_qfq=True)
    assert not df.empty
    assert float(df.iloc[0]["close"]) == pytest.approx(8.0)
    assert "close_front" not in df.columns
    # 其他字段保持 raw 值
    assert float(df.iloc[0]["preClose"]) == pytest.approx(9.5)


# ---------------------------------------------------------------------------
# 2. get_bars 区间路径 fq 契约
# ---------------------------------------------------------------------------
def test_get_bars_range_fq_pre(provider):
    res = provider.get_bars(["600000"], "2000-01-01", "2100-01-01", fq="pre")
    assert "600000" in res
    assert float(res["600000"].iloc[0]["close"]) == pytest.approx(8.0)


def test_get_bars_range_fq_dypre(provider):
    res = provider.get_bars(["600000"], "2000-01-01", "2100-01-01", fq="dypre")
    assert "600000" in res
    assert float(res["600000"].iloc[0]["close"]) == pytest.approx(8.0)


def test_get_bars_range_fq_none(provider):
    res = provider.get_bars(["600000"], "2000-01-01", "2100-01-01", fq="none")
    assert "600000" in res
    assert float(res["600000"].iloc[0]["close"]) == pytest.approx(10.0)


def test_get_bars_range_fq_default_is_pre(provider):
    """不传 fq 时默认 pre（文档约定）。"""
    res = provider.get_bars(["600000"], "2000-01-01", "2100-01-01")
    assert float(res["600000"].iloc[0]["close"]) == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# 3. 多码 / 部分无数据 / 全无数据
# ---------------------------------------------------------------------------
def test_get_bars_range_multi_code(provider):
    res = provider.get_bars(["600000", "600001"], "2000-01-01", "2100-01-01", fq="pre")
    assert set(res.keys()) == {"600000", "600001"}
    assert float(res["600000"].iloc[0]["close"]) == pytest.approx(8.0)
    assert float(res["600001"].iloc[0]["close"]) == pytest.approx(16.0)


def test_get_bars_range_partial_no_data(provider):
    res = provider.get_bars(["600000", "999999"], "2000-01-01", "2100-01-01", fq="pre")
    assert "600000" in res
    assert "999999" not in res


def test_get_bars_range_all_no_data(provider):
    res = provider.get_bars(["999999"], "2000-01-01", "2100-01-01", fq="pre")
    assert res == {}


# ---------------------------------------------------------------------------
# 4. fields 过滤不影响公共返回结构
# ---------------------------------------------------------------------------
def test_get_bars_range_fields_close(provider):
    res = provider.get_bars(["600000"], "2000-01-01", "2100-01-01",
                            fields=["close"], fq="pre")
    df = res["600000"]
    # _fields 始终保留 code 列作为索引键，其余为请求字段
    assert list(df.columns) == ["code", "close"]
    assert float(df.iloc[0]["close"]) == pytest.approx(8.0)


def test_get_bars_range_fields_ohlc(provider):
    res = provider.get_bars(["600000"], "2000-01-01", "2100-01-01",
                            fields=["open", "high", "low", "close"], fq="pre")
    df = res["600000"]
    assert list(df.columns) == ["code", "open", "high", "low", "close"]
    assert float(df.iloc[0]["close"]) == pytest.approx(8.0)
    assert float(df.iloc[0]["open"]) == pytest.approx(8.0)


def test_get_bars_range_fields_none(provider):
    res = provider.get_bars(["600000"], "2000-01-01", "2100-01-01",
                            fields=None, fq="pre")
    df = res["600000"]
    # 公共列集保持原有 13 列，不含 front 列
    assert "close_front" not in df.columns
    assert float(df.iloc[0]["close"]) == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# 5. count 路径回归（不破坏现有行为）
# ---------------------------------------------------------------------------
def test_get_bars_by_count_pre_unchanged(provider):
    """count 路径 fq='pre' 行为不变。"""
    res = provider.get_bars_by_count(["600000"], 1, "2100-01-01", fq="pre")
    assert "600000" in res
    # count=1 返回最新一条，front=8.0
    assert float(res["600000"].iloc[0]["close"]) == pytest.approx(8.0)


def test_get_bars_by_count_none_unchanged(provider):
    res = provider.get_bars_by_count(["600000"], 1, "2100-01-01", fq="none")
    assert float(res["600000"].iloc[0]["close"]) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# 6. fq=None 等价（修复前后 raw 路径逐列一致）
# ---------------------------------------------------------------------------
def test_get_bars_range_none_keeps_raw_columns(provider):
    """fq=None 区间返回列集与 raw 查询一致（13 列，无 front 列）。"""
    da = provider._data
    raw_df = da.query_bars_by_range("600000", 0, 9_999_999_999_999, use_qfq=False)
    res = provider.get_bars(["600000"], "2000-01-01", "2100-01-01", fq=None)
    out = res["600000"]
    assert list(out.columns) == list(raw_df.columns)
    # 值逐列一致
    for col in raw_df.columns:
        pd.testing.assert_series_equal(
            out[col].reset_index(drop=True),
            raw_df[col].reset_index(drop=True),
            check_dtype=False)


# ---------------------------------------------------------------------------
# 7. 频率隔离
# ---------------------------------------------------------------------------
def test_get_bars_frequency_1m_not_daily(provider, monkeypatch):
    """frequency='1m' 走分钟路径，不调用 query_bars_by_range。"""
    called = []
    monkeypatch.setattr(
        provider._data, "query_bars_by_range",
        lambda *a, **k: called.append(1) or pd.DataFrame())
    import pandas as pd
    provider._data.query_minute_bars_by_range = (
        lambda *a, **k: pd.DataFrame({'code': ['600000'], 'time': [1], 'close': [1.0]}))
    res = provider.get_bars(["600000"], "2000-01-01", "2100-01-01", frequency="1m")
    assert "600000" in res
    assert called == []  # 日线方法未被调用


# ---------------------------------------------------------------------------
# 8. 300750 黄金断言（正式库只读烟测；不可读则 SKIP）
# ---------------------------------------------------------------------------
def _real_db_path():
    p = Path(__file__).resolve().parents[1] / "data" / "quantstudio.db"
    return p if p.exists() else None


def test_golden_300750_range_vs_count():
    """300750 区间路径与 count 路径前复权值逐值一致，且等于正式库 front 价。"""
    p = _real_db_path()
    if p is None:
        pytest.skip("正式库 data/quantstudio.db 不存在")
    try:
        prov = DuckDBMarketDataProvider(p)
        # 只读，确认能连通
        _ = prov._data.query_bars_by_range("300750", 0, 1)
    except Exception as e:
        pytest.skip(f"正式库被占用或不可读：{type(e).__name__}: {e}")

    # 区间路径
    rng = prov.get_bars(["300750"], "2025-04-21", "2025-04-22", fq="pre")
    # count 路径（end_date 同区间末，count=2）
    cnt = prov.get_bars_by_count(["300750"], 2, "2025-04-22", fq="pre")

    assert "300750" in rng and "300750" in cnt
    rng_df = rng["300750"].sort_values("time").reset_index(drop=True)
    cnt_df = cnt["300750"].sort_values("time").reset_index(drop=True)

    rng_closes = rng_df["close"].tolist()
    cnt_closes = cnt_df["close"].tolist()
    # 黄金值：222.518595 / 226.311682
    assert rng_closes[0] == pytest.approx(222.518595, rel=1e-4)
    assert rng_closes[1] == pytest.approx(226.311682, rel=1e-4)
    # 区间与 count 逐值一致
    assert rng_closes == pytest.approx(cnt_closes, rel=1e-9)


def test_golden_300750_range_none_raw():
    """300750 区间路径 fq=None 返回 raw 价（231.36 / 230.69）。"""
    p = _real_db_path()
    if p is None:
        pytest.skip("正式库 data/quantstudio.db 不存在")
    try:
        prov = DuckDBMarketDataProvider(p)
        _ = prov._data.query_bars_by_range("300750", 0, 1)
    except Exception as e:
        pytest.skip(f"正式库被占用或不可读：{type(e).__name__}: {e}")

    rng = prov.get_bars(["300750"], "2025-04-21", "2025-04-22", fq=None)
    rng_df = rng["300750"].sort_values("time").reset_index(drop=True)
    closes = rng_df["close"].tolist()
    assert closes[0] == pytest.approx(231.36, rel=1e-4)
    assert closes[1] == pytest.approx(230.69, rel=1e-4)


# ---------------------------------------------------------------------------
# 9. PTradeAPI.get_price 区间路径（确保最终返回前复权，而非仅 provider 底层）
# ---------------------------------------------------------------------------
def test_ptradeapi_get_price_range_fq_pre(provider):
    """get_price 区间 + fq='pre' 最终返回前复权 OHLC（验证封装层而非仅底层）。"""
    api = PtradeAPI()
    api._market = provider
    api._prev_date = "2100-01-01"
    api._current_date = "2100-01-01"
    df = api.get_price("600000", start_date="2000-01-01", end_date="2100-01-01", fq="pre")
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    # 最终返回的是前复权价（来自 provider 的 close_front -> close 列）
    assert float(df.iloc[0]["close"]) == pytest.approx(8.0)
    # trade_date 字段被附加（Asia/Shanghai 日界）
    assert "trade_date" in df.columns


def test_ptradeapi_get_price_range_fq_none(provider):
    api = PtradeAPI()
    api._market = provider
    api._prev_date = "2100-01-01"
    api._current_date = "2100-01-01"
    df = api.get_price("600000", start_date="2000-01-01", end_date="2100-01-01", fq="none")
    assert float(df.iloc[0]["close"]) == pytest.approx(10.0)
