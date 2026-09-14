# -*- coding: utf-8 -*-
"""过滤池分阶段诊断：定位哪一步把池子清空。"""
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
mod._ensure_runtime_state()
prev_api = PREV.replace("-", "")

codes = list(_api.get_Ashares(prev_api) or [])
print("1 get_Ashares:", len(codes), codes[:3])
stage = [c for c in codes if c[:3] not in mod.EXCLUDED_PREFIXES]
print("2 板块剔除后:", len(stage))
try:
    st = _api.get_stock_status(stage, query_type="ST", query_date=prev_api)
    stage2 = [c for c in stage if not mod._mapping_true(st, c)]
except Exception as e:
    print("   ST 查询异常:", e); stage2 = stage
print("3 非ST后:", len(stage2))
try:
    ha = _api.get_stock_status(stage2, query_type="HALT", query_date=prev_api)
    stage3 = [c for c in stage2 if not mod._mapping_true(ha, c)]
except Exception as e:
    print("   HALT 查询异常:", e); stage3 = stage2
print("4 非停牌后:", len(stage3))
try:
    info = _api.get_stock_info(stage3, field=["listed_date"]) or {}
    sample = list(info.items())[:2]
    print("   get_stock_info 样例:", sample)
    pd_ = datetime.datetime.strptime(PREV, "%Y-%m-%d")
    stage4 = []
    for c in stage3:
        ld = (info.get(c) or info.get(c.split(".")[0]) or {}).get("listed_date")
        if not ld: continue
        try:
            if (pd_ - datetime.datetime.strptime(str(ld)[:10], "%Y-%m-%d")).days >= 365:
                stage4.append(c)
        except Exception: continue
except Exception as e:
    print("   listed_date 异常:", e); stage4 = stage3
print("5 上市满1年后:", len(stage4))
try:
    val = _api.get_fundamentals(stage4, "valuation", fields=["float_value"], date=PREV)
    fv = {}
    for c, row in (val.iterrows() if val is not None and len(val) > 0 else []):
        v = mod._finite(row.get("float_value"), None)
        if v is not None and v > mod.FLOAT_VALUE_MIN:
            fv[str(c).split(".")[0]] = v
    print("6 流通市值>5亿后:", len(fv))
except Exception as e:
    print("   valuation 异常:", e); fv = {}
stage5 = [c for c in stage4 if c.split(".")[0] in fv]
np_map = mod._load_annual_np(stage5, prev_api)
print("7 income_statement 覆盖标的数:", len(np_map))
raw_sample = _api.get_fundamentals(stage5[:2], "income_statement", fields=["end_date","np_parent_company_owners"], date=PREV, start_year=2015, end_year=2021)
print("   income 原始样例行数:", 0 if raw_sample is None else len(raw_sample))
if raw_sample is not None and len(raw_sample) > 0:
    print(raw_sample.head(4).to_string())
passing = []
for c, series in np_map.items():
    latest = max(series) if series else None
    if latest is None: continue
    t, t3 = series.get(latest), series.get(latest-3)
    if t and t3 and t > 0 and t3 > 0 and (t/t3)**(1/3.)-1 > mod.CAGR_MIN:
        passing.append(c)
print("8 CAGR>15% 后:", len(passing))
pool = mod._build_filter_pool(prev_api)
print("9 _build_filter_pool 返回:", len(pool))
