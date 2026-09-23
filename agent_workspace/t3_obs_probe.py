# -*- coding: utf-8 -*-
"""未决项 1 取证（只读，低成本版）：仅 PK 前缀查询 + 即时 flush，避免全表扫描。

判定目标：
  ① 抽样 code 的 revision_no 分布（是否出现过 revision≥2 → 是否曾有修订）
  ② outbox 行数（已知 0）
  ③ 抽样 code 的 last_seen_at（观察是否在近期持续运行）
"""
import sqlite3
import sys
import time


def p(*a):
    print(*a, flush=True)


t0 = time.time()
c = sqlite3.connect("file:data/qfq_aux.db?mode=ro", uri=True, timeout=10)
p(f"[open] read-only ok in {time.time() - t0:.2f}s")

p("[1] 抽样 code：revision_no 分布 + 观察时间（PK 前缀限定）")
for at, code in (("STOCK", "600519"), ("STOCK", "000001"), ("STOCK", "300750"), ("ETF", "510300")):
    t1 = time.time()
    rows = c.execute(
        "SELECT revision_no, count(*) FROM qfq_factor_observation "
        "WHERE asset_type=? AND code=? GROUP BY revision_no ORDER BY revision_no", (at, code)
    ).fetchall()
    win = c.execute(
        "SELECT min(last_seen_at), max(last_seen_at) FROM qfq_factor_observation "
        "WHERE asset_type=? AND code=?", (at, code)
    ).fetchone()
    p(f"    {at}/{code}: revisions={rows or '无观测行'}  last_seen∈[{win[0]}, {win[1]}]  ({time.time()-t1:.2f}s)")

p("[2] outbox 行数")
p("    qfq_factor_revision_alert =", c.execute("SELECT count(*) FROM qfq_factor_revision_alert").fetchone()[0])

p("[3] 游标表 qfq_deep_audit_cursor")
try:
    for row in c.execute("SELECT asset_type, cursor_code, round_no, updated_at FROM qfq_deep_audit_cursor").fetchall():
        p("   ", row)
except Exception as e:
    p("    查询失败:", e)

c.close()
p(f"[done] 总耗时 {time.time() - t0:.2f}s")
sys.exit(0)
