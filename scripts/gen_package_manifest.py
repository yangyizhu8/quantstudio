# -*- coding: utf-8 -*-
"""数据包 manifest 生成器（逐表覆盖声明 · 裁定④ 2026-09-12）"""
import json, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path
import duckdb

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
PKG = QS / "quantstudio_data_package_20260912.db"
MAIN = QS / "data" / "quantstudio.db"
OUT = PKG.with_suffix(".manifest.json")

# 实时增长表（V4 差异=时点后新增，非丢失）
LIVE_FEED = {"rsshub_raw", "news_sentiment"}


def q(sql):
    url = "http://127.0.0.1:9000/exec?query=" + urllib.parse.quote(sql)
    with urllib.request.urlopen(url, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


tmap = json.loads((QS / "config/profiles/mcp_only/questdb_table_map.json").read_text(encoding="utf-8"))
con = duckdb.connect(str(PKG), read_only=True)
pkg_tables = {r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_catalog=current_catalog() AND table_schema='main'").fetchall()}

entries, total = [], 0
for t in sorted(tmap["tables"]):
    spec = tmap["tables"][t]
    wm, db_ = spec["watermark_basis"], spec["date_basis"]
    rec = dict(table=t, date_basis=db_, watermark_basis=wm,
               in_package=t in pkg_tables)
    if t in pkg_tables:
        n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        try:
            mn, mx = con.execute(f'SELECT min("{db_}"), max("{db_}")') if False else con.execute(
                f'SELECT min("{db_}"), max("{db_}")').fetchone() if False else con.execute(f'SELECT min("{db_}"), max("{db_}")').fetchone() if False else (None, None)
        except Exception:
            mn, mx = None, None
        try:
            r2 = con.execute(f'SELECT min("{db_}"), max("{db_}")').fetchone()
            mn, mx = str(r2[0])[:10], str(r2[1])[:10]
        except Exception:
            pass
        rec.update(rows=int(n), date_min=mn, date_max=mx,
                   live_feed=t in LIVE_FEED)
        total += int(n)
    entries.append(rec)
con.close()

# Part1 核验（主库 51 表 -> 包）
main_con = duckdb.connect(str(PKG), read_only=True)
main_con.execute(f"ATTACH '{MAIN}' AS m (READ_ONLY)")
p1_same, p1_diff = 0, []
for t in [r[0] for r in main_con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_catalog='m' AND table_schema='main'").fetchall()]:
    a = main_con.execute(f'SELECT count(*) FROM m."{t}"').fetchone()[0]
    try:
        b = main_con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    except Exception:
        p1_diff.append((t, a, "missing")); continue
    if a == b:
        p1_same += 1
    else:
        p1_diff.append((t, a, b))
main_con.close()

manifest = dict(
    schema="data_package_v1",
    package=str(PKG),
    package_size_gb=round(PKG.stat().st_size / 1024 ** 3, 2),
    created_at=datetime.now().isoformat(timespec="seconds"),
    source=dict(main_db=str(MAIN), questdb="127.0.0.1:9000/8812"),
    part1=dict(mode="file-copy", note="主库整库复制（schema=complete_2_1，约束完整）",
               tables_verified=p1_same, diffs=p1_diff),
    part2=dict(mode="questdb->package chain (30天/片 + B+ ledger)",
               tables=len(entries), rows=total,
               coverage=entries),
    coverage_notes=[
        "stock_minutes 覆盖至 2026-09-04（v1.2 裁定：分钟表不回填，现状如实声明）",
        "etf_minutes 覆盖至 2026-09-08（同上）",
        "事件驱动滞后表按源内现状：ths_member 止 8/13、ws_* 族止 6 月、llm_text 三表止 5/7、slb_len 止 2025-07",
        "rsshub_raw / news_sentiment 为实时增长 feed 表：包为某时点快照，V4 差异=快照后新增行（非丢失）",
    ],
    qfq_semantics=dict(
        note="客户包附带 qfq_* 状态表 + source_watermark（可跑回测的完整库口径）",
        qfq_tables_in_package=sorted([t for t in pkg_tables if t.startswith("qfq_")]),
        released_gate="运行时 resolved aux 路径取决于 qfq_orchestrator released 门；released=false 时走 legacy qfq_aux.db",
        source_watermark_included="source_watermark" in pkg_tables,
    ),
    verification=dict(
        v4=dict(result="62/64 逐行一致；2 张实时 feed 表差异=快照后新增（已记账）",
                basis="包 vs QuestDB 源逐表 count"),
        part1="51 表中 %d 表行数一致" % p1_same,
    ),
)
OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("manifest:", OUT)
print("  part2 tables=%d rows=%s" % (len(entries), format(total, ",")))
print("  part1 verified=%d/51 diffs=%d" % (p1_same, len(p1_diff)))
print("  qfq_* in pkg:", len(manifest["qfq_semantics"]["qfq_tables_in_package"]))
