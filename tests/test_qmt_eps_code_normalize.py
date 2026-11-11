# -*- coding: utf-8 -*-
"""QMT EPS 快照「code 归一化」回归测试（QD-2026-0023-F，2026-11-11）。

被测对象：编译器模板串 `_QS_QMT_HISTORY_EXT` 内的
  - `_qs_eps_code6(code)`      标的代码归一化（本批新增）
  - `_qs_eps_align_rows(...)`  批量对齐（接入归一化 + code_miss 计数）
  - `_qs_eps_pick(code, d8)`   PIT 点查（接入归一化）

**为什么不放进 tests/test_qmt_eps_snapshot.py**：该文件 docstring 明写
「本文件只测**导出器**（wrapper 消费层与 QmtSourceConverter 注入路径不在此批）」，
把编译器模板层测试塞进去会破坏其单一职责边界。故新建本文件。

**为什么不 import 编译器模块**：`_qs_eps_code6` 等函数位于模板**字符串**内，
在 `source_import_qmt.py` 的 AST 里不存在（QD-2026-0023 §C 教训：模板串内函数
必须从文本/产物抽取）。本文件用正则取 `_QS_QMT_HISTORY_EXT` 串体再切片 exec。

缺陷背景（真机实证 2026-11-11）：QMT 面板 code 带 `.SZ/.SH/.BJ` 后缀，快照 key 为
6 位纯数字，未归一化导致 `recs.get()` 恒 miss ⇒ 每行误计入 span_short、overridden=0。
"""

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _ROOT / 'quantstudio' / 'strategy_compiler' / 'source_import_qmt.py'


def _history_ext():
    """取模板串 _QS_QMT_HISTORY_EXT 的串体（零依赖，不 import 编译器）。"""
    src = _SRC.read_text(encoding='utf-8')
    m = re.search(r"_QS_QMT_HISTORY_EXT = '''(.*?)'''", src, re.S)
    assert m, '未能在 source_import_qmt.py 中定位 _QS_QMT_HISTORY_EXT 模板串'
    return m.group(1)


def _extract(ext, name):
    """从模板串切出顶层函数源码（到下一个 \\ndef 为止）。"""
    key = 'def %s(' % name
    i = ext.find(key)
    assert i >= 0, '模板串未找到函数 %s' % name
    j = ext.find('\ndef ', i + 1)
    return ext[i:j if j > 0 else len(ext)]


def _make_ns(records, d8_fn=None):
    """构造被测命名空间：注入 _qs_eps_snap_state 桩 + datetime 别名。"""
    ext = _history_ext()
    code = '\n'.join([
        'import datetime as _qs_dt',
        _extract(ext, '_qs_eps_d8'),
        _extract(ext, '_qs_eps_code6'),
        _extract(ext, '_qs_eps_pick'),
        _extract(ext, '_qs_eps_align_rows'),
    ])
    ns = {
        '_QS_QMT_EPS_STATE': None,
        '_qs_eps_snap_state': lambda: {
            'state': 'embedded', 'records': records, 'base8': 20260801,
            'n': len(records), 'reason': 'stub',
        },
    }
    exec(compile(code, '<eps_layer>', 'exec'), ns)
    return ns


# ── 快照样本：002568 两期（end_d8, 值），600000 一期 ──────────────────────
_RECS = {
    # 顺序须与导出器口径一致：按 (end_d8, ann_d8) 升序（_qs_eps_pick 依赖此序取最新可见期）
    '002568': [(20260429, 20251231, 0.62), (20260429, 20260331, 0.21)],
    '600000': [(20260428, 20260331, 1.05)],
}

