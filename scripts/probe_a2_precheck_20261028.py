# -*- coding: utf-8 -*-
"""A2 预检 + 捎带令（按件2 交接件 §4.1/§4.2 口径）· 2026-10-28

用法（daemon 停机时）：python scripts/probe_a2_precheck_20261028.py
产出：①主库待补行数（预期 ≤813,140）②5 码分钟覆盖判向
"""
import duckdb, datetime as dt

DB = r"data\quantstudio.db"
def ms(s):
    return int((dt.datetime.strptime(s, '%Y-%m-%d') - dt.datetime(1970, 1, 1)).total_seconds() * 1000) - 8 * 3600 * 1000

con = duckdb.connect(DB, read_only=True)

# ── 4.1 主库待补行数（B 窗工作量）────────────────────────────
S, E = "2026-01-05", "2026-08-13"
q = """select count(*) from stock_daily
       where time between ? and ? and close_front_ratio is null
         and close is not null and close <> 0 and close_front is not null"""
todo = con.execute(q, [ms(S), ms(E)]).fetchone()[0]
total = con.execute("select count(*) from stock_daily where time between ? and ?", [ms(S), ms(E)]).fetchone()[0]
print(f"[4.1] {S}~{E} 区间总行 = {total:,}")
print(f"[4.1] 待补行数(close_front_ratio IS NULL 且可算) = **{todo:,}**")
print(f"[4.1] 判据：预期 ≤ 813,140；=0 ⇒ 件2 已无需回写")

# ── 4.2 5 码分钟覆盖判向 ─────────────────────────────────
print(f"\n[4.2] 5 码分钟覆盖（区间 2026-01-01~2026-08-14；基准=stock_daily distinct time）")
MS, ME = ms("2026-01-01"), ms("2026-08-14")
for code in ("600916", "000799", "002328", "002468", "002607"):
    n_bar = con.execute("select count(*) from stock_minutes where code=? and time between ? and ?",
                        [code, MS, ME]).fetchone()[0]
    n_mday = con.execute("select count(distinct epoch_ms(time)::DATE) from stock_minutes where code=? and time between ? and ?",
                         [code, MS, ME]).fetchone()[0]
    n_dday = con.execute("select count(distinct epoch_ms(time)::DATE) from stock_daily where code=? and time between ? and ?",
                         [code, MS, ME]).fetchone()[0]
    verdict = ("主库无分钟 ⇒ **采集管线覆盖缺口**" if n_bar == 0 else
               (f"分钟覆盖 {n_mday}/{n_dday} 日 ⇒ 若副本更少则**副本陈旧**，若同样缺则**采集缺口**"))
    print(f"  {code}: 分钟 {n_bar:,} 根 / {n_mday} 个交易日  vs  日线基准 {n_dday} 日  ⇒ {verdict}")
con.close()