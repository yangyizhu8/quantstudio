# -*- coding: utf-8 -*-
r"""test_qmt_close_batch —— D-27b 件 B/C/D/E 验收单测（零 QMT 依赖）。

判据映射（docs/qmt-d27-fetch-convergence-design.md v1.4 §5 判据 b）：
  五态负向：批内缺码 / 空表 / 单码取值异常（含 NaN 非数值）/ 返回非 dict /
            批级异常 → 失败码全部回落逐只（_qs_last_close）且不抛异常；
  EQC 触发性：一次性标志逻辑无 bug，首次 valuation 调用确实打印 CLOSE_EQC
            （防「标志常置位→EQC 从不触发」的静默失效）；
  EQC 口径：None/NaN 双哨兵归一记 miss、不计 diff；diff 仅对双侧均有值计数；
            hit+miss==n 对账；分母＝批量命中码集（防回落码自指空转）；
            批量命中 0 → 显式 n=0 SKIP；
  逐位一致：批量值与逐只值逐位一致（mock get_market_data_ex 返回
            dict{code: DataFrame}，时间升序含 close 列），kwargs 同款对齐；
  件 B 兼容：_qs_fin_to_rows_hist 不传 table 调用不报错（缺省形参）；
            HISTDBG3 探针读数算术（drop_duplicates 口径、NaN 视相等、
            每表一次）。

注意：经 _qs_get_fundamentals 的集成用例，mock 码必须命中 _qs_fin_to_rows
轨 B 证券代码正则（^\d{6}\.(SH|SZ|BJ)$），否则 raw 走 UNDETERMINED 保守路径
→ rows 空 → 预取/回退全不触发（用例自伪）。

加载模式：沿用 tests/test_qmt_hist_vectorize.py 与 tests/test_qmt_progress_
audit.py 的既有形态（SourceFileLoader 载入源文件 → 按 _assemble 序 exec 全部
模板串 → 单一命名空间，注入 _qs_pick_ctx 直通替身与 pd）。

run: pytest tests/test_qmt_close_batch.py -q
"""
import importlib.machinery
import importlib.util
import pathlib
import re
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "quantstudio" / "strategy_compiler" / "source_import_qmt.py"

_TEMPLATES = ("_QS_QMT_LOG_EXT", "_QS_QMT_CONTEXT_VIEW_EXT", "_QS_QMT_DATA_VIEW_EXT",
              "_QS_QMT_HISTORY_EXT", "_QS_QMT_POSITION_EXT", "_QS_QMT_ORDER_EXT",
              "_QS_QMT_ASHARES_EXT", "_QS_QMT_TRADE_DAYS_EXT",
              "_QS_QMT_STOCK_INFO_EXT", "_QS_QMT_FUNDAMENTALS_EXT",
              "_QS_QMT_WRAPPER_ZONE")


def _load(path, name):
    """加载源文件（须注册 sys.modules：@dataclass 字符串注解依赖之——与既有
    两个测试文件的加载纪律同款）。"""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def _ns(mod):
    """按 _assemble 序 exec 全部模板 → 单一命名空间（注入 _qs_pick_ctx 直通替身）。"""
    ns = {"_qs_pick_ctx": lambda C=None: C, "pd": pd}
    for t in _TEMPLATES:
        exec(getattr(mod, t), ns)
    return ns


@pytest.fixture(scope="module")
def cur():
    return _ns(_load(SRC, "qmt_d27b_cur"))


@pytest.fixture(autouse=True)
def _reset_once(cur):
    """每用例前清空一次性标志：模块级命名空间跨用例共享，标志一次即恒置位，
    不清则一次性语义用例（触发性/每表一次）相互污染。"""
    cur["_QS_QMT_ONCE"].clear()


def _lines(capsys):
    return capsys.readouterr().out.splitlines()


def _pick(lines, key):
    return [l for l in lines if key in l]


