"""R5 driver: one independent backtest run per invocation (G3.5 独立进程).

Usage: python run_r5.py <A|B> <run_tag>
  A = 主口径（commission 万3 + slippage 0.001，R2.5⑧）
  B = 灵敏度对照（commission 万3 + slippage 0.002）
Window/capital fixed per customer confirmation: 2025-01-02..2026-07-31, 100000 CNY.
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

STRATEGY = ROOT / 'agent_workspace' / 'low_turnover_tail_premium' / 'strategy.py'
LOGDIR = ROOT / 'agent_workspace' / 'low_turnover_tail_premium' / 'r5_logs'
LOGDIR.mkdir(parents=True, exist_ok=True)
logfile = LOGDIR / ('%s_%s.log' % (group, tag))

fh = logging.FileHandler(logfile, mode='w', encoding='utf-8')
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(fh)

# 成本契约（R2.5⑧）：双边万3 佣金最低5元 + 引擎默认印花税/过户费 + 比例滑点（A=0.001/B=0.002）
base = DEFAULT_TRADE_COST.__dict__
cost = DEFAULT_TRADE_COST.__class__(**{
    **base,
    'commission_rate': 0.0003,
    'min_commission': 5.0,
    'slippage_rate': 0.001 if group == 'A' else 0.002,
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
    db_path=ROOT / 'data' / 'quantstudio.db.bak.balance_refill',  # 备用库（主库被 mcp 守护写锁；window 2025-01-02..2026-07-31 覆盖完整）
    capital=100_000,
    match_price_mode='close',
    engine_profile='daily-bar-v1',
    cost=cost,
)

manifest = {
    'group': group,
    'tag': tag,
    'strategy_source': str(STRATEGY),
    'strategy_sha256': sha256(STRATEGY),
    'result_dir': str(Path(output_dir).resolve()),
    'window': ['2025-01-02', '2026-07-31'],
    'capital': 100000,
    'match_price_mode': 'close',
    'engine_profile': 'daily-bar-v1',
    'db_used': 'quantstudio.db.bak.balance_refill (备用库, 用户裁定; 主库 mcp_stock_minutes 写锁中)',
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
out = LOGDIR / ('%s_%s_manifest.json' % (group, tag))
with open(out, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
print(json.dumps({'manifest': str(out), 'result_dir': manifest['result_dir']},
                 ensure_ascii=False))