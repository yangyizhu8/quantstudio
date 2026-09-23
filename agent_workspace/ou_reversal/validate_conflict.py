
import os, sys, json
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "scripts"))
design = json.load(open(os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "agent_strategy_design.json"), encoding="utf-8-sig"))
import agent_skill_common as asc
print("name conflict ->", asc.strategy_name_conflict_errors(design, os.path.join(ROOT, "quantstudio", "backtest", "strategies")))