class _FakeCtx(object):
    """零 QMT 依赖 mock：len(codes)>1 → 批量路径；len==1 → 逐只路径（_qs_last_close）。

    closes：dict{code: close 值 或标记}——批量与逐只共用底层数据；
      'EMPTY' → 空 DataFrame（态②空表）；'BAD' → 末行 close 非数值（态③）；
      'NAN' → 末行 close=NaN（态③非数值·双哨兵之一）。
    percode：dict{code: 覆盖值}——仅逐只路径生效（构造批量≠逐只的真实差分场景，
      EQC 须为真实差分而非自证）。
    batch_override：None（正常 dict{code: df}）/ 'not_dict'（返回 list，态④）/
      'boom'（抛 RuntimeError，态⑤）——仅批量路径生效。
    drop_codes：批量返回中剔除的码（态①批内缺码）。
    fin：get_financial_data 返回值（{code: {field: v}} code_major 形态）。
    """

    def __init__(self, closes=None, percode=None, batch_override=None,
                 drop_codes=(), fin=None):
        self.closes = closes or {}
        self.percode = percode or {}
        self.batch_override = batch_override
        self.drop_codes = set(drop_codes)
        self.fin = fin or {}
        self.batch_calls = []    # [(codes, kwargs)]
        self.percode_calls = []  # [(code, kwargs)]
        self.desc_codes = set()  # M1 用例：这些码的批量帧时间降序 → asc=0
        # size=1 批与逐只调用在同一 API 形态上不可区分；直调 _qs_close_batch 的
        # 用例（如 test_c2）置 True 使 size=1 批也计入 batch_calls（验收期局部
        # 修复 2026-10-13：mock 按 len>1 分类会把合法尾批误记为逐只）。
        self.record_size1_as_batch = False

    @staticmethod
    def _frame(v):
        if v is None or v == 'EMPTY':
            return pd.DataFrame()
        if v == 'BAD':
            return pd.DataFrame({'close': [1.0, 'x']},  # 末行非数值（object 列）
                                index=pd.date_range('2026-09-01', periods=2))
        if v == 'NAN':
            return pd.DataFrame({'close': [1.0, float('nan')]},
                                index=pd.date_range('2026-09-01', periods=2))
        return pd.DataFrame({'close': [9.0, 10.0, float(v)]},  # 时间升序
                            index=pd.date_range('2026-09-01', periods=3))

    def get_market_data_ex(self, fields, codes, **kw):
        assert list(fields) == ['close']
        if len(codes) > 1 or self.record_size1_as_batch:  # 批量路径
            self.batch_calls.append((list(codes), dict(kw)))
            if self.batch_override == 'boom':
                raise RuntimeError('batch boom')          # 态⑤
            if self.batch_override == 'not_dict':
                return [self._frame(self.closes.get(c))    # 态④
                        for c in codes]
            return {c: (self._desc_frame() if c in self.desc_codes
                        else self._frame(self.closes.get(c)))
                    for c in codes if c not in self.drop_codes}
        c = codes[0]                                        # 逐只路径
        self.percode_calls.append((c, dict(kw)))
        v = self.percode.get(c, self.closes.get(c))
        return {c: self._frame(v)}

    def get_financial_data(self, *a, **kw):
        return dict(self.fin)

    @staticmethod
    def _desc_frame():
        """M1 用例帧：时间降序（iloc[-1] 值与升序帧同值，升序性与取值正交）。"""
        return pd.DataFrame({'close': [8.0, 9.0, 10.0]},
                            index=pd.date_range('2026-09-01', periods=3)[::-1])


class _FakePanel(object):
    """Panel duck-typing 面：major_axis / items / __getitem__ / to_frame
    （与 test_qmt_hist_vectorize._FakePanel 同款最小面）。"""

    def __init__(self, data):
        self._d = data
        self.items = list(data.keys())
        self.major_axis = [0, 1, 2]

    def __getitem__(self, k):
        return self._d[k]

    def to_frame(self):
        parts = []
        for c, df in self._d.items():
            d = df.copy()
            d.index = pd.MultiIndex.from_product([[c], list(d.index)])
            parts.append(d)
        return pd.concat(parts)


