# -*- coding: utf-8 -*-
"""按裁 (a) 生成 QFQ 门禁负例白名单（known_violations）。

依据：170 对「晚于源端首日」的例外经逐对探测，**全部 QuestDB 0 行** ⇒ 不可修复。
格式：可机读 JSON，供 rebase 判据未来消费（当前不接入，仅登记）。
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
SRC = ROOT / "docs" / "evidence" / "exception-170-probe-20261110.json"
OUT = ROOT / "data" / "qfq_known_violations.json"
CST = timezone(timedelta(hours=8))

probe = json.loads(SRC.read_text(encoding="utf-8"))
no_src = probe["no_source"]
print(f"源：{len(no_src)} 条无源例外")

# 按 (asset, table, code) 聚合，存日期列表
agg = defaultdict(list)
for e in no_src:
    agg[("STOCK", "stock_daily", e["code"])].append(e["day"])

entries = []
for (asset, table, code), days in sorted(agg.items()):
    entries.append({
        "asset": asset,
        "table": table,
        "code": code,
        "days": sorted(days),
        "day_count": len(days),
        "reason": "source_absent",
        "evidence": f"QuestDB {table} 对上述日期逐对探测均返回 0 行",
        "first_day_in_source": next(e["first_day"] for e in no_src if e["code"] == code),
        "probed_at": probe.get("generated_at"),
    })

payload = {
    "_meta": {
        "purpose": (
            "QFQ rebase 门禁负例白名单（known_violations）：这些 (code, 日期) 的缺口"
            "在**源端也不存在**，属不可修复的历史债。登记后可被 rebase 判据消费，"
            "使 missing_target 不计入这些组合（丙方案·历史冻结的配套件）。"
        ),
        "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "generator": "scripts/probe_170_exceptions.py → 本脚本聚合",
        "source_evidence": str(SRC.relative_to(ROOT)),
        "routing_ruling": "总调度 2026-11-10 裁 (a)：有源则逐对回填、无源则入本白名单",
        "consumed_by": "qfq_reanchor_engine（**尚未接入**，需 P1-P3 闸门满足后实施）",
        "counts": {
            "total_pairs": len(no_src),
            "total_codes": len(agg),
            "by_asset": {"STOCK": len(no_src)},
        },
        "invariants": [
            "本文件只增不改：已登记项不得删除（历史债是事实）",
            "新增项必须有 QuestDB 0 行的实测证据",
            "消费方必须 fail-closed：拿不到本文件 ⇒ 不豁免",
        ],
    },
    "entries": entries,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\n✅ 已写入 {OUT.name}")
print(f"   {len(entries)} 个 (asset,table,code) 条目，覆盖 {len(no_src)} 个 (code,日期) 对")
print(f"   文件大小 {OUT.stat().st_size/1024:.1f}KB")
print("\n条目样例（前 5）:")
for e in entries[:5]:
    print(f"  {e['code']} {e['table']}: {e['day_count']} 天 (首日 {e['first_day_in_source']})")
