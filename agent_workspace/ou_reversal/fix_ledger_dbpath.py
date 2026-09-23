import json, os
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
lp = os.path.join(ROOT, 'agent_workspace', 'ou_reversal_csi300_10', 'workspace_state.json')
led = json.load(open(lp, encoding='utf-8'))
led['backtest_db_path'] = r'D:\miniQMT策略实盘\QuantStudio\data\quantstudio.old_20260920.db'
led['external_db_override_confirmed'] = True
open(lp, 'w', encoding='utf-8').write(json.dumps(led, ensure_ascii=False, indent=2) + '\n')
print('backtest_db_path set:', led['backtest_db_path'])