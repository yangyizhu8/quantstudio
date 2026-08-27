# -*- coding: utf-8 -*-
"""etf_minutes 修复完成监控：轮询 PID 44088 退出 → 自动验证 → 输出报告。"""
import io
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def alive():
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "if (Get-Process -Id 44088 -ErrorAction SilentlyContinue) { 'Y' } else { 'N' }"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() == "Y"


def run_verify():
    import duckdb
    import pandas as pd
    con = duckdb.connect("data/quantstudio.db")
    con.execute("ATTACH 'data/qfq_aux.db' AS auxdb (TYPE sqlite, READ_ONLY)")
    codes = set()
    with open("data/logs/front_corruption_etf_minutes.csv", encoding="utf-8", errors="replace") as f:
        f.readline()
        for ln in f:
            p = ln.split(",")
            if p and p[0]:
                codes.add(p[0].strip())
    con.register("codes_t", pd.DataFrame({"code": sorted(codes)}))
    con.execute(
        "CREATE TEMP TABLE factordf AS SELECT code, time, adj_factor FROM ("
        "  SELECT f.code, f.time, f.adj_factor,"
        "         row_number() OVER (PARTITION BY f.code, strftime(epoch_ms(f.time), '%Y-%m-%d') "
        "                          ORDER BY f.time DESC) AS rn"
        "  FROM auxdb.fund_adj f WHERE f.code IN (SELECT code FROM codes_t)"
        ") WHERE rn = 1")
    left = con.execute("""
    SELECT COUNT(*) FROM etf_minutes tgt
    JOIN (
      SELECT code, time, adj_factor,
             MAX(adj_factor) OVER (PARTITION BY code) AS adj_latest
      FROM factordf
    ) f
    ON tgt.code = f.code
       AND strftime(epoch_ms(tgt.time), '%Y-%m-%d') = strftime(epoch_ms(f.time), '%Y-%m-%d')
    WHERE tgt.close_front IS NOT NULL
      AND abs(tgt.close_front - tgt.close * f.adj_factor / f.adj_latest) / tgt.close_front > 1e-6
    """).fetchone()[0]
    con.close()
    return left


# 主循环：最长 4 小时
print("监控启动: 等待 PID 44088 退出 ...", flush=True)
for i in range(480):
    if not alive():
        print(f"[{time.strftime('%H:%M:%S')}] 进程已退出", flush=True)
        left = run_verify()
        print(f"etf_minutes 剩余偏离行: {left}", flush=True)
        print("VERIFY_RESULT:" + ("ZERO" if left == 0 else f"LEFT={left}"), flush=True)
        sys.exit(0)
    if i % 30 == 0:
        print(f"[{time.strftime('%H:%M:%S')}] 仍在运行 (已等 {i*30}s)", flush=True)
    time.sleep(30)
print("监控超时（4h），进程仍存活", flush=True)
sys.exit(1)
