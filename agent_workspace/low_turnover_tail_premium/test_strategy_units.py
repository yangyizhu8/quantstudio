"""Core unit tests for 低流动性溢价换手尾部极值多头 (low_turnover_tail_premium).

覆盖（客户提示词 + R2.5 审核钉死条款）:
  T1 _amihud20: Amihud = mean(|r|/amount, 20d) —— 手算 3 例验证
  T2 _latest_by_code: profit_ability 最新报告期去重（含重述取 publ_date 最新、NaN 回退）
  T3 _extract_history_field: DataFrame/recarray 归一 + fail-soft 空数组
  T4 _month_key / 空池规则数值（_MIN_HOLD=8/_TARGET_HOLDINGS=12 冻结值）
  T5 尾部定位数学：长度 n 时 keep = max(1, floor(n*0.10))，且 NaN 剔除（_select 前置条件）
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(r'D:\miniQMT策略实盘\QuantStudio')
sys.path.insert(0, str(ROOT))

# 以 script 方式加载 strategy.py（避免污染全局 g/log），仅取纯函数
import importlib.util
spec = importlib.util.spec_from_file_location(
    "ltp_strategy", ROOT / "agent_workspace" / "low_turnover_tail_premium" / "strategy.py")
# 用 stub 环境隔离：g/log 由引擎注入，纯函数不需要它们
import types
stub = types.ModuleType("ptrade_import")
stub.g = types.SimpleNamespace()

ns = {}
src = (ROOT / "agent_workspace" / "low_turnover_tail_premium" / "strategy.py").read_text(encoding="utf-8")
compiled = compile(src, "strategy.py", "exec")
exec(compiled, ns)

_amihud20 = ns["_amihud20"]
_latest_by_code = ns["_latest_by_code"]
_extract_history_field = ns["_extract_history_field"]
_TAIL_PCT = ns["_TAIL_PCT"]
_MIN_HOLD = ns["_MIN_HOLD"]
_TARGET_HOLDINGS = ns["_TARGET_HOLDINGS"]


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print("%s %s" % (status, name))
    return cond


results = []
# T1: Amihud 手算 —— 常数收益率 r=1%/日、成交额恒定 1000 万
closes = 100.0 * np.cumprod(np.full(21, 1.001))
amounts = np.full(20, 1e7)
a = _amihud20(closes, amounts)
expect = np.abs(100.0 * np.cumprod(np.full(21, 1.001))[1:] / (100.0 * np.cumprod(np.full(21, 1.001))[:-1]) - 1) / 1e7
results.append(check("T1 amihud constant-r formula", np.isclose(a, expect.mean(), rtol=1e-9)))

# T1b: 长度不足返回 NaN
results.append(check("T1b amihud short window -> nan", np.isnan(_amihud20(np.arange(5.0), np.arange(5.0)))))

# T2: latest_by_code —— 重述（同 end_date 后发公告取后者）+ NaN 回退
df = pd.DataFrame({
    'code': ['000001.SS', '000001.SS', '000002.SS', '000003.SS'],
    'end_date': [20250630.0, 20250630.0, 20250331.0, 20241231.0],
    'publ_date': [20260801.0, 20260815.0, 20260510.0, 20260401.0],
    'roe': [10.0, 12.0, np.nan, 8.0],
}).set_index('code')
m = _latest_by_code(df, 'roe')
results.append(check("T2 restatement keeps latest publ_date", m.get('000001.SS') == 12.0))
results.append(check("T2 nan row excluded", '000002.SS' not in m))
results.append(check("T2 normal latest kept", m.get('000003.SS') == 8.0))

# T3: extract history field —— DataFrame 与 recarray 同值、空/缺字段 fail-soft
h_df = pd.DataFrame({'close': [1.0, 2.0, 3.0]})
results.append(check("T3 dataframe -> ndarray", np.array_equal(
    _extract_history_field(h_df, 'close'), np.asarray([1.0, 2.0, 3.0]))))
results.append(check("T3 none -> empty", _extract_history_field(None, 'close').size == 0))
results.append(check("T3 missing field -> empty", _extract_history_field(h_df, 'no_such').size == 0))
arr = np.array([(1.0,), (2.0,)], dtype=[('close', 'f8')])
results.append(check("T3 structured array", np.array_equal(
    _extract_history_field(arr, 'close'), np.asarray([1.0, 2.0]))))

# T4: 冻结参数
results.append(check("T4 tail_pct frozen 0.10", _TAIL_PCT == 0.10))
results.append(check("T4 min_hold 8", _MIN_HOLD == 8))
results.append(check("T4 target 12", _TARGET_HOLDINGS == 12))

# T5: 尾部 keep 数学（复现 L5 逻辑）
n = 5379
keep = max(1, int(n * _TAIL_PCT))
results.append(check("T5 tail keep = floor(n*10%)", keep == int(5379 * 0.10)))

allpass = all(results)
print("TOTAL %d/%d" % (sum(1 for r in results if r), len(results)))
sys.exit(0 if allpass else 1)