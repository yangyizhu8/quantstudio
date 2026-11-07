"""tests for QFQ 因子快照层·嵌入式（qfq factor snapshot embedded，2026-11-08）。

设计：docs/qmt-adjust-basis-align-design-20261106.md §二（甲案：QMT 侧向本地
对齐）＋ §2.3 三态表；V0 探测：docs/evidence/qmt-qfq-snapshot-probe-20261106.md。
R331 文件版（运行期读 JSON 快照文件）真机证伪——QMT 沙箱 PermissionError 禁
一切策略级文件读取 -> 本批改嵌入式：常量源（export_qfq_factor_snapshot.py
--emit-py 产出）经 QmtSourceConverter.convert(qfq_embed=<path>) 在转换期注入
产物源码。

测试模式（沿 test_qmt_tz_shim.py / 旧文件版同款）：QmtSourceConverter 渲染
产物（convert(qfq_embed=...) 真注入路径）-> exec 进独立命名空间 -> mock C
（get_market_data_ex 回放 {code: df} 并记录 dividend_type）断言。每个用例
独立 exec（产物模块级 _QS_QMT_QFQ_STATE 三态缓存隔离）。

【可复现性铁律】产物内一切文本禁转换时刻戳——embedded 审计行观测锚 = n+base
两值，禁 gen=/任何时间戳片段（T1 断言状态行**精确等值** + 产物全文无 'gen='）。

用例（任务包 T1-T8）：
  T1 三态（常量在位判）：embedded 行精确等值含 n=/base=；不嵌入 -> absent
     必打（fail-loud）；broken 覆盖 enabled 非 true/DATA 非 dict/跳变对坏/
     base 坏四形态
  T2 幂等：态缓存生效，重复取数（含后缀/裸两种 code 形态）不重复打印状态行
  T3 换算等价（5 快照形态 x 5 行日期 = 25 例 >= 20）：五列换算值逐位 ==
     raw * F(t)/F_base（== 优先，math.isclose rel=0 abs=1e-12 兜底）；
     volume 不换算（仍为 raw*100）；embedded 态 dividend_type='none'
  T4 无除权零扰动：embedded 但 code 无记录 -> 与 absent 路径逐位相同
     （assert_frame_equal 含 dtype）
  T5 GBK + 注入面：嵌入产物整体 encode('gbk') 通过（含 HISTORY_EXT 新快照
     段）；两常量确随产物注入；产物全文无 'gen='；模板串无 open( 文件 IO
  T6 教训回归（R325）：_QS_QMT_HISTORY_EXT 源码串无 'columns', []) or
     真值陷阱写法（pandas Index 布尔求值抛 ValueError 被外层 except 吞掉）
  T7 嵌入往返（export --emit-py 全链）：合成 DuckDB fixture 跑导出脚本
     main(--emit-py) 两次 -> exec 后 DATA 与源 records 逐值相等（repr 往返）
     且字节幂等（两次输出逐字节相同 -> sha 相同）；再真注入走通 embedded 态
  T8 repr 精度守卫：固定种子 500 随机浮点 + 500 合成比率 => float(repr(x))
     == x 全真（禁 round/截断的机检哨兵）
"""

import hashlib
import importlib.machinery
import importlib.util
import math
import pathlib
import random
import sys
from datetime import datetime, timezone

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

# T3 快照形态（emit-py 契约：base 为 int）：末端锚 1.0 / 末端非 1.0（须除以
# F_base）/ base 居中两跳之间 / 真实 6 位小数比率 / base 早于全部跳变（F_base=1.0）
_T3_CONFIGS = [
    ('anchor_last1', [(20250523, 0.5), (20260529, 1.0)], 20260601),
    ('fbase_ne_1', [(20250523, 0.5), (20260529, 0.75)], 20260601),
    ('base_mid', [(20250523, 1.5), (20260529, 0.25)], 20260525),
    ('realistic', [(20260110, 0.967451), (20260529, 1.0)], 20260601),
    ('base_early', [(20250523, 0.5), (20260529, 1.0)], 20250101),
]


def _render_ns(qfq_embed=None):
    """渲染产物 + exec 进独立命名空间（每用例独立 exec = 三态缓存隔离）。

    qfq_embed=<emit-py 路径> 走 convert 真注入路径；缺省 None = 不注入（产物
    不含嵌入常量 -> absent 态）。"""
    result = QmtSourceConverter(_MIN_SOURCE).convert(qfq_embed=qfq_embed)
    assert result.errors == []
    code = result.converted_code
    # 产物须整体物理 GBK 可编码（含 HISTORY_EXT 快照段——写盘点前提）
    code.encode('gbk')
    ns = {}
    exec(compile(code, '<qmt-product>', 'exec'), ns)
    return ns


