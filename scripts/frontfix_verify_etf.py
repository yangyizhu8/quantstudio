# -*- coding: utf-8 -*-
"""etf_daily 修复后验证：159307 样本断言 + raw 列不变性（主库 vs 备份）。"""
import glob
import io
import sys

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

MAIN = "data/quantstudio.db"
bak = sorted(glob.glob("data/backup/quantstudio.db.bak_frontfix_*"))[-1]
print("对比备份:", bak)

con = duckdb.connect(MAIN, read_only=True)
cb = duckdb.connect(bak, read_only=True)

# 1) 159307 样本（ZCode 黄金行 05-26；期望 close*adj_i/adj_latest）
row = con.execute(
    "SELECT code, close, close_front FROM etf_daily WHERE code='159307' "
    "AND strftime(epoch_ms(time), '%Y-%m-%d')='2026-05-26'").fetchone()
print("159307 @05-26 修复后:", row)
if row:
    exp = row[1] * 1.0 / 1.1039
    rel = abs(row[2] - exp) / exp
    print(f"期望 close_front = {exp:.6f}, 实际 {row[2]}, 相对误差 {rel:.2e} (应<1e-6)" if row else "")

# 2) 159623 @2023-08（CSV 漏行样本）
row2 = con.execute(
    "SELECT code, close, close_front FROM etf_daily WHERE code='159623' "
    "AND strftime(epoch_ms(time), '%Y-%m-%d')='2023-08-15' LIMIT 1").fetchone()
print("159623 @2023-08 修复后:", row2)

# 3) raw 列不变性（COUNT/SUM 对比主库 vs 备份，对 etf_daily）
for col in ["open", "high", "low", "close"]:
    a = con.execute(f"SELECT COUNT({col}), SUM({col}) FROM etf_daily").fetchone()
    b = cb.execute(f"SELECT COUNT({col}), SUM({col}) FROM etf_daily").fetchone()
    same = abs(a[1] - b[1]) < 1e-6 if a[1] is not None else a[1] == b[1]
    print(f"raw {col}: main count={a[0]} sum={a[1]:.4f} | bak count={b[0]} sum={b[1]:.4f} | same={same}")

# 4) 剩余偏离行统计（修复后重扫）
factors = duckdb.connect(MAIN, read_only=True)
factors.execute("ATTACH 'data/qfq_aux.db' AS auxdb (TYPE sqlite, READ_ONLY)")
left = factors.execute("""
SELECT COUNT(*) FROM etf_daily tgt
JOIN (
  SELECT code, time, adj_factor,
         MAX(adj_factor) OVER (PARTITION BY code) AS adj_latest
  FROM auxdb.fund_adj
) f ON tgt.code = f.code AND tgt.time = f.time
WHERE tgt.close_front IS NOT NULL
  AND abs(tgt.close_front - tgt.close * f.adj_factor / f.adj_latest) / tgt.close_front > 1e-6
""").fetchone()[0]
print(f"修复后剩余偏离行: {left} (应=0)")
factors.close()
con.close()
cb.close()
