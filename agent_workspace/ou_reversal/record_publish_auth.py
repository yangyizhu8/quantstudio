
import json, os, datetime
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).replace(microsecond=0).isoformat()
TEXT = "发布，我的意图旨在测试回测功能和转换ptrade管线的双端对齐功能，策略本身盈亏不重要。"
for rel in [r"output\generated_strategies\ou_reversal_csi300_10\agent_strategy_design.json",
            r"agent_workspace\ou_reversal_csi300_10\agent_strategy_design.json"]:
    p = os.path.join(ROOT, rel)
    j = json.load(open(p, encoding="utf-8"))
    j["confirmation_evidence"]["publish_authorization"] = {
        "confirmed": True, "customer_text": TEXT, "confirmed_at": now, "source": "customer_reply",
        "confirmed_scope": "R6 正式发布授权（发布至 quantstudio/backtest/strategies/沪深300均值回归超跌反弹.py）",
        "note": "客户明确指示发布，并说明本策略的用途是测试回测功能与 PTrade 转换管线的双端对齐，策略盈亏非本次目标。"
    }
    j["confirmation_evidence"]["robustness_exemption"] = {
        "confirmed": True, "customer_text": TEXT, "confirmed_at": now, "source": "customer_reply",
        "confirmed_scope": "R5.5 稳健性门控豁免（客户风险接受型发布）",
        "note": "客户知悉 R5 绩效为负（总收益 −29.29% / 超额 −46.45 pp / 最大回撤 −30.75%），裁定盈亏非目标，"
                "本策略用于验证回测与 PTrade 转换管线双端对齐能力。R5.5 结果如后续执行仍原样留痕，不豁免为 PASS。"
    }
    j.setdefault("r6_publication", {})["authorization"] = {
        "customer_text": TEXT, "authorized_at": now,
        "purpose": "验证本地回测功能与 PTrade 转换管线双端对齐（策略盈亏非目标）",
        "r5_5": "CUSTOMER_WAIVED（风险接受型发布；如执行则留痕，不豁免为 PASS）"
    }
    open(p, "w", encoding="utf-8").write(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
    print(rel, "-> publish authorization + R5.5 exemption recorded")
lp = os.path.join(ROOT, "agent_workspace", "ou_reversal", "workspace_state.json")
led = json.load(open(lp, encoding="utf-8"))
led["pipeline"]["R6"] = {"status": "PENDING", "customer_text": TEXT, "authorized_at": now,
                         "purpose": "回测功能 + PTrade 转换管线双端对齐验证",
                         "r5_5": "CUSTOMER_WAIVED"}
open(lp, "w", encoding="utf-8").write(json.dumps(led, ensure_ascii=False, indent=2) + "\n")
print("ledger updated")