def _mk_hist():
    """3 只 × 5 行：报告期键 3 组（m_anntime 3 值）、值列 i=0 时全 0.0（uv=1）。"""
    return {'%03d.SZ' % i: pd.DataFrame(
        {'s_fa_eps_basic': [0.1 * i, 0.1 * i, 0.2 * i, 0.2 * i, 0.3 * i],
         'm_anntime': [1.6e12, 1.6e12, 1.7e12, 1.7e12, 1.8e12],
         'm_timetag': [1.61e12, 1.61e12, 1.71e12, 1.71e12, 1.81e12]},
        index=[0, 1, 2, 3, 4]) for i in range(3)}


# ------------------------------------------------- 判据 b① 五态负向（直调）

def test_c1_five_state_fallback_direct(cur):
    """五态负向（_qs_close_batch 直调）：缺码/空表/非数值（含 NaN）/非 dict/
    批级异常 → 失败码一律 None（交调用方回落逐只），任何情况下不抛异常。"""
    codes = ['000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ', '000005.SZ']
    fx = _FakeCtx(closes={'000001.SZ': 10.0, '000002.SZ': 11.0,
                          '000003.SZ': 'EMPTY', '000004.SZ': 'BAD',
                          '000005.SZ': 'NAN'},
                  drop_codes=('000002.SZ',))               # 态①批内缺码
    out = cur['_qs_close_batch'](codes, C=fx)
    assert out == {'000001.SZ': 10.0, '000002.SZ': None, '000003.SZ': None,
                   '000004.SZ': None, '000005.SZ': None}
    # 态④返回非 dict → 整批回退（批须 >1 码才走批量分支）
    fx4 = _FakeCtx(closes={'000001.SZ': 10.0, '000002.SZ': 11.0},
                   batch_override='not_dict')
    assert cur['_qs_close_batch'](['000001.SZ', '000002.SZ'], C=fx4) == \
        {'000001.SZ': None, '000002.SZ': None}
    # 态⑤批级异常 → 整批回退，不冒泡
    fx5 = _FakeCtx(closes={'000001.SZ': 10.0, '000002.SZ': 11.0},
                   batch_override='boom')
    assert cur['_qs_close_batch'](['000001.SZ', '000002.SZ'], C=fx5) == \
        {'000001.SZ': None, '000002.SZ': None}
    # 空码集：不调用 API、直接空 dict
    fx6 = _FakeCtx(closes={'000001.SZ': 10.0})
    assert cur['_qs_close_batch']([], C=fx6) == {}
    assert fx6.batch_calls == []


# ------------------------------------------- 判据 b① 五态回落（valuation 集成）

def test_d1_fallback_to_percode_in_loop(cur):
    """五态回落集成：valuation 循环内批量失败码回落 _qs_last_close 逐只，
    float_value 与回落值逐位一致；回落仍失败 → NaN 契约行不变；不抛异常。"""
    codes = ['000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ', '000005.SZ']
    fx = _FakeCtx(closes={'000001.SZ': 10.0, '000002.SZ': 11.0,
                          '000003.SZ': 'EMPTY', '000004.SZ': 'BAD',
                          '000005.SZ': 'NAN'},
                  percode={'000002.SZ': 7.7},             # 态①回落逐只得 7.7
                  drop_codes=('000002.SZ',),
                  fin={c: {'circulating_capital': 100.0} for c in codes})
    out = cur['_qs_get_fundamentals']('valuation', codes,
                                      fields=['float_value'], C=fx)
    assert out.loc['000001.SZ', 'float_value'] == 1000.0  # 批量命中 10.0 × 100
    assert out.loc['000002.SZ', 'float_value'] == 770.0   # 回落逐只 7.7 × 100
    assert pd.isna(out.loc['000003.SZ', 'float_value'])   # 空表回落仍失败 → NaN
    assert pd.isna(out.loc['000004.SZ', 'float_value'])   # 非数值回落仍失败 → NaN
    assert pd.isna(out.loc['000005.SZ', 'float_value'])   # NaN 回落仍 NaN → NaN
    assert '000002.SZ' in [c for c, _ in fx.percode_calls]  # 确证逐只回落发生


