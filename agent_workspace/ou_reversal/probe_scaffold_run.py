
import os, sys, json, tempfile, shutil
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "scripts"))
from create_agent_workspace import create_workspace
from pathlib import Path
design_path = Path(ROOT) / "output" / "generated_strategies" / "ou_reversal_csi300_10" / "agent_strategy_design.json"
tmp = Path(tempfile.mkdtemp(prefix="ouws_"))
out = tmp / "ou_reversal_csi300_10"
# workspace creation does NOT require confirmations? check
try:
    p = create_workspace(design_path, out, overwrite=False)
    print("CREATED:", p)
    for f in sorted(Path(p).rglob("*")):
        print("   ", f.relative_to(p))
    ws = json.loads((Path(p) / "workspace_state.json").read_text(encoding="utf-8"))
    print("ledger quantstudio_output:", ws.get("quantstudio_output"))
    print("ledger canonical_source:", ws.get("canonical_source"))
    print("ledger keys:", list(ws.keys()))
except Exception as e:
    print("BLOCKED:", type(e).__name__, e)
print("tmp at:", tmp)
