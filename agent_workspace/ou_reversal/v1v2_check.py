import json, os
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
D = os.path.join(ROOT, 'output', 'ptrade_export', '沪深300均值回归超跌反弹')
rep = json.load(open(os.path.join(D, 'source_import_report.json'), encoding='utf-8'))
dm = rep.get('design_metadata_resolution', {})
print('status        :', dm.get('status'))
print('engine_profile:', dm.get('engine_profile'))
print('strategy_id   :', dm.get('strategy_id'))
print('source_import :', rep.get('status'))
acts = rep.get('actions', [])
print('SHIM actions  :', [a.get('api_name') for a in acts if a.get('action_type')=='SHIM'])
print('BLOCK actions :', [a.get('api_name') for a in acts if a.get('action_type')=='BLOCK'])