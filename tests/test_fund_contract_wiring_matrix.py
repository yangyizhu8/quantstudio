# -*- coding: utf-8 -*-
"""D4 契约接线测试矩阵（ZCode 必改②第一道防线，2026-08-28）。

遍历 base.py FUND_TABLES 全部表名，经 get_fundamentals API 层抽查非空——
今后任何人往 FUND_TABLES 加表却没接线，本矩阵直接红（而不等某策略 R3 炸）。

对每张表：单只代码 + 最小字段，引擎 attach 后经 get_fundamentals 查询。
在库表数据存在的前提下应返回非空；未接线（else 分支空表）→ 断言失败。
"""
import pytest
import pandas as pd
import warnings

from quantstudio.backtest.ptrade_api import _api, Context, Portfolio, bare_code
from quantstudio.backtest.backtest_engine import BacktestEngine
from quantstudio.backtest.providers.base import FundamentalDataProvider

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


# 每表一个探测字段（取 FUND_TABLES 中非 code/end_date 的第一个实义字段）
_PROBE_FIELD = {
    "valuation": "float_value",
    "balance_statement": "total_assets",
    "income_statement": "np_parent_company_owners",
    "cashflow_statement": "net_operate_cash_flow",
    "eps": "eps",
    "profit_ability": "roe",
    "growth_ability": "roe",
}

# 契约声明但数据源整体不存在（fin_indicator 无 current_ratio/turnover 等字段；
# DuckDB 亦无对应表）→ 无法接线。矩阵对此类**豁免接线断言**但显式告警——
# 防止误解为"接线遗漏"；数据源接入时须移除本豁免（fail-closed 留痕）。
KNOWN_UNSOURCED_TABLES = {"debt_paying_ability", "operating_ability"}


@pytest.mark.parametrize("table", sorted(FundamentalDataProvider.FUND_TABLES.keys()))
def test_fund_contract_wired(api, table):
    """契约接线矩阵：FUND_TABLES 每张表经 get_fundamentals API 层应返回非空（数据在库前提下）。"""
    if table in KNOWN_UNSOURCED_TABLES:
        warnings.warn(f"契约表 {table} 声明但无数据源（fin_indicator 缺字段/无对应表）——豁免接线断言，留痕")
        return
    # 不请求 'code'（它是 index）；只请求实义字段
    fields = [_PROBE_FIELD[table]] if table in _PROBE_FIELD else None
    df = api.get_fundamentals("600519.SS", table=table,
                              fields=fields, date="2026-01-05", is_dataframe=True)
    assert df is not None
    # 未接线的表（else 空表）会有列但 0 行；线已接且有数据应 >0 行
    assert len(df) > 0, f"契约表 {table} 经 get_fundamentals 返回空——接线缺失"
    # 返回列应含请求字段
    if table in _PROBE_FIELD:
        assert _PROBE_FIELD[table] in df.columns