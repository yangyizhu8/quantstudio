
import json, os
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
for rel in [r"output\generated_strategies\ou_reversal_csi300_10\agent_strategy_design.json",
            r"agent_workspace\ou_reversal_csi300_10\agent_strategy_design.json"]:
    p = os.path.join(ROOT, rel)
    j = json.load(open(p, encoding="utf-8"))
    apis = j["components"]["required_apis"]
    apis = [a for a in apis if a != "get_position"]
    apis = ["log" if a == "log.info" else a for a in apis]
    j["components"]["required_apis"] = apis
    j.setdefault("r3_notes", {})["required_apis_declarative_fix"] = (
        "R3 实现后按实际调用面校正声明（纯声明准确化，无策略语义变更）："
        "① 移除 get_position——策略统一经 get_positions() 契约视图读取持仓（持仓视图契约，禁止按位置取值）；"
        "② log.info → log——校验器按属性基名（log）登记调用，声明 'log.info' 永不匹配。")
    open(p, "w", encoding="utf-8").write(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
    print(rel, "->", apis)
