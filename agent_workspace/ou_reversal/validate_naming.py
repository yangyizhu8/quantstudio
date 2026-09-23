
import os, sys, json
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "scripts"))
design = json.load(open(os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "agent_strategy_design.json"), encoding="utf-8-sig"))
import agent_skill_common as asc
for fn in ("strategy_naming_errors", "strategy_name_conflict_errors", "confirmation_errors", "etf_t0_contract_errors", "parameter_optimization_hook_errors"):
    f = getattr(asc, fn, None)
    if not f: continue
    try:
        print("%-36s -> %s" % (fn, f(design)))
    except Exception as e:
        print("%-36s raised %s: %s" % (fn, type(e).__name__, e))
# conflict check needs project root maybe
import inspect
print()
print(inspect.signature(asc.strategy_name_conflict_errors))
print(inspect.getsource(asc.strategy_name_conflict_errors)[:1500])
