#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QMT 复权因子快照导出（output/qmt_snapshot/qfq_factor_snapshot.json）。

设计：docs/qmt-adjust-basis-align-design-20261106.md §二（甲案：QMT 侧向本地
对齐；快照只存跳变点，体积可控）。
V0 探测证据：docs/evidence/qmt-qfq-snapshot-probe-20261106.md（§四修正：快照源
= 回测主库 stock_daily 的 *_front_ratio 物化列；qfq_aux.db 不在取数路径，弃用）。

机制：本地 fq='pre' 读 close_front 物化列（duckdb_data_access.py:645-653 就地
别名），其比率列 close_front_ratio 的**跳变点序列**即本地累积复权因子——导出成
紧凑 JSON 后，QMT 侧取不复权原始价（dividend_type='none'）按 P_adj(t) =
P_raw(t) * F(t) / F(base) 换算（消费点 source_import_qmt.py 的 _QS_QMT_HISTORY_EXT
快照三态层），数学上必然复现本地 front 值（V0-4 定量闭合）。

跳变判定：同一 code 相邻两行（按 time 升序）|ratio - prev| > 1e-9 即跳变，记
[date8, 变化后的比率值]；close_front_ratio 为 NULL 的行用 close_front/close
兜底（close 为 0/NULL、或 close_front 为 NULL 则跳过该行并计数——跳过行不更新
prev，跨缺口与下一可用行比较，除权停牌期后在复牌行仍能捕捉跳变）。

幂等可重跑：同库重跑输出相同 records（generated_at 随运行时点变化）；快照文件
可单独删除即令 QMT 侧回退 absent（实盘运行机不部署快照文件——D-30 同款纪律）。

--emit-py（嵌入式批，2026-11-08）：额外写出 Python 常量源文件（两条模块级赋值
_QS_QMT_QFQ_SNAP_DATA / _QS_QMT_QFQ_SNAP_META），供 QmtSourceConverter.convert
(qfq_embed=<该文件>) 在转换期原样注入产物源码——QMT 沙箱禁策略级 fileIO（真机
探针证伪 R331 文件版读文件路径）。格式硬约束：全 ASCII；跳变点 (date8, ratio)
元组、ratio repr() 全精度；META 仅 enabled/base/n 三键且**禁任何时间戳字段**
（同库同窗重跑字节一致——幂等）；键按 code 排序输出。

用法（于仓库根目录）：
    python scripts/export_qfq_factor_snapshot.py \
        [--db data/quantstudio.db] \
        [--out output/qmt_snapshot/qfq_factor_snapshot.json] \
        [--since 20240101] [--universe-file <codes.txt>] \
        [--emit-py output/qmt_snapshot/qfq_snap_embed.py]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import duckdb

DEFAULT_DB = 'data/quantstudio.db'
DEFAULT_OUT = 'output/qmt_snapshot/qfq_factor_snapshot.json'
DEFAULT_SINCE = '20240101'

# 跳变容差：|ratio - prev| > JUMP_EPS 即跳变（上游因子微调是平滑小量，跳变点只
# 应捕捉真实除权事件；与铁律 25「连续有界」判据同一工程口径）
JUMP_EPS = 1e-9

# 流式取数批大小（fetchmany——禁一次 fetchall 全库入内存，性能约束）
BATCH_ROWS = 50000

_SCAN_SQL = """
SELECT code,
       strftime(to_timestamp(time / 1000), '%Y%m%d') AS d8,
       close_front_ratio,
       close_front,
       close
FROM stock_daily
WHERE time >= ?
ORDER BY code, time
"""