def _embed_body(records, base=20260601, enabled=True, data_literal=None,
                meta_literal=None):
    """构造 emit-py 形态的常量源文本（与 export_qfq_factor_snapshot.py
    build_emit_py_source 同格式：跳变点 (date8, ratio) 元组 + repr 全精度 +
    META 仅 enabled/base/n 三键 int base）。data_literal/meta_literal 直给
    源码串以构造 broken 坏形态。"""
    n = len(records) if records else 0
    if data_literal is None:
        items = []
        for code in sorted(records):
            pairs = ', '.join('(%d, %r)' % (int(d), float(r))
                              for d, r in records[code])
            items.append("    '%s': (%s)," % (str(code), pairs))
        data_literal = '_QS_QMT_QFQ_SNAP_DATA = {\n%s\n}' % ('\n'.join(items),)
    if meta_literal is None:
        meta_literal = ("_QS_QMT_QFQ_SNAP_META = {'enabled': %s, 'base': %d, "
                        "'n': %d}" % ('True' if enabled else 'False',
                                      int(base), n))
    return data_literal + '\n' + meta_literal + '\n'


def _write_embed(dir_path, body, name='qfq_embed.py'):
    """按 emit 契约写常量源文件：全 ASCII、\n 行尾（二进制写）。"""
    p = pathlib.Path(dir_path) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body.encode('ascii'))
    return p


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
# T1 三态（常量在位判）
# ---------------------------------------------------------------------------

def test_t1_embedded_line_exact(tmp_path, capsys):
    p = _write_embed(tmp_path, _embed_body(
        {'600971': [(20250523, 0.967451), (20260529, 1.0)]}))
    ns = _render_ns(qfq_embed=str(p))
    assert callable(ns.get('_qs_qfq_snap_state'))
    out, c = _fetch_hist(ns, '600971.SH', _T3_DATES)
    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith('QS_QMT_QFQ_SNAP')]
    assert len(lines) == 1
    # 精确等值（可复现性铁律：观测锚 = n+base 两值，禁 gen=/任何时间戳片段）
    assert lines[0] == 'QS_QMT_QFQ_SNAP embedded n=1 base=20260601'
    assert c.dividend_types == ['none']  # embedded -> 取不复权原始价
    assert ns['_QS_QMT_QFQ_STATE']['state'] == 'embedded'
    assert '600971.SH' in out  # 后缀 code 形态取数成功


def test_t1_absent_must_print_and_keep_front(capsys):
    ns = _render_ns()  # 不嵌入（产物不含常量 -> absent 态）
    out, c = _fetch_hist(ns, '600971.SH', _T3_DATES)
    captured = capsys.readouterr().out
    assert 'QS_QMT_QFQ_SNAP absent' in captured  # fail-loud 必打
    assert captured.count('QS_QMT_QFQ_SNAP') == 1
    assert ns['_QS_QMT_QFQ_STATE']['state'] == 'absent'
    # absent 维持原 div（fq='pre' -> front，行为与嵌入前一致）
    assert c.dividend_types == ['front']
    assert '600971.SH' in out


def test_t1_broken_enabled_false_must_print(tmp_path, capsys):
    p = _write_embed(tmp_path, _embed_body(
        {'600971': [(20260529, 1.0)]}, enabled=False))
    ns = _render_ns(qfq_embed=str(p))
    out, c = _fetch_hist(ns, '600971.SH', _T3_DATES)
    captured = capsys.readouterr().out
    assert 'QS_QMT_QFQ_SNAP broken' in captured
    assert 'enabled is not true' in captured
    assert ns['_QS_QMT_QFQ_STATE']['state'] == 'broken'
    assert c.dividend_types == ['front']  # broken 回退原 div
    assert '600971.SH' in out


