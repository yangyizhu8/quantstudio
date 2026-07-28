"""临时探针：对 FBP 策略跑 cProfile，聚焦阶段1/2 验收指标。

仅统计 query_bars_by_count_multi_table / query_bars_by_count_batch /
query_security_metadata 的调用次数与耗时，确认 O(N)->O(1) 批量生效。
不改任何框架代码。输出后自行删除。
"""
from __future__ import annotations
import sys, cProfile, pstats
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from quantstudio.backtest.run_ptrade_strategy import main as run_strategy

STRATEGY = _ROOT / "quantstudio" / "backtest" / "strategies" / "first_board_pullback_daily__candidate_quantstudio.py"
START = sys.argv[1] if len(sys.argv) > 1 else "2026-04-01"
END = sys.argv[2] if len(sys.argv) > 2 else "2026-04-02"

prof = cProfile.Profile()
old = sys.argv
sys.argv = ["run_ptrade_strategy", str(STRATEGY), START, END]
try:
    prof.enable()
    run_strategy()
    prof.disable()
except SystemExit:
    prof.disable()
finally:
    sys.argv = old

ps = pstats.Stats(prof).sort_stats("cumulative")
lines = [f"===== FBP cProfile 焦点函数（{START}..{END}） ====="]
targets = ["query_bars_by_count_multi_table", "query_bars_by_count_batch",
           "query_security_metadata", "filter_stock_by_status", "get_bars_by_count",
           "_resolve_status_source"]
for func, stat in ps.stats.items():
    fname = func[2]
    for t in targets:
        if t in fname:
            cc, nc, tt, ct, _ = stat
            lines.append(f"{t:34s} ncalls={nc:6d}  tottime={tt:8.3f}s  cumtime={ct:8.3f}s")
            break
lines.append("===== 完成 =====")
Path("bench_artifacts/profile_fbp_out.txt").write_text("\n".join(lines), encoding="utf-8")
