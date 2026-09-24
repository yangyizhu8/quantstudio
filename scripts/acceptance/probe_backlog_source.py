"""挂账定性批 · 来源定位：检查仓内是否存在 X/R/D/NO_ADJ 分类定义与缺陷清单。"""
import json
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
CAND = [
    "agent_workspace/_wt_base741/config/qfq_rebase_admissible_securities.json",
    "agent_workspace/_wt_base741/config/profiles/mcp_only/qfq_resume_1897.json",
    "agent_workspace/_wt_base741/config/profiles/mcp_only/qfq_resume_1975.json",
]
TOKENS = ["512250", "REPAIR_SKIP", "NO_ADJ", "ratio_c", '"X"', '"R"', '"D"']
for rel in CAND:
    p = ROOT / rel
    if not p.exists():
        print(f"[缺] {rel}")
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[读失败] {rel}: {type(e).__name__}: {e}")
        continue
    s = json.dumps(d, ensure_ascii=False)
    print(f"\n### {rel}  ({len(s)/1024:.0f} KB)")
    print(f"  顶层类型={type(d).__name__}", end="")
    if isinstance(d, dict):
        ks = list(d.keys())
        print(f" 键数={len(ks)} 前10={ks[:10]}")
        for k in ks[:2]:
            v = d[k]
            print(f"    {k}: {type(v).__name__}"
                  + (f" {list(v.keys())[:6]}" if isinstance(v, dict)
                     else f" len={len(v)} {v[:3] if isinstance(v, list) else v}"))
    elif isinstance(d, list):
        print(f" 元素数={len(d)} 首元素={str(d[0])[:120] if d else 'N/A'}")
    print("  含 token:", {t: (t in s) for t in TOKENS})