def test_t1_broken_structure_forms_must_print(tmp_path, capsys):
    # 形态 1：DATA 非 dict（常量在位但结构校验失败）
    p1 = _write_embed(
        tmp_path, _embed_body(
            None, data_literal='_QS_QMT_QFQ_SNAP_DATA = [600971]'),
        name='broken_data_list.py')
    ns1 = _render_ns(qfq_embed=str(p1))
    _fetch_hist(ns1, '600971.SH', _T3_DATES)
    assert 'QS_QMT_QFQ_SNAP broken' in capsys.readouterr().out
    assert ns1['_QS_QMT_QFQ_STATE']['state'] == 'broken'
    # 形态 2：跳变对不可数值化（ratio 非 float 可转）
    p2 = _write_embed(
        tmp_path, _embed_body(
            None, data_literal="_QS_QMT_QFQ_SNAP_DATA = "
            "{'600971': ((20260529, 'x'),)}"),
        name='broken_pair.py')
    ns2 = _render_ns(qfq_embed=str(p2))
    _fetch_hist(ns2, '600971.SH', _T3_DATES)
    assert 'QS_QMT_QFQ_SNAP broken' in capsys.readouterr().out
    assert ns2['_QS_QMT_QFQ_STATE']['state'] == 'broken'
    # 形态 3：base 非 YYYYMMDD 量级 int
    p3 = _write_embed(tmp_path, _embed_body(
        {'600971': [(20260529, 1.0)]}, base=123), name='broken_base.py')
    ns3 = _render_ns(qfq_embed=str(p3))
    _fetch_hist(ns3, '600971.SH', _T3_DATES)
    assert 'QS_QMT_QFQ_SNAP broken' in capsys.readouterr().out
    assert ns3['_QS_QMT_QFQ_STATE']['state'] == 'broken'


# ---------------------------------------------------------------------------
# T2 幂等
# ---------------------------------------------------------------------------

def test_t2_state_cached_single_print(tmp_path, capsys):
    p = _write_embed(tmp_path, _embed_body({'600971': [(20260529, 1.0)]}))
    ns = _render_ns(qfq_embed=str(p))
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
        p = _write_embed(tmp_path / name, _embed_body({'600971': jumps},
                                                      base=base))
        ns = _render_ns(qfq_embed=str(p))
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
    # embedded 路径：快照只含 600971 记录，取 300214（V0 零跳变对照同款形态）
    p = _write_embed(tmp_path / 'embedded', _embed_body(
        {'600971': [(20250523, 0.967451), (20260529, 1.0)]}))
    ns_emb = _render_ns(qfq_embed=str(p))
    got_emb, c_emb = _fetch_hist(ns_emb, '300214.SH', _T3_DATES)
    assert c_emb.dividend_types == ['none']
    assert 'QS_QMT_QFQ_SNAP embedded' in capsys.readouterr().out
    # absent 路径（同一合成 df 基底）
    ns_abs = _render_ns()
    got_abs, c_abs = _fetch_hist(ns_abs, '300214.SH', _T3_DATES)
    assert c_abs.dividend_types == ['front']
    capsys.readouterr()
    # 五列 + volume 逐位相同（assert_frame_equal 默认含 dtype——未记录 code
    # 整段换算被跳过，dtype 零扰动）
    assert_frame_equal(got_emb['300214.SH'], got_abs['300214.SH'])


# ---------------------------------------------------------------------------
# T5 GBK + 注入面
# ---------------------------------------------------------------------------

def test_t5_product_gbk_encodable_with_embed(tmp_path):
    p = _write_embed(tmp_path, _embed_body(
        {'600971': [(20250523, 0.9674512975496247), (20260529, 1.0)]}))
    result = QmtSourceConverter(_MIN_SOURCE).convert(qfq_embed=str(p))
    assert result.errors == []
    code = result.converted_code
    assert code.encode('gbk')  # 整体物理 GBK 可编码（含 HISTORY_EXT 快照段）
    assert '_QS_QMT_QFQ_SNAP_DATA' in code   # 嵌入常量确随产物注入
    assert '_QS_QMT_QFQ_SNAP_META' in code
    assert 'gen=' not in code                 # 禁转换时刻戳字段（观测锚=n+base）


def test_t5_template_no_file_io():
    # 嵌入式前提：模板串（运行期层）零文件 IO——读取动作全部发生在转换期
    # （_inject_qfq_embed），R331 文件版真机 PermissionError 根因不复存在
    assert 'open(' not in _QS_QMT_HISTORY_EXT


# ---------------------------------------------------------------------------
# T6 教训回归（R325：pandas Index 真值陷阱）
# ---------------------------------------------------------------------------

def test_t6_no_columns_truthiness_trap_in_template():
    assert "'columns', []) or" not in _QS_QMT_HISTORY_EXT


# ---------------------------------------------------------------------------
# T7 嵌入往返（export --emit-py 全链）
# ---------------------------------------------------------------------------

