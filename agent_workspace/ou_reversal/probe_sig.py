
import json, os
p = r"D:\miniQMT策略实盘\QuantStudio\skills\quantstudio-strategy-compiler\references\ptrade-api-signatures.json"
d = json.load(open(p, encoding="utf-8"))
print("top keys:", list(d.keys())[:20])
fns = d.get("functions") or d.get("apis") or d
if isinstance(fns, dict):
    keys = sorted(fns.keys())
else:
    keys = sorted(x.get("name") for x in fns)
print("n_functions:", len(keys))
want = ["set_benchmark","set_commission","set_universe","get_index_stocks","get_history",
        "get_index_day_bar","filter_stock_by_status","get_stock_status","get_positions",
        "get_position","order_target_value","order_target","order_value","order","get_open_orders",
        "is_trade","set_slippage","run_daily","get_stock_info","get_trading_day","get_all_stocks"]
for w in want:
    e = fns.get(w) if isinstance(fns, dict) else next((x for x in fns if x.get("name")==w), None)
    print("---", w, ":", json.dumps(e, ensure_ascii=False)[:500] if e else "ABSENT")
