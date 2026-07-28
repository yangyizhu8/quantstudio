"""黄金结果绑定与字节级一致性校验（可重复、参数化）。

运行：
    python run_golden.py --mode optimized --out bench_artifacts/golden_optimized.json
    python run_golden.py --mode baseline  --out bench_artifacts/golden_baseline.json

绑定字段（baseline 与 optimized 必须字节级一致，因优化是语义等价内部改写）：
    nav_history（含每日 cash / market_value / positions 计数）
    trade_records（含 commission / tax）
    metrics_summary
    round_trips（订单 / 往返）
    corporate_actions
输出 JSON 含 golden（完整结果）、summary（标量）、hash（golden 规范化 JSON 的 sha256）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import quantstudio as _quantstudio

# 断言导入的 quantstudio 确实属于当前 worktree，否则直接失败。
assert Path(_quantstudio.__file__).resolve().is_relative_to(_ROOT), (
    f"quantstudio 解析到错误位置: {_quantstudio.__file__} 不属于 worktree {_ROOT}"
)

from quantstudio._paths import db_path as _default_db_path
from quantstudio.backtest.backtest_engine import BacktestEngine
from quantstudio.backtest.run_ptrade_strategy import main as run_strategy

PROJECT = Path(__file__).resolve().parent.parent.parent
STRATEGY_PATH = PROJECT / "quantstudio" / "backtest" / "strategies" / "小市值策略ptrade.py"
_ACTUAL_DB = _default_db_path()
_ACTUAL_MODULE = _quantstudio.__file__

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess

_captured = {}
_orig_run = BacktestEngine.run


def _run_capture(self):
    r = _orig_run(self)
    _captured["result"] = r[0] if isinstance(r, tuple) else r
    return r


def _install_baseline_patches():
    """复现优化前行为：关闭 _existing_tables 的 SHOW TABLES 缓存。

    bars cache 已于 2026-07-28 门 1 收敛整改中移除，不再模拟。
    """

    def no_cache_existing(self):
        conn = self._get_conn()
        if conn is None:
            return set()
        return {row[0] for row in conn.execute("SHOW TABLES").fetchall()}

    DuckDBDataAccess._existing_tables = no_cache_existing


def run_once(mode, start, end, strategy):
    if mode == "baseline":
        _install_baseline_patches()
    BacktestEngine.run = _run_capture
    old_argv = sys.argv
    sys.argv = ["run_ptrade_strategy", strategy, start, end]
    try:
        run_strategy()
    finally:
        sys.argv = old_argv
        BacktestEngine.run = _orig_run

    res = _captured["result"]
    last = res.nav_history[-1]
    golden = {
        "nav_history": res.nav_history,
        "trade_records": res.trade_records,
        "metrics_summary": getattr(res, "metrics_summary", None),
        "round_trips": getattr(res, "round_trips", None),
        "corporate_actions": getattr(res, "corporate_actions", None),
    }
    canonical = json.dumps(golden, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    summary = {
        "final_nav": float(last["nav"]),
        "final_cash": float(last["cash"]),
        "final_market_value": float(last["market_value"]),
        "final_positions_count": last.get("positions"),
        "trades_count": len(res.trade_records),
        "commission_sum": float(sum(float(t.get("commission", 0) or 0)
                                     for t in res.trade_records)),
        "tax_sum": float(sum(float(t.get("tax", 0) or 0)
                             for t in res.trade_records)),
        "nav_history_len": len(res.nav_history),
        "round_trips_count": len(getattr(res, "round_trips", []) or []),
        "corporate_actions_count": len(getattr(res, "corporate_actions", []) or []),
    }
    return {"mode": mode, "golden": golden, "summary": summary, "hash": digest,
            "actual_db": str(_ACTUAL_DB), "actual_module": str(_ACTUAL_MODULE)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["baseline", "optimized"], default="optimized")
    ap.add_argument("--out", default="bench_artifacts/golden_optimized.json")
    ap.add_argument("--strategy", default=str(STRATEGY_PATH))
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-04-29")
    args = ap.parse_args()
    print(f"[GOLDEN] actual_db={_ACTUAL_DB}")
    print(f"[GOLDEN] actual_module={_ACTUAL_MODULE}")
    out = run_once(args.mode, args.start, args.end, args.strategy)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2,
                                         default=str), encoding="utf-8")
    print(json.dumps({"mode": out["mode"], "hash": out["hash"],
                      "summary": out["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
