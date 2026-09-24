"""挂账定性批 · ⑤ etf_basic 5 重复码取证（只读）。

目标：确认 etf_basic 表中重复 code 的具体码、重复形态与是否幂等风险。
"""
import sys
from pathlib import Path

import duckdb

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
DB = ROOT / "data" / "quantstudio.db"

con = duckdb.connect(str(DB), read_only=True)
print(f"库={DB.name}")

# 1) 表结构
cols = [r[1] for r in con.execute('PRAGMA table_info("etf_basic")').fetchall()]
print(f"\n=== etf_basic 列({len(cols)}) ===")
print(" ", cols)

n = con.execute('SELECT COUNT(*) FROM "etf_basic"').fetchone()[0]
nc = con.execute('SELECT COUNT(DISTINCT code) FROM "etf_basic"').fetchone()[0]
print(f"\n总行数={n:,}   distinct(code)={nc:,}   重复行数={n - nc}")

# 2) 重复 code 清单
print("\n=== 重复 code（行数 > 1）===")
dups = con.execute('''
    SELECT code, COUNT(*) AS c FROM "etf_basic"
    GROUP BY code HAVING COUNT(*) > 1 ORDER BY c DESC, code
''').fetchall()
print(f"  重复 code 数 = {len(dups)}")
for code, c in dups[:20]:
    print(f"    {code}: {c} 行")

# 3) 每个重复 code 的逐行差异
if dups:
    print("\n=== 重复行逐行对比（前 5 个 code，全列）===")
    for code, c in dups[:5]:
        rows = con.execute(
            f'SELECT * FROM "etf_basic" WHERE code = ? ORDER BY 1', [code]).fetchall()
        print(f"\n  --- {code} ({c} 行) ---")
        for r in rows:
            pairs = {k: v for k, v in zip(cols, r) if v is not None}
            print("    ", {k: str(v)[:28] for k, v in pairs.items()})

# 4) 主键/唯一约束检查
print("\n=== 约束/索引 ===")
try:
    for r in con.execute(
            "SELECT index_name, is_unique, is_primary, sql FROM duckdb_indexes() "
            "WHERE table_name = 'etf_basic'").fetchall():
        print("  ", r)
except Exception as e:
    print("  duckdb_indexes 查询失败:", e)
try:
    dd = con.execute(
        "SELECT sql FROM duckdb_tables() WHERE table_name = 'etf_basic'").fetchone()
    print("  DDL:", (dd[0] if dd else "N/A"))
except Exception as e:
    print("  DDL 查询失败:", e)

# 5) writer DDL 声明（对齐检查）
print("\n=== 写入侧 PK 声明（writers.py）===")
wp = ROOT / "quantstudio" / "pipeline" / "writers.py"
if wp.exists():
    txt = wp.read_text(encoding="utf-8")
    i = txt.find('"etf_basic"')
    while i != -1 and i < len(txt):
        seg = txt[i:i + 260]
        if "CREATE TABLE" in seg or "PRIMARY KEY" in seg or "pk" in seg.lower():
            print("  ...", seg.replace("\n", " ")[:220])
            break
        i = txt.find('"etf_basic"', i + 1)
con.close()