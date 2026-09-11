# -*- coding: utf-8 -*-
"""R1 capability probe - PART 2 (unit / PIT / listing-date / exrights)"""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "quantstudio.db")
import pandas as pd
from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api, query, valuation

DATE, PREV = "2026-07-30", "2026-07-29"
cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"),
                   research_dir=os.path.join(ROOT, "output", "research"))
eng = BacktestEngine(db_path=DB, strategy={}, start="2026-01-01", end=DATE,
                     config=cfg, strategy_type="ptrade")
_api.attach(eng, None, None, DATE, PREV, {})
api = _api
def run(n, f):
    try:
        r = f()
        print("[OK]   " + n + " -> " + (r if isinstance(r, str) else repr(r))[:800])
    except Exception as e:
        print("[FAIL] " + n + " -> " + type(e).__name__ + ": " + str(e)[:300])

print("== A. get_stock_exrights 用北京口径正确除权日复测 ==")
for code, d in [("600519.SS", "20260626"), ("600519.SS", "20251219"), ("600036.SS", "20260710"), ("601398.SS", "20260512")]:
    run("get_stock_exrights " + code + " " + d, lambda c=code, d=d: api.get_stock_exrights(c, d))
run("get_stock_exrights 无 date", lambda: api.get_stock_exrights("600519.SS"))

print("\n== B. 流通市值单位对拍：fields 路径 vs ORM 路径 ==")
run("fields float_value 600519/000060", lambda: api.get_fundamentals(
    ["600519.SS", "000060.SZ"], "valuation", fields=["float_value", "total_value", "a_floats", "total_share"]).to_string())
def orm():
    q = query(valuation.code, valuation.circulating_market_cap, valuation.market_cap).filter(valuation.code.in_(["600519.SS", "000060.SZ"]))
    return api.get_fundamentals(q).to_string()
run("ORM circulating_market_cap", orm)
run("raw close 600519/000060 决策日前一交易日", lambda: api.get_history(
    1, frequency="1d", field="close", security_list=["600519.SS", "000060.SZ"], fq="pre", include=False).to_string())

print("\n== C. turnover_ratio 逐日可得性（近 20 日序列走 valuation 表）==")
def turnover_series():
    days = [str(x)[:10] for x in api.get_trade_days(end_date=DATE, count=21)][:-1]
    out = []
    for d in days[-5:]:
        df = api.get_fundamentals(["600519.SS", "000060.SZ"], "valuation", fields=["turnover_ratio"], date=d)
        out.append(d + ":" + str({str(i).split('.')[0]: round(float(r["turnover_ratio"]), 4) for i, r in df.iterrows()}))
    return " | ".join(out)
run("21日窗口逐日 turnover_ratio（末5日）", turnover_series)

print("\n== D. listed_date：get_stock_info vs stock_basic 真实上市日 ==")
import duckdb
con = duckdb.connect(DB, read_only=True)
for c in ["600519", "000001", "301583"]:
    api_v = api.get_stock_info([c + (".SS" if c[0] == "6" else ".SZ")], field=["listed_date"])
    real = con.execute("SELECT list_date FROM stock_basic WHERE code=?", [c]).fetchone()[0]
    real_bj = pd.Timestamp(int(real), unit="ms", tz="Asia/Shanghai").strftime("%Y-%m-%d")
    first_bar = con.execute("SELECT MIN(time) FROM stock_daily WHERE code=?", [c]).fetchone()[0]
    fb_bj = pd.Timestamp(int(first_bar), unit="ms", tz="Asia/Shanghai").strftime("%Y-%m-%d")
    print("   " + c + "  get_stock_info=" + str(api_v) + "  stock_basic.list_date=" + real_bj + "  首根K线=" + fb_bj)
con.close()

print("\n== E. 决策日 2026-07-30 的 12 个月分红窗口（北京口径）==")
con = duckdb.connect(DB, read_only=True)
cut = int(pd.Timestamp("2026-07-30", tz="Asia/Shanghai").timestamp() * 1000)
start = int(pd.Timestamp("2025-07-30", tz="Asia/Shanghai").timestamp() * 1000)
print("   窗口内有税前现金分红的股票数:", con.execute(
    "SELECT COUNT(DISTINCT code) FROM stock_dividend WHERE ex_date > ? AND ex_date <= ? AND cash_div_before_tax > 0", [start, cut]).fetchone()[0])
print("   窗口内记录数:", con.execute(
    "SELECT COUNT(*) FROM stock_dividend WHERE ex_date > ? AND ex_date <= ? AND cash_div_before_tax > 0", [start, cut]).fetchone()[0])
con.close()
print("\nDONE")
