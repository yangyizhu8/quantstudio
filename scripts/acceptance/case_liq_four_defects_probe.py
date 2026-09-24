"""客户新案（李梓嘉不会章鱼杀）四缺陷 —— 实验体证（只读，不改生产代码）。

缺陷①：异常静默降级 PASS   → 实验 B（制造检查异常，看 verdict）
缺陷②：空输出当检出        → 实验 A（空 stdout 判定式真值）
缺陷③：规则库缺 6 表行数/水位 → 已由 case_liq_rulelib_probe.py 取证（7 规则 0 阈值字段）
缺陷④：时机/通道与 daemon 锁 → 实验 C（只读连接 vs 库状态）
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("qo", ROOT / "scripts" / "quality_orchestrator.py")
qo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qo)

print("=" * 72)
print("实验 A【缺陷②：空输出当检出】复刻 orchestrator 判定式")
print("=" * 72)
# orchestrator L108 判定式： result["detected"] = "gap=0" not in output
for label, output in (
    ("正常无缺口（脚本输出含 gap=0）", "[EpsBackfill] check: gap=0 (OK（免疫闭环）)"),
    ("正常有缺口（输出含 gap=5）",     "[EpsBackfill] check: gap=5 (GAP（回补未生效/源端变化）)"),
    ("**脚本崩溃/超时/缺库（空输出）**", ""),
    ("缺库报错（仅 stderr，stdout 空）", ""),
):
    detected = "gap=0" not in output
    verdict = "检出(FAIL?)" if detected else "未检出"
    print(f"  {label:34s} stdout={output[:38]!r:42s} ⇒ detected={detected} ({verdict})")
print("  ⇒ 空输出被判「检出」= 假阳性；且 exit_code / stderr 均未参与判定。")
# 用真实脚本制造「空 stdout」：不存在的库（真实走 exit 2 + stderr）
fake_db = str(ROOT / "data" / "__no_such_db__.db")
proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "backfill_eps_gap.py"),
                       "--check", "--db", fake_db],
                      capture_output=True, text=True, timeout=60)
print(f"\n  真实复现：--db 指向不存在库 ⇒ returncode={proc.returncode} "
      f"stdout={proc.stdout.strip()!r} stderr={proc.stderr.strip()[:60]!r}")
detected = "gap=0" not in proc.stdout.strip()
print(f"  orchestrator 判定式对该真实失败的结果：detected={detected} ⇒ "
      f"{'**假阳性（把运行失败当缺口检出）**' if detected else '未检出'}")
print(f"  注意：orchestrator 把 returncode 存进 result['exit_code'] 但**从不校验**"
      f"（{proc.returncode} 非 0 仍按输出文本判）")

print()
print("=" * 72)
print("实验 B【缺陷①：异常静默降级 PASS】")
print("=" * 72)
# 复刻 run_check 的异常路径（QO L132-134）
def run_check_minimal(rule_id, raise_exc=False):
    result = {"rule": rule_id, "level": "L1", "detected": False, "details": ""}
    try:
        if raise_exc:
            raise RuntimeError("模拟 gap_heal 锁崩溃")
        result["detected"] = False
        result["details"] = "正常"
    except Exception as e:
        result["detected"] = False              # ← QO 原实现：异常也置 False
        result["details"] = f"检查异常: {e}"
    return result

r = run_check_minimal("gap_heal", raise_exc=True)
detected_any = r.get("detected")
verdict = "FAIL" if (detected_any and r["level"] == "L1") else ("WARN" if detected_any else "PASS")
print(f"  规则异常时：detected={r['detected']}  details={r['details']!r}")
print(f"  ⇒ generate_report 判定：verdict={verdict}  ← 异常被降级成 PASS（缺陷①成立）")

print()
print("=== 实验 B 补充：未实现规则的结构性无检查 ===")
impl_rule_ids = []
import inspect
src = inspect.getsource(qo.run_check)
for rid in qo.QUALITY_RULES:
    if f'"{rid}"' in src or f"'{rid}'" in src:
        impl_rule_ids.append(rid)
print(f"  规则总数={len(qo.QUALITY_RULES)}；run_check 内出现 if/elif 分支的规则={impl_rule_ids}")
missing = [r for r in qo.QUALITY_RULES if r not in impl_rule_ids]
print(f"  **无任何检查分支的规则**（结构性恒不检出）：{missing}")
print("  ⇒ 这些规则仅存在于 QUALITY_RULES 声明（服务 --list-rules 与报告 total_rules），"
      "实际永不执行检测，且报告计入 total_rules 造成「覆盖完整」的错觉。")

print()
print("=" * 72)
print("实验 C【缺陷④：时机/通道与 daemon 锁冲突】")
print("=" * 72)
db = ROOT / "data" / "quantstudio.db"
print(f"  生产库: {db}  exists={db.exists()}  "
      f"size={db.stat().st_size/1024/1024/1024:.2f} GB" if db.exists() else "  (无库)")
# 巡检期持有的连接角色（QO L97 / L105）
print("  巡检期打开连接的方式：")
print("    gap_heal      : duckdb.connect(..., read_only=True)   ← 只读")
print("    eps_backfill  : subprocess → backfill_eps_gap --check → read_only=True  ← 只读")
print("    cloud_parity  : subprocess 外部脚本（透传，连接方式不由本模块决定）")
print("  与 daemon 的关系：daemon 为唯一写者并持独占（）；DuckDB 1.5.x/Windows 下")
print("    **库被任一进程打开时（含只读）其他进程以读写打开立即失败**（既有实测，")
print("    见项目记忆 duckdb 跨进程锁事实）⇒ 巡检在 daemon 活跃期运行必遇锁冲突；")
print("    且若巡检「写」路径（L1 自动修复）在 daemon 活跃期执行，冲突面更大。")
print("  ⇒ 缺陷④成立：模块**无时机/通道设计**（无空档窗协议、无锁探测、无与 daemon 的排程耦合）")