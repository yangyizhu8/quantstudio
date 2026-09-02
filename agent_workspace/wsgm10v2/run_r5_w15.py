"""R5 week10 driver: wsgm10v2 主运行 2025 全年窗口（2026-08-30 启动令）。

窗口：2025-01-01 ~ 2025-12-31（全年）；独立进程单次回测；产物三件套 + manifest
（hash-bound）。沿用 run_r5.py 结构（group A=基线滑点 / B=固定滑点 0.02 压力）。
"""
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(r'D:\miniQMT策略实盘\QuantStudio')
sys.path.insert(0, str(ROOT))

group, tag = sys.argv[1], sys.argv[2]
assert group in ('A', 'B'), 'group must be A or B'

from quantstudio.backtest.run_ptrade_strategy import run_backtest
from quantstudio.backtest.backtest_engine import DEFAULT_TRADE_COST

STRATEGY = ROOT / 'agent_workspace' / 'wsgm10v2' / 'strategy.py'
LOGDIR = ROOT / 'agent_workspace' / 'wsgm10v2' / 'r5_logs'
LOGDIR.mkdir(parents=True, exist_ok=True)
logfile = LOGDIR / ('w15_%s_%s.log' % (group, tag))

fh = logging.FileHandler(logfile, mode='w', encoding='utf-8')
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(fh)

if group == 'A':
    cost = DEFAULT_TRADE_COST
else:
    cost = DEFAULT_TRADE_COST.__class__(**{**DEFAULT_TRADE_COST.__dict__,
                                           'fixed_slippage': 0.02,
                                           'slippage_rate': 0.0})


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


result, output_dir, engine = run_backtest(
    str(STRATEGY), '2025-01-01', '2026-03-31',
    db_path=ROOT / 'data' / 'quantstudio.db',
    capital=100_000,
    match_price_mode='close',
    engine_profile='daily-bar-v1',
    cost=cost,
)

manifest = {
    'group': group,
    'tag': tag,
    'campaign': 'week15_R5_main_ext',
    'strategy': 'v2',
    'strategy_source': str(STRATEGY),
    'strategy_sha256': sha256(STRATEGY),
    'result_dir': str(Path(output_dir).resolve()),
    'window': ['2025-01-01', '2026-03-31'],
    'capital': 100000,
    'match_price_mode': 'close',
    'engine_profile': 'daily-bar-v1',
    'cost': {k: float(v) for k, v in cost.__dict__.items()},
    'artifacts': {},
    'run_log': str(logfile.resolve()),
    'run_log_sha256': sha256(logfile),
    'metrics_summary': dict(result.metrics_summary) if result.metrics_summary else {},
}
for name in ('config.csv', 'daily_stats.csv', 'trades.csv'):
    p = Path(output_dir) / name
    manifest['artifacts'][name] = ({'path': str(p.resolve()), 'sha256': sha256(p)}
                                   if p.exists() else None)
out = LOGDIR / ('w15_%s_%s_manifest.json' % (group, tag))
with open(out, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
print(json.dumps({'manifest': str(out), 'result_dir': manifest['result_dir'],
                  'metrics': manifest['metrics_summary']},
                 ensure_ascii=False))
