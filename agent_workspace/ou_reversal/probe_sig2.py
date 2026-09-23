
import json
p = r"D:\miniQMT策略实盘\QuantStudio\skills\quantstudio-strategy-compiler\references\ptrade-api-signatures.json"
d = json.load(open(p, encoding="utf-8"))
print("profile_id=%s version=%s verified_on=%s" % (d.get("profile_id"), d.get("profile_version"), d.get("verified_on")))
sigs = d["signatures"]
print("n_signatures:", len(sigs))
print("names:", ", ".join(sorted(sigs.keys())))
print()
want = ["set_benchmark","set_commission","set_universe","get_index_stocks","get_history",
        "get_index_day_bar","filter_stock_by_status","get_stock_status","get_positions",
        "get_position","order_target_value","order_target","order_value","order","get_open_orders",
        "is_trade","set_slippage","run_daily","get_stock_info","get_trading_day","get_all_stocks",
        "get_history_batch","get_etf_list_local","log"]
for w in want:
    e = sigs.get(w)
    print("---", w, ":", json.dumps(e, ensure_ascii=False)[:600] if e else "ABSENT")
print()
print("local_only_symbols:", json.dumps(d.get("local_only_symbols"), ensure_ascii=False)[:1500])
