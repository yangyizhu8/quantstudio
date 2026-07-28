"""临时探针：dump 全量 cumulative top-80 到文件，定位 74s 真实来源。

写 bench_artifacts/callee_out.txt。不改框架代码。
"""
from __future__ import annotations
import sys, io, cProfile, pstats
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from quantstudio.backtest.run_ptrade_strategy import main as run_strategy

STRATEGY = _ROOT / "quantstudio" / "backtest" / "strategies" / "小市值策略ptrade.py"
START, END = "2026-01-01", "2026-04-29"

prof = cProfile.Profile()
old = sys.argv
sys.argv = ["run_ptrade_strategy", str(STRATEGY), START, END]
try:
    prof.enable(); run_strategy(); prof.disable()
except SystemExit:
    prof.disable()
finally:
    sys.argv = old

ps = pstats.Stats(prof).sort_stats("cumulative")
sio = io.StringIO()
ps.print_stats(80, stream=sio)
Path("bench_artifacts/callee_out.txt").write_text(sio.getvalue(), encoding="utf-8")
