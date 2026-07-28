"""临时探针（只读观测）：统计回测期间各类 DuckDB execute 的耗时与次数。

按 SQL 的 FROM 表 / 关键特征聚合，定位 10.7s _duckdb.execute 的真实来源。
不改任何框架代码；运行完可自行删除。
"""
from __future__ import annotations
import sys, time, re
from pathlib import Path

# 强制 stdout/stderr 为 utf-8，避免 Windows GBK 下打印 ✓/✗ 崩溃。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import duckdb
_orig_execute = duckdb.DuckDBPyConnection.execute
_stats = {}  # key -> [count, total_s, example_sql]

def _norm(sql: str) -> str:
    s = re.sub(r"\s+", " ", str(sql)).strip()
    # 去掉数值字面量与字符串字面量，保留结构
    s = re.sub(r"'[^']*'", "?", s)
    s = re.sub(r"\b\d+\b", "#", s)
    return s

def _key_of(sql: str) -> str:
    s = _norm(sql)
    # 取前 70 字符作为结构指纹，足以区分 COUNT(*) 与各类估值 PIT 查询
    return s[:70]

def _patched(self, sql, params=None):
    t0 = time.perf_counter()
    if params is not None:
        r = _orig_execute(self, sql, params)
    else:
        r = _orig_execute(self, sql)
    dt = time.perf_counter() - t0
    k = _key_of(sql)
    st = _stats.setdefault(k, [0, 0.0, None])
    st[0] += 1
    st[1] += dt
    if st[2] is None:
        st[2] = re.sub(r"\s+", " ", str(sql)).strip()[:160]
    return r

duckdb.DuckDBPyConnection.execute = _patched

from quantstudio.backtest.run_ptrade_strategy import main as run_strategy

PROJECT = Path(__file__).resolve().parent.parent.parent
STRATEGY = PROJECT / "quantstudio" / "backtest" / "strategies" / "小市值策略ptrade.py"

old = sys.argv
sys.argv = ["run_ptrade_strategy", str(STRATEGY), "2026-01-01", "2026-04-29"]
try:
    run_strategy()
except SystemExit:
    pass
except Exception as e:
    print(f"RUN ERROR: {type(e).__name__}: {e}")
finally:
    sys.argv = old

print("\n===== DuckDB execute 聚合（按 FROM 表+特征） =====")
total = 0.0
for k, (cnt, s, ex) in sorted(_stats.items(), key=lambda kv: -kv[1][1]):
    total += s
    print(f"{s:8.3f}s  x{cnt:5d}  {k}")
    print(f"           eg: {ex}")
print(f"TOTAL execute wall: {total:.3f}s  (聚合 {len(_stats)} 类)")
