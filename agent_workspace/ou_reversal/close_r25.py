
import json, os, datetime, hashlib
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
p = os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "agent_strategy_design.json")
j = json.load(open(p, encoding="utf-8"))

now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).replace(microsecond=0).isoformat()
TEXT = "确认，无异议，继续推进"
SCOPE = ("R2.5 确认包全文：13 条近似（A-1~A-13，A-5 按修正后表述）+ 七键确认清单 + 参数冻结清单（13 项）"
         "——见 output/generated_strategies/ou_reversal_csi300_10/R2_5_CONFIRMATION_PACKAGE.md")
FORM = "整包确认（客户未逐条分列；确认文本统一，确认对象为上述包全文）"

keys = ["generation_target", "strategy_semantics", "portfolio_contract",
        "rebalance_funding_contract", "r5_deployment_invariants",
        "execution_approximations", "component_plan"]
j["confirmation_evidence"] = {
    k: {
        "confirmed": True,
        "customer_text": TEXT,
        "confirmed_at": now,
        "source": "customer_reply",
        "confirmed_scope": SCOPE,
        "confirmation_form": FORM,
        "note": "客户于 R2.5 呈报包（逐条枚举 13 近似 + 7 键 + 参数冻结清单）后直接回复确认；原话如上，未逐条分列。"
    } for k in keys
}
j["user_confirmations"] = {
    "generation_target": True,
    "strategy_semantics": True,
    "execution_approximations": True,
    "component_plan": True,
    "backtest_validation_mode": True,
    "static_etf_whitelist": False,
}
for a in j["approximations"]:
    a["confirmed"] = True
    a.setdefault("customer_text", TEXT)
    a.setdefault("confirmed_at", now)
    a.setdefault("source", "customer_reply")
j["r2_5_closed_at"] = now
j["open_questions"] = []
open(p, "w", encoding="utf-8").write(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
print("confirmations written at", now)
print("evidence keys:", list(j["confirmation_evidence"].keys()))
print("approximations confirmed:", sum(1 for a in j["approximations"] if a["confirmed"]), "/", len(j["approximations"]))
