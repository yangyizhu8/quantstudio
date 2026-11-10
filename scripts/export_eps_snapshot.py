#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QMT EPS 面板快照导出（output/qmt_snapshot/eps_snapshot.json + 可选 --emit-py 常量源）。

设计：docs/qmt-eps-align-design-20261108.md §十一（C1 甲案：EPS 面板同源对齐，
本地 fin_indicator 口径重建，QMT 侧消费嵌入式常量源，零 fileIO）。
范式样板：scripts/export_qfq_factor_snapshot.py（同款 main(argv)->int 退出码 /
--db/--out/--since/--emit-py 参数 / _SCAN_SQL + fetchmany 分批 / repr 全精度序列化
/ 产物禁时间戳字节幂等）。

口径裁定（§11.1 / §11.3，任务包 eps-impl-zcode-taskpkg-20261109.md）：
- 主源 fin_indicator（口径 eps），按 (code, end_date) 左联 income_statement；
- 分叉行（eps 与 basic_eps 均非 NULL 且不等）以 basic_eps 为准——对齐 QMT
  s_fa_eps_basic 的 basic 语义（§11.1-②：全库 2,346 行双向分叉）；
- eps NULL 行跳过并计数（任务包判据 7 字面口径：不回退 basic_eps）；
- ann_date NULL 行必须排除（不当 0——公告日是 PIT 可见性锚，NULL 即不可见）。

PIT 映射（§11.2 定谳）：ann_date ↔ m_anntime（公告日＝可见性锚）、end_date ↔
m_timetag（报告期）。导出值 = (end_d8, ann_d8, eps) 三元组，消费端按
ann_date <= D-1 取最近一期。d8 换算取**北京墙钟（固定 +8）**——库内财务
时间轴毫秒 epoch 均为北京午夜锚（实测证据见 _TZ_BJ 注释）。--since 为
公告日扫描窗下界（ann_date 锚；粗窗宁可多取仅更早历史）。

--emit-py（嵌入式批，2026-11-09）：额外写出 Python 常量源（两条模块级赋值
_QS_QMT_EPS_SNAP_DATA / _QS_QMT_EPS_SNAP_META），供转换期注入产物源码（与
qfq_snap_embed 同机制）。格式硬约束：全 ASCII；键＝6 位 code 字符串且按 code
排序（禁 dict 迭代顺序抖动——幂等）；值＝按 (end_d8, ann_d8) 升序的三元组
序列，eps 用 repr() 全精度（禁 round/截断；float(repr(x)) == x 往返守卫见
tests/test_qmt_eps_snapshot.py T8）；META 仅 enabled/base/n 三键且禁任何时间
戳字段（同库同参重跑字节一致）；base＝窗内最新公告日（观测锚＝n+base 两值）。

体积护栏（§11.3-5：嵌入上限 1.5 MB）：emit 产物超限打印 WARN，并用
--universe-file（只导指定 code 集合）与 --recent-periods N（每 code 只保留
近 N 期，按 end_d8 升序取尾 N）两裁剪参数重导。

用法（于仓库根目录）：
    python scripts/export_eps_snapshot.py \
        [--db data/quantstudio.db] \
        [--out output/qmt_snapshot/eps_snapshot.json] \
        [--since 20000101] [--universe-file <codes.txt>] \
        [--recent-periods N] \
        [--emit-py output/qmt_snapshot/eps_snap_embed.py]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import duckdb

DEFAULT_DB = 'data/quantstudio.db'
DEFAULT_OUT = 'output/qmt_snapshot/eps_snapshot.json'
# 公告日窗下界默认值：EPS PIT 面板需要完整报告期史（消费端按 ann_date<=D-1
# 取最近一期），取远早于库内最早公告的粗窗，默认即全量
DEFAULT_SINCE = '20000101'

# 流式取数批大小（fetchmany——禁一次 fetchall 全库入内存，性能约束，同 qfq 范式）
BATCH_ROWS = 50000

