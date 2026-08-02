"""One-off audit: canonical schema vs mcp column_map vs QuestDB actual columns."""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

QDB = "http://127.0.0.1:9000/exec?query="


def qdb_columns(table):
    q = f"SELECT * FROM table_columns('{table}')"
    with urllib.request.urlopen(QDB + urllib.parse.quote(q), timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [row[0] for row in data["dataset"]]


def qdb_exists(table):
    q = f"SELECT count() FROM tables() WHERE table_name = '{table}'"
    with urllib.request.urlopen(QDB + urllib.parse.quote(q), timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["dataset"][0][0] == 1


from quantstudio.pipeline.sources.mcp_adapter import (  # noqa: E402
    _CANONICAL_TO_QUESTDB, _MCP_SUPPORTED, _PASSTHROUGH_TABLES,
    _PASSTHROUGH_BIG_TABLES)

cfg = json.loads((ROOT / "config/profiles/mcp_only/alignment_rules.json")
                 .read_text(encoding="utf-8"))
schemas = cfg["schemas"]
mcp_maps = {k: v for k, v in cfg["source_mappings"]["mcp"].items()
            if not k.startswith("_")}

# 管线派生字段（不由 column_map 提供，不算缺失）
DERIVED = {
    "adj_factor", "div_proc",
    "open_front", "high_front", "low_front", "close_front",
    "open_back", "high_back", "low_back", "close_back",
    "open_front_ratio", "high_front_ratio", "low_front_ratio",
    "close_front_ratio", "open_back_ratio", "high_back_ratio",
    "low_back_ratio", "close_back_ratio",
    "turn", "isST", "dividend_type", "update_time",
    "is_st_reliable", "is_st_reliable_source",
    "is_delisting_risk", "is_delisting_risk_source", "data_source",
    "suspendFlag", "settelementPrice", "openInterest",
}

tasks_cfg = json.loads((ROOT / "config/profiles/mcp_only/collector_tasks.json")
                       .read_text(encoding="utf-8"))
task_tables = {}
for t in tasks_cfg["tasks"]:
    task_tables.setdefault(t["table"], []).append(
        (t["name"], t.get("enabled", True)))

print("=" * 70)
print("A. 映射表审计（17 张 source_mappings.mcp）")
print("=" * 70)
for table in sorted(mcp_maps):
    src = _CANONICAL_TO_QUESTDB.get(table, table)
    m = mcp_maps[table]
    if not qdb_exists(src):
        print(f"[ERROR] {table}: QuestDB 源表 {src} 不存在!")
        continue
    qcols = set(qdb_columns(src))
    schema = schemas.get(table, {}).get("columns", {})
    required = {c for c, spec in schema.items() if spec.get("required")}
    optional = set(schema) - required
    if m.get("identity"):
        covered = {c for c in schema if c in qcols}
        stale = set()
    else:
        cmap = m.get("column_map", {})
        stale = set(cmap.keys()) - qcols
        covered = set(cmap.values())
    miss_req = required - covered - DERIVED
    miss_opt = optional - covered - DERIVED
    unmapped_src = qcols - set(m.get("column_map", {}).keys()) - {"ingest_time"}
    has_task = table in task_tables
    flag = "OK"
    if miss_req or stale:
        flag = "ERROR"
    elif miss_opt:
        flag = "WARN"
    print(f"\n[{flag}] {table} <- {src} (task={'Y' if has_task else 'N'})")
    if miss_req:
        print(f"  必填列无映射来源: {sorted(miss_req)}")
    if stale:
        print(f"  column_map 引用了 QuestDB 不存在的列: {sorted(stale)}")
    if miss_opt:
        print(f"  可选列未覆盖: {sorted(miss_opt)}")
    if unmapped_src and not m.get("identity"):
        print(f"  QuestDB 列未映射(参考): {sorted(unmapped_src)}")

print()
print("=" * 70)
print("B. _MCP_SUPPORTED 有但无 source_mappings.mcp 映射的表")
print("=" * 70)
for (tbl, freq), src in sorted(_MCP_SUPPORTED.items()):
    if tbl not in mcp_maps and tbl not in _PASSTHROUGH_TABLES:
        print(f"  {tbl}/{freq} <- {src} (task={'Y' if tbl in task_tables else 'N'})")

print()
print("=" * 70)
print("C. collector_tasks 任务覆盖核对")
print("=" * 70)
for tbl, lst in sorted(task_tables.items()):
    in_supported = any(t == tbl for (t, f) in _MCP_SUPPORTED)
    in_passthrough = tbl in _PASSTHROUGH_TABLES
    names = ",".join(f"{n}({'on' if e else 'off'})" for n, e in lst)
    if not in_supported and not in_passthrough:
        print(f"  [ERROR] task 表 {tbl} 不在 _MCP_SUPPORTED 也不在 passthrough: {names}")
    else:
        kind = "passthrough" if in_passthrough else "mapped"
        big = " BIG" if tbl in _PASSTHROUGH_BIG_TABLES else ""
        print(f"  [{kind}{big}] {tbl}: {names}")

print()
print("=" * 70)
print("D. passthrough 表在 QuestDB 的存在性")
print("=" * 70)
missing = [t for t in sorted(_PASSTHROUGH_TABLES) if not qdb_exists(t)]
print(f"  passthrough 共 {len(_PASSTHROUGH_TABLES)} 张, QuestDB 缺失: {missing}")
