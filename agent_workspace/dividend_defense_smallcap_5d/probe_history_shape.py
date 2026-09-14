# -*- coding: utf-8 -*-
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api
DB = os.path.join(ROOT, "data", "quantstudio.db")
DATE, PREV = "2026-07-30", "2026-07-29"
cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"), research_dir=os.path.join(ROOT, "output", "research"))
eng = BacktestEngine(db_path=DB, strategy={}, start="2026-01-01", end=DATE, config=cfg, strategy_type="ptrade")
_api.attach(eng, None, None, DATE, PREV, {})
POOL = ["600519.SS", "000060.SZ", "000001.SZ"]
a = _api.get_history(POOL, count=1, unit="1d", fields=["close"], fq="pre", include=False, is_dict=False)
print("multi is_dict=False index:", list(a.index))
print(a.to_string())
b = _api.get_history("600519.SS", count=1, unit="1d", fields=["close"], fq="pre", include=False, is_dict=False)
print("single is_dict=False type:", type(b).__name__, "shape:", getattr(b,"shape",None), "index:", list(getattr(b,"index",[]))[:3])
d = _api.get_history(POOL, count=1, unit="1d", fields=["close"], fq="pre", include=False, is_dict=True)
print("is_dict=True keys:", list(d.keys()))
it = d["600519.SS"]
print("item type:", type(it).__name__)
