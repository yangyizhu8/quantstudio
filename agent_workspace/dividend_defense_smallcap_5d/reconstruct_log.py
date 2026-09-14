# -*- coding: utf-8 -*-
"""PS *> /Out-File 折行还原器：把物理行按「时间戳前缀」重组为逻辑日志行。

用法：python reconstruct_log.py <log_path> [<keyword>]
背景（案例第六条）：PowerShell *> 重定向在控制台宽度处**折行**（非截断），
长日志行的尾部落在下一物理行；直接统计物理行会把完整审计行误判为截断。
"""
import re, sys

def logical_lines(path):
    raw = open(path, 'rb').read()
    txt = raw.decode('utf-16', 'replace') if raw[:2] == b'\xff\xfe' else raw.decode('utf-8', 'replace')
    lines = txt.splitlines()
    starts = [i for i, l in enumerate(lines) if re.match(r'^\d{2}:\d{2}:\d{2} ', l)]
    out = []
    for k, s in enumerate(starts):
        e = starts[k+1] if k+1 < len(starts) else len(lines)
        out.append(''.join(lines[s:e]).strip())
    return lines, out

if __name__ == '__main__':
    path = sys.argv[1]
    kw = sys.argv[2] if len(sys.argv) > 2 else 'QS_PERF'
    phys, logi = logical_lines(path)
    hits = [l for l in logi if kw in l]
    print('physical=%d logical=%d hits(%s)=%d' % (len(phys), len(logi), kw, len(hits)))
    for l in hits[:3]:
        print(l)