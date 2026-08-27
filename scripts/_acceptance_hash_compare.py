"""验收 V2 共享对比脚本：在给定工作区（当前 or HEAD worktree）转换 SAMPLE，输出产物 sha256。"""
import hashlib
import sys

sys.path.insert(0, '.')
from quantstudio.strategy_compiler.source_import import SourceConverter

SAMPLE = ("def initialize(context):\n    pass\n\n"
          "def handle_data(context, data):\n"
          "    df = get_fundamentals('000001.SZ', table='eps', fields=['eps'])\n"
          "    df2 = get_fundamentals(['000001.SZ','600000.SH'], table='growth_ability', "
          "fields=['or_yoy'])\n"
          "    df3 = get_fundamentals('000001.SZ', table='eps', fields=['diluted_eps'])\n")

def main():
    for basis in ('passthrough', 'basic', 'diluted'):
        conv = SourceConverter(fidelity_eps_basis=basis)
        out = conv.convert(SAMPLE).converted_code
        h = hashlib.sha256(out.encode('utf-8')).hexdigest()
        print(f'{basis}: sha256={h} len={len(out)}')
    # 默认构造（无显式 basis）应等于 passthrough
    d = SourceConverter().convert(SAMPLE).converted_code
    p = SourceConverter(fidelity_eps_basis='passthrough').convert(SAMPLE).converted_code
    print('default==passthrough:', d == p)

if __name__ == '__main__':
    main()