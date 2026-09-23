
import os, sys, json
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "scripts"))
design = json.load(open(os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "agent_strategy_design.json"), encoding="utf-8-sig"))

schema = json.load(open(os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "schemas", "agent_strategy_design.schema.json"), encoding="utf-8"))
try:
    import jsonschema
    errs = sorted(jsonschema.Draft7Validator(schema).iter_errors(design), key=lambda e: list(e.path))
    if not errs:
        print("SCHEMA: PASS")
    else:
        print("SCHEMA: %d error(s)" % len(errs))
        for e in errs[:15]:
            print("   -", list(e.path), e.message[:220])
except ImportError:
    print("SCHEMA: jsonschema not installed")

import agent_skill_common as asc
print()
print("portfolio_contract_errors:", asc.portfolio_contract_errors(design))
print("execution_funding_errors:", asc.execution_funding_errors(design))
print("confirmation_evidence_errors:", asc.confirmation_evidence_errors(design))
for fn in ("strategy_name_errors", "naming_errors", "design_errors"):
    f = getattr(asc, fn, None)
    if f:
        try:
            print("%s: %s" % (fn, f(design)))
        except Exception as e:
            print("%s raised: %s" % (fn, e))
print()
print("available cross-check fns:", [n for n in dir(asc) if n.endswith("_errors")])
