# -*- coding: utf-8 -*-
"""Phase 2 预检：环境与口径确认（只读）。"""
import io
import os
import shutil
import subprocess
import sys

import duckdb
import sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

print("=== 1) 主库 DuckDB 只读连接 ===")
try:
    c = duckdb.connect("data/quantstudio.db", read_only=True)
    print("SELECT 1 ->", c.execute("SELECT 1").fetchone())
    print("version ->", c.execute("SELECT version()").fetchone()[0][:60])
except Exception as e:
    print("FAIL:", str(e)[:150])
    sys.exit(1)

print()
print("=== 2) 4 张价格表结构（关键列）===")
for t in ["stock_daily", "etf_daily", "stock_minutes", "etf_minutes"]:
    cols = [r[0] for r in c.execute(f"DESCRIBE {t}").fetchall()]
    has_src = "source" in cols
    has_front = all(x in cols for x in ["open_front", "high_front", "low_front", "close_front"])
    has_raw = all(x in cols for x in ["open", "high", "low", "close"])
    has_time = "time" in cols
    print(f"{t}: 列数={len(cols)} source={has_src} time={has_time} raw4={has_raw} front4={has_front}")
    # source 分布抽样
    if has_src:
        dist = c.execute(f"SELECT source, COUNT(*) FROM {t} GROUP BY 1 ORDER BY 2 DESC LIMIT 4").fetchall()
        print(f"   source 分布: {dist}")

print()
print("=== 3) 破坏 CSV 表头与行数 ===")
for f in sorted(os.listdir("data/logs")):
    if f.startswith("front_corruption_") and f.endswith(".csv"):
        p = os.path.join("data/logs", f)
        sz = os.path.getsize(p)
        with open(p, encoding="utf-8", errors="replace") as fh:
            head = fh.readline().strip()
        n = sum(1 for _ in open(p, encoding="utf-8", errors="replace")) - 1
        print(f"{f}: {sz/1024/1024:.1f}MB, 行数(含表头减1)={n}")
        print(f"   表头: {head[:220]}")

print()
print("=== 4) qfq_aux.db 因子表 ===")
try:
    ac = sqlite3.connect("data/qfq_aux.db")
    r1 = ac.execute("SELECT COUNT(*), MAX(time) FROM adj_factor").fetchone()
    r2 = ac.execute("SELECT COUNT(*), MAX(time) FROM fund_adj").fetchone()
    print(f"adj_factor: 行数={r1[0]}, MAX(time)={r1[1]}")
    print(f"fund_adj: 行数={r2[0]}, MAX(time)={r2[1]}")
    ac.close()
except Exception as e:
    print("FAIL:", str(e)[:150])

print()
print("=== 5) 磁盘空间 ===")
du = shutil.disk_usage("data")
print(f"可用: {du.free/1e9:.1f}GB / 总计 {du.total/1e9:.1f}GB")

print()
print("=== 6) 活动 python 进程（daemon/GUI 检查）===")
out = subprocess.run(
    ["powershell.exe", "-NoProfile", "-Command",
     "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | ForEach-Object { "
     "$_.ProcessId.ToString() + '|' + $_.CommandLine.Substring(0, [Math]::Min(150, $_.CommandLine.Length)) } "
     "| Out-String -Width 220"],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
for ln in out.stdout.splitlines():
    ln = ln.strip()
    if ln and ("daemon" in ln.lower() or "gui" in ln.lower() or "quantstudio" in ln.lower() or "main_" in ln.lower()):
        print("  ", ln[:180])

c.close()
print()
print("preflight done")
