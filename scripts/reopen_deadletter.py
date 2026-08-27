# -*- coding: utf-8 -*-
"""批量重开 dead_letter → pending（仅当前世代 b6_formal_20260807_v2 的 stock_dividend trigger）。"""
import sys
import datetime
import duckdb

DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
apply = "--apply" in sys.argv
con = duckdb.connect(DB)
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
rows = con.execute("""
    SELECT trigger_id, code FROM qfq_trigger_queue
    WHERE status='dead_letter' AND price_source='mcp'
      AND source_generation='mcp-gen1' AND cutover_id='b6_formal_20260807_v2'
""").fetchall()
print(f"dead_letter 候选: {len(rows)}")
if apply and rows:
    ids = [r[0] for r in rows]
    ph = ",".join("?" * len(ids))
    con.execute(f"""
        UPDATE qfq_trigger_queue SET status='pending', attempt_count=0,
        next_retry_at=NULL, dead_letter_at=NULL, claimed_by=NULL, claimed_at=NULL,
        updated_at=?
        WHERE trigger_id IN ({ph})""", [now] + ids)
    con.commit()
    print(f"重开 {len(ids)} 个 dead_letter → pending")
con.close()