def build_emit_py_source(records, base):
    """构造 --emit-py 常量源文本（嵌入式 QFQ 快照数据源，2026-11-08）。

    两条模块级赋值（消费点 source_import_qmt.py 的 QFQ 嵌入式三态层经
    convert(qfq_embed=...) 在转换期原样注入产物源码）：
      _QS_QMT_QFQ_SNAP_DATA = {'600971': ((20250523, 0.96...), ...), ...}
      _QS_QMT_QFQ_SNAP_META = {'enabled': True, 'base': 20261030, 'n': 5385}
    格式硬约束：键=6 位 code 字符串且按 code 排序（禁 dict 迭代顺序抖动——
    幂等）；跳变点=(date8 int, ratio float) 元组，ratio 用 repr() 全精度（禁
    round/截断；float(repr(x)) == x 往返守卫见 tests/test_qmt_qfq_snapshot.py
    T8）；META 仅 enabled/base/n 三键——**禁任何时间戳字段**（可复现性铁律：
    同一导出数据+同一配置 => 本文件字节级一致，产物内禁转换时刻戳）；全
    ASCII；行尾统一 \\n（二进制写出，禁平台 CRLF 翻译）。base 接受
    'YYYYMMDD' 字符串或 int，一律写 int。
    """
    lines = [
        '# QMT QFQ factor snapshot constants (embedded data source).',
        '# Regenerate via: python scripts/export_qfq_factor_snapshot.py --emit-py <path>',
        '_QS_QMT_QFQ_SNAP_DATA = {',
    ]
    for code in sorted(records):
        pairs = ', '.join('(%d, %s)' % (int(d), repr(float(r)))
                          for d, r in records[code])
        # 外包元组必须带尾逗号：单跳变点 code 的 ((d, r)) 会折叠成裸二元组，
        # 运行期逐对归一将 ValueError 判 broken（T7 拦截，2026-11-08 修复）。
        lines.append("    '%s': (%s,)," % (str(code), pairs))
    lines.append('}')
    lines.append("_QS_QMT_QFQ_SNAP_META = {'enabled': True, 'base': %d, 'n': %d}"
                 % (int(str(base).strip()), len(records)))
    return '\n'.join(lines) + '\n'


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description='导出 QMT 复权因子快照（跳变点紧凑 JSON；见模块 docstring）')
    ap.add_argument('--db', default=DEFAULT_DB,
                    help='DuckDB 库路径（默认生产库 %s，相对 cwd）' % DEFAULT_DB)
    ap.add_argument('--out', default=DEFAULT_OUT,
                    help='输出 JSON 路径（默认 %s）' % DEFAULT_OUT)
    ap.add_argument('--since', default=DEFAULT_SINCE,
                    help='扫描起始日 YYYYMMDD（默认 %s；UTC 零点换算毫秒下界，'
                         '粗窗宁可多取仅更早历史）' % DEFAULT_SINCE)
    ap.add_argument('--universe-file', default=None,
                    help='可选：代码清单文件（每行一个代码，空行忽略）——给出则'
                         '打印 universe 覆盖率对比')
    ap.add_argument('--emit-py', default=None,
                    help='可选：额外写出 Python 常量源文件（嵌入式 QFQ 快照数据'
                         '源，convert(qfq_embed=...) 注入产物；全 ASCII、无时间戳'
                         '字段、按 code 排序——同库同窗重跑字节一致）')
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print('[qfq-snapshot] FAIL db 不存在: %s' % db_path, file=sys.stderr)
        return 2
    if not (args.since and args.since.isdigit() and len(args.since) == 8):
        print('[qfq-snapshot] FAIL --since 须为 YYYYMMDD 8 位数字: %r'
              % (args.since,), file=sys.stderr)
        return 2
    since_ms = int(datetime.strptime(args.since, '%Y%m%d')
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)

    con = duckdb.connect(db_path, read_only=True)  # 只读连接（WAL/daemon 纪律）
    try:
        cur = con.execute(_SCAN_SQL, [since_ms])
        records = {}        # code -> [[date8, ratio], ...]（只收录有跳变的 code）
        all_codes = set()   # 扫描窗内出现过的全部 code（universe 覆盖率分母用）
        fallback_rows = 0   # ratio 为 NULL 且 close_front/close 兜底成功的行数
        base_rows = 0       # 【R347】补记为基准跳变点的 code 首值行数
        skipped_rows = 0    # 兜底不可行（close_front/close NULL 或 close=0）跳过行数
        max_d8 = ''         # 扫描窗内最大 date8（= base，库内最新交易日）
        cur_code = None
        prev_ratio = None
        jumps = []
        while True:
            batch = cur.fetchmany(BATCH_ROWS)
            if not batch:
                break
            for code, d8, ratio, close_front, close in batch:
                if code != cur_code:
                    if cur_code is not None and jumps:
                        records[cur_code] = jumps
                    all_codes.add(code)
                    cur_code = code
                    prev_ratio = None
                    jumps = []
                if d8 and d8 > max_d8:
                    max_d8 = d8
                if ratio is None:
                    if close_front is None or close is None or close == 0:
                        skipped_rows += 1
                        continue
                    ratio = float(close_front) / float(close)
                    fallback_rows += 1
                else:
                    ratio = float(ratio)
                if prev_ratio is None:
                    # 【R347 修复】首个有效比率即「该 code 的基准比率」——若其 != 1.0
                    # 必须记录为基准跳变点；否则换算端按「无记录 ⇒ 1.0」处理，
                    # 会让该 code 换算窗内的**远端整段**失去复权（实证：301557 远端
                    # c251 真机 74.38 = raw×1.0 vs 本地 front 51.91，差 22.5）。
                    if abs(ratio - 1.0) > JUMP_EPS:
                        jumps.append([int(d8), ratio])
                        base_rows += 1
                elif abs(ratio - prev_ratio) > JUMP_EPS:
                    jumps.append([int(d8), ratio])
                prev_ratio = ratio
        if cur_code is not None and jumps:
            records[cur_code] = jumps
    finally:
        con.close()

    if not all_codes:
        print('[qfq-snapshot] FAIL 扫描窗 %s 起无任何行（检查 --db/--since）'
              % args.since, file=sys.stderr)
        return 3
    base = max_d8
    doc = {
        'enabled': True,
        'base': base,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'source': db_path,
        'since': args.since,
        'window': '%s-%s' % (args.since, base),
        'records': {k: records[k] for k in sorted(records)},
    }
    out_path = os.path.abspath(args.out)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    data = json.dumps(doc, ensure_ascii=False).encode('utf-8')
    with open(out_path, 'wb') as f:
        f.write(data)

    n_codes = len(all_codes)
    n_jump = len(records)
    n_points = sum(len(v) for v in records.values())
    size = os.path.getsize(out_path)
    print('[qfq-snapshot] 写出 %s' % out_path)
    print('[qfq-snapshot] 总标的数=%d 有跳变标的数=%d 跳变点总数=%d'
          % (n_codes, n_jump, n_points))
    print('[qfq-snapshot] NULL兜底行数=%d 跳过行数=%d 文件字节数=%d'
          % (fallback_rows, skipped_rows, size))
    if size > 2 * 1024 * 1024:
        # 方案 §七 R3：快照体积超 2MB 告警线（只存跳变点预估 <1MB，超线须核查）
        print('[qfq-snapshot] WARN 体积超 2MB 告警线（方案 R3），请核查扫描窗')
    if args.emit_py:
        # 嵌入式数据源（2026-11-08）：二进制写出（行尾恒 \n，禁平台 CRLF 翻译）
        emit_path = os.path.abspath(args.emit_py)
        emit_dir = os.path.dirname(emit_path)
        if emit_dir:
            os.makedirs(emit_dir, exist_ok=True)
        emit_text = build_emit_py_source(records, base)
        emit_payload = emit_text.encode('ascii')
        with open(emit_path, 'wb') as f:
            f.write(emit_payload)
        print('[qfq-snapshot] emit_py=%s bytes=%d' % (emit_path, len(emit_payload)))
    if args.universe_file:
        with open(args.universe_file, 'r', encoding='utf-8') as f:
            uni = {ln.strip() for ln in f if ln.strip()}
        uni_scanned = uni & all_codes
        uni_jump = uni & set(records)
        pct = (len(uni_scanned) * 100.0 / len(uni)) if uni else 0.0
        print('[qfq-snapshot] universe基数=%d universe内扫到数据=%d '
              'universe内有跳变记录=%d 覆盖率=%.1f%%'
              % (len(uni), len(uni_scanned), len(uni_jump), pct))
    else:
        print('[qfq-snapshot] 未给 --universe-file：总标的数=%d = 潜在 universe 上限'
              % n_codes)
    return 0


if __name__ == '__main__':
    sys.exit(main())