def test_d2_boom_batch_full_fallback(cur, capsys):
    """态⑤批级异常整批回退：所有码回落逐只、float_value 正确；EQC 显式
    n=0 SKIP（批量命中 0，禁止误导性 diff=0——方案 §2.3 审计 N6）。"""
    codes = ['000001.SZ', '000002.SZ']
    fx = _FakeCtx(closes={'000001.SZ': 10.0, '000002.SZ': 11.0},
                  batch_override='boom',
                  fin={c: {'circulating_capital': 100.0} for c in codes})
    out = cur['_qs_get_fundamentals']('valuation', codes,
                                      fields=['float_value'], C=fx)
    assert out.loc['000001.SZ', 'float_value'] == 1000.0
    assert out.loc['000002.SZ', 'float_value'] == 1100.0
    assert [c for c, _ in fx.percode_calls] == codes      # 两码均逐只回落
    assert any('QS_QMT_CLOSE_EQC n=0 SKIP' in l for l in _lines(capsys))


# ------------------------------------------------------- 判据 b② EQC 触发性

def test_e1_eqc_triggers_once_and_kwargs_aligned(cur, capsys):
    """EQC 触发性：一次性标志无 bug——首次 valuation 调用确实打印 CLOSE_EQC，
    第二次不再打印（close_eqc）；CLOSE_BATCH 批间行同样仅首次打印
    （close_batch_log）；批量与逐只 kwargs 完全同款（窗口/dividend_type/period）。"""
    codes = ['000001.SZ', '000002.SZ']
    fx = _FakeCtx(closes={'000001.SZ': 10.0, '000002.SZ': 11.0},
                  fin={c: {'circulating_capital': 100.0} for c in codes})
    cur['_qs_get_fundamentals']('valuation', codes, fields=['float_value'], C=fx)
    lines1 = _lines(capsys)
    eqc1 = _pick(lines1, 'QS_QMT_CLOSE_EQC')
    assert len(eqc1) == 1, 'EQC 未触发（标志逻辑 bug 致静默失效）'
    assert re.search(r'n=2 hit=2 diff=0 miss=0', eqc1[0])
    assert re.search(r'asc=1', eqc1[0]), 'M1：升序帧 → asc=1'
    assert len(_pick(lines1, 'QS_QMT_CLOSE_BATCH')) == 1
    assert cur['_QS_QMT_ONCE'].get('close_eqc') == 1
    # 批量调用形态：codes 原序、period='1d'、dividend_type='none'、
    # _qs_snap_kwargs 对 fake（无 get_bar_timetag）回退 count=1
    bc, kw_b = fx.batch_calls[0]
    assert bc == codes
    assert kw_b.get('period') == '1d' and kw_b.get('dividend_type') == 'none'
    assert kw_b.get('count') == 1
    # 第二次调用：EQC / CLOSE_BATCH 均不再打印
    cur['_qs_get_fundamentals']('valuation', codes, fields=['float_value'], C=fx)
    lines2 = _lines(capsys)
    assert not _pick(lines2, 'QS_QMT_CLOSE_EQC')
    assert not _pick(lines2, 'QS_QMT_CLOSE_BATCH')


# ----------------------------------------------------------- 判据 b③ EQC 口径

def test_e2_eqc_metrics(cur, capsys):
    """EQC 口径：None/NaN 双哨兵归一记 miss、不计 diff；diff 仅对双侧均有值
    计数；hit+miss==n 对账；分母＝批量命中码集（批量侧 None 码不入分母）。"""
    codes = ['000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ']
    fx = _FakeCtx(closes={'000001.SZ': 10.0, '000002.SZ': 5.0,
                          '000003.SZ': 6.0, '000004.SZ': 7.0},
                  percode={'000002.SZ': 'EMPTY',   # 逐只 None → miss（不计 diff）
                           '000003.SZ': 'NAN',     # 逐只 NaN → miss（不计 diff）
                           '000004.SZ': 6.1},      # 双侧有值且不等 → diff
                  fin={c: {'circulating_capital': 100.0} for c in codes})
    cur['_qs_get_fundamentals']('valuation', codes, fields=['float_value'], C=fx)
    line = _pick(_lines(capsys), 'QS_QMT_CLOSE_EQC')[0]
    m = re.search(r'n=(\d+) hit=(\d+) diff=(\d+) miss=(\d+)', line)
    assert m, line
    n, h, d, mi = (int(x) for x in m.groups())
    assert (n, h, d, mi) == (4, 2, 1, 2)   # 01 hit；04 hit+diff；02/03 双哨兵 miss
    assert h + mi == n


