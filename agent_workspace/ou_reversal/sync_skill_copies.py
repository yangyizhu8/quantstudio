
import hashlib, shutil, os
from pathlib import Path
ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
SRC = ROOT / "skills" / "quantstudio-strategy-compiler"
DST = Path(r"C:\Users\Administrator\.agents\skills\quantstudio-strategy-compiler")
files = ["SKILL.md", "references/no-lookahead-rules.md"]
for rel in files:
    s, d = SRC / rel, DST / rel
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s, d)
    hs = hashlib.sha256(s.read_bytes()).hexdigest()
    hd = hashlib.sha256(d.read_bytes()).hexdigest()
    print("%-34s src=%s dst=%s %s" % (rel, hs[:16], hd[:16], "MATCH" if hs == hd else "MISMATCH"))
