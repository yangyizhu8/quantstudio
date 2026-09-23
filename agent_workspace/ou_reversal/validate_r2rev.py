
import os, sys, json
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "scripts"))
design = json.load(open(os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "agent_strategy_design.json"), encoding="utf-8-sig"))
schema = json.load(open(os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "schemas", "agent_strategy_design.schema.json"), encoding="utf-8"))
import jsonschema, agent_skill_common as asc
errs = sorted(jsonschema.Draft7Validator(schema).iter_errors(design), key=lambda e: list(e.path))
print("SCHEMA:", "PASS" if not errs else "%d error(s)" % len(errs))
for e in errs[:10]:
    print("   -", list(e.path), e.message[:200])
print("portfolio_contract_errors:", asc.portfolio_contract_errors(design))
print("execution_funding_errors :", asc.execution_funding_errors(design))
print("naming_errors            :", asc.strategy_naming_errors(design))
print("name_conflict            :", asc.strategy_name_conflict_errors(design, os.path.join(ROOT, "quantstudio", "backtest", "strategies")))
print("confirmation_errors      :", [m[:110] for m in asc.confirmation_errors(design)])
print()
print("engine_profile:", json.dumps(design["engine_profile"], ensure_ascii=False)[:220])
print("decision_events:", [ (e["name"], e["lifecycle"], e.get("time")) for e in design["timing"]["decision_events"] ])
