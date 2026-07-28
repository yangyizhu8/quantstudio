"""回测端到端 cProfile 热路径定位（只读观测，不改任何框架代码）。

用法：
  python profile_backtest.py --out bench_artifacts/profile.txt

输出：按 cumulative 与 tottime 排序的 top-N 函数，含命中次数与耗时，
用于定位真正的 CPU 热点（而非仅 SQL 调用计数）。
"""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/benchmarks -> repo root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import quantstudio as _q

assert Path(_q.__file__).resolve().is_relative_to(_ROOT), (
    f"quantstudio 解析到错误位置: {_q.__file__} 不属于 worktree {_ROOT}"
)

from quantstudio.backtest.run_ptrade_strategy import main as run_strategy

PROJECT = Path(__file__).resolve().parent.parent.parent
STRATEGY_PATH = PROJECT / "quantstudio" / "backtest" / "strategies" / "小市值策略ptrade.py"


def run_once(strategy: str, start: str, end: str):
    old_argv = sys.argv
    sys.argv = ["run_ptrade_strategy", strategy, start, end]
    prof = cProfile.Profile()
    try:
        prof.enable()
        try:
            run_strategy()
        except SystemExit:
            pass
        finally:
            prof.disable()
    finally:
        sys.argv = old_argv
    return prof


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default=str(STRATEGY_PATH))
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-04-29")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--out", default="bench_artifacts/profile.txt")
    args = ap.parse_args()

    prof = run_once(args.strategy, args.start, args.end)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    ps = pstats.Stats(prof, stream=buf)
    ps.sort_stats("cumulative")
    ps.print_stats(args.top)
    cum = buf.getvalue()

    buf2 = io.StringIO()
    ps2 = pstats.Stats(prof, stream=buf2)
    ps2.sort_stats("tottime")
    ps2.print_stats(args.top)
    tot = buf2.getvalue()

    with out.open("w", encoding="utf-8") as f:
        f.write(cum)
        f.write("\n===== TOP BY TOTTIME =====\n")
        f.write(tot)

    # 终端仅打印 tottime 段，便于快速看热点
    print(tot)
    print(f"[profile] cumulative+tottime 已写入 {out}")


if __name__ == "__main__":
    main()
