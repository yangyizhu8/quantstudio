# -*- coding: utf-8 -*-
"""B2 分界测试 / as-of 正确性 —— 同一脚本在修复前(worktree HEAD)与修复后(主工作树)各跑一次。
用法: python b2_probe.py <repo_root>  (DB 走环境变量 QUANTSTUDIO_DB)
"""
import os, sys, json, hashlib
ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, ROOT)
DB = os.environ["QUANTSTUDIO_DB"]
import pandas as pd
from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api

DATE, PREV = "2026-07-30", "2026-07-29"
CODES = ["600519.SS", "000060.SZ", "000001.SZ"]
FIELDS = ["float_value", "total_value", "turnover_ratio", "pe_ratio", "a_floats"]

cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"),
                   research_dir=os.path.join(ROOT, "output", "research"))
eng = BacktestEngine(db_path=DB, strategy={}, start="2026-01-01", end=DATE,
                     config=cfg, strategy_type="ptrade")
_api.attach(eng, None, None, DATE, PREV, {})

def sig(df):
    if df is None or len(df) == 0:
        return {"empty": True, "cols": list(df.columns) if df is not None else None}
    d = df.copy()
    d.index = [str(i) for i in d.index]
    d = d.reindex(sorted(d.columns), axis=1)
    h = hashlib.sha256(pd.util.hash_pandas_object(d, index=True).values.tobytes()).hexdigest()[:16]
    return {"empty": False, "shape": list(d.shape), "cols": list(d.columns),
            "index": list(d.index), "hash": h}

cases = {
    "A_date_None": None,
    "B_date_T": DATE,
    "C_date_T_minus_1": PREV,
    "D_date_T_minus_7": "2026-07-23",
}
out = {}
for name, d in cases.items():
    if d is None:
        df = _api.get_fundamentals(CODES, "valuation", fields=FIELDS)
    else:
        df = _api.get_fundamentals(CODES, "valuation", fields=FIELDS, date=d)
    out[name] = sig(df)
    if name == "D_date_T_minus_7":
        out[name + "_values"] = {str(i).split(".")[0]: round(float(r["turnover_ratio"]), 4)
                                 for i, r in df.iterrows()} if len(df) else {}
print(json.dumps(out, ensure_ascii=False, indent=1))
