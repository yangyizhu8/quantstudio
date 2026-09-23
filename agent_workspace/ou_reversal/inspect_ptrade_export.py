import json, os, re
ROOT = r'D:\miniQMT策略实盘\QuantStudio'
D = os.path.join(ROOT, 'output', 'ptrade_export', '沪深300均值回归超跌反弹')
rep = json.load(open(os.path.join(D, 'source_import_report.json'), encoding='utf-8'))
print('== source_import_report ==')
for k, v in rep.items():
    if k in ('rewrites', 'shims', 'issues', 'injections'):
        print('  %s: %s' % (k, json.dumps(v, ensure_ascii=False)[:800]))
    else:
        print('  %s: %s' % (k, json.dumps(v, ensure_ascii=False)[:300]))
print()
card = json.load(open(os.path.join(D, 'run_card.json'), encoding='utf-8'))
print('== run_card ==')
for k in ('strategy_id','stage','status','targets','validation','engine_profile','notes'):
    if k in card: print('  %s: %s' % (k, json.dumps(card[k], ensure_ascii=False)[:500]))
print()
src = open(os.path.join(D, '沪深300均值回归超跌反弹_ptrade.py'), encoding='utf-8').read()
print('== 转换产物关键检查 ==')
print('  总行数:', src.count(chr(10))+1)
for pat, label in ((r'get_index_day_bar', 'get_index_day_bar 调用'), (r'def _qs_shim_get_index_day_bar|_shim', 'shim 定义'),
                   (r'include=True', 'include=True 出现'), (r'include=False', 'include=False 出现'),
                   (r'import duckdb', 'duckdb 导入(应无)'), (r'quantstudio\.', 'quantstudio 内部导入(应无)')):
    print('  %-28s %d' % (label, len(re.findall(pat, src))))