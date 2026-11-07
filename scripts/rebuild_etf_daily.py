# -*- coding: utf-8 -*-
"""候选 B 执行：etf_daily 单表重建（DROP → 按原 DDL 重建 → parquet 导入 → V1/V2/V3）。

⚠ 本脚本为写操作（生产库）。执行前置：
  1. 全库备份已完成且 SHA256 校验一致
  2. daemon 已停（无进程、无 status 文件）
  3. etf_daily parquet dump 已就绪且回读校验一致
用法：python rebuild_etf_daily.py --execute    （不带 --execute 只做只读预检）
"""
import hashlib
import sys
from datetime import datetime
from pathlib import Path

import duckdb

PROD = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"
FIXDIR = Path(r"D:\qs_art_fix_20261107")
DUMP = FIXDIR / "etf_daily_dump.parquet"
BASELINE_CNT = 2220975
BASELINE_HASH = 20569599082417213915958292

DDL = (
    'CREATE TABLE etf_daily(code VARCHAR, "time" BIGINT, open DOUBLE, high DOUBLE, '
    'low DOUBLE, "close" DOUBLE, preClose DOUBLE, pctChg DOUBLE, volume DOUBLE, '
    'amount DOUBLE, turn DOUBLE, open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, '
    'close_front DOUBLE, open_back DOUBLE, high_back DOUBLE, low_back DOUBLE, '
    'close_back DOUBLE, open_front_ratio DOUBLE, high_front_ratio DOUBLE, '
    'low_front_ratio DOUBLE, close_front_ratio DOUBLE, open_back_ratio DOUBLE, '
    'high_back_ratio DOUBLE, low_back_ratio DOUBLE, close_back_ratio DOUBLE, '
    'isST INTEGER, dividend_type VARCHAR, update_time VARCHAR, data_source VARCHAR, '
    'PRIMARY KEY(code, "time"))'
)

EXECUTE = "--execute" in sys.argv
print(f"[{datetime.now():%H:%M:%S}] 模式 = {'执行(DROP+重建)' if EXECUTE else '预检(只读)'}")

# ── 预检（始终执行）────────────────────────────────────────────────
assert DUMP.exists(), f"dump 缺失: {DUMP}"
assert DUMP.stat().st_size > 50 * 1024 * 1024, "dump 体积异常"
print(f"  dump = {DUMP.name}  {DUMP.stat().st_size/1024/1024:.1f} MB")

con = duckdb.connect(PROD, read_only=not EXECUTE)

before_cnt = con.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0]
before_h = con.execute("SELECT SUM(CAST(hash(COLUMNS(*)) AS HUGEINT)) FROM etf_daily").fetchone()[0]
print(f"  before: rows={before_cnt:,} hash={before_h}")
assert before_cnt == BASELINE_CNT, f"行数与基线不符: {before_cnt} != {BASELINE_CNT}"
assert before_h == BASELINE_HASH, f"hash 与基线不符: {before_h} != {BASELINE_HASH}"
print("  ✅ 预检通过（与基线一致）")

if not EXECUTE:
    con.close()
    print("  [预检模式结束，未做任何写操作]")
    raise SystemExit(0)

# ── 执行（DROP → 重建 → 导入）──────────────────────────────────────
print(f"\n[{datetime.now():%H:%M:%S}] 开始重建")
try:
    con.execute("DROP TABLE etf_daily")
    print("  ✅ DROP TABLE etf_daily")
except Exception as e:
    print(f"  ❌ DROP 失败: {type(e).__name__}: {str(e)[:200]}")
    con.close()
    raise SystemExit(2)

try:
    con.execute(DDL)
    print("  ✅ 按原 DDL 重建（含 PRIMARY KEY(code, time)）")
except Exception as e:
    print(f"  ❌ 重建失败: {type(e).__name__}: {str(e)[:200]}")
    con.close()
    raise SystemExit(3)

try:
    con.execute(f"COPY etf_daily FROM '{DUMP}' (FORMAT parquet)")
    print("  ✅ parquet 导入完成")
except Exception as e:
    print(f"  ❌ 导入失败: {type(e).__name__}: {str(e)[:300]}")
    con.close()
    raise SystemExit(4)

# ── V1/V2 ─────────────────────────────────────────────────────────
after_cnt = con.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0]
after_h = con.execute("SELECT SUM(CAST(hash(COLUMNS(*)) AS HUGEINT)) FROM etf_daily").fetchone()[0]
v1 = after_cnt == BASELINE_CNT
v2 = after_h == BASELINE_HASH
print(f"\n  V1 行数: {after_cnt:,} == {BASELINE_CNT:,} ? {'✅' if v1 else '❌'}")
print(f"  V2 hash: {after_h} == {BASELINE_HASH} ? {'✅' if v2 else '❌'}")

# ── V3 唯一性约束生效（插入重复 PK 应报 Constraint 而非 ART fatal）────
v3 = False
v3_msg = ""
try:
    row = con.execute('SELECT code, "time" FROM etf_daily LIMIT 1').fetchone()
    con.execute("BEGIN")
    con.execute('INSERT INTO etf_daily (code, "time") VALUES (?, ?)', [row[0], row[1]])
    con.execute("ROLLBACK")
    v3_msg = "插入重复 PK 未报错（异常）"
except Exception as e:
    con.execute("ROLLBACK") if "Transaction" in type(e).__name__ else None
    msg = str(e)
    if "ART" in msg or "gated leaf" in msg:
        v3_msg = f"仍为 ART 索引错误(未修复): {type(e).__name__}: {msg[:150]}"
    elif "Constraint" in type(e).__name__ or "Duplicate" in msg or "unique" in msg.lower():
        v3 = True
        v3_msg = f"✅ 正常约束拒绝: {type(e).__name__}: {msg[:150]}"
    else:
        v3_msg = f"其他错误: {type(e).__name__}: {msg[:150]}"
print(f"  V3 唯一性: {v3_msg}")

con.execute("CHECKPOINT") if False else None
con.close()

# 落回执
rep = FIXDIR / f"rebuild_report_{datetime.now():%Y%m%d_%H%M%S}.txt"
rep.write_text(
    f"etf_daily 重建回执 {datetime.now():%Y-%m-%d %H:%M:%S}\n"
    f"before: rows={before_cnt} hash={before_h}\n"
    f"after : rows={after_cnt} hash={after_h}\n"
    f"V1={v1} V2={v2} V3={v3}\nV3_msg={v3_msg}\n",
    encoding="utf-8")
print(f"\n  回执 = {rep}")
print(f"[{datetime.now():%H:%M:%S}] 结束（V1={v1} V2={v2} V3={v3}）")
