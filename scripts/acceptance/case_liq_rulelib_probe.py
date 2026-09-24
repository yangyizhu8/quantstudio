"""P2 客户新案 缺陷③ 取证：规则库清单 + 行数/水位检查覆盖面。

客户报告缺陷③：规则库缺 6 表行数/水位检查与阈值定义。
本脚本：枚举现有规则、判定行数/水位类规则覆盖、并扫描"6 表"线索来源。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("qo", ROOT / "scripts" / "quality_orchestrator.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("=== 规则库清单 ===")
print(f"规则总数 = {len(m.QUALITY_RULES)}")
for rid, r in m.QUALITY_RULES.items():
    print(f"  [{r['level']:6s}] {rid:22s} {r['description']}")
    print(f"             detection: {r['detection']}")

print()
print("=== 缺陷③取证：行数/水位/阈值 类规则是否覆盖 ===")
kw = ["行数", "水位", "row", "count", "watermark", "守恒", "parity"]
for k in kw:
    hits = [rid for rid, r in m.QUALITY_RULES.items()
            if k in (r.get("description", "") + r.get("detection", "") + r.get("repair", ""))]
    print(f"  关键词「{k}」命中规则: {hits if hits else '（无）'}")

print()
print("=== 「6 表」线索扫描（规则库/阈值定义/表清单）===")
cands = list(ROOT.glob("scripts/*.py")) + list(ROOT.glob("docs/**/*.md"))
hits = []
for p in cands:
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for kw2 in ("6 表", "6表", "六表", "六张表", "六表行数", "行数+水位", "行数与水位"):
        if kw2 in t:
            hits.append((str(p.relative_to(ROOT)), kw2))
print("  命中文件/关键词:")
for h in hits[:20]:
    print(f"    {h[0]}  ← 「{h[1]}」")
if not hits:
    print("    （无命中：需向客户确认「6 表」具体清单）")

print()
print("=== 阈值定义面扫描（QUALITY_RULES 内是否含阈值字段）===")
having_threshold = []
for rid, r in m.QUALITY_RULES.items():
    if any(k in r for k in ("threshold", "阈值", "tolerance", "limit")):
        having_threshold.append(rid)
print(f"  含阈值字段的规则: {having_threshold if having_threshold else '（无）'}")
print(f"  规则字段集样例: {sorted(m.QUALITY_RULES['gap_heal'].keys())}")