def test_e3_eqc_sample_capped_at_20(cur, capsys):
    """EQC 抽样上限 ≤20：25 只批量命中 → n=20（前 20 只），非全量。"""
    codes = ['%06d.SZ' % i for i in range(25)]
    fx = _FakeCtx(closes={c: 10.0 + i for i, c in enumerate(codes)},
                  fin={c: {'circulating_capital': 100.0} for c in codes})
    cur['_qs_get_fundamentals']('valuation', codes, fields=['float_value'], C=fx)
    line = _pick(_lines(capsys), 'QS_QMT_CLOSE_EQC')[0]
    assert re.search(r'n=20 hit=20 diff=0 miss=0', line)


def test_e4_asc_flag_machine_check(cur, capsys):
    """M1（用户批复方案 A，2026-10-13）：批量帧时间升序机检——降序帧 →
    asc=0 且与取值正确性正交（iloc[-1] 同值 → diff 仍为 0）；无有效帧 →
    close_asc=-1（未知，非 vacuous-true）。"""
    codes = ['000001.SZ', '000002.SZ']
    fx = _FakeCtx(closes={'000001.SZ': 10.0, '000002.SZ': 10.0},
                  fin={c: {'circulating_capital': 100.0} for c in codes})
    fx.desc_codes = {'000002.SZ'}  # 该码批量帧时间降序 → asc=0
    cur['_qs_get_fundamentals']('valuation', codes, fields=['float_value'], C=fx)
    line = _pick(_lines(capsys), 'QS_QMT_CLOSE_EQC')[0]
    assert re.search(r'asc=0', line)
    assert re.search(r'diff=0', line)  # 取值仍逐位一致（升序性≠取值正确性）
    assert cur['_QS_QMT_ONCE'].get('close_asc') == 0
    # 零有效帧（批级异常整批回落）→ close_asc=-1 未知 + SKIP 行携带
    # （须 ≥2 码：mock 的 boom 只在批量分支触发，单码走逐只分支）
    cur2 = _ns(_load(SRC, "qmt_d27b_cur2"))  # 新产物命名空间（一次性标志复位）
    _c2 = ['000003.SZ', '000004.SZ']
    fx2 = _FakeCtx(closes={c: 3.0 for c in _c2},
                   batch_override='boom',
                   fin={c: {'circulating_capital': 100.0} for c in _c2})
    cur2['_qs_get_fundamentals']('valuation', _c2,
                                 fields=['float_value'], C=fx2)
    line2 = _pick(_lines(capsys), 'QS_QMT_CLOSE_EQC')[-1]  # 本用例第 2 条 EQC 行
    assert re.search(r'n=0 SKIP', line2)
    assert cur2['_QS_QMT_ONCE'].get('close_asc') == -1


# ------------------------------------------------- 判据 b④ 批量 vs 逐只逐位一致

def test_c2_batch_equals_percode_bitwise(cur, capsys):
    """逐位一致：同 mock 下 _qs_close_batch 与逐只 _qs_last_close 返回完全相同
    的 float；批次切分（batch=3、7 码 → 3/3/1）与批间审计行 i=<k>/<n> 契约。"""
    codes = ['%06d.SZ' % i for i in range(7)]
    fx = _FakeCtx(closes={c: 10.0 + i for i, c in enumerate(codes)})
    fx.record_size1_as_batch = True  # 本用例无逐只混入，size=1 尾批计为批量
    bmap = cur['_qs_close_batch'](codes, C=fx, batch=3)
    assert len(fx.batch_calls) == 3
    assert [len(bc) for bc, _ in fx.batch_calls] == [3, 3, 1]
    lines = _pick(_lines(capsys), 'QS_QMT_CLOSE_BATCH')
    assert len(lines) == 3, '每批一行（仅首次调用打印）'
    assert re.search(r'i=1/3 size=3 hit=3 miss=0 elapsed=', lines[0])
    assert re.search(r'i=2/3 size=3 hit=3 miss=0', lines[1])
    assert re.search(r'i=3/3 size=1 hit=1 miss=0', lines[2])
    for c in codes:
        assert isinstance(bmap[c], float)
        assert bmap[c] == cur['_qs_last_close'](fx, c)
    # kwargs 同款：批量（内部一次求 _qs_snap_kwargs 复用）与逐只逐位一致。
    # 逐只 kwargs 用独立干净 mock 取样（fx 已开 record_size1_as_batch，
    # 其 size-1 调用全部走批量通道，percode_calls 不再可靠）。
    kw_b = fx.batch_calls[0][1]
    fx2 = _FakeCtx(closes={'999999.SZ': 1.0})
    cur['_qs_last_close'](fx2, '999999.SZ')
    kw_p = fx2.percode_calls[0][1]
    assert kw_b == kw_p


