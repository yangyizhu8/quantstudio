"""诊断2：复现引擎路径 registry.market.preload(...) -> get_daily_snapshot(...) 是否命中缓存。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.backtest.providers.base import DataProviderRegistry

DB = ROOT / "data" / "quantstudio.db"
reg = DataProviderRegistry.from_duckdb(DB)
# 复现引擎：self._providers.market.preload(self.start, self.end)
reg.market.preload("2026-01-01", "2026-04-29")
cache = reg.market._data._daily_snapshot_cache
print("preload called; cache_size =", len(cache))
print("daily_snapshot_loaded =", reg.market._data._daily_snapshot_loaded)

# 模拟每日 get_daily_snapshot（传入交易日字符串）
for d in ["2026-01-02", "2026-01-05", "2026-04-29", "2026-03-15"]:
    t0 = time.perf_counter()
    df = reg.market.get_daily_snapshot(d)
    dt = time.perf_counter() - t0
    print(f"get_daily_snapshot({d}) took {dt*1000:.2f}ms rows={len(df)}")

reg.market._data.close()
print("OK")
