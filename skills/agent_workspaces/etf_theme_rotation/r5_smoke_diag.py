import sys, os, time
sys.path.insert(0, r"D:\miniQMT策略实盘\QuantStudio")
from quantstudio.backtest.strategy_runner import StrategyRunner

STRAT = r"D:\miniQMT策略实盘\QuantStudio\quantstudio\backtest\strategies\etf_theme_rotation_quantstudio.py"
# 诊断窗口：缩短到约 3 个月，快速验证逻辑与不变量
START, END = "2025-01-01", "2025-04-01"

print("[diag] launch at", time.strftime('%H:%M:%S'))
t0 = time.time()
runner = StrategyRunner()
engine, result = runner.run(
    STRAT, START, END,
    capital=100_000,
    match_price_mode="next_open",
    engine_profile="daily-bar-v1",
    etf_t0=True,
)
local_result, _ = result
nav = local_result.nav_history
trades = local_result.trade_records
print("[diag] run done in %.1fs, nav=%d trades=%d" % (time.time()-t0, len(nav), len(trades)))

n_rebalances = len(trades)
final_total = nav[-1]["nav"] if nav else 0
final_cash = nav[-1].get("cash", 0) if nav else 0
cash_ratio = (final_cash / final_total) if final_total else 1.0
distinct_codes = len({t.get("code") for t in trades if t.get("code")})

print("=== R5 DIAG RESULT ===")
print("trade_records count =", len(trades))
print("nav points =", len(nav))
print("final nav = %.2f" % final_total)
print("final cash_ratio = %.4f" % cash_ratio)
print("distinct traded codes =", distinct_codes)

assert n_rebalances > 0, "R5 FAIL: 零成交/零调仓"
assert cash_ratio <= 0.21, "R5 FAIL: 现金占比>0.2"
assert distinct_codes <= 5 * 2, "R5 FAIL: 交易标的数量异常偏多"
print("R5 DIAG PASS: 至少一次调仓 / 现金<=0.2 / 交易标的数合理 全部满足")
print("R5 DIAG OK")
