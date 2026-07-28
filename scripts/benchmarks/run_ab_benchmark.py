"""P0 性能优化 A/B benchmark（可重复、参数化、自包含）。

单次运行：`python run_ab_benchmark.py --mode optimized --out run1.json`
交错编排：`python run_ab_benchmark.py --order B,O,O,B,B,O`（自身派生子进程）

mode=baseline 通过进程内 monkeypatch「复现优化前代码路径」：
  - _existing_tables 每次都执行 SHOW TABLES（等价于优化前的无缓存实现）。
  这样 baseline 与 optimized 共用同一份代码二进制与同一 harness，仅优化点被关闭，
  避免任何二进制/环境差异，且可重复。

注意：
  - 回测使用的数据库路径由 quantstudio._paths.db_path() 决定（内部固定），本脚本不
    再接受无效的 --db 参数；运行时会打印实际库绝对路径与实际 quantstudio 模块路径，
    并断言导入的 quantstudio.__file__ 属于当前 worktree，防止被 PYTHONPATH 主仓库遮蔽。
  - 最终验证结论只把 SHOW TABLES 152→1 与 SQL 调用减少作为确定性收益；端到端耗时波动
    大（运行间噪声），仅作为高噪声观察，不宣称稳定提升。

输出 JSON 字段：elapsed_s, sql_calls, sql_total_ms, by_table, final_nav, trades,
nav_hash, mode, order_index, actual_db, actual_module。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 强制使用本仓库（worktree）的 quantstudio，避免被 PYTHONPATH 中的主仓库遮蔽。
_ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/benchmarks -> repo root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import argparse
import hashlib
import json
import re
import statistics
import subprocess
import time

import duckdb
import quantstudio as _quantstudio

# 断言导入的 quantstudio 确实属于当前 worktree，否则直接失败（防止测错代码）。
assert Path(_quantstudio.__file__).resolve().is_relative_to(_ROOT), (
    f"quantstudio 解析到错误位置: {_quantstudio.__file__} 不属于 worktree {_ROOT}"
)

from quantstudio._paths import db_path as _default_db_path
from quantstudio.backtest.backtest_engine import BacktestEngine
from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.run_ptrade_strategy import main as run_strategy

PROJECT = Path(__file__).resolve().parent.parent.parent  # scripts/benchmarks -> repo root
STRATEGY_PATH = PROJECT / "quantstudio" / "backtest" / "strategies" / "小市值策略ptrade.py"
_ACTUAL_DB = _default_db_path()
_ACTUAL_MODULE = _quantstudio.__file__

STATS = {"sql_calls": 0, "sql_total_ms": 0.0, "by_table": {}}
_TABLE_RE = re.compile(r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)\b", re.I)

orig_execute = duckdb.DuckDBPyConnection.execute


def execute_proxy(self, sql, *a, **k):
    STATS["sql_calls"] += 1
    t = time.perf_counter()
    try:
        return orig_execute(self, sql, *a, **k)
    finally:
        dt = (time.perf_counter() - t) * 1000.0
        STATS["sql_total_ms"] += dt
        s = str(sql).strip()
        if s.upper().startswith("SHOW"):
            tbl = "SHOW_TABLES"
        else:
            m = _TABLE_RE.search(s)
            tbl = m.group(1) if m else "<other>"
        d = STATS["by_table"].setdefault(tbl, {"calls": 0, "ms": 0.0})
        d["calls"] += 1
        d["ms"] += dt


def _install_baseline_patches():
    """进程内复现优化前行为：关闭 _existing_tables 的 SHOW TABLES 缓存。

    bars cache 已于 2026-07-28 门 1 收敛整改中移除，不再模拟。
    """

    def no_cache_existing(self):
        conn = self._get_conn()
        if conn is None:
            return set()
        return {row[0] for row in conn.execute("SHOW TABLES").fetchall()}

    DuckDBDataAccess._existing_tables = no_cache_existing


def run_once(mode: str, start: str, end: str, strategy: str = None):
    duckdb.DuckDBPyConnection.execute = execute_proxy
    if mode == "baseline":
        _install_baseline_patches()
    # optimized 模式直接运行，无额外 patch（bars cache 已移除；SHOW TABLES 缓存生效）。

    captured = {}
    orig_run = BacktestEngine.run

    def _run_capture(self):
        r = orig_run(self)
        # engine.run() 返回 (BacktestResult, output_dir) 元组
        captured["result"] = r[0] if isinstance(r, tuple) else r
        return r

    BacktestEngine.run = _run_capture

    old_argv = sys.argv
    strategy_path = strategy or str(STRATEGY_PATH)
    sys.argv = ["run_ptrade_strategy", strategy_path, start, end]
    try:
        t0 = time.perf_counter()
        run_strategy()
        elapsed = time.perf_counter() - t0
    finally:
        sys.argv = old_argv
        BacktestEngine.run = orig_run

    res = captured.get("result")
    final_nav = None
    trades = None
    nav_hash = None
    if res is not None and getattr(res, "nav_history", None):
        final_nav = float(res.nav_history[-1]["nav"])
        trades = len(res.trade_records)
        nav_blob = json.dumps(res.nav_history, ensure_ascii=False, sort_keys=True,
                              default=str).encode("utf-8")
        nav_hash = hashlib.sha256(nav_blob).hexdigest()

    return {
        "mode": mode,
        "elapsed_s": round(elapsed, 4),
        "sql_calls": STATS["sql_calls"],
        "sql_total_ms": round(STATS["sql_total_ms"], 2),
        "by_table": {k: {"calls": v["calls"], "ms": round(v["ms"], 2)}
                     for k, v in STATS["by_table"].items()},
        "final_nav": final_nav,
        "trades": trades,
        "nav_hash": nav_hash,
        "actual_db": str(_ACTUAL_DB),
        "actual_module": str(_ACTUAL_MODULE),
    }


def _aggregate(order, start, end, strategy):
    out_dir = Path("bench_artifacts")
    out_dir.mkdir(exist_ok=True)
    runs = []
    for i, mode in enumerate(order):
        out = out_dir / f"ab_run_{i+1}_{mode}.json"
        cmd = [sys.executable, str(Path(__file__)), "--single", "--mode", mode,
               "--out", str(out), "--strategy", str(strategy)]
        print(f"[A/B] run {i+1}/{len(order)} mode={mode} -> {out.name}", flush=True)
        subprocess.run(cmd, check=True)
        runs.append(json.loads(out.read_text(encoding="utf-8")))
    summary = _summarize(runs)
    (out_dir / "ab_summary.json").write_text(
        json.dumps({"order": order, "runs": runs, "summary": summary},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _summarize(runs):
    base = [r["elapsed_s"] for r in runs if r["mode"] == "baseline"]
    opt = [r["elapsed_s"] for r in runs if r["mode"] == "optimized"]
    out = {"order": [r["mode"] for r in runs]}
    for name, arr in (("baseline", base), ("optimized", opt)):
        if arr:
            out[f"{name}_elapsed"] = {
                "per_run": arr,
                "median": round(statistics.median(arr), 4),
                "min": round(min(arr), 4),
                "max": round(max(arr), 4),
                "range_pct": round((max(arr) - min(arr)) / min(arr) * 100, 2),
            }
    if base and opt:
        b_med, o_med = statistics.median(base), statistics.median(opt)
        out["paired_median_diff_pct"] = round((b_med - o_med) / b_med * 100, 2)
        out["paired_median_abs_s"] = round(b_med - o_med, 4)
    # SQL 调用 / SHOW TABLES 调用汇总（取中位数）
    def med_of(key):
        bv = [r[key] for r in runs if r["mode"] == "baseline"]
        ov = [r[key] for r in runs if r["mode"] == "optimized"]
        return (round(statistics.median(bv), 2) if bv else None,
                round(statistics.median(ov), 2) if ov else None)
    out["sql_calls_median"] = dict(zip(("baseline", "optimized"), med_of("sql_calls")))
    # SHOW TABLES 调用中位数（确定性收益，独立报告）
    def show_med(mode):
        vals = []
        for r in runs:
            if r["mode"] == mode:
                vals.append(r["by_table"].get("SHOW_TABLES", {}).get("calls", 0))
        return round(statistics.median(vals), 2) if vals else None
    out["show_tables_calls_median"] = {"baseline": show_med("baseline"),
                                       "optimized": show_med("optimized")}
    # 结果一致性（baseline vs optimized nav_hash 应相同）
    out["nav_hash_consistent"] = (
        len({r["nav_hash"] for r in runs if r["nav_hash"]}) == 1
    )
    out["final_nav"] = {r["mode"]: r["final_nav"] for r in runs}
    out["actual_db"] = runs[0]["actual_db"]
    out["actual_module"] = runs[0]["actual_module"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--single", action="store_true", help="只跑一次（由 --mode 指定）")
    ap.add_argument("--mode", choices=["baseline", "optimized"], default="optimized")
    ap.add_argument("--out", default="bench_artifacts/ab_run.json")
    ap.add_argument("--strategy", default=str(STRATEGY_PATH),
                    help="策略文件路径（默认小市值策略ptrade.py）")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-04-29")
    ap.add_argument("--order", default="B,O,O,B,B,O",
                    help="交错顺序（仅非 --single 时使用），B=baseline O=optimized")
    args = ap.parse_args()

    print(f"[A/B] actual_db={_ACTUAL_DB}")
    print(f"[A/B] actual_module={_ACTUAL_MODULE}")

    if args.single:
        STATS["sql_calls"] = 0
        STATS["sql_total_ms"] = 0.0
        STATS["by_table"] = {}
        result = run_once(args.mode, args.start, args.end, args.strategy)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return

    order = [x.strip().upper() for x in args.order.split(",") if x.strip()]
    order = ["baseline" if x == "B" else "optimized" for x in order]
    _aggregate(order, args.start, args.end, args.strategy)


if __name__ == "__main__":
    main()