# QMT 面板上的北京午夜毫秒 epoch（与导出器 _ms 同口径）
def _ms(d8):
    import datetime
    return int(datetime.datetime.strptime(d8, '%Y%m%d').replace(
        tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp() * 1000)


# ===========================================================================
# T1 · A-1 核心：五种 code 形态全部归一化为 6 位纯数字
# ===========================================================================

def test_t1_code6_normalizes_five_forms():
    ns = _make_ns(_RECS)
    f = ns['_qs_eps_code6']
    assert f('002568.SZ') == '002568'
    assert f('600000.SH') == '600000'
    assert f('830799.BJ') == '830799'
    assert f('002568') == '002568'
    assert f(' 002568.sz ') == '002568'   # 小写 + 前后空白


def test_t1b_code6_rejects_invalid():
    ns = _make_ns(_RECS)
    f = ns['_qs_eps_code6']
    for bad in (None, '', '   ', 'abc', '12345', '1234567', 'xxxxxx.SZ', 12345):
        assert f(bad) is None, '非法输入应返回 None：%r' % (bad,)


# ===========================================================================
# T2 · A-1 端到端：带后缀面板行必须命中快照（overridden > 0）
#     —— 这是本轮真机缺陷的直接回归守卫（修复前此处恒 0）
# ===========================================================================

def test_t2_suffixed_codes_hit_snapshot():
    ns = _make_ns(_RECS)
    rows = {
        '002568.SZ': [
            {'s_fa_eps_basic': 0.99, 'm_timetag': _ms('20260331'), 'm_anntime': _ms('20260429')},
            {'s_fa_eps_basic': 0.99, 'm_timetag': _ms('20251231'), 'm_anntime': _ms('20260429')},
        ],
        '600000.SH': [
            {'s_fa_eps_basic': 9.99, 'm_timetag': _ms('20260331'), 'm_anntime': _ms('20260428')},
        ],
    }
    fmap = {'eps': 's_fa_eps_basic', 'publ_date': 'm_anntime', 'end_date': 'm_timetag'}
    out, st = ns['_qs_eps_align_rows'](rows, fmap)

    assert st['overridden'] == 3, '带后缀 code 必须全部命中，实测 %r' % (st,)
    assert st['code_miss'] == 0
    # 覆盖后取快照值
    assert out['002568.SZ'][0]['s_fa_eps_basic'] == 0.21
    assert out['002568.SZ'][1]['s_fa_eps_basic'] == 0.62
    assert out['600000.SH'][0]['s_fa_eps_basic'] == 1.05
    # publ_date / end_date 原样保留（PIT 语义不变）
    assert out['002568.SZ'][0]['m_anntime'] == _ms('20260429')
    assert out['002568.SZ'][0]['m_timetag'] == _ms('20260331')


# ===========================================================================
# T3 · A-2：code_miss 与 span_short 语义分离
#     —— 修复前二者混算，导致真机排查误判为「期次范围不足」
# ===========================================================================

def test_t3_code_miss_separated_from_span_short():
    ns = _make_ns(_RECS)
    rows = {
        '999999.SZ': [   # code 不在快照 ⇒ code_miss
            {'s_fa_eps_basic': 1.0, 'm_timetag': _ms('20260331'), 'm_anntime': _ms('20260429')},
        ],
        '002568.SZ': [   # code 在快照，但期次不符 ⇒ span_short
            {'s_fa_eps_basic': 1.0, 'm_timetag': _ms('20110331'), 'm_anntime': _ms('20110429')},
        ],
    }
    fmap = {'eps': 's_fa_eps_basic', 'publ_date': 'm_anntime', 'end_date': 'm_timetag'}
    _, st = ns['_qs_eps_align_rows'](rows, fmap)

    assert st['code_miss'] == 1, '不在快照的 code 应计 code_miss：%r' % (st,)
    assert st['span_short'] == 1, '期次缺失应计 span_short：%r' % (st,)
    assert st['overridden'] == 0
    assert st['unmatched'] == 0


def test_t3b_unmatched_when_no_usable_end_key():
    ns = _make_ns(_RECS)
    rows = {'002568.SZ': [{'s_fa_eps_basic': 1.0, 'm_timetag': None, 'm_anntime': None}]}
    fmap = {'eps': 's_fa_eps_basic', 'publ_date': 'm_anntime', 'end_date': 'm_timetag'}
    _, st = ns['_qs_eps_align_rows'](rows, fmap)
    assert st['unmatched'] == 1
    assert st['code_miss'] == 0 and st['span_short'] == 0


# ===========================================================================
# T4 · A-1（pick 面）：带后缀 code 的 PIT 点查同样必须命中
# ===========================================================================

def test_t4_pick_accepts_suffixed_code():
    ns = _make_ns(_RECS)
    pick = ns['_qs_eps_pick']
    # ann_d8=20260429 可见 ⇒ 20260601 应取到 20260331 期 0.21
    assert pick('002568.SZ', 20260601) == 0.21
    assert pick('002568', 20260601) == 0.21          # 无后缀等价
    assert pick(' 002568.sz ', 20260601) == 0.21     # 小写+空白等价
    # 公告日未到（20260401 < ann 20260429）⇒ 只能取更早可见期或 None
    assert pick('002568.SZ', 20260401) is None
    # 不在快照的 code
    assert pick('999999.SZ', 20260601) is None


# ===========================================================================
# T5 · 正向对照：明文六位 code 行为与修复前一致（零漂移守卫）
# ===========================================================================

def test_t5_plain_six_digit_unchanged():
    ns = _make_ns(_RECS)
    rows = {'002568': [
        {'s_fa_eps_basic': 0.99, 'm_timetag': _ms('20260331'), 'm_anntime': _ms('20260429')},
    ]}
    fmap = {'eps': 's_fa_eps_basic', 'publ_date': 'm_anntime', 'end_date': 'm_timetag'}
    out, st = ns['_qs_eps_align_rows'](rows, fmap)
    assert st['overridden'] == 1
    assert out['002568'][0]['s_fa_eps_basic'] == 0.21
