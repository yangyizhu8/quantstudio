# -*- coding: utf-8 -*-
import os, sys, datetime
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "quantstudio.db")
from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api
from quantstudio.backtest.strategy_runner import load_strategy
STRATEGY = os.path.join(ROOT, "agent_workspace", "dividend_defense_smallcap_5d", "strategy.py")
DATE, PREV = "2021-07-13", "2021-07-12"
cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"), research_dir=os.path.join(ROOT, "output", "research"))
eng = BacktestEngine(db_path=DB, strategy={}, start="2020-01-01", end=DATE, config=cfg, strategy_type="ptrade")
_api.attach(eng, None, None, DATE, PREV, {})
_funcs, mod = load_strategy(STRATEGY)
CODES = ["600519.SS", "601398.SS", "000060.SZ", "600036.SS"]
prev_api = "20210712"
np_map = mod._load_annual_np(CODES, prev_api)
for c in CODES:
    s = np_map.get(c.split(".")[0], {})
    print(c, "年度点:", sorted(s.items()))
print()
raw = _api.get_fundamentals(CODES, "income_statement", fields=["end_date","np_parent_company_owners"], date=PREV, start_year=2015, end_year=2021)
print("原始行数:", 0 if raw is None else len(raw))
if raw is not None and len(raw) > 0:
    for code, grp in raw.groupby(level=0):
        months = sorted({mod._bj_ms(r["end_date"]).strftime("%Y-%m") for _, r in grp.iterrows() if mod._bj_ms(r["end_date"])})
        print("  ", code, "期数:", len(grp), "月份序列:", months[:12])
