# -*- coding: utf-8 -*-
"""补充修复：分钟表 front/back 列（复权口径）随 close 同步缩放。
close 已由 restore_minutes_raw.py 修复；front/back 列此前未动，导致
QFQ rebase 一致性检查（末bar front vs 日线 front）仍然 BLOCK。
原理：整行污染统一 × (adj_latest/adj_row)，还原统一 × (adj_row/adj_latest)，
front/back 列与 close 同比例缩放：front_new = front_old × ratio。
ratio 来自 restore_minutes_raw.py 保存的备份 CSV。
"""
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
DB = ROOT / "data" / "quantstudio.db"

def fix(table: str, apply: bool) -> None:
    bak = ROOT / "data" / f"minute_fix_ratio_{table}.csv"
    if not bak.exists():
        print(f"[{table}] 无 ratio 备份 {bak.name}，跳过")
        return
    ratio = pd.read_csv(bak)
    print(f"[{table}] ratio 行数: {len(ratio)}")
    con = duckdb.connect(str(DB))
    con.register("ratio_df", ratio)
    if not apply:
        # dry-run：检查 front/back 与日线一致性现状
        n = con.execute(f"""
            SELECT COUNT(*) FROM ratio_df""").fetchone()[0]
        sample = con.execute(f"""
            SELECT t.code, t.time, t.close, t.close_front, t.close_back
            FROM {table} t JOIN ratio_df r ON t.code=r.code AND t.time=r.time
            WHERE t.code='600519' AND t.time=1781506800000""").fetchall()
        print(f"[{table}] dry-run 行数={n} 600519 6/15 15:00: {sample}")
        con.close()
        return
    upd = con.execute(f"""
        UPDATE {table} AS t SET
            open_front = t.open_front * r.ratio,
            high_front = t.high_front * r.ratio,
            low_front = t.low_front * r.ratio,
            close_front = t.close_front * r.ratio,
            open_back = t.open_back * r.ratio,
            high_back = t.high_back * r.ratio,
            low_back = t.low_back * r.ratio,
            close_back = t.close_back * r.ratio
        FROM ratio_df AS r
        WHERE t.code = r.code AND t.time = r.time""")
    con.commit()
    print(f"[{table}] UPDATED front/back 列 ({upd.rowcount})")
    con.close()

if __name__ == "__main__":
    apply = "--apply" in sys.argv
    only = None
    for a in sys.argv[1:]:
        if a.startswith("--only="):
            only = a.split("=", 1)[1]
    print("MODE:", "APPLY" if apply else "DRY-RUN", "| scope:", only or "ALL")
    if only in (None, "stock"):
        fix("stock_minutes", apply)
    if only in (None, "etf"):
        fix("etf_minutes", apply)
