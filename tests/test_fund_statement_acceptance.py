# -*- coding: utf-8 -*-
"""D4 三大报表接线验收：同比双期 + PIT 双边界 + 列名契约 + 缺列告警（ZCode 验收补强）。"""
import logging
import pytest
import pandas as pd

from quantstudio.backtest.ptrade_api import _api, Context, Portfolio
from quantstudio.backtest.backtest_engine import BacktestEngine

DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"


@pytest.fixture(scope="module")
def api():
    eng = BacktestEngine(db_path=DB, strategy={}, start="2026-01-05", end="2026-01-15",
                         engine_profile="daily-bar-v1", capital=100_000)
    _api.reset_session()
    cols = ["code", "open", "high", "low", "close", "volume", "preClose", "pctChg"]
    curr = prev = pd.DataFrame([["600519.SS", 1, 1, 1, 1, 0, 1, 0.0]], columns=cols)
    _api.attach_day(eng, curr, prev, "2026-01-05", "2026-01-02", {})
    yield _api


def test_income_yoy_two_periods(api):
    """同比双期可取：income 2024/2025 两报告期行（非仅最新——F-Score D3 同比依赖）。"""
    df = api.get_fundamentals("600519.SS", table="income_statement",
                              fields=["end_date", "np_parent_company_owners"],
                              date="2026-01-05", start_year=2024, end_year=2025,
                              is_dataframe=True)
    assert len(df) >= 2, f"应返回多报告期行，got {len(df)}"
    yrs = sorted({pd.Timestamp(e, unit="ms", tz="Asia/Shanghai").year for e in df["end_date"]})
    assert 2024 in yrs and 2025 in yrs, f"同比两期应含 2024+2025，got {yrs}"


def test_pit_boundary_visible_same_day(api):
    """PIT 边界1：ann_date == 查询日（披露当天 23:59:59）应可见。"""
    df = api.get_fundamentals("600519.SS", table="income_statement",
                              fields=["end_date", "np_parent_company_owners"],
                              date="2026-01-05", is_dataframe=True)
    assert len(df) > 0


def test_pit_boundary_no_future(api):
    """PIT 边界2：ann_date > 查询日不可见（未来函数禁）。"""
    df = api.get_fundamentals("600519.SS", table="income_statement",
                              fields=["end_date"],
                              date="2019-01-05", is_dataframe=True)
    assert len(df) > 0
    max_end = max(pd.Timestamp(e, unit="ms", tz="Asia/Shanghai") for e in df["end_date"])
    # 2019-01-05 as-of 不可能见到 2019 年报（年报次年 3-4 月披露）
    assert max_end.year < 2019, f"未来函数：2019-01-05 as-of 出现 {max_end.year} 报告期"


def test_balance_fields_flow_ratio(api):
    """F-Score 流动比率字段（⑤⑥）可取（balance 全字段重采后）。"""
    df = api.get_fundamentals("600519.SS", table="balance_statement",
                              fields=["end_date", "total_current_assets",
                                      "total_current_liability", "total_liability"],
                              date="2026-01-05", start_year=2024, end_year=2025,
                              is_dataframe=True)
    assert len(df) >= 2
    assert "total_current_assets" in df.columns
    assert df["total_current_assets"].notna().any(), "total_current_assets 全空——重采未生效"


def test_missing_col_warns_not_silent(api, caplog):
    """缺列不静默：请求不存在字段 → 返回缺列 + log.warning。"""
    with caplog.at_level(logging.WARNING):
        df = api.get_fundamentals("600519.SS", table="income_statement",
                                  fields=["end_date", "not_a_real_col"],
                                  date="2026-01-05", is_dataframe=True)
    assert len(df) > 0
    assert "not_a_real_col" in df.columns
    joined = "\n".join(caplog.messages)
    assert "缺列" in joined or "not_a_real_col" in joined