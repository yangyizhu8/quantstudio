
import json, os, io
ROOT = r"D:\miniQMT策略实盘\QuantStudio"

new_a5 = ("【基准单日缺口】CSI300 在 2026-08-03 无 index_daily 数据（窗口 287 开市日中 286 天有数据，"
          "缺口仅此 1 天，探针 probe_gap_detail2.py 实测）。两处影响："
          "① 基准净值曲线：该日基准收盘缺失 → 引擎回落 first_bench 兜底（backtest_engine.py:2303/609），"
          "该日基准 NAV 显示为 100，前后两日正常（曲线出现 1 个尖点，不影响策略买卖）；"
          "② 择时门：受影响的是**执行日 D = 2026-08-04**（其信号日 S = 2026-08-03 正是缺失日）——"
          "该日择时门消费的指数末行为 2026-07-31（比信号日旧 1 个交易日，无穿越）；"
          "其余执行日不受影响（如 D = 2026-08-03 的信号日 S = 2026-07-31，消费末行即 2026-07-31，正常）。"
          "D4-A 裁定接受为已知近似。")

p = os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "agent_strategy_design.json")
j = json.load(open(p, encoding="utf-8"))
for a in j["approximations"]:
    if a["id"] == "A-5":
        a["description"] = new_a5
open(p, "w", encoding="utf-8").write(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
print("design A-5 updated")

# patch the two markdown files
targets = [
  os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "R2_5_CONFIRMATION_PACKAGE.md"),
  os.path.join(ROOT, "output", "generated_strategies", "ou_reversal_csi300_10", "R2_AGENT_COMPONENT_PLAN.md"),
]
old_variants = [
  "| A-5 | 沪深300 指数 2026-08-03 缺一天数据 | 基准曲线该日跳一下；择时门该日用 7-31 读数（不穿越）；仅 1 天 |",
  "| A-5 | 基准 2026-08-03 单日缺口（D4-A） |",
]
new_variants = [
  "| A-5 | 沪深300 指数 2026-08-03 缺一天数据（窗口 287 开市日中 286 天有数据，缺口仅此 1 天） | ① 基准曲线该日回落到 100（尖点），不影响买卖；② 受影响的是**执行日 2026-08-04**（信号日 08-03 缺失）→ 择时门消费末行为 2026-07-31（旧 1 个交易日，无穿越）；其余执行日不受影响 |",
  "| A-5 | 基准 2026-08-03 单日缺口（D4-A）——受影响执行日 = 2026-08-04（信号日 08-03 缺失 → 消费末行 07-31） |",
]
for t, ov, nv in zip(targets, old_variants, new_variants):
    txt = open(t, encoding="utf-8").read()
    if ov in txt:
        open(t, "w", encoding="utf-8").write(txt.replace(ov, nv))
        print("patched:", os.path.basename(t))
    else:
        print("ANCHOR NOT FOUND in", os.path.basename(t))
