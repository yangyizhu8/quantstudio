import os, sys
from pathlib import Path
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
ROOT = Path(r'D:\miniQMT策略实盘\QuantStudio')
sys.path.insert(0, str(ROOT))
from quantstudio.backtest.run_ptrade_strategy import run_backtest
DB = ROOT / 'data' / 'quantstudio.old_20260920.db'
STRAT = ROOT / 'agent_workspace' / 'ou_reversal_csi300_10' / 'strategy.py'
res, out, eng = run_backtest(str(STRAT), '2025-07-01', '2025-08-29', db_path=str(DB),
                             capital=100000.0, match_price_mode='open',
                             engine_profile='daily-bar-v1')
print('output_dir:', out)
