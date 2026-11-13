# -*- coding: utf-8 -*-
"""cp 标签解析修正版（R387 审核补正原件）。

与首版的差异：cp 标签解析由 p.split('.')[-3]（取段错误，输出 '?'）
改为 f.rsplit('.', 2)[-2]。扫描逻辑不变。
用法：python pyd_scan_fixed.py > raw_cptag_fixed.txt
"""
import os
import struct
import sys

import numpy as np

d = os.path.dirname(np.__file__)
rows = []
tags = set()
for root, dirs, files in os.walk(d):
    for f in sorted(files):
        if f.endswith('.pyd'):
            rel = os.path.relpath(os.path.join(root, f), d)
            parts = f.rsplit('.', 2)
            tag = parts[1] if len(parts) == 3 else '?'
            tags.add(tag)
            rows.append((rel, tag))

print('PY %s BITS %d EXE %s' % (sys.version.split()[0],
                                struct.calcsize('P') * 8, sys.executable))
print('NUMPY %s @ %s' % (np.__version__, d))
print('NUMPY_PYD_TOTAL %d' % len(rows))
for rel, tag in rows:
    print('  pyd %-58s %s' % (rel, tag))
print('NUMPY_PYD_CP_TAGS %s' % sorted(tags))
print('UNIQUE_TAGS %d' % len(tags))
print('ALL_SAME_TAG %s' % (len(tags) == 1))
print('PYD_SCAN_FIXED_END')
