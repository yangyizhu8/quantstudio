import json, os, re
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
D = os.path.join(ROOT, 'output', 'ptrade_export', '沪深300均值回归超跌反弹')
src = open(os.path.join(D, '沪深300均值回归超跌反弹_ptrade.py'), encoding='utf-8').read()
lines = src.split(chr(10))
print('== include=True 出现位置（转换产物）==')
for i, l in enumerate(lines, 1):
    if 'include=True' in l:
        print('  L%-5d %s' % (i, l.strip()[:150]))
print()
print('== get_index_day_bar 出现位置 ==')
for i, l in enumerate(lines, 1):
    if 'get_index_day_bar' in l:
        print('  L%-5d %s' % (i, l.strip()[:150]))
print()
rep = json.load(open(os.path.join(D, 'source_import_report.json'), encoding='utf-8'))
dm = rep.get('design_metadata_resolution', {})
print('== design_metadata_resolution ==')
for k, v in dm.items():
    print('  %-22s %s' % (k, json.dumps(v, ensure_ascii=False)[:220]))