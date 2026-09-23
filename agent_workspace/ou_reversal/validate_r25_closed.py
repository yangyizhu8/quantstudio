
import os, sys, json
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "scripts"))
p = os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "agent_strategy_design.json")
design = json.load(open(p, encoding="utf-8-sig"))
schema = json.load(open(os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "schemas", "agent_strategy_design.schema.json"), encoding="utf-8"))
import jsonschema, agent_skill_common as asc
errs = sorted(jsonschema.Draft7Validator(schema).iter_errors(design), key=lambda e: list(e.path))
print("SCHEMA:", "PASS (0 error)" if not errs else ["%s %s" % (list(e.path), e.message[:100]) for e in errs])
print("confirmation_errors :", asc.confirmation_errors(design))
print("portfolio_contract  :", asc.portfolio_contract_errors(design))
print("execution_funding   :", asc.execution_funding_errors(design))
print("naming              :", asc.strategy_naming_errors(design))
print("name_conflict       :", asc.strategy_name_conflict_errors(design, os.path.join(ROOT, "quantstudio", "backtest", "strategies")))
print("evidence_errors     :", asc.confirmation_evidence_errors(design))
