#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R5 回测执行器（ou_reversal_csi300_10）。

显式指定数据库绝对路径（R5 外部库覆盖，客户已批准 2026-09-23），
调用框架统一入口 run_backtest，产出三件套 + 记录 provenance。

用法：
    python agent_workspace/ou_reversal_csi300_10/r5_run.py <run_tag>
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "quantstudio.old_20260920.db"   # R5 外部库（客户批准）
STRATEGY = ROOT / "agent_workspace" / "ou_reversal_csi300_10" / "strategy.py"
START, END = "2025-07-01", "2026-09-01"
CAPITAL = 100_000.0

from quantstudio.backtest.run_ptrade_strategy import run_backtest  # noqa: E402


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else "run1"
    print("R5 run tag=%s" % tag)
    print("  strategy : %s" % STRATEGY)
    print("  strategy sha256: %s" % sha256(STRATEGY))
    print("  db       : %s" % DB_PATH)
    print("  db exists: %s" % DB_PATH.exists())
    print("  window   : %s .. %s   capital=%.0f" % (START, END, CAPITAL))

    result, output_dir, engine = run_backtest(
        str(STRATEGY), START, END,
        db_path=str(DB_PATH),
        capital=CAPITAL,
        match_price_mode="open",
        engine_profile="daily-bar-v1",
    )
    out = Path(output_dir)
    print("  output_dir: %s" % out)
    prov = {
        "run_tag": tag,
        "strategy_path": str(STRATEGY),
        "strategy_sha256": sha256(STRATEGY),
        "backtest_db_path": str(DB_PATH.resolve()),
        "backtest_db_sha256": sha256(DB_PATH),
        "start": START, "end": END, "init_capital": CAPITAL,
        "match_price_mode": "open",
        "engine_profile": "daily-bar-v1",
        "engine_semantics_version": engine.engine_semantics_version,
        "output_dir": str(out.resolve()),
        "artifacts": {},
    }
    for name in ("config.csv", "daily_stats.csv", "trades.csv"):
        f = out / name
        prov["artifacts"][name] = {"path": str(f.resolve()), "sha256": sha256(f), "bytes": f.stat().st_size}
    prov_path = ROOT / "agent_workspace" / "ou_reversal_csi300_10" / ("r5_provenance_%s.json" % tag)
    prov_path.write_text(json.dumps(prov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("  provenance: %s" % prov_path)
    for k, v in prov["artifacts"].items():
        print("    %-16s %s  %s" % (k, v["sha256"][:16], v["bytes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
