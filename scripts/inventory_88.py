# -*- coding: utf-8 -*-
"""88/88 全量盘点（2026-09-12 定稿包，只读）

口径：collector_tasks.json 的 88 任务表 = 24（Part1 主库拷贝）+ 64（Part2 QuestDB 回填）
逐表核：存在性 / 行数 / 日期范围 / manifest 勾稽
"""
import json
from pathlib import Path
import duckdb

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
PKG = QS / "quantstudio_data_package_20260912.db"
MF = PKG.with_suffix(".manifest.json")

tasks = json.loads((QS / "config/profiles/mcp_only/collector_tasks.json").read_text(encoding="utf-8"))["tasks"]
tmap = json.loads((QS / "config/profiles/mcp_only/questdb_table_map.json").read_text(encoding="utf-8"))["tables"]
mf = json.loads(MF.read_text(encoding="utf-8"))

part1_rows = {d["table"]: d["rows"] for d in mf["part1"].get("details", [])}
part2_rows = {c["table"]: c.get("rows") for c in mf["part2"]["coverage"]}

con = duckdb.connect(str(PKG), read_only=True)
have = {r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_catalog=current_catalog() AND table_schema='main'").fetchall()}

DATE_CANDIDATES = ("trade_date", "time", "ann_date", "publish_date", "report_date",
                   "in_date", "cal_date", "list_date", "end_date", "surv_date",
                   "news_date", "pub_time", "publish_time", "inserted_at", "ingest_time",
                   "trade_time", "ts", "event_time", "hold_date", "change_date", "in_date")


def date_col(t):
    if t in tmap:
        return tmap[t]["date_basis"]
    try:
        cols = [r[0] for r in con.execute(f'DESCRIBE "{t}"').fetchall()]
    except Exception:
        return None
    for c in DATE_CANDIDATES:
        if c in cols:
            return c
    return None


seen, rows_out = set(), []
missing, mismatch = [], []
for task in tasks:
    t = task.get("table")
    if not t or t in seen:
        continue
    seen.add(t)
    src = "Part1" if t in part1_rows else ("Part2" if t in part2_rows else "?")
    if t not in have:
        missing.append(t)
        rows_out.append((t, src, "MISSING", None, None, None))
        continue
    n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    dc = date_col(t)
    mn = mx = None
    if dc:
        try:
            r = con.execute(f'SELECT min("{dc}"), max("{dc}") FROM "{t}"').fetchone()
            def _fmt(v):
                if v is None:
                    return None
                # epoch 毫秒列（如 Part1 行情表 time）→ 可读日期
                if isinstance(v, int) and v > 10 ** 10:
                    import datetime as _dt
                    return _dt.datetime.fromtimestamp(v / 1000).strftime("%Y-%m-%d")
                return str(v)[:10]
            mn, mx = _fmt(r[0]), _fmt(r[1])
        except Exception:
            pass
    exp = part1_rows.get(t, part2_rows.get(t))
    ok = (exp is None) or (int(exp) == int(n))
    if not ok:
        mismatch.append((t, exp, n))
    rows_out.append((t, src, "OK" if ok else "ROWS-DIFF", n, mn, mx))

con.close()
print("=== 88/88 全量盘点 ===")
print(f"任务表数: {len(rows_out)}  存在: {len(rows_out) - len(missing)}  缺失: {len(missing)}  "
      f"行数勾稽不符: {len(mismatch)}")
print()
print(f"{'表':<30} {'来源':<7} {'状态':<10} {'行数':>14} {'起':<11} {'止':<11}")
for t, src, st, n, mn, mx in sorted(rows_out):
    ns = format(n, ",") if n is not None else "-"
    print(f"{t:<30} {src:<7} {st:<10} {ns:>14} {str(mn or '-'):<11} {str(mx or '-'):<11}")
print()
if missing:
    print("缺失表:", missing)
if mismatch:
    print("行数勾稽不符（表, manifest, 实际）:", mismatch)
total = sum(r[3] for r in rows_out if r[3])
print(f"包总行数: {total:,}")
print("INVENTORY:", "PASS" if not missing and not mismatch else "NOT-PASS")
json.dump(dict(tables=len(rows_out), missing=missing, mismatch=mismatch,
               total_rows=total,
               detail=[dict(table=t, source=s, status=st, rows=n, date_min=mn, date_max=mx)
                       for t, s, st, n, mn, mx in rows_out]),
          open(QS / "data/logs/inventory_88.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
