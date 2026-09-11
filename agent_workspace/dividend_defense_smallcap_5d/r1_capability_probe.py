# -*- coding: utf-8 -*-
"""R1 capability probe - dividend_defense_smallcap_5d

Read-only capability inspection through the REAL injected APIs (not raw SQL).
Run: python agent_workspace/dividend_defense_smallcap_5d/r1_capability_probe.py
"""
import os, sys, json, traceback
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "quantstudio.db")

def sec(t):
    print("\n== " + t + " ==")

def run(name, fn):
    try:
        r = fn()
        print("[OK]   " + name + " -> " + (r if isinstance(r, str) else repr(r))[:900])
    except Exception as e:
        print("[FAIL] " + name + " -> " + type(e).__name__ + ": " + str(e)[:400])

from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api

DATE, PREV = "2026-07-30", "2026-07-29"
cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"),
                   research_dir=os.path.join(ROOT, "output", "research"))
engine = BacktestEngine(db_path=DB, strategy={}, start="2026-01-01", end=DATE,
                        config=cfg, strategy_type="ptrade")
_api.attach(engine, None, None, DATE, PREV, {})
api = _api

sec("1. valuation 表（流通市值 / 换手率）")
run("get_fundamentals valuation float_value", lambda: api.get_fundamentals(
    ["600519.SS", "601398.SS", "300750.SZ"], "valuation",
    fields=["float_value", "market_cap", "circulating_market_cap", "turnover_ratio", "pe_ratio"]).to_string())

sec("2. get_history 换手率字段路径")
for f in ["turnover_rate", "turn", "turnover_ratio"]:
    run("get_history field=" + f, lambda f=f: api.get_history(
        5, frequency="1d", field=f, security_list=["600519.SS"], fq="pre", include=False).to_string())

sec("3. income_statement PIT 接线（np_parent_company_owners 三年序列）")
run("get_fundamentals income_statement", lambda: api.get_fundamentals(
    ["600519.SS"], "income_statement", fields=["end_date", "np_parent_company_owners", "net_profit"],
    date=DATE, start_year=2021, end_year=2025).to_string())

sec("4. growth_ability / profit_ability 表可用性")
for t in ["growth_ability", "profit_ability", "eps"]:
    run("get_fundamentals " + t, lambda t=t: api.get_fundamentals(
        ["600519.SS"], t, fields=None, date=DATE).to_string())

sec("5. get_stock_info listed_date（上市满1年）")
run("get_stock_info listed_date", lambda: api.get_stock_info(["600519.SS", "301583.SZ"], field=["listed_date"]))

sec("6. get_stock_exrights（PTrade 同名 API · 股息率契约锚点）")
for code, d in [("600519.SS", "20260625"), ("601398.SS", "20260512"), ("600036.SS", "20260709")]:
    run("get_stock_exrights " + code + " " + d, lambda c=code, d=d: api.get_stock_exrights(c, d))

sec("7. get_stock_status ST / HALT")
run("get_stock_status ST", lambda: api.get_stock_status(["600519.SS", "000001.SZ"], query_type="ST"))
run("get_stock_status HALT", lambda: api.get_stock_status(["600519.SS", "000001.SZ"], query_type="HALT"))

sec("8. 全A股池 / 沪深300 成分")
run("get_Ashares count", lambda: len(api.get_Ashares(date=DATE)))
run("get_Ashares sample", lambda: api.get_Ashares(date=DATE)[:8])
run("get_index_stocks 000300.SS count", lambda: len(api.get_index_stocks("000300.SS", date=DATE)))
run("get_index_stocks 000300.SS sample", lambda: api.get_index_stocks("000300.SS", date=DATE)[:8])

sec("9. 基准与交易日历")
run("get_trade_days 近5", lambda: api.get_trade_days(end_date=DATE, count=5))
print("\nDONE")