# ------------------------------------------------- 判据 b⑤ 件 B table 缺省兼容

def test_b1_hist_table_default_compat(cur, capsys):
    """table=None 缺省形参兼容：不传 table 调 _qs_fin_to_rows_hist 不报错，
    返回契约 {code: [行 dict, ...]} 保持；探针以 t=None 打印一次且二次不重打。"""
    panel = _FakePanel(_mk_hist())
    out = cur['_qs_fin_to_rows_hist'](panel, shape=None, codes=list(panel.items))
    assert len(out) == 3
    assert all(isinstance(v, list) and v for v in out.values())
    res = capsys.readouterr()
    assert not res.err, '不传 table 不得报错'
    h3 = _pick(res.out.splitlines(), 'QS_QMT_HISTDBG3')
    assert len(h3) == 1 and 't=None' in h3[0]
    # 二次调用不重打（一次性标志）
    cur['_qs_fin_to_rows_hist'](panel, shape=None, codes=list(panel.items))
    assert not _pick(_lines(capsys), 'QS_QMT_HISTDBG3')
    assert cur['_QS_QMT_ONCE'].get('histdbg3:None') == 1


def test_b2_histdbg3_probe_metrics_and_once_per_table(cur, capsys):
    """HISTDBG3 读数算术与每表一次：ua/ut/uk/uv/after 全按 drop_duplicates
    口径；t=<table> 标注正确；同表二次不重打、异表首调重打。"""
    panel = _FakePanel(_mk_hist())
    cur['_qs_fin_to_rows_hist'](panel, shape=None, codes=list(panel.items),
                                table='eps')
    line = _pick(_lines(capsys), 'QS_QMT_HISTDBG3')[0]
    for frag in ('t=eps', 'code=000.SZ', 'rows=5', 'ua=3', 'ut=3', 'uk=3',
                 'uv=1', 'after=3', 'head=', 'tail='):
        assert frag in line, '缺字段 %s：%s' % (frag, line)
    # 同表二次：不重打
    cur['_qs_fin_to_rows_hist'](panel, shape=None, codes=list(panel.items),
                                table='eps')
    assert not _pick(_lines(capsys), 'QS_QMT_HISTDBG3')
    # 异表（growth_ability）首调：重打且 t= 正确
    cur['_qs_fin_to_rows_hist'](panel, shape=None, codes=list(panel.items),
                                table='growth_ability')
    line3 = _pick(_lines(capsys), 'QS_QMT_HISTDBG3')[0]
    assert 't=growth_ability' in line3


def test_b3_histdbg3_nan_counts_as_equal(cur, capsys):
    """NaN 口径（审计 #5）：m_anntime 全 NaN → ua=1（drop_duplicates 视 NaN
    相等），非 nunique() 的 ua=0 误判形态；uk 按组合去重。"""
    df = pd.DataFrame(
        {'s_fa_eps_basic': [0.1, 0.2, 0.3, 0.4, 0.5],
         'm_anntime': [float('nan')] * 5,
         'm_timetag': [1.61e12, 1.61e12, 1.71e12, 1.71e12, 1.81e12]},
        index=[0, 1, 2, 3, 4])
    panel = _FakePanel({'000.SZ': df})
    cur['_qs_fin_to_rows_hist'](panel, shape=None, codes=['000.SZ'], table='eps')
    line = _pick(_lines(capsys), 'QS_QMT_HISTDBG3')[0]
    assert 'ua=1' in line
    assert 'ut=3' in line and 'uk=3' in line
