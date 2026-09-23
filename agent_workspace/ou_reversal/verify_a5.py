
import os, sys, json
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "scripts"))
p = os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "agent_strategy_design.json")
design = json.load(open(p, encoding="utf-8-sig"))
schema = json.load(open(os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "schemas", "agent_strategy_design.schema.json"), encoding="utf-8"))
import jsonschema, agent_skill_common as asc
errs = sorted(jsonschema.Draft7Validator(schema).iter_errors(design), key=lambda e: list(e.path))
print("SCHEMA:", "PASS" if not errs else ["%s %s" % (list(e.path), e.message[:80]) for e in errs])
print("portfolio:", asc.portfolio_contract_errors(design), "| funding:", asc.execution_funding_errors(design))
a5 = [a for a in design["approximations"] if a["id"] == "A-5"][0]
print()
print("A-5:", a5["description"][:400])
print()
for f in ("R2_5_CONFIRMATION_PACKAGE.md", "R2_AGENT_COMPONENT_PLAN.md"):
    t = open(os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", f), encoding="utf-8").read()
    hit = [l for l in t.splitlines() if "A-5" in l]
    print(f, "->", (hit[0][:230] if hit else "NOT FOUND"))
