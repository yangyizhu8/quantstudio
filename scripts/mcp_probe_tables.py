"""MCP 采集任务扩展 — 云端表探测脚本（修复3 步骤1）。

对候选的 8 张 QuantStudio canonical 表逐一调用 MCPClient.query_snapshot(limit=1)，
探测云端 QuestDB 真实源表名，输出探测结果供回填 _CANONICAL_TO_QUESTDB /
_MCP_SUPPORTED / collector_tasks.json / alignment_rules.json。

用法（需先配置 MCP API Key，见 source_tab MCP 卡片 → 写 config/secrets.env）：
    python scripts/mcp_probe_tables.py

输出：
    - 控制台逐表结果（EXISTS / MISSING / ERROR）
    - 写入 docs/mcp_migration/table_mapping_expanded.md 的探测结果章节（追加）
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# 让脚本可直接以仓库根运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.mcp.client import MCPClient, load_mcp_api_key  # noqa: E402

logger = logging.getLogger("mcp_probe")

# 待探测的 8 张表（canonical 名 → 任务书建议候选 QuestDB 源表名）
CANDIDATE_TABLES = [
    ("stock_daily_valuation", "stock_daily_basic"),
    ("index_constituents", "index_weight"),
    ("sw_industry", "sw_daily"),
    ("industry_classification", "sw_classify"),
    ("industry_membership", "sw_weight"),
    ("stock_float_share", "stock_float_share"),
    ("stock_namechange", "stock_namechange"),
    ("stock_delist", "stock_delist"),
]


def probe_one(client: MCPClient, canonical: str, candidate: str) -> dict:
    """探测单表：先试 canonical 名，再试候选名。"""
    for name in (canonical, candidate):
        try:
            page = client.query_snapshot(dataset_id=name, limit=1)
            rows = getattr(page, "rows", None) or []
            if rows:
                return {"canonical": canonical, "questdb": name,
                        "status": "EXISTS", "sample": rows[0]}
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    return {"canonical": canonical, "questdb": candidate,
            "status": "MISSING", "error": locals().get("last_err", "unknown")}


def main():
    key = load_mcp_api_key()
    if not key:
        print("[FAIL] 未找到 MCP_API_KEY（请先在 GUI 数据源页 MCP 卡片填写并保存，"
              "或设置环境变量 MCP_API_KEY）")
        sys.exit(2)
    client = MCPClient(api_key=key)
    print(f"探测 {len(CANDIDATE_TABLES)} 张表（query_snapshot limit=1）...\n")
    results = []
    for canonical, candidate in CANDIDATE_TABLES:
        r = probe_one(client, canonical, candidate)
        results.append(r)
        print(f"  [{r['status']:7}] {canonical:28} -> {r['questdb']}")

    # 输出 JSON 摘要
    out = ROOT / "docs" / "mcp_migration" / "probe_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n探测结果已写入: {out}")

    exists = [r for r in results if r["status"] == "EXISTS"]
    missing = [r for r in results if r["status"] != "EXISTS"]
    print(f"\n存在的表: {len(exists)}  缺失/报错: {len(missing)}")
    if missing:
        print("缺失（记录为 gap，不进配置）:")
        for r in missing:
            print(f"  - {r['canonical']} (候选 {r['questdb']})")


if __name__ == "__main__":
    main()
