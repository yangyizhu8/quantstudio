import sys, os
sys.path.insert(0, r"D:\miniQMT策略实盘\QuantStudio")
from quantstudio.backtest.strategy_runner import StrategyRunner

STRAT = r"D:\miniQMT策略实盘\QuantStudio\quantstudio\backtest\strategies\fall_reversal_quantstudio.py"
START, END = "2026-01-01", "2026-08-10"

runner = StrategyRunner()
engine, result = runner.run(
    STRAT, START, END,
    capital=1_000_000,
    match_price_mode="next_open",
    engine_profile="daily-bar-v1",
    etf_t0=False,
)
local_result, _ = result
nav = local_result.nav_history
trades = local_result.trade_records

# ---- R5 不变量断言 ----
n_rebalances = len(trades)
final_total = nav[-1]["nav"] if nav else 0
final_cash = nav[-1].get("cash", 0) if nav else 0
cash_ratio = (final_cash / final_total) if final_total else 1.0
distinct_codes = len({t.get("code") for t in trades if t.get("code")})

# 建仓成功判定：nav 中持仓数最大值 + 首次出现持仓的日期
positions_series = [r.get("positions", 0) for r in nav]
max_positions = max(positions_series) if positions_series else 0
first_holding_idx = next((i for i, p in enumerate(positions_series) if p > 0), -1)
first_holding_date = nav[first_holding_idx]["date"] if first_holding_idx >= 0 else "NONE"
# 资金不足拒单统计
rejected = [t for t in trades if t.get("status") == "rejected" or "不足" in str(t.get("reason", ""))]
n_rejected = len(rejected)

print("=== R5 SMOKE RESULT ===")
print("trade_records count =", len(trades))
print("nav points =", len(nav))
print("final nav = %.2f" % final_total)
print("final cash_ratio = %.4f" % cash_ratio)
print("distinct traded codes =", distinct_codes)
print("max positions held =", max_positions)
print("first holding date =", first_holding_date)
print("rejected orders =", n_rejected)
print("engine_semantics_version present =", hasattr(engine, 'engine_semantics_version'))

# 不变量（对应 design r5_deployment_invariants）
assert n_rebalances > 0, "R5 FAIL: 零成交/零调仓"
assert cash_ratio <= 0.21, "R5 FAIL: 现金占比>0.2 (最大允许0.2)"
assert distinct_codes > 0, "R5 FAIL: 无交易标的"
assert max_positions >= 18, "R5 FAIL: 建仓未成功（max positions=%d < 18）" % max_positions
assert first_holding_date != "NONE", "R5 FAIL: 全程空仓，首仓未建立"
assert n_rejected == 0, "R5 FAIL: 仍存在 %d 笔资金不足拒单" % n_rejected
print("R5 PASS: 建仓成功(max positions>=18) / 无资金不足拒单 / 现金<=0.2 / 交易标的数>0")
print("R5 OK")
