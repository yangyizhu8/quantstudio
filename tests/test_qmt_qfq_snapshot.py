"""tests for QFQ 因子快照层（qfq factor snapshot，QMT wrapper 取数面，2026-11-07）。

设计：docs/qmt-adjust-basis-align-design-20261106.md §二（甲案：QMT 侧向本地
对齐）＋ §2.3 三态表；V0 探测：docs/evidence/qmt-qfq-snapshot-probe-20261106.md。

测试模式（与 tests/test_qmt_instrument_snapshot.py 同款）：QmtSourceConverter
渲染产物 -> exec 进独立命名空间 -> tmp_path 写快照 -> 改命名空间内
_QS_QMT_QFQ_SNAP_PATH -> mock C（get_market_data_ex 回放 {code: df} 并记录
dividend_type）断言。每个用例独立 exec（产物模块级 _QS_QMT_QFQ_STATE 三态缓存
隔离——运行中新建/变更文件不生效）。

用例（任务包 T1-T6）：
  T1 三态：loaded 行含 n=/base=/gen=；absent/broken 必打印（fail-loud，
     broken 覆盖坏 JSON/records 非 dict/enabled 非 true 三形态）
  T2 幂等：态缓存生效，重复取数（含后缀/裸两种 code 形态）不重复打印状态行
  T3 换算等价（5 快照形态 x 5 行日期 = 25 例 >= 20）：五列换算值逐位 ==
     raw * F(t)/F_base（== 优先，math.isclose rel=0 abs=1e-12 兜底）；
     volume 不换算（仍为 raw*100）；loaded 态 dividend_type='none'
  T4 无除权零扰动：快照 loaded 但 code 无记录 -> 与 absent 路径逐位相同
     （assert_frame_equal 含 dtype）
  T5 GBK：产物整体 encode('gbk') 通过（含 HISTORY_EXT 新快照段）
  T6 教训回归（R325）：_QS_QMT_HISTORY_EXT 源码串无 'columns', []) or
     真值陷阱写法（pandas Index 布尔求值抛 ValueError 被外层 except 吞掉）
"""

import json
import math
from datetime import datetime

import pandas as pd
from pandas.testing import assert_frame_equal

from quantstudio.strategy_compiler.source_import_qmt import (
    QmtSourceConverter, _QS_QMT_HISTORY_EXT)

# 与 tests/test_qmt_instrument_snapshot.py 最小正例同源：生命周期双件 +
# get_history 调用（渲染产物内嵌 HISTORY_EXT 快照层）。
_MIN_SOURCE = (
    "def initialize(ctx):\n"
    "    g.pool = ['600000.SS']\n"
    "    log.info('init')\n"
    "\n"
    "\n"
    "def handle_data(ctx, data):\n"
    "    hist = get_history('600000.SS', 5, '1d', ['close'])\n"
    "    total = context.portfolio.total_value\n"
    "    log.info('total=%s' % total)\n"
)

_FIELDS = ['open', 'high', 'low', 'close', 'preClose', 'volume']

# mock bar 日（E1 剔除阈值）：合成 df 全部行日期必须 < 该日（naive 构造 +
# fromtimestamp 还原，任意时区机器上 bar 日期恒为 20260603）
_BAR_DAY = datetime(2026, 6, 3, 12, 0)

# T3 行日期：跨两个跳变日之前/当日/之间/当日/之后（V0 实测 600971 两个跳变点
# 20250523/20260529 的同款邻界形态）
_T3_DATES = ['20250522', '20250523', '20250526', '20260529', '20260601']

# T3 快照形态：末端锚 1.0 / 末端非 1.0（须除以 F_base）/ base 居中两跳之间 /
# 真实 6 位小数比率 / base 早于全部跳变（F_base=1.0）
_T3_CONFIGS = [
    ('anchor_last1', [[20250523, 0.5], [20260529, 1.0]], '20260601'),
    ('fbase_ne_1', [[20250523, 0.5], [20260529, 0.75]], '20260601'),
    ('base_mid', [[20250523, 1.5], [20260529, 0.25]], '20260525'),
    ('realistic', [[20260110, 0.967451], [20260529, 1.0]], '20260601'),
    ('base_early', [[20250523, 0.5], [20260529, 1.0]], '20250101'),
]


def _render_ns():
    """渲染产物 + exec 进独立命名空间（每用例独立 exec = 三态缓存隔离）。"""
    result = QmtSourceConverter(_MIN_SOURCE).convert()
    assert result.errors == []
    code = result.converted_code
    # 产物须整体物理 GBK 可编码（含 HISTORY_EXT 新增快照段——写盘点前提）
    code.encode('gbk')
    ns = {}
    exec(compile(code, '<qmt-product>', 'exec'), ns)
    return ns


def _bind(ns, path):
    """把产物命名空间内快照约定路径改绑到测试路径（函数 globals 即 ns）。"""
    ns['_QS_QMT_QFQ_SNAP_PATH'] = str(path)


