# -*- coding: utf-8 -*-
"""复算 income_statement 业务字段指纹（对拍派单方回填前基线）"""
from pathlib import Path
import duckdb

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
con = duckdb.connect(str(QS / "data" / "quantstudio.db"), read_only=True)

BIZ = ["code", "end_date", "ann_date", "operating_revenue", "operating_cost",
       "operating_profit", "total_profit", "net_profit", "np_parent_company_owners",
       "sale_expense", "manage_expense", "finance_expense", "rd_expense",
       "income_tax", "basic_eps"]
FULL = BIZ + ["update_time", "data_source"]

def fp(cols, where=""):
    expr = ", ".join(f"COALESCE(CAST({c} AS VARCHAR),'~')" for c in cols)
    sql = (f"SELECT SUM(CAST(hash(concat_ws('|', {expr})) AS HUGEINT)) "
           f"FROM income_statement {where}")
    return con.execute(sql).fetchone()[0]

print("列序:", ",".join(BIZ))
print()
BASE_BIZ = 1186810097110654605595247
BASE_FULL = 1189233607408065504705746

print("=== ② 存量行零衰减（V4 口径 · 业务字段指纹）===")
w18 = "WHERE year(epoch_ms(end_date)) >= 2018"
b18 = fp(BIZ, w18)
print(f"  基线(回填前全表=128,764)     = {BASE_BIZ}")
print(f"  现 FY2018+ 子集               = {b18}")
print(f"  -> {'一致 ✓ 存量业务字段零衰减' if b18 == BASE_BIZ else '不一致 ✗'}")
print()
print("=== ②附 全字段指纹（含 update_time / data_source）===")
f18 = fp(FULL, w18)
print(f"  基线(回填前)                  = {BASE_FULL}")
print(f"  现 FY2018+ 子集               = {f18}")
print(f"  -> {'一致' if f18 == BASE_FULL else '已变化（update_time 刷新，符合 upsert 语义）'}")
print()
print("=== 参考：全表指纹（163,818 行）===")
print(f"  业务字段 = {fp(BIZ)}")
print(f"  全字段   = {fp(FULL)}")
print()
print("=== 参照校验：即使用同一算法对基线备份库复算 ===")
con.execute(f"ATTACH '{QS/'data'/'quantstudio_backup_20260912.db'}' AS old (READ_ONLY)")
expr = ", ".join(f"COALESCE(CAST({c} AS VARCHAR),'~')" for c in BIZ)
ob = con.execute(f"SELECT SUM(CAST(hash(concat_ws('|', {expr})) AS HUGEINT)) "
                 "FROM old.income_statement").fetchone()[0]
exprf = ", ".join(f"COALESCE(CAST({c} AS VARCHAR),'~')" for c in FULL)
of = con.execute(f"SELECT SUM(CAST(hash(concat_ws('|', {exprf})) AS HUGEINT)) "
                 "FROM old.income_statement").fetchone()[0]
print(f"  写前备份库(128,764) 业务字段 = {ob}  {'== 基线 ✓' if ob == BASE_BIZ else '≠ 基线'}")
print(f"  写前备份库(128,764) 全字段   = {of}  {'== 基线 ✓' if of == BASE_FULL else '≠ 基线'}")
con.close()
