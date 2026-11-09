# -*- coding: utf-8 -*-
"""全量探测：死信的 (code, 日期) 中，QuestDB 实际有数据的比例。

输出真实可补量，作为回填基础（替代「日期边界」粗过滤）。
"""
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import duckdb

QDB = "http://127.0.0.1:9000"
PROD = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
CST = timezone(timedelta(hours=8))
OUT = r"D:\miniQMT策略实盘\QuantStudio\docs\evidence\qfq-gap-probe-20261110.json"


def qdb_codes_on(table: str, day: str, tcol: str = "trade_time") -> set:
    """返回该日 QuestDB 有的 ts_code 集合（去后缀）。"""
    sql = (f"SELECT DISTINCT ts_code FROM {table} "
           f"WHERE {tcol} >= '{day}' AND {tcol} < dateadd('d',1,'{day}')")
    url = f"{QDB}/exec?" + urllib.parse.urlencode({"query": sql})
    with urllib.request.urlopen(url, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    out = set()
    for row in d.get("dataset", []):
        s = str(row[0])
        out.add(s.split(".")[0])
    return out


con = duckdb.connect(PROD, read_only=True)

JOBS = [
    ("STOCK", "minute", "stock_minutes", "trade_time", "%分钟%覆盖不全%"),
    ("ETF", "minute", "etf_minutes", "trade_time", "%分钟%覆盖不全%"),
    ("STOCK", "daily", "stock_daily", "trade_date", "%日线覆盖不全%"),
    ("ETF", "daily", "etf_daily", "trade_date", "%日线覆盖不全%"),
]

result = {}
t_all = time.time()

for asset, kind, table, tcol, pat in JOBS:
    print("=" * 74)
    print(f"[{asset} {kind}] 探测 {table}")
    print("=" * 74)

    rows = con.execute("""
        SELECT CAST(to_timestamp(effective_date/1000) AS DATE) dd, code
        FROM qfq_trigger_queue
        WHERE status='dead_letter' AND asset_type=? AND last_error LIKE ?
        GROUP BY 1,2 ORDER BY 1
    """, [asset, pat]).fetchall()

    by_day = defaultdict(set)
    for dd, code in rows:
        by_day[str(dd)].add(str(code))

    total_pairs = len(rows)
    fillable = []
    no_source = []
    t0 = time.time()
    for i, (day, codes) in enumerate(sorted(by_day.items()), 1):
        try:
            have = qdb_codes_on(table, day, tcol)
        except Exception:
            have = set()
        inter = codes & have
        if inter:
            fillable.append({"day": day, "codes": sorted(inter)})
        miss = codes - have
        if miss:
            no_source.append({"day": day, "codes": sorted(miss)})
        if i % 200 == 0:
            print(f"  [{i}/{len(by_day)}] 已用 {time.time()-t0:.0f}s "
                  f"可补组={len(fillable)}")

    n_fill = sum(len(x["codes"]) for x in fillable)
    n_no = sum(len(x["codes"]) for x in no_source)
    print(f"  死信 (code,日期) 总 = {total_pairs:,}")
    print(f"  ✅ 源端有 ⇒ 可补 = {n_fill:,}（{n_fill/max(total_pairs,1)*100:.1f}%）"
          f"  涉及 {len(fillable)} 个日期")
    print(f"  ❌ 源端无 ⇒ 无源 = {n_no:,}（{n_no/max(total_pairs,1)*100:.1f}%）"
          f"  涉及 {len(no_source)} 个日期")
    print(f"  耗时 {time.time()-t0:.0f}s")

    result[f"{asset}_{kind}"] = {
        "table": table, "total_pairs": total_pairs,
        "fillable_pairs": n_fill, "no_source_pairs": n_no,
        "fillable_days": len(fillable), "no_source_days": len(no_source),
        "fillable": fillable, "no_source": no_source,
    }

con.close()
result["_meta"] = {
    "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
    "elapsed_sec": round(time.time() - t_all, 1),
    "method": "逐日探测 QuestDB 实际 ts_code 集合，与死信 code 求交集",
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\n[evidence] {OUT}")
print(f"总耗时 {result['_meta']['elapsed_sec']}s")