def _write_qfq(tmp_path, records, base='20260601', enabled=True):
    """按导出脚本约定写快照：UTF-8 / ensure_ascii=False / int 保持 int。"""
    path = tmp_path / 'qfq_factor_snapshot.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {'enabled': enabled, 'base': base,
           'generated_at': '2026-11-07T00:00:00+08:00',
           'source': 'test', 'since': '20240101',
           'window': '20240101-%s' % base, 'records': records}
    path.write_bytes(json.dumps(doc, ensure_ascii=False).encode('utf-8'))
    return path


class _MockC:
    """mock ContextInfo：get_market_data_ex 回放 {code: df}（copy 防跨用例
    污染）并记录 dividend_type（div 选择断言用）。"""

    def __init__(self, mkt):
        self._mkt = mkt
        self.barpos = 0
        self.dividend_types = []
        self.calls = 0

    def get_bar_timetag(self, ts):
        return int(_BAR_DAY.timestamp() * 1000)

    def get_market_data_ex(self, field_list, stock_list, period='1d',
                           start_time=None, end_time=None, dividend_type=None):
        self.calls += 1
        self.dividend_types.append(dividend_type)
        return {c: self._mkt[c].copy() for c in stock_list if c in self._mkt}


def _make_df(dates):
    """合成日线 df：五列价格 + volume（值全为整值/半值浮点，精确二进制表示，
    避免 == 比较精度噪声——任务包 T3 口径）。"""
    rows = {}
    for i, d in enumerate(dates):
        v = float(i + 2)  # 2.0, 3.0, ...（精确可表示）
        rows[d] = {'open': v, 'high': v + 2.0, 'low': v / 2.0,
                   'close': v * 4.0, 'preClose': v, 'volume': 100.0 + i}
    return pd.DataFrame.from_dict(rows, orient='index')


def _fetch_hist(ns, code, dates, fq='pre'):
    """经产物内 _qs_get_history 取数（mock C 回放合成 df），返回 (out, mock)。"""
    c = _MockC({code: _make_df(dates)})
    out = ns['_qs_get_history']([code], len(dates), '1d', list(_FIELDS),
                                fq=fq, C=c)
    return out, c


def _f_of(jumps, d8):
    """任务包公式侧实现：jumps 中 <= d8 的最后跳变值（无则 1.0）——与 wrapper
    _qs_qfq_factor 同语义（测试独立性：不调产物内函数）。"""
    d8 = int(d8)
    f = 1.0
    for d, r in jumps:
        if d <= d8:
            f = float(r)
        else:
            break
    return f


# ---------------------------------------------------------------------------
# T1 三态
# ---------------------------------------------------------------------------

def test_t1_loaded_line_fields(tmp_path, capsys):
    ns = _render_ns()
    _bind(ns, _write_qfq(
        tmp_path, {'600971': [[20250523, 0.967451], [20260529, 1.0]]}))
    assert callable(ns.get('_qs_qfq_snap_load'))
    out, c = _fetch_hist(ns, '600971.SH', _T3_DATES)
    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith('QS_QMT_QFQ_SNAP')]
    assert len(lines) == 1
    assert lines[0].startswith('QS_QMT_QFQ_SNAP loaded ')
    for field in ('n=1', 'base=20260601', 'gen='):
        assert field in lines[0]
    assert c.dividend_types == ['none']  # loaded -> 取不复权原始价
    assert ns['_QS_QMT_QFQ_STATE']['state'] == 'loaded'
    assert '600971.SH' in out  # 后缀 code 形态取数成功


def test_t1_absent_must_print_and_keep_front(tmp_path, capsys):
    ns = _render_ns()
    _bind(ns, tmp_path / 'nonexistent.json')  # 不存在
    _fetch_hist(ns, '600971.SH', _T3_DATES)
    assert 'QS_QMT_QFQ_SNAP absent' in capsys.readouterr().out  # fail-loud 必打
    assert ns['_QS_QMT_QFQ_STATE']['state'] == 'absent'
    # absent 维持原 div（fq='pre' -> front，行为与今日一致）
    ns2 = _render_ns()
    _bind(ns2, tmp_path / 'nonexistent.json')
    _out2, c2 = _fetch_hist(ns2, '600971.SH', _T3_DATES)
    assert c2.dividend_types == ['front']


