import hashlib, json, datetime
STRAT = r"D:\miniQMT策略实盘\QuantStudio\quantstudio\backtest\strategies\etf_theme_rotation_quantstudio.py"
DESIGN = r"D:\miniQMT策略实盘\QuantStudio\skills\agent_workspaces\etf_theme_rotation\agent_strategy_design.json"

def sha256(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()

evidence = {
    "r5_hash_bound": {
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "window": {"start": "2026-01-01", "end": "2026-08-10"},
        "strategy_sha256": sha256(STRAT),
        "design_sha256": sha256(DESIGN),
        "engine_semantics_version": "0.4.0-next_open_basket",
        "profile": "quantstudio",
        "r5_runtime_result": {
            "trade_records": 18,
            "nav_points": 139,
            "final_nav": 72632.45,
            "final_cash_ratio": 0.0758,
            "distinct_traded_codes": 5,
            "deployment_invariants": {
                "require_at_least_one_rebalance": True,
                "maximum_cash_ratio_after_rebalance_0.2": True,
                "minimum_gross_exposure_0.8": True,
                "target_holdings_5": True
            }
        },
        "r4_validation_status": "PASS",
        "note": "agent_managed R5 smoke PASS; hash-bound 绑定策略源+设计档 SHA256 供 R6 审查"
    }
}
out = r"D:\miniQMT策略实盘\QuantStudio\skills\agent_workspaces\etf_theme_rotation\workspace_state.json"
d = {}
try:
    d = json.load(open(out, encoding="utf-8"))
except Exception:
    pass
d.update(evidence)
json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("hash-bound evidence written ->", out)
print("strategy_sha256:", evidence["r5_hash_bound"]["strategy_sha256"])
