# -*- coding: utf-8 -*-
"""B2 本地验证脚本：date 分界 + as-of 正确性（排序无关哈希，自断言）

用法： python agent_workspace/dividend_defense_smallcap_5d/verify_b2_local.py

说明：as-of 分支的行序来自 SQL 返回序（不保证稳定），故本脚本对索引排序后再哈希；
     硬断言以「取值对拍」为准，不以 as-of 哈希值作为跨会话基准。
"""
import os, sys, hashlib
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "quantstudio.db")
import pandas as pd
from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api

DATE, PREV, T7 = "2026-07-30", "2026-07-29", "2026-07-23"
CODES = ["600519.SS", "000060.SZ", "000001.SZ"]
FIELDS = ["float_value", "total_value", "turnover_ratio", "pe_ratio", "a_floats"]
EXPECT = {"600519": 0.2713, "000060": 2.6914, "000001": 0.5647}

cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"),
                   research_dir=os.path.join(ROOT, "output", "research"))
eng = BacktestEngine(db_path=DB, strategy={}, start="2026-01-01", end=DATE,
                     config=cfg, strategy_type="ptrade")
_api.attach(eng, None, None, DATE, PREV, {})

def sig(df):
    d = df.copy()
    d.index = [str(i) for i in d.index]
    d = d.reindex(sorted(d.columns), axis=1).sort_index()
    return hashlib.sha256(pd.util.hash_pandas_object(d, index=True).values.tobytes()).hexdigest()[:16]

def get(d):
    return _api.get_fundamentals(CODES, "valuation", fields=FIELDS) if d is None \
        else _api.get_fundamentals(CODES, "valuation", fields=FIELDS, date=d)

h_none, h_T, h_T1, h_T7 = sig(get(None)), sig(get(DATE)), sig(get(PREV)), sig(get(T7))
vals = {str(i).split(".")[0]: round(float(r["turnover_ratio"]), 4) for i, r in get(T7).iterrows()}

fails = []
if not (h_none == h_T == h_T1):
    fails.append("分界测试 FAIL：date 未传 / = T / = T-1 三者哈希不一致 -> %s %s %s" % (h_none, h_T, h_T1))
if h_T7 == h_T1:
    fails.append("as-of FAIL：date = T-7 仍返回快照值（修复未生效）")
for k, v in EXPECT.items():
    if abs(vals.get(k, -1.0) - v) > 1e-6:
        fails.append("as-of FAIL：%s 期望 %.4f 实得 %s" % (k, v, vals.get(k)))

print("=== B2 本地验证 ===")
print("  分界哈希(排序无关)  未传=%s  =T=%s  =T-1=%s   <- 三者须相同" % (h_none, h_T, h_T1))
print("  as-of 取值 date=%s  实得=%s" % (T7, vals))
print("  期望值              %s" % EXPECT)
print("  RESULT: %s" % ("PASS" if not fails else "FAIL"))
for f in fails:
    print("   - " + f)
sys.exit(0 if not fails else 1)
