# -*- coding: utf-8 -*-
"""生成 qfq_frozen_bounds.json —— per-code 冻结边界（settle 乙件①）。

依据：
  · 总调度 2026-11-10：frozen_before = per-code 终确认（97.2% 锚定精度）
  · 边界语义：各 code 在**源端**（MCP ≡ QuestDB，P0 同源定理）可得的最早交易日
  · 本件**只生成数据文件，不改任何判据**（可逆）

输出结构：
{
  "_meta": {...},
  "STOCK": {"stock_minutes": {"000001": 1234567890000, ...}, "stock_daily": {...}},
  "ETF":   {...}
}
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
OUT = ROOT / "data" / "qfq_frozen_bounds.json"
CST = timezone(timedelta(hours=8))
QDB = "http://127.0.0.1:9000"

JOBS = [
    ("STOCK", "stock_minutes", "stock_minutes", "trade_time"),
    ("STOCK", "stock_daily", "stock_daily", "trade_date"),
    ("ETF", "etf_minutes", "etf_minutes", "trade_time"),
    ("ETF", "etf_daily", "etf_daily", "trade_date"),
]


def q(sql: str, timeout: int = 600):
    url = f"{QDB}/exec?" + urllib.parse.urlencode({"query": sql})
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def to_ms(v) -> int | None:
    if v is None:
        return None
    s = str(v).replace("Z", "")
    try:
        dt = datetime.fromisoformat(s).replace(tzinfo=CST)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def main() -> int:
    print("=" * 78)
    print("生成 per-code 冻结边界（源端 = QuestDB ≡ MCP，P0 同源定理）")
    print("=" * 78)

    bounds: dict[str, dict[str, dict[str, int]]] = {"STOCK": {}, "ETF": {}}
    counts = {}

    for asset, table, qtable, tcol in JOBS:
        print(f"\n  [{asset}/{table}] 查 {qtable} 的 per-code 首日 ...")
        t0 = datetime.now()
        d = q(f"SELECT ts_code, min({tcol}) FROM {qtable} GROUP BY ts_code")
        rows = d.get("dataset", [])
        m: dict[str, int] = {}
        bad = 0
        for ts, mn in rows:
            code = str(ts).split(".")[0]
            v = to_ms(mn)
            if v is None:
                bad += 1
                continue
            m[code] = v
        bounds[asset][table] = m
        counts[f"{asset}/{table}"] = {"codes": len(m), "unparsed": bad}
        el = (datetime.now() - t0).total_seconds()
        print(f"    ✅ {len(m):,} 个 code（无法解析 {bad}）  耗时 {el:.1f}s")

    # 汇总统计
    payload = {
        "_meta": {
            "purpose": (
                "QFQ settle 冻结边界（per-code）：各 code 在源端可得的最早交易日。"
                "早于该日期的缺行不计入 rebase 的 missing_target（丙方案·历史冻结）。"
            ),
            "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
            "generator": "scripts/gen_frozen_bounds.py",
            "routing_ruling": (
                "总调度 2026-11-10：frozen_before = per-code 终确认"
                "（97.2% 锚定精度为据；「同日不同 code 观感不一致」非缺陷）"
            ),
            "source": (
                "QuestDB 127.0.0.1:9000（P0 同源定理：MCP ≡ QuestDB 远程代理，"
                "故源端边界 = QuestDB 边界）"
            ),
            "consumed_by": "qfq_reanchor_engine._resolve_frozen_before（**尚未接入**，待 P3/P4 闸门）",
            "counts": counts,
            "invariants": [
                "本文件只增不改：已登记的边界不得前移（前移=扩大豁免=放松判据）",
                "边界必须来自实测，禁止手填",
                "缺失该 code 时 fail-closed（不豁免）",
                "环境变量 QS_QFQ_FROZEN_BEFORE=0 时全文件失效（一键退回严格模式）",
            ],
        },
        "bounds": bounds,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")

    sz = OUT.stat().st_size / 1024
    print("\n" + "=" * 78)
    print("产出")
    print("=" * 78)
    print(f"  文件: {OUT.name}  大小 {sz:.1f}KB")
    total = sum(v["codes"] for v in counts.values())
    print(f"  总条目: {total:,}")
    for k, v in counts.items():
        print(f"    {k:<22} {v['codes']:>6,} codes")
    if sz > 400:
        print(f"\n  ⚠ 文件超 400KB（预估 260KB），请复核是否异常")
    else:
        print(f"\n  ✅ 大小在预估范围内（约 260KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