# 嵌入产物体积告警线：1.5 MB（方案 §11.3-5 体积上限，超限提示走裁剪参数）
SIZE_WARN_BYTES = 1536 * 1024

# 表存在性检查（main schema；缺表即显式退出码 2，禁静默降级）
_TABLE_CHECK_SQL = (
    "SELECT COUNT(*) FROM information_schema.tables "
    "WHERE table_schema = 'main' AND table_name = ?")

# ann_date NULL 行计数（PIT 可见性锚缺失必须排除，不当 0——显式计数打印）
_ANN_NULL_SQL = 'SELECT COUNT(*) FROM fin_indicator WHERE ann_date IS NULL'

# 主扫描：fin_indicator 左联 income_statement 取分叉裁定值。
# WHERE 按 ann_date（公告日＝可见性锚）过滤窗下界；ORDER BY 保证分批流式下
# 逐 code 有序且同键冲突时「保留先到」确定（income_statement 实测
# (code,end_date) 无重复，联表不扩行——冲突计数为防御位，显式打印不静默）。
_SCAN_SQL = """
SELECT fi.code, fi.end_date, fi.ann_date, fi.eps, ist.basic_eps
FROM fin_indicator fi
LEFT JOIN income_statement ist
       ON ist.code = fi.code AND ist.end_date = fi.end_date
WHERE fi.ann_date >= ?
ORDER BY fi.code, fi.end_date, fi.ann_date, ist.ann_date
"""

_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)

# 库内财务时间轴锚＝北京墙钟（Asia/Shanghai 固定 +8，无夏令时）：fin_indicator
# 的 ann_date/end_date 毫秒 epoch 均为北京午夜锚（实测 002568 end epoch
# 1774886400000 = 2026-03-30T16:00Z = 北京 2026-03-31 零点），且本地管线
# duckdb_data_access.py:1955 以 Asia/Shanghai 锚定财务年界——d8 换算必须取
# 固定 +8 墙钟（取 UTC 会系统性早一天，与设计文档锚 2026-03-31/2026-04-29
# 不符；取 session 时区则跨时区机器漂移，均禁用）
_TZ_BJ = timezone(timedelta(hours=8))


def _ms_to_d8(ms):
    """毫秒 epoch -> YYYYMMDD int（北京墙钟，机器时区无关）。

    整数 timedelta 运算构造 UTC 时刻后 astimezone(+8) 取墙钟——不经浮点
    epoch 除法，微秒级精确；固定 +8 与库内采集锚一致（见 _TZ_BJ 注释）。
    """
    dt = _EPOCH_UTC + timedelta(milliseconds=int(ms))
    return int(dt.astimezone(_TZ_BJ).strftime('%Y%m%d'))


