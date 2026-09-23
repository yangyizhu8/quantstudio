
import json, os
p = r"D:\miniQMT策略实盘\QuantStudio\output\generated_strategies\ou_reversal_csi300_10\agent_strategy_design.json"
j = json.load(open(p, encoding="utf-8"))
j["r4_assertions"]["proposed_additional"] = [
  "A4-4 [审核方批准 2026-09-22，定级：本策略专属断言] handle_data 内不得出现 data[...] 订阅或属性读取（AST 扫描）。定级说明（防误读）：零 data 依赖系本策略可行的策略级选择（涨跌停走前复权 close 比值、停牌走 get_stock_status）；E1-3 保留其他策略以 data[code] 做执行层判断的权利，本断言不得泛化为 skill 级规则。"
]
j["r4_assertions"]["proposed_additional_status"] = "APPROVED_AS_STRATEGY_LOCAL (2026-09-22)"
open(p, "w", encoding="utf-8").write(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
print("keys:", list(j["r4_assertions"].keys()))
print("status:", j["r4_assertions"]["proposed_additional_status"])
