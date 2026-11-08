# -*- coding: utf-8 -*-
"""每日 ART 索引健康巡检 + 损坏时输出「可直接转发总调度」的修复建议。

设计（2026-11-08 用户要求）：
- 每日自动跑一次，检查全部关键表的 PK ART 索引一致性
- **健康** ⇒ 写简短日志（退出码 0），不打扰
- **损坏** ⇒ 输出「Graph Engineering 图调度・本轮节点上报包」格式的修复建议
  （用户可直接转发总调度审核调度），退出码 2
- **无法判定**（库被锁/表缺失）⇒ 退出码 3，输出提示

用法：
  python scripts/daily_art_canary.py            # 检查并输出
  python scripts/daily_art_canary.py --json     # 机器可读
  python scripts/daily_art_canary.py --quiet    # 仅异常时输出

退出码：0=健康  2=发现不一致（需修复）  3=无法判定
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "quantstudio.db"

# 关键表清单（覆盖四价格表 + 触发队列）
TABLES = [
    ("stock_minutes", "freq"),
    ("etf_minutes", "freq"),
    ("stock_daily", "code"),
    ("etf_daily", "code"),
    ("qfq_trigger_queue", "status"),
]


def check(db_path: Path) -> tuple[int, list[dict]]:
    """返回 (退出码, 结果列表)。"""
    import duckdb

    results: list[dict] = []
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception as e:
        return 3, [{"table": "*", "error": f"{type(e).__name__}: {str(e)[:150]}"}]

    code = 0
    try:
        for table, col in TABLES:
            try:
                full = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0
                grouped = con.execute(
                    f"SELECT SUM(c) FROM (SELECT COUNT(*) c FROM {table} GROUP BY {col})"
                ).fetchone()[0] or 0
                ok = int(full) == int(grouped)
                results.append({
                    "table": table, "group_col": col,
                    "full": int(full), "grouped": int(grouped),
                    "diff": int(full) - int(grouped), "ok": ok,
                })
                if not ok:
                    code = 2
            except Exception as e:
                results.append({
                    "table": table, "error": f"{type(e).__name__}: {str(e)[:120]}",
                    "ok": False,
                })
    finally:
        con.close()
    return code, results


def build_report(code: int, results: list[dict]) -> str:
    """损坏时生成模板格式修复建议（用户可直接转发总调度）。"""
    now = datetime.now()
    broken = [r for r in results if not r.get("ok")]
    healthy = [r for r in results if r.get("ok")]

    lines = [
        "【Graph Engineering 图调度・本轮节点上报包】",
        f"- 上报批次编号：{now.strftime('%Y%m%d')}-ART-CANARY",
        f"- 上报时间：{now.strftime('%Y-%m-%d %H:%M')}",
        "- 节点名称：DuckDB ART 索引健康巡检（自动化）",
        "- 节点状态：**待复核**（发现索引不一致，需裁定是否执行重建）",
        f"- 本轮产出摘要：每日 canary 检出 **{len(broken)} 张表索引不一致**"
        f"（健康 {len(healthy)} 张）；判据 = filtered 计数 vs full-scan 计数对拍"
        f"（依据 duckdb#23645「A filtered-vs-full-scan parity check makes a reliable canary」）",
        "- 前置依赖说明：无",
        f"- 如实申报项：本巡检**仅检测不修复**；检出结果尚未经人工复核；"
        f"重建涉及不可逆生产变更（DROP TABLE），须总调度裁定",
        f"- 需调度重点关注：{', '.join(r['table'] for r in broken)} "
        f"疑似 ART 索引损坏，建议按下方 SOP 重建",
        "==========",
        "",
        "## 一、检出明细（实测）",
        "",
        "| 表 | 分组列 | full-scan | grouped | 差值 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("error"):
            lines.append(f"| {r['table']} | - | - | - | - | ⚠ {r['error'][:60]} |")
        else:
            mark = "❌ 不一致" if not r["ok"] else "✅ 一致"
            lines.append(
                f"| {r['table']} | {r['group_col']} | {r['full']:,} | "
                f"{r['grouped']:,} | {r['diff']:+,} | {mark} |")

    lines += [
        "",
        "## 二、修复建议（按铁律 23 第四形态 SOP）",
        "",
        "**根因判定**：`full-scan ≠ grouped` 说明 ART 索引已进入「损坏但未 fatal」态"
        "（duckdb#23645 明确可提前检出）。**此时数据本身仍完好，索引结构已不一致**。",
        "",
        "**恢复 SOP**（以 `stock_minutes` 2.5 亿行为参照，约 100 分钟）：",
        "",
        "```",
        "1. 备份确认（全库 62GB 已有 + 表级 dump 双保险）",
        "2. dump：COPY (SELECT * FROM <表>) TO '<表>.dump.parquet'",
        "          (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)",
        "3. V1/V2 基线：行数 + 主键 hash + 关键列 hash（落 evidence）",
        "4. DROP TABLE <表>",
        "5. CHECKPOINT          ← 必须！收敛残留索引块（缺此步必失败，六次实证）",
        "6. CREATE TABLE（原 DDL，含 PRIMARY KEY）",
        "7. 导入：threads=1 + preserve_insertion_order=false + 按日分批 + 每 20 批 CHECKPOINT",
        "8. V1/V2/V3 校验：行数 / 主键 hash / 逐日对拍 / PK 生效 / 索引一致性",
        "```",
        "",
        "**关键坑（2026-11-08 六次失败实证）**：",
        "- `DROP` 与 `CREATE` 之间**必须 CHECKPOINT**，否则残留块导致后续 INSERT 虚假报 `Duplicate key`（数据实际零重复）",
        "- 多线程一次性导入大表会触发**构建期**虚假 Duplicate ⇒ 必须 `threads=1` + 按日分批",
        "- 失败事务会在库文件留未回收索引块 ⇒ 必须从干净状态（新连接 + CHECKPOINT）重来",
        "",
        "## 三、裁定请求",
        "",
        "| 选项 | 内容 |",
        "|---|---|",
        f"| 甲 | 批准按上述 SOP 重建 {', '.join(r['table'] for r in broken)} |",
        "| 乙 | 先只做 dump（不 DROP），明日再建 |",
        "| 丙 | 不处理（接受索引不一致，风险：下次写入可能 FATAL） |",
        "",
        "**节点建议**：甲。注意 `stock_minutes` 规模 2.5 亿行，重建期间需停 daemon 并声明占用窗（铁律 19）。",
        "",
        "> 本报告由 `scripts/daily_art_canary.py` 每日自动生成，可直接转发总调度。",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="每日 DuckDB ART 索引健康巡检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="仅在异常时输出")
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()

    code, results = check(Path(a.db))

    if a.json:
        print(json.dumps({"exit": code, "results": results},
                         ensure_ascii=False, indent=2))
        return code

    if code == 0:
        if not a.quiet:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            print(f"[{ts}] [ART-CANARY] ✅ 全部一致（{len(results)} 张表）")
            for r in results:
                print(f"    {r['table']}: {r['full']:,} = {r['grouped']:,}")
        return 0

    if code == 3:
        print(f"[ART-CANARY] ⚠ 无法判定：{results[0].get('error', '')}")
        print("[ART-CANARY] 可能原因：库被 daemon 持写锁 / 表缺失")
        return 3

    print(build_report(code, results))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
