"""验收④：T1 窗口变更的**产物等价性**（分批策略变更不得改变最终产物）。

判据：`_export_batches` 产出必须满足
  ① 覆盖完整 [start, end] 无空洞；
  ② 每批「服务端多导的边界数据」由 `_fetch_export` 的日期裁剪兜底 ⇒
     并集裁剪后 == 单批裁剪后（集合等价）。
本脚本以**符号化验证**（不联网）：对多组 (start, end, is_minute, est_rows)
枚举窗口切分，逐日检查覆盖率与批数与窗口关系，并验证新窗口下每批行数估算均 < 5M。
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter  # noqa: E402


class _A(MCPAdapter):
    def __init__(self):
        pass


a = _A()
LIMIT = MCPAdapter._EXPORT_ROW_LIMIT_BIG

CASES = [
    ("stock_minutes", 480_000_000, True, "2026-01-01", "2026-09-22"),
    ("etf_minutes", 120_000_000, True, "2026-01-01", "2026-09-22"),
    ("stock_minutes", 480_000_000, True, "2025-01-01", "2026-09-22"),
    ("stock_float_share", 14_300_000, False, "2024-01-01", "2026-09-23"),
    ("stock_daily", 14_000_000, False, "2025-01-01", "2026-09-07"),
]

print("=== 覆盖完整性与批数 ===")
fallback_calls = 0
for name, est, is_min, s, e in CASES:
    b = a._export_batches(s, e, is_min, est_rows=est, table=name)
    sd = datetime.strptime(s, "%Y-%m-%d").date()
    ed = datetime.strptime(e, "%Y-%m-%d").date()
    # ① 覆盖：拼出所有 [bs, be] 区间的并集是否覆盖每一天
    covered = set()
    for bs, be in b:
        cur = datetime.strptime(bs, "%Y-%m-%d").date()
        last = datetime.strptime(be, "%Y-%m-%d").date()
        while cur <= last:
            covered.add(cur)
            cur += timedelta(days=1)
    full = {sd + timedelta(days=i) for i in range((ed - sd).days + 1)}
    missing = full - covered
    span_max = max((datetime.strptime(be, "%Y-%m-%d")
                    - datetime.strptime(bs, "%Y-%m-%d")).days for bs, be in b)
    # ② 每批估算行数 = 日行数 × 批内日历天数（= 跨度天数，不含实现里已含的 1.2 余量系数）
    daily = est / 243
    est_per_batch = daily * span_max
    status = "OK" if not missing else f"MISSING {len(missing)}d"
    print(f"  {name:18} {s}~{e}: 批={len(b):3}  窗口={span_max:3}d  "
          f"批估算≈{est_per_batch/1e6:5.2f}M  含余量≈{est_per_batch*1.2/1e6:5.2f}M  覆盖={status}")
    assert not missing, f"{name} 存在空洞 {sorted(missing)[:3]}"
    # 预算约束只对本批**显式声明**的表生效（_ROW_LIMIT_BUDGET_TABLES）；未声明表
    # （stock_daily 等）沿用既有 365 天窗——其单批超 5M 属**既有状况**，本批刻意不动
    # （避免以「防潜在截断」为名改动在产行为；已在方案 §二/§四 声明为不改动范围）。
    if name in MCPAdapter._ROW_LIMIT_BUDGET_TABLES:
        assert est_per_batch * 1.2 < LIMIT, (
            f"{name} 批估算×余量 {est_per_batch*1.2/1e6:.2f}M 超 5M 上限")
    else:
        print(f"      （未声明表：预算约束不适用，沿用既有窗口 —— 既有行为保持）")

print("\n=== 每批边界不重叠且首尾严格衔接 ===")
for name, est, is_min, s, e in CASES:
    b = a._export_batches(s, e, is_min, est_rows=est, table=name)
    assert b[0][0] == s, (name, b[0][0], s)
    assert b[-1][1] == e, (name, b[-1][1], e)
    for (_, e1), (s2, _) in zip(b, b[1:]):
        gap = (datetime.strptime(s2, "%Y-%m-%d")
               - datetime.strptime(e1, "%Y-%m-%d")).days
        assert gap == 1, f"{name} 相邻批 {e1}->{s2} 间隔 {gap} 天（应 =1）"
    print(f"  {name:18} 衔接 OK（{len(b)} 批，首={b[0]} 尾={b[-1]}）")

print("\n=== 单批（小表）路径不受影响 ===")
b_small = a._export_batches("2024-01-01", "2026-09-23", False, est_rows=250_000,
                            table="stock_float_share")
assert b_small == [("2024-01-01", "2026-09-23")], b_small
print("  index_constituents 级别小表 -> 单批 OK")

print("\n=== 回退护栏：cfg 置 0 恢复旧行为 ===")
a.minute_export_window_days = 0
a.minute_export_target_rows = 0
try:
    b_old = a._export_batches("2026-01-01", "2026-01-11", True,
                              est_rows=480_000_000, table="stock_minutes")
    span = max((datetime.strptime(be, "%Y-%m-%d") - datetime.strptime(bs, "%Y-%m-%d")).days
               for bs, be in b_old)
    print(f"  cfg=0 -> 最大跨度={span}d（旧行为：纯行数约束 2 天）")
    assert span == 2, span
finally:
    del a.minute_export_window_days
    del a.minute_export_target_rows

print("\nPASS：窗口变更不改变产物覆盖面（无空洞/无重叠/无遗漏），且回退护栏有效")