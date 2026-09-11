# -*- coding: utf-8 -*-
"""探测：date 解析失败的现行行为（用于 A2 测试断言与方案措辞更正）"""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "quantstudio.db")
from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api
DATE, PREV = "2026-07-30", "2026-07-29"
cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"),
                   research_dir=os.path.join(ROOT, "output", "research"))
eng = BacktestEngine(db_path=DB, strategy={}, start="2026-01-01", end=DATE, config=cfg, strategy_type="ptrade")
_api.attach(eng, None, None, DATE, PREV, {})
for bad in ["not-a-date", "2026-13-45", ""]:
    try:
        df = _api.get_fundamentals(["600519.SS"], "valuation", fields=["float_value"], date=bad)
        print("  date=%-12r -> EXC=None type=%s len=%d" % (bad, type(df).__name__, len(df)))
    except Exception as e:
        print("  date=%-12r -> RAISED %s: %s" % (bad, type(e).__name__, str(e)[:80]))
# 缓存键结构验证
import inspect
src = inspect.getsource(_api.get_fundamentals)
print()
print("cache_key 定义:", [l.strip() for l in src.splitlines() if "cache_key = (" in l])
print("cache_key 含 date:", "str(date)" in src)
