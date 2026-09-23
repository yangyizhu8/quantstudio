
import json, os, datetime
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).replace(microsecond=0).isoformat()
TEXT = "F1-B 确认"
a14 = {
  "id": "A-14",
  "description": (
    "【候选递补（F1-B 裁定）】单只预算 = 运行时总资产 × 0.97 ÷ 10；当某候选按 D 日开盘价连 1 手（100 股）都买不起时，"
    "在该候选的 OU 降序名单内**顺延**取下一位，直到凑满 10 只可买标的或名单耗尽。「取前 10」实际落地为「取前 10 只可买的」——"
    "对高价股的系统性排除是「10 万本金 ÷ 10 只等权」的算术约束的现实反映（沪深300 成员中约 13% 股价 > 97 元，"
    "逐快照实测 10.7%~15.4%），不是因子偏好。"
    "边界：① 递补仅在 OU 名单内顺延，不回全池重选；② 名单耗尽仍不足 10 只时回落 A-7 语义（按实际数量等权，note 标记）；"
    "③ 涨跌停/停牌/无价等不可交易日**不递补**，留现金（C10-A 口径）；④ 不改动因子定义、五层过滤、排序规则、择时门、成本口径与 E1 取数。"
    "实现：路径①「同批真值循环」——按名单顺序逐一下单，以引擎返回的 order.status/reason 判定（filled 与 below_rebalance_threshold 视为入选；"
    "delta_below_one_lot 触发递补；其余 reason 不递补），不复制引擎可买性规则，保持 A4-4 零 data 依赖不变。"
  ),
  "confirmed": True,
  "customer_text": TEXT,
  "confirmed_at": now,
  "source": "customer_reply",
  "confirmed_scope": "F1 裁定 B（候选递补）+ 新近似 A-14 表述（见呈报包）",
  "note": "客户于 F1-B 呈报包后原话确认；R2.5 已关闭，本近似按新增近似流程单独取证。"
}
for rel in [r"output\generated_strategies\ou_reversal_csi300_10\agent_strategy_design.json",
            r"agent_workspace\ou_reversal_csi300_10\agent_strategy_design.json"]:
    p = os.path.join(ROOT, rel)
    j = json.load(open(p, encoding="utf-8"))
    j["approximations"] = [a for a in j["approximations"] if a["id"] != "A-14"] + [a14]
    j["confirmation_evidence"]["f1_b_candidate_backfill"] = {
        "confirmed": True, "customer_text": TEXT, "confirmed_at": now, "source": "customer_reply",
        "confirmed_scope": a14["confirmed_scope"], "note": a14["note"]}
    inv = j["r5_deployment_invariants"]
    inv["tradable_field_definition"] = (
        "QS_REBALANCE_AUDIT 的 tradable 字段定义为 min(过滤后候选池大小, target_holdings)——"
        "即「候选充足前提下策略可持有的数量」；selected = 递补后实际选定的目标数量。"
        "递补生效后 selected 恒等于 tradable（候选池 ≈150 只 ≫ 10），部署不变量按此口径核账。")
    j["components"]["implementation_notes"].append(
        "【F1-B 递补实现（路径①）】按 OU 降序名单顺序逐一下单并以引擎返回真值判定："
        "status=='filled' 或 reason=='below_rebalance_threshold' → 入选；reason=='delta_below_one_lot' → 顺延下一位；"
        "其余 reason（limit_up/down_blocked、halted、no_price、insufficient_cash_or_rounding）→ 不递补、留现金。"
        "守卫：现金 ≤ 总资产×0.03（已投满目标敞口）即停止；尝试上限 3×目标数=30 次。"
        "依据：Order.__bool__ = (filled>0 or filled_amount>0)（backtest_engine.py:124-126）→ no-op 恒 falsy；"
        "_qs_noop_target 保留 reason（ptrade_api.py:2666-2672）且 no-op 不触任何状态（幂等安全）。")
    open(p, "w", encoding="utf-8").write(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
    print(rel, "-> approx:", len(j["approximations"]), "| A-14 confirmed:", [a["confirmed"] for a in j["approximations"] if a["id"]=="A-14"])
