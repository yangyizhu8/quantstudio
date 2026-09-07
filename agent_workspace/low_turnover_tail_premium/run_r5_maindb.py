"""主库核对驱动：A 组主口径在权威主库 quantstudio.db 上双跑，验证备用库结论一致性。
Usage: python run_r5_maindb.py <run_tag>
"""
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(r'D:\\miniQMT策略实盘\\QuantStudio')
sys.path.insert(0, str(ROOT))

tag = sys.argv[1]

from quantstudio.backtest.run_ptrade_strategy import run_backtest
from quantstudio.backtest.backtest_engine import DEFAULT_TRADE_COST

STRATEGY = ROOT / 'agent_workspace' / 'low_turnover_tail_premium' / 'strategy.py'
LOGDIR = ROOT / 'agent_workspace' / 'low_turnover_tail_premium' / 'r5_logs_maindb'
LOGDIR.mkdir(parents=True, exist_ok=True)
logfile = LOGDIR / ('M_%s.log' % tag)

fh = logging.FileHandler(logfile, mode='w', encoding='utf-8')
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(fh)

# 主口径成本（R2.5⑧）
base = DEFAULT_TRADE_COST.__dict__
cost = DEFAULT_TRADE_COST.__class__(**{
    **base,
    'commission_rate': 0.0003,
    'min_commission': 5.0,
    'slippage_rate': 0.001,
    'fixed_slippage': 0.0,
})


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


result, output_dir, engine = run_backtest(
    str(STRATEGY), '2025-01-02', '2026-07-31',
    db_path=ROOT / 'data' / 'quantstudio.db',
    capital=100_000,
    match_price_mode='close',
    engine_profile='daily-bar-v1',
    cost=cost,
)

manifest = {
    'group': 'A_maindb',
    'tag': tag,
    'strategy_source': str(STRATEGY),
    'strategy_sha256': sha256(STRATEGY),
    'result_dir': str(Path(output_dir).resolve()),
    'window': ['2025-01-02', '2026-07-31'],
    'capital': 100000,
    'match_price_mode': 'close',
    'engine_profile': 'daily-bar-v1',
    'db_used': 'quantstudio.db (主库, 守护已解锁)',
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
out = LOGDIR / ('M_%s_manifest.json' % tag)
with open(out, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
print(json.dumps({'manifest': str(out), 'result_dir': manifest['result_dir'],
                  'strategy_return_pct': manifest['metrics_summary'].get('strategy_return_pct'),
                  'benchmark_return_pct': manifest['metrics_summary'].get('benchmark_return_pct')},
                 ensure_ascii=False))
