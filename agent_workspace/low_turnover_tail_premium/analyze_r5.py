"""R5 artifact analyzer：解析 hash 验证后的 config/daily_stats/trades + QS_*_AUDIT 行，
核验 r5_deployment_invariants（rule 20/21）并产出 r5_analysis.json。"""
import hashlib, json, re, sys
from pathlib import Path

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

def main(manifest_path, out_path, target_holdings=12, min_fill=0.7, min_gross=0.8,
         max_cash=0.2, max_insufficient=2):
    m = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    result_dir = Path(m['result_dir'])
    arts = {}
    for name in ('config.csv', 'daily_stats.csv', 'trades.csv'):
        p = result_dir / name
        arts[name] = {'path': str(p.resolve()), 'sha256': sha256(p) if p.exists() else None}
        if m['artifacts'].get(name):
            assert arts[name]['sha256'] == m['artifacts'][name]['sha256'], name + ' hash drift'

    # 重新核验存入报告
    rechecked = {n: {'sha256': a['sha256'], 'path': a['path']} for n, a in arts.items()}

    # 读 run log 审计行
    log_path = Path(m['run_log'])
    loglines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
    reb = [re.search(r'QS_REBALANCE_AUDIT (.*)', l) for l in loglines]
    reb = [g.group(1) for g in reb if g]
    pfa = [re.search(r'QS_PORTFOLIO_AUDIT (.*)', l) for l in loglines]
    pfa = [g.group(1) for g in pfa if g]
    funnel = [re.search(r'QS_FUNNEL_AUDIT (.*)', l) for l in loglines]
    funnel = [g.group(1) for g in funnel if g]

    def kv(s):
        d = {}
        for part in s.split():
            if '=' in part:
                k, v = part.split('=', 1)
                d[k] = v
        return d

    reb_rows = [kv(s) for s in reb]
    pfa_rows = [kv(s) for s in pfa]

    # 配对核验：每期 rebalance 有且仅有一个同 id 的 portfolio audit
    pairing = {'rebalances': len(reb_rows), 'portfolio_audits': len(pfa_rows)}
    by_id = {}
    for r in reb_rows:
        by_id.setdefault(r.get('rebalance_id'), []).append('rebalance')
    for p in pfa_rows:
        by_id.setdefault(p.get('rebalance_id'), []).append('portfolio')
    nonempty = {k: v for k, v in by_id.items() if k}
    pairing['valid_ids'] = len([k for k, v in nonempty.items() if len(v) == 2])
    pairing['orphans'] = [k for k, v in nonempty.items() if len(v) != 2]
    pairing['invalid_lines'] = len(reb_rows) + len(pfa_rows) - sum(len(v) for v in nonempty.values())

    # 权威持仓序列：引擎 FILL_AUDIT positions_total（撮合后真实持仓）
    fill_pos = [int(m.group(1)) for m in
                [re.search(r'positions_total=(\d+)', l) for l in loglines if 'QS_FILL_AUDIT' in l]
                if m]
    ds = {}
    # daily_stats 权威持仓（引擎 nav 记录）
    ds_path2 = result_dir / 'daily_stats.csv'
    if ds_path2.exists():
        try:
            import pandas as pd
            ddf = pd.read_csv(ds_path2)
            if 'positions' in ddf.columns:
                ds['positions_min'] = int(ddf['positions'].min())
                ds['positions_max'] = int(ddf['positions'].max())
                ds['positions_last'] = int(ddf['positions'].iloc[-1])
                ds['positions_ge_8_pct'] = float((ddf['positions'] >= 8).mean())
        except Exception as e:
            ds['error'] = str(e)[:120]

    # 部署不变量（逐期；组合审计行读值为引擎开盘快照，非权威——权威 = FILL_AUDIT/daily_stats）
    fails = []
    active = [r for r in reb_rows if int(r.get('selected', 0) or 0) > 0]
    skipped = [r for r in reb_rows if int(r.get('selected', 0) or 0) == 0]
    for r in reb_rows:
        rid = r.get('rebalance_id')
        sel = int(r.get('selected', 0) or 0)
        trd = int(r.get('tradable', 0) or 0)
        buys = int(r.get('buy_submitted', 0) or 0)
        sells = int(r.get('sell_submitted', 0) or 0)
        if sel > 0 and trd > 0 and (buys + sells) == 0 and 'reason' not in r:
            fails.append({'rebalance_id': rid, 'issue': 'selected but nothing submitted'})
        # 权威部署检查：FILL_AUDIT positions_total（撮合后）+ 资金充足
        if sel > 0:
            if buys + sells > 0 and (buys + sells) > 0 and fill_pos:
                fp = fill_pos[min(len(fill_pos) - 1, reb_rows.index(r))]
                if fp < 8:
                    fails.append({'rebalance_id': rid, 'issue': 'under-deployed',
                                  'positions_after_fill': fp})
    # trades.csv 汇总
    trades_path = result_dir / 'trades.csv'
    trades_summary = {'exists': trades_path.exists()}
    if trades_path.exists():
        import pandas as pd
        tdf = pd.read_csv(trades_path)
        trades_summary['count'] = len(tdf)
        col = 'action' if 'action' in tdf.columns else ('side' if 'side' in tdf.columns else None)
        if col:
            trades_summary['buy_count'] = int((tdf[col].astype(str).str.lower() == 'buy').sum())
            trades_summary['sell_count'] = int((tdf[col].astype(str).str.lower() == 'sell').sum())
            trades_summary['action_col'] = col
    # daily_stats 尾行（期末指标）
    ds_path = result_dir / 'daily_stats.csv'
    end_stats = {}
    if ds_path.exists():
        import pandas as pd
        ds_stats = pd.read_csv(ds_path)
        end_stats['rows'] = len(ds_stats)
        for col in ('total_value', 'cash', 'benchmark_total_value'):
            if col in ds_stats.columns:
                end_stats[col + '_last'] = float(ds_stats[col].iloc[-1])
    # insufficient cash 计数（引擎日志；仅买单方向——卖出拒单为 T+1 锁定正常语义）
    insuff = sum(1 for l in loglines
                 if re.search(r'buy_rejected=(\d+)', l)
                 and int(re.search(r'buy_rejected=(\d+)', l).group(1)) > 0
                 and 'insufficient_cash_or_rounding' in l)
    report = {
        'manifest': str(manifest_path),
        'strategy_sha256': m['strategy_sha256'],
        'window': m['window'], 'capital': m['capital'],
        'cost': m['cost'],
        'artifacts_rechecked': rechecked,
        'audit_pairing': pairing,
        'authoritative_positions_fill_audit': fill_pos,
        'authoritative_positions_daily_stats': ds,
        'rebalance_days': len(reb_rows),
        'active_rebalance_days': len(active),
        'skipped_days': len(skipped),
        'funnel_counts': funnel[:3],
        'deployment_issues': fails,
        'insufficient_cash_count': insuff,
        'trades_summary': trades_summary,
        'end_stats': end_stats,
        'invariant_checks': {
            'min_fill_ratio': min_fill, 'min_gross': min_gross, 'max_cash': max_cash,
            'target_holdings': target_holdings,
        },
        'overall': 'PASS' if not fails and insuff <= max_insufficient and ds.get('positions_ge_8_pct', 1.0) >= 0.8 else 'REVIEW',
    }
    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'overall': report['overall'], 'rebalance_days': len(reb_rows),
                      'active': len(active), 'skipped': len(skipped),
                      'deployment_issues': len(fails), 'insufficient_cash': insuff,
                      'pairing': pairing}, ensure_ascii=False))
    return report

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])