
import os, sys, json, tempfile
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "scripts"))
from create_agent_workspace import create_workspace
from pathlib import Path
src = Path(ROOT) / "output" / "generated_strategies" / "ou_reversal_csi300_10" / "agent_strategy_design.json"
d = json.loads(src.read_text(encoding="utf-8"))
# temporary draft copy with confirmations forced true (throwaway probe only)
d["user_confirmations"] = {k: True for k in d["user_confirmations"]}
for a in d["approximations"]:
    a["confirmed"] = True
tmp = Path(tempfile.mkdtemp(prefix="ouws2_"))
probe_design = tmp / "design.json"
probe_design.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
out = tmp / "ou_reversal_csi300_10"
try:
    p = create_workspace(probe_design, out, overwrite=False)
    print("CREATED:", p)
    for f in sorted(Path(p).rglob("*")):
        rel = f.relative_to(p)
        print("   ", rel, "" if f.is_dir() else "(%d bytes)" % f.stat().st_size)
    ws = json.loads((Path(p) / "workspace_state.json").read_text(encoding="utf-8"))
    print()
    print("ledger quantstudio_output:", ws.get("quantstudio_output"))
    print("ledger canonical_source:", ws.get("canonical_source"))
    print("ledger stage:", ws.get("stage"))
except Exception as e:
    print("BLOCKED:", type(e).__name__, e)
