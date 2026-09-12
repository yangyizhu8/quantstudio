# -*- coding: utf-8 -*-
"""纯新增对账：存量 FY2018+ 行逐值不被改写/删除（写前快照 vs 当前主库）"""
from pathlib import Path
import duckdb

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
OLD = QS / "data" / "quantstudio_backup_20260912.db"
NEW = QS / "data" / "quantstudio.db"

BIZ = ["ann_date", "operating_revenue", "operating_cost", "operating_profit",
       "total_profit", "net_profit", "np_parent_company_owners", "sale_expense",
       "manage_expense", "finance_expense", "rd_expense", "income_tax", "basic_eps"]
ALLCOLS = ["code", "end_date"] + BIZ + ["update_time", "data_source"]

con = duckdb.connect(str(NEW))
con.execute(f"ATTACH '{OLD}' AS old (READ_ONLY)")

print("=" * 80)
print("0) 口径注明（B2 约定：日期字段存储为北京时间 00:00）")
print("=" * 80)
r = con.execute("SELECT min(end_date), max(end_date) FROM income_statement").fetchone()
import datetime
mn_ms, mx_ms = int(r[0]), int(r[1])
utc = datetime.datetime.utcfromtimestamp(mn_ms / 1000)
bj = datetime.datetime.utcfromtimestamp(mn_ms / 1000 + 8 * 3600)
print(f"  MIN(end_date) 原始 = {mn_ms} ms")
print(f"    · Beijing 读法（本库约定） = {bj:%Y-%m-%d %H:%M:%S}  <== 约定正确值")
print(f"    · UTC 读法（raw epoch_ms） = {utc:%Y-%m-%d %H:%M:%S}  <== 差 1 天")
print(f"  （两种读法对『年报年份』判据无影响：同一 MS 落同一年份桶）")

print()
print("=" * 80)
print("1) 键唯一性（code, end_date）")
print("=" * 80)
for lbl, cat in (("写前快照", "old"), ("当前主库", "main")):
    n, u = con.execute(
        f"SELECT count(*), count(DISTINCT (code, end_date)) FROM {cat}.income_statement").fetchone()
    print(f"  {lbl}: rows={n:,}  distinct(code,end_date)={u:,}  {'唯一 ✓' if n == u else '有重复 ✗'}")

print()
print("=" * 80)
print("2) 存量 FY2018+ 行逐值对拍（全部 13 个业务字段）")
print("=" * 80)
sel_biz = ", ".join(f"n.{c} AS n_{c}, o.{c} AS o_{c}" for c in BIZ)
con.execute(f"""
CREATE TEMP TABLE cmp AS
SELECT n.code AS n_code, o.code AS o_code,
       n.end_date AS n_end_date, o.end_date AS o_end_date, {sel_biz},
       (n.update_time IS NOT DISTINCT FROM o.update_time) AS same_upt,
       (n.data_source IS NOT DISTINCT FROM o.data_source) AS same_src
FROM main.income_statement n
FULL OUTER JOIN old.income_statement o
  ON n.code = o.code AND n.end_date = o.end_date
WHERE year(epoch_ms(coalesce(n.end_date, o.end_date))) >= 2018
""")
tot = con.execute("SELECT count(*) FROM cmp").fetchone()[0]
only_old = con.execute("SELECT count(*) FROM cmp WHERE n_code IS NULL").fetchone()[0]
only_new = con.execute("SELECT count(*) FROM cmp WHERE o_code IS NULL").fetchone()[0]
print(f"  参与对拍行数 = {tot:,}   仅新库有 = {only_new}   仅旧库有(被删) = {only_old}")
print(f"  (对拍集合 = 写前 FY2018+ 全部 {tot - only_new:,} 行 ∪ 新增侧)")
print(f"  参与对拍行数 = {tot:,}   仅新库有 = {only_new}   仅旧库有(被删) = {only_old}")

diff_total = 0
for c in BIZ:
    d = con.execute(
        f"SELECT count(*) FROM cmp WHERE n_{c} IS DISTINCT FROM o_{c}").fetchone()[0]
    diff_total += d
    if d:
        print(f"    !! {c}: {d:,} 行不一致")
print(f"  业务字段不一致合计 = {diff_total}  {'-> 存量行逐值零改写 ✓' if diff_total == 0 else '-> 存在改写 ✗'}")
upt = con.execute("SELECT count(*) FROM cmp WHERE NOT same_upt").fetchone()[0]
src = con.execute("SELECT count(*) FROM cmp WHERE NOT same_src").fetchone()[0]
print(f"  元数据列: update_time 变 {upt:,} 行 / data_source 变 {src:,} 行")

print()
print("=" * 80)
print("3) 行数恒等式")
print("=" * 80)
old_n = con.execute("SELECT count(*) FROM old.income_statement").fetchone()[0]
new_n = con.execute("SELECT count(*) FROM income_statement").fetchone()[0]
new1517 = con.execute("SELECT count(*) FROM income_statement "
                      "WHERE year(epoch_ms(end_date)) BETWEEN 2015 AND 2017").fetchone()[0]
new18 = con.execute("SELECT count(*) FROM income_statement "
                    "WHERE year(epoch_ms(end_date)) >= 2018").fetchone()[0]
print(f"  写前存量 = {old_n:,}")
print(f"  补入新增 = {new1517:,}")
print(f"  期望全表 = {old_n + new1517:,}")
print(f"  实际全表 = {new_n:,}   {'✓ 恒等式成立' if new_n == old_n + new1517 else '✗'}")
print(f"  其中 FY2018+ = {new18:,}   {'✓ 与写前一致' if new18 == old_n else '✗ 存量行数变化'}")
con.close()