def _load_export_module():
    """按路径加载导出脚本（scripts/ 非包；与 test_qmt_close_batch.py 的
    SourceFileLoader 纪律同款——注册 sys.modules 防 dataclass 注解依赖）。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    path = root / 'scripts' / 'export_qfq_factor_snapshot.py'
    name = 'export_qfq_factor_snapshot_for_test'
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def _make_fixture_db(db_path):
    """小型合成 DuckDB fixture：两 code 有跳变（600971 两跳 / 000001 一跳）、
    一 code 无跳变（300214）——行锚取 12:00 UTC（strftime 按 session 时区渲染
    timestamptz，正午锚在 ±11h 时区内恒落同一 d8，测试机时区无关）。"""
    import duckdb
    con = duckdb.connect(str(db_path))
    con.execute('CREATE TABLE stock_daily (code VARCHAR, time BIGINT, '
                'close_front_ratio DOUBLE, close_front DOUBLE, close DOUBLE)')
    rows = [
        ('600971', '20250522', 0.5, 5.0, 10.0),
        ('600971', '20250523', 0.9674512975496247, 9.674512975496247, 10.0),
        ('600971', '20260528', 0.9674512975496247, 9.674512975496247, 10.0),
        ('600971', '20260529', 1.0, 10.0, 10.0),
        ('300214', '20250601', 0.5, 5.0, 10.0),
        ('000001', '20240602', 0.9674512975496247, 9.674512975496247, 10.0),
        ('000001', '20240603', 0.75, 7.5, 10.0),
    ]
    for code, d8, ratio, close_front, close in rows:
        ts = int(datetime.strptime(d8, '%Y%m%d').replace(
            hour=12, tzinfo=timezone.utc).timestamp() * 1000)
        con.execute('INSERT INTO stock_daily VALUES (?, ?, ?, ?, ?)',
                    [code, ts, ratio, close_front, close])
    con.close()


def test_t7_emit_py_roundtrip_and_byte_idempotent(tmp_path, capsys):
    mod = _load_export_module()
    db = tmp_path / 'fixture.duckdb'
    _make_fixture_db(db)
    out_json = tmp_path / 'out' / 'qfq_factor_snapshot.json'
    emit1 = tmp_path / 'a' / 'qfq_snap_embed.py'
    emit2 = tmp_path / 'b' / 'qfq_snap_embed.py'
    argv = ['--db', str(db), '--out', str(out_json), '--since', '20240101']
    assert mod.main(argv + ['--emit-py', str(emit1)]) == 0
    assert mod.main(argv + ['--emit-py', str(emit2)]) == 0
    captured = capsys.readouterr().out
    assert 'emit_py=' in captured and 'bytes=' in captured  # 打印行含两字段
    b1, b2 = emit1.read_bytes(), emit2.read_bytes()
    assert b1 == b2  # 字节幂等：同库同窗重跑输出一致（禁时间戳的机检面）
    assert hashlib.sha256(b1).hexdigest() == hashlib.sha256(b2).hexdigest()
    src = b1.decode('ascii')  # 全 ASCII
    assert 'generated_at' not in src
    ns = {}
    exec(compile(src, '<emit-py>', 'exec'), ns)
    # exec 后 DATA 与源 records 逐值相等（repr 往返：float(repr(x)) == x）
    assert ns['_QS_QMT_QFQ_SNAP_DATA'] == {
        '000001': ((20240603, 0.75),),
        '600971': ((20250523, 0.9674512975496247), (20260529, 1.0)),
    }
    assert ns['_QS_QMT_QFQ_SNAP_META'] == {'enabled': True, 'base': 20260529,
                                           'n': 2}
    # 真注入端到端：emit-py -> convert -> embedded 态 -> div='none'
    ns2 = _render_ns(qfq_embed=str(emit1))
    assert callable(ns2.get('_qs_qfq_snap_state'))
    _out, c2 = _fetch_hist(ns2, '600971.SH', _T3_DATES)
    assert c2.dividend_types == ['none']
    assert 'QS_QMT_QFQ_SNAP embedded n=2 base=20260529' in \
        capsys.readouterr().out


# ---------------------------------------------------------------------------
# T8 repr 精度守卫
# ---------------------------------------------------------------------------

def test_t8_repr_precision_guard():
    """repr 全精度守卫：emit-py 比率经 repr() 序列化，float(repr(x)) == x 是
    嵌入往返逐值相等的数学前提（禁 round/截断的机检哨兵）。"""
    rng = random.Random(20261108)
    for _ in range(500):  # 通用量级浮点（含极端指数）
        x = rng.uniform(-1.0, 1.0) * 10 ** rng.randint(-8, 8)
        assert float(repr(x)) == x, (x, repr(x))
    for _ in range(500):  # 复权比率量级（T7 fixture 仅 3 值 -> 合成 500）
        r = rng.uniform(0.05, 20.0)
        assert float(repr(r)) == r, (r, repr(r))
