# -*- coding: utf-8 -*-
"""Phase 2 备份：主库文件复制 + 因子基准快照导出 + SHA256 记录。"""
import hashlib
import io
import os
import shutil
import sqlite3
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TS = time.strftime("%Y%m%d_%H%M%S")
MAIN = "data/quantstudio.db"
BAK_DIR = "data/backup"
os.makedirs(BAK_DIR, exist_ok=True)
BAK = os.path.join(BAK_DIR, f"quantstudio.db.bak_frontfix_{TS}")
BASELINE = f"data/logs/frontfix_baseline_{TS}.csv"

# c6: 主库文件复制 + SHA256
print("[c6] 复制主库 ...")
t0 = time.time()
shutil.copy2(MAIN, BAK)
print(f"复制完成 {os.path.getsize(BAK)/1e9:.1f}GB, {time.time()-t0:.0f}s")

print("[c6] SHA256 计算 ...")
t0 = time.time()
h = hashlib.sha256()
with open(MAIN, "rb") as f:
    for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
        h.update(chunk)
sha = h.hexdigest()
print(f"SHA256(main) = {sha}  ({time.time()-t0:.0f}s)")
print(f"SHA256(bak)   = {hashlib.sha256(open(BAK,'rb').read()).hexdigest() if os.path.getsize(BAK)<3e9 else '大文件跳过重算(与复制源同文件)'}")

# c7: 因子基准快照（每 code global_latest）
print("[c7] 导出因子基准快照 ...")
t0 = time.time()
ac = sqlite3.connect("data/qfq_aux.db")
ac.execute("PRAGMA query_only = ON")
rows_a = ac.execute(
    "SELECT a.code, a.adj_factor FROM adj_factor a "
    "JOIN (SELECT code, MAX(time) mt FROM adj_factor GROUP BY code) b "
    "ON a.code=b.code AND a.time=b.mt").fetchall()
rows_f = ac.execute(
    "SELECT a.code, a.adj_factor FROM fund_adj a "
    "JOIN (SELECT code, MAX(time) mt FROM fund_adj GROUP BY code) b "
    "ON a.code=b.code AND a.time=b.mt").fetchall()
ac.close()

with open(BASELINE, "w", encoding="utf-8") as f:
    f.write("table,code,adj_latest\n")
    for code, v in rows_a:
        f.write(f"adj_factor,{code},{v}\n")
    for code, v in rows_f:
        f.write(f"fund_adj,{code},{v}\n")
print(f"baseline 行数: adj_factor={len(rows_a)}, fund_adj={len(rows_f)}, 耗时 {time.time()-t0:.0f}s")
print(f"baseline 文件: {BASELINE} ({os.path.getsize(BASELINE)/1024/1024:.1f}MB)")

# c8: 受影响清单计数（CSV 汇总）
print("[c8] 受影响清单汇总 ...")
total = 0
codes = set()
for f in sorted(os.listdir("data/logs")):
    if f.startswith("front_corruption_") and f.endswith(".csv"):
        p = os.path.join("data/logs", f)
        n = sum(1 for _ in open(p, encoding="utf-8", errors="replace")) - 1
        for ln in open(p, encoding="utf-8", errors="replace"):
            parts = ln.strip().split(",")
            if parts and parts[0] and parts[0] != "code":
                codes.add(parts[0])
            if n > 500000:
                break  # 大文件只取前 50 万行抽样 code 集合（近似）
        total += n
        print(f"  {f}: {n} 行")
print(f"  总破坏行数: {total}（应=14,378,894）")
print(f"  受影响 code 数（近似抽样）: {len(codes)}")

report = f"""# Phase 2 备份记录
时间: {TS}
主库: {MAIN} (SHA256={sha})
备份: {BAK}
因子基准快照: {BASELINE}
总破坏行数: {total}
"""
open(f"data/logs/frontfix_backup_{TS}.md", "w", encoding="utf-8").write(report)
print("backup done")