def test_t1_broken_three_forms_must_print(tmp_path, capsys):
    # 形态 1：坏 JSON（截断字节）
    ns = _render_ns()
    p = tmp_path / 'snap_bad_json.json'
    p.write_bytes(b'{"enabled": true, "records": {"600971": [[2025052')
    _bind(ns, p)
    _fetch_hist(ns, '600971.SH', _T3_DATES)
    assert 'QS_QMT_QFQ_SNAP broken' in capsys.readouterr().out
    # 形态 2：records 非 dict
    ns2 = _render_ns()
    p2 = tmp_path / 'snap_records_list.json'
    p2.write_bytes(json.dumps({'enabled': True, 'base': '20260601',
                               'records': ['600971']}).encode('utf-8'))
    _bind(ns2, p2)
    _fetch_hist(ns2, '600971.SH', _T3_DATES)
    assert 'QS_QMT_QFQ_SNAP broken' in capsys.readouterr().out
    # 形态 3：enabled 非 true
    ns3 = _render_ns()
    p3 = tmp_path / 'snap_enabled_false.json'
    p3.write_bytes(json.dumps({'enabled': False, 'base': '20260601',
                               'records': {'600971': [[20260529, 1.0]]}}
                              ).encode('utf-8'))
    _bind(ns3, p3)
    _out3, c3 = _fetch_hist(ns3, '600971.SH', _T3_DATES)
    assert 'QS_QMT_QFQ_SNAP broken' in capsys.readouterr().out
    assert c3.dividend_types == ['front']  # broken 回退原 div


# ---------------------------------------------------------------------------
# T2 幂等
# ---------------------------------------------------------------------------

def test_t2_state_cached_single_print(tmp_path, capsys):
    ns = _render_ns()
    _bind(ns, _write_qfq(tmp_path, {'600971': [[20260529, 1.0]]}))
    _fetch_hist(ns, '600971.SH', _T3_DATES)   # 后缀形态（首取，打印状态行）
    _fetch_hist(ns, '600971.SH', _T3_DATES)   # 重复取数
    _fetch_hist(ns, '600971', _T3_DATES)      # 裸 6 位形态（快照键直命中）
    out = capsys.readouterr().out
    assert out.count('QS_QMT_QFQ_SNAP') == 1  # 状态行整次运行恰一次


# ---------------------------------------------------------------------------
# T3 换算等价（25 例）
# ---------------------------------------------------------------------------

def test_t3_conversion_equivalence(tmp_path, capsys):
    raw = _make_df(_T3_DATES)
    for name, jumps, base in _T3_CONFIGS:
        ns = _render_ns()
        _bind(ns, _write_qfq(tmp_path / name, {'600971': jumps}, base=base))
        out, c = _fetch_hist(ns, '600971.SH', _T3_DATES)
        capsys.readouterr()  # 清状态行
        assert c.dividend_types == ['none']
        got = out['600971.SH']
        fbase = _f_of(jumps, base)
        for col in ('open', 'high', 'low', 'close', 'preClose'):
            for d in _T3_DATES:  # 5 形态 x 5 日期 = 25 例（每例五列断言）
                exp = raw.loc[d, col] * (_f_of(jumps, d) / fbase)
                g = got.loc[d, col]
                assert g == exp or math.isclose(g, exp, rel_tol=0.0,
                                                abs_tol=1e-12), (
                    name, col, d, g, exp)
        for d in _T3_DATES:
            # volume 不换算：仅 QMT 手->股折算（x100），快照换算不得触达
            assert got.loc[d, 'volume'] == raw.loc[d, 'volume'] * 100


# ---------------------------------------------------------------------------
# T4 无除权零扰动
# ---------------------------------------------------------------------------

def test_t4_no_record_code_zero_perturbation(tmp_path, capsys):
    # loaded 路径：快照只含 600971 记录，取 300214（V0 零跳变对照同款形态）
    ns_load = _render_ns()
    _bind(ns_load, _write_qfq(
        tmp_path / 'loaded',
        {'600971': [[20250523, 0.967451], [20260529, 1.0]]}))
    got_load, c_load = _fetch_hist(ns_load, '300214.SH', _T3_DATES)
    assert c_load.dividend_types == ['none']
    assert 'QS_QMT_QFQ_SNAP loaded' in capsys.readouterr().out
    # absent 路径（同一合成 df 基底）
    ns_abs = _render_ns()
    _bind(ns_abs, tmp_path / 'loaded' / 'nonexistent.json')
    got_abs, c_abs = _fetch_hist(ns_abs, '300214.SH', _T3_DATES)
    assert c_abs.dividend_types == ['front']
    capsys.readouterr()
    # 五列 + volume 逐位相同（assert_frame_equal 默认含 dtype——未记录 code
    # 整段换算被跳过，dtype 零扰动）
    assert_frame_equal(got_load['300214.SH'], got_abs['300214.SH'])


# ---------------------------------------------------------------------------
# T5 GBK
# ---------------------------------------------------------------------------

def test_t5_product_gbk_encodable():
    result = QmtSourceConverter(_MIN_SOURCE).convert()
    assert result.errors == []
    code = result.converted_code
    assert code.encode('gbk')  # 整体物理 GBK 可编码（含 HISTORY_EXT 新快照段）
    assert '_QS_QMT_QFQ_SNAP_PATH' in code  # 新段确随产物注入


# ---------------------------------------------------------------------------
# T6 教训回归（R325：pandas Index 真值陷阱）
# ---------------------------------------------------------------------------

def test_t6_no_columns_truthiness_trap_in_template():
    assert "'columns', []) or" not in _QS_QMT_HISTORY_EXT