def _build_emit_py_source(records, base, compress=False):
    """构造 --emit-py 常量源文本（嵌入式 EPS 面板数据源）。

    两条模块级赋值（与 qfq_snap_embed 同范式，消费端经转换期注入产物源码）：
      _QS_QMT_EPS_SNAP_DATA = {'002568': ((20251231, 20260429, 0.62), ...), ...}
      _QS_QMT_EPS_SNAP_META = {'enabled': True, 'base': 20260429, 'n': 5211}
    compress=True 时改产压缩形态（单条 _QS_QMT_EPS_SNAP_ZB64 + 同款 META，无 DATA），
    供体积 > 1.5MB 场景使用（§13.3 两级裁定的第二级）。
    格式硬约束：键=6 位 code 字符串且按 code 排序（幂等）；值=按 (end_d8,
    ann_d8) 升序的三元组序列，eps 用 repr() 全精度（禁 round/截断）；外包
    元组恒带尾逗号（单期 code 的 ((e, a, eps)) 不得折叠成裸三元组——qfq 快照
    T7 同款守卫）；META 仅 enabled/base/n 三键，禁任何时间戳字段（同库同参
    重跑字节一致）；全 ASCII、行尾统一 \\n（二进制写出，禁平台 CRLF 翻译）；
    base=窗内最新公告日（int）。
    """
    lines = [
        '# QMT EPS snapshot constants (embedded data source).',
        '# Regenerate via: python scripts/export_eps_snapshot.py --emit-py <path>',
        '_QS_QMT_EPS_SNAP_DATA = {',
    ]
    if compress:
        # 压缩形态（--compress，2026-11-10 段二同批·§13.3 两级裁定）：体积 > 1.5MB
        # 时启用——产出单条 _QS_QMT_EPS_SNAP_ZB64 = base64(zlib(json(records)))，
        # META 同款（不含 DATA）；消费端 _qs_eps_snap_state() 双形态自适应
        # （先看 DATA，缺则解 ZB64）。json 用紧凑分隔符+排序键，保证同库同参字节一致；
        # 隐式字符串拼接分块（每块 116 字符）便于 diff 与粘贴，运行时自动并为单一 str。
        import base64 as _b64
        import json as _js
        import zlib as _zl
        payload = _js.dumps(
            {str(k): [[int(e), int(a), float(v)] for e, a, v in records[k]]
             for k in sorted(records)},
            separators=(',', ':'), sort_keys=True)
        blob = _b64.b64encode(
            _zl.compress(payload.encode('utf-8'), 9)).decode('ascii')
        lines = [
            '# QMT EPS snapshot constants (embedded data source, zlib+base64 form).',
            '# Regenerate via: python scripts/export_eps_snapshot.py --emit-py <path> --compress',
            '_QS_QMT_EPS_SNAP_ZB64 = (',
        ]
        for _i in range(0, len(blob), 116):
            lines.append("    '%s'" % blob[_i:_i + 116])
        lines.append(')')
        lines.append("_QS_QMT_EPS_SNAP_META = {'enabled': True, 'base': %d, 'n': %d}"
                     % (int(base), len(records)))
        return '\n'.join(lines) + '\n'
    for code in sorted(records):
        pairs = ', '.join('(%d, %d, %s)' % (e, a, repr(float(v)))
                          for e, a, v in records[code])
        lines.append("    '%s': (%s,)," % (str(code), pairs))
    lines.append('}')
    lines.append("_QS_QMT_EPS_SNAP_META = {'enabled': True, 'base': %d, 'n': %d}"
                 % (int(base), len(records)))
    return '\n'.join(lines) + '\n'


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description='导出 QMT EPS 面板快照（PIT 三元组 JSON；见模块 docstring）')
    ap.add_argument('--db', default=DEFAULT_DB,
                    help='DuckDB 库路径（默认生产库 %s，相对 cwd）' % DEFAULT_DB)
    ap.add_argument('--out', default=DEFAULT_OUT,
                    help='输出 JSON 路径（默认 %s）' % DEFAULT_OUT)
    ap.add_argument('--since', default=DEFAULT_SINCE,
                    help='公告日扫描窗下界 YYYYMMDD（默认 %s；按 ann_date 过滤'
                         '——PIT 可见性锚，粗窗宁可多取仅更早历史）' % DEFAULT_SINCE)
    ap.add_argument('--universe-file', default=None,
                    help='可选：代码清单文件（每行一个 6 位代码，空行忽略）——'
                         '只导出清单内 code 并打印 universe 覆盖率')
    ap.add_argument('--recent-periods', default=None,
                    help='可选：每 code 只保留最近 N 期（按 end_d8 升序取尾 N，'
                         '须为正整数）——体积裁剪参数之一')
    ap.add_argument('--emit-py', default=None,
                    help='可选：额外写出 Python 常量源文件（嵌入式 EPS 面板数据'
                         '源；全 ASCII、无时间戳字段、按 code 排序——同库同参'
                         '重跑字节一致）')
    ap.add_argument('--compress', action='store_true',
                    help='产出 zlib+base64 压缩形态（_QS_QMT_EPS_SNAP_ZB64，体积约 1/5；'
                         '>1.5MB 场景用·§13.3 两级裁定第二级）')
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print('[eps-snapshot] FAIL db 不存在: %s' % db_path, file=sys.stderr)
        return 2
    if not (args.since and args.since.isdigit() and len(args.since) == 8):
        print('[eps-snapshot] FAIL --since 须为 YYYYMMDD 8 位数字: %r'
              % (args.since,), file=sys.stderr)
        return 2
    recent_n = None
    if args.recent_periods is not None:
        if not (args.recent_periods.isdigit() and
                int(args.recent_periods) > 0):
            print('[eps-snapshot] FAIL --recent-periods 须为正整数: %r'
                  % (args.recent_periods,), file=sys.stderr)
            return 2
        recent_n = int(args.recent_periods)
    universe = None
    if args.universe_file:
        try:
            with open(args.universe_file, 'r', encoding='utf-8') as f:
                universe = {ln.strip() for ln in f if ln.strip()}
        except OSError as exc:
            print('[eps-snapshot] FAIL --universe-file 读取失败 %r: %s'
                  % (args.universe_file, exc), file=sys.stderr)
            return 2
        if not universe:
            print('[eps-snapshot] FAIL --universe-file 为空（无任何代码）: %r'
                  % (args.universe_file,), file=sys.stderr)
            return 2
    since_ms = int(datetime.strptime(args.since, '%Y%m%d')
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)

    # 只读连接（WAL/daemon 纪律）；连接失败（被 daemon/其他进程占用、库损坏
    # 等）显式退出码 2——禁裸 traceback（退出码 1）逸出
    try:
        con = duckdb.connect(db_path, read_only=True)
    except duckdb.Error as exc:
        print('[eps-snapshot] FAIL db 连接失败（可能被 daemon/其他进程占用）: '
              '%s: %s' % (db_path, exc), file=sys.stderr)
        return 2
    try:
        # 表存在性显式检查（缺 fin_indicator＝无主源；缺 income_statement＝
        # 分叉行裁定不可执行——两者均显式退出，禁静默降级为「仅 eps」导出）
        for tbl in ('fin_indicator', 'income_statement'):
            n = con.execute(_TABLE_CHECK_SQL, [tbl]).fetchone()[0]
            if n == 0:
                print('[eps-snapshot] FAIL 库内无 %s 表（%s）'
                      % (tbl, 'EPS 主源' if tbl == 'fin_indicator'
                         else '分叉行裁定依赖 basic_eps'), file=sys.stderr)
                return 2
        ann_null_rows = con.execute(_ANN_NULL_SQL).fetchone()[0]

        cur = con.execute(_SCAN_SQL, [since_ms])
        per_code = {}       # code -> {(end_d8, ann_d8): eps}（dict 键天然去重）
        scanned_rows = 0    # 扫描窗内原始行数（分母）
        eps_null_rows = 0   # eps NULL 跳过行数（判据 7：不回退 basic_eps）
        eps_nan_rows = 0    # eps NaN 跳过行数（NaN 入面板会让下游比较全 False）
        diverged_rows = 0   # eps 与 basic_eps 分叉、按 basic_eps 取值行数
        bad_code_rows = 0   # code 非 6 位数字的行数（键契约守卫，跳过并打印）
        bad_time_rows = 0   # end_date NULL 行数（防御位：主键契约外形态）
        dup_rows = 0        # 同 (code,end_d8,ann_d8) 同值重复行数（无损去重）
        conflict_rows = 0   # 同键异值冲突行数（保留先到——ORDER BY 确定序）
        while True:
            batch = cur.fetchmany(BATCH_ROWS)
            if not batch:
                break
            for code, end_ms, ann_ms, eps, basic in batch:
                scanned_rows += 1
                if not (isinstance(code, str) and len(code) == 6
                        and code.isdigit()):
                    bad_code_rows += 1
                    continue
                if end_ms is None:
                    bad_time_rows += 1
                    continue
                if eps is None:
                    eps_null_rows += 1
                    continue
                value = float(eps)
                if value != value:  # NaN 判定（self-inequality，禁 math.isnan 依赖）
                    eps_nan_rows += 1
                    continue
                if basic is not None:
                    basic_f = float(basic)
                    if basic_f == basic_f and basic_f != value:
                        value = basic_f  # 分叉行以 basic_eps 为准（basic 语义）
                        diverged_rows += 1
                key = (_ms_to_d8(end_ms), _ms_to_d8(ann_ms))
                bucket = per_code.setdefault(code, {})
                prev = bucket.get(key)
                if prev is None:
                    bucket[key] = value
                elif prev == value:
                    dup_rows += 1
                else:
                    conflict_rows += 1
    finally:
        con.close()

    # 裁剪：universe 过滤 -> 每 code 按 (end_d8, ann_d8) 升序 -> 近 N 期取尾
    records = {}
    trimmed_rows = 0
    for code in sorted(per_code):
        if universe is not None and code not in universe:
            continue
        tuples = [(e, a, v) for (e, a), v in sorted(per_code[code].items())]
        if recent_n is not None and len(tuples) > recent_n:
            trimmed_rows += len(tuples) - recent_n
            tuples = tuples[-recent_n:]
        records[code] = tuples

    if not records:
        print('[eps-snapshot] FAIL 无可用行（扫描 %d 行全被排除/过滤：检查 '
              '--db/--since/--universe-file）' % scanned_rows, file=sys.stderr)
        return 2

    base = max(t[1] for tuples in records.values() for t in tuples)
    doc = {
        'enabled': True,
        'base': base,
        'since': args.since,
        'records': {k: records[k] for k in sorted(records)},
    }
    out_path = os.path.abspath(args.out)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    data = json.dumps(doc, ensure_ascii=False).encode('utf-8')
    with open(out_path, 'wb') as f:
        f.write(data)

    n_codes = len(records)
    n_rows = sum(len(v) for v in records.values())
    size = os.path.getsize(out_path)
    print('[eps-snapshot] 写出 %s' % out_path)
    print('[eps-snapshot] 总标的数=%d 导出行数=%d 文件字节数=%d'
          % (n_codes, n_rows, size))
    print('[eps-snapshot] 扫描行数=%d annNULL排除行数=%d epsNULL跳过行数=%d'
          % (scanned_rows, ann_null_rows, eps_null_rows))
    print('[eps-snapshot] epsNaN跳过行数=%d 分叉行数=%d(basic_eps取值)'
          % (eps_nan_rows, diverged_rows))
    print('[eps-snapshot] 非法code行数=%d 无效时间轴行数=%d 重复行数=%d '
          '冲突行数=%d' % (bad_code_rows, bad_time_rows, dup_rows, conflict_rows))
    if args.emit_py:
        # 嵌入式数据源：二进制写出（行尾恒 \n，禁平台 CRLF 翻译），全 ASCII
        emit_path = os.path.abspath(args.emit_py)
        emit_dir = os.path.dirname(emit_path)
        if emit_dir:
            os.makedirs(emit_dir, exist_ok=True)
        emit_text = _build_emit_py_source(records, base, compress=bool(getattr(args, 'compress', False)))
        emit_payload = emit_text.encode('ascii')
        with open(emit_path, 'wb') as f:
            f.write(emit_payload)
        print('[eps-snapshot] emit_py=%s bytes=%d' % (emit_path, len(emit_payload)))
        if len(emit_payload) > SIZE_WARN_BYTES:
            print('[eps-snapshot] WARN emit 体积 %d B 超 1.5MB 上限（方案 '
                  '§11.3-5）：请用 --universe-file / --recent-periods 裁剪后重导'
                  % len(emit_payload))
    if universe is not None:
        hit = universe & set(records)
        pct = (len(hit) * 100.0 / len(universe)) if universe else 0.0
        print('[eps-snapshot] universe基数=%d universe内有记录=%d 覆盖率=%.1f%%'
              % (len(universe), len(hit), pct))
    if recent_n is not None:
        print('[eps-snapshot] recent-periods=%d 裁剪行数=%d'
              % (recent_n, trimmed_rows))
    return 0


if __name__ == '__main__':
    sys.exit(main())
