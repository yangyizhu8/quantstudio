# -*- coding: utf-8 -*-
"""test_qmt_hist_fold —— D-28 报告期折叠单测（判型＝数据修正型）。

落实 docs/qmt-hist-fold-design.md §五「12 例明拆」：wrapper 现行树
`_qs_hist_fold`（:1383 起）/ 批次与单码接入（:1612/:1642）/
`_qs_get_fundamentals` 键恒附（:1696-1700）的行为契约——
折叠语义（keep='last'）、NaN 口径、回落保护、单码路径对称、
weekly/CANSLIM/F-Score 三可达消费点不变式（逐位一致）、组内值突变
预期语义（末行值 + intravar>0）、空帧边界、HISTFOLD 审计行每表一次、
QSFUNDHIST kept 语义、SG-MS-PEG 真实函数前后对照（importlib 加载
策略文件原函数——测试侧 import 不违「策略源码零改动」）。

run: pytest tests/test_qmt_hist_fold.py -q
"""
import importlib.machinery
import importlib.util
import pathlib
import re
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "quantstudio" / "strategy_compiler" / "source_import_qmt.py"
STRAT = (ROOT / "quantstudio" / "backtest" / "strategies"
         / "SG-MS-PEG-HL防守型小市值成长交叉验证选股策略.py")
WEEKLY = (ROOT / "quantstudio" / "backtest" / "strategies"
          / "weekly_smallcap_growth_momentum_10_quantstudio.py")

_TEMPLATES = ("_QS_QMT_LOG_EXT", "_QS_QMT_CONTEXT_VIEW_EXT", "_QS_QMT_DATA_VIEW_EXT",
              "_QS_QMT_HISTORY_EXT", "_QS_QMT_POSITION_EXT", "_QS_QMT_ORDER_EXT",
              "_QS_QMT_ASHARES_EXT", "_QS_QMT_TRADE_DAYS_EXT",
              "_QS_QMT_STOCK_INFO_EXT", "_QS_QMT_FUNDAMENTALS_EXT",
              "_QS_QMT_WRAPPER_ZONE")

_HISTFOLD_RE = re.compile(
    r'QS_QMT_HISTFOLD t=(\S+) code=(\S+) before=(\d+) after=(\d+) '
    r'intravar=(-?\d+)')
_QSFUNDHIST_RE = re.compile(
    r'QSFUNDHIST t=(\S+) kept=(\d+) drop=(\d+) cc=(\d+) n=(\d+)')


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # @dataclass 字符串注解需 sys.modules
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def wmod():
    """加载 wrapper 模块（与 test_qmt_hist_vectorize 同构的加载机制）。"""
    return _load(SRC, "qs_qmt_hist_fold_wrapper_under_test")


def _ns(mod):
    """模板 exec 命名空间：函数体在模板常量内，模块面无同名属性。"""
    ns = {"_qs_pick_ctx": lambda C=None: C, "pd": pd, "np": np}
    for n in ("_QS_QMT_FUND_FIELD_MAP", "_qs_snap_kwargs", "_qs_norm_ymd"):
        if hasattr(mod, n):
            ns[n] = getattr(mod, n)
    for t in _TEMPLATES:
        exec(getattr(mod, t), ns)
    return ns


@pytest.fixture(scope="module")
def ns(wmod):
    return _ns(wmod)


@pytest.fixture(autouse=True)
def _fresh_once(ns):
    """每用例前后清空一次性标志——HISTFOLD「每表恰一次」断言须确定性。"""
    ns["_QS_QMT_ONCE"].clear()
    yield
    ns["_QS_QMT_ONCE"].clear()


def _synth_daily(periods, n_daily, code=None):
    """合成 QMT 日频 hist 帧（键恒附后真实形态）。

    每报告期 (m_timetag) 展开为 n_daily 个日频行，键列与值列呈 ffill
    形态（组内恒等）；值列传 list 则逐日注入（用例 8 组内值突变）。
    同场带本地镜像列 publ_date/end_date（消费点不变式用）与可选 code 列。
    periods 元素: (ann, tag, {值列: 标量 或 长度 n_daily 的 list})。
    """
    rows = []
    for ann, tag, vals in periods:
        for d in range(n_daily):
            r = {'m_anntime': ann, 'm_timetag': tag,
                 'publ_date': ann, 'end_date': tag}
            for c, v in vals.items():
                r[c] = v[d] if isinstance(v, list) else v
            if code is not None:
                r['code'] = code
            rows.append(r)
    return pd.DataFrame(rows)


# ---------- 用例 1：折叠语义（每报告期恰一行，取末行值） ----------

def test_01_fold_semantics_one_row_per_period_keep_last(ns):
    df = _synth_daily([
        (20210330.0, 20201231.0, {'eps': [1.0, 1.1, 1.2, 1.3]}),
        (20220430.0, 20211231.0, {'eps': [2.0, 2.1, 2.2, 2.3]}),
        (20230428.0, 20221231.0, {'eps': [3.0, 3.1, 3.2, 3.3]}),
    ], n_daily=4)
    folded = ns["_qs_hist_fold"](df, 'eps', '000001.SZ')
    # 每报告期恰一行（3 报告期 → 3 行）
    assert len(folded) == 3
    # keep='last'：取组内末行值（1.3/2.3/3.3，非首行 1.0/2.0/3.0）
    assert folded['eps'].tolist() == [1.3, 2.3, 3.3]
    # 键列保持原值；行序跟随保留行原位（报告期升序）
    assert folded['m_timetag'].tolist() == [20201231.0, 20211231.0, 20221231.0]
    assert folded['m_anntime'].tolist() == [20210330.0, 20220430.0, 20230428.0]


# ---------- 用例 2：NaN 口径（组合键全 NaN 行折叠为一行） ----------

def test_02_nan_keys_treated_equal_collapse_to_one(ns):
    df = _synth_daily([
        (20210330.0, 20201231.0, {'eps': 1.0}),
        (np.nan, np.nan, {'eps': [9.1, 9.2]}),   # 组合键全 NaN × 2 行
        (20220430.0, 20211231.0, {'eps': 2.0}),
    ], n_daily=2)
    folded = ns["_qs_hist_fold"](df, 'eps', '000001.SZ')
    # 2 个有效报告期 + 全 NaN 键组恰 1 行（drop_duplicates NaN 视相等）
    assert len(folded) == 3
    nan_rows = folded[folded['m_anntime'].isna() & folded['m_timetag'].isna()]
    assert len(nan_rows) == 1
    assert nan_rows['eps'].iloc[0] == 9.2   # NaN 组亦取末行


# ---------- 用例 3：回落保护（缺键列 / 折叠表达式异常 → dropna 不抛出） ----------

class _BoomFrame(pd.DataFrame):
    """折叠表达式异常注入：drop_duplicates 必抛，其余面（dropna）继承。"""

    def drop_duplicates(self, *a, **k):
        raise ValueError('boom: fold expression failure')


def test_03_fallback_missing_key_column_and_expression_error(ns, capsys):
    # (a) 缺 m_timetag 列 → 不折叠，走 dropna(how='all')
    df = pd.DataFrame({
        'eps': [1.0, np.nan, 2.0], 'm_anntime': [20210330.0, np.nan, 20220430.0]})
    out = ns["_qs_hist_fold"](df, 'eps', '000001.SZ')
    assert len(out) == 2                    # 全 NaN 行被移除，其余保留
    assert out['eps'].tolist() == [1.0, 2.0]
    # 缺键列不走折叠路径 → 无 HISTFOLD 审计行
    assert 'QS_QMT_HISTFOLD' not in capsys.readouterr().out
    # (b) 折叠表达式异常 → 回落 dropna(how='all')，不外抛
    boom = _BoomFrame({'eps': [1.0, np.nan], 'm_anntime': [1.0, np.nan],
                       'm_timetag': [1.0, np.nan]})
    out2 = ns["_qs_hist_fold"](boom, 'eps', '000001.SZ')   # 不抛即通过
    assert len(out2) == 1 and out2['eps'].iloc[0] == 1.0


# ---------- 用例 4：单码 DataFrame 路径同款折叠 + 缺键列二阶行为 ----------

def test_04_single_code_path_fold_and_dropna_second_order(ns):
    # (a) 单码路径同款折叠：3 报告期 × 3 日频行 → 每 code 3 条记录取末行值
    df = _synth_daily([
        (20210330.0, 20201231.0, {'eps': [1.0, 1.1, 1.2]}),
        (20220430.0, 20211231.0, {'eps': [2.0, 2.1, 2.2]}),
    ], n_daily=3)
    rows = ns["_qs_fin_to_rows_hist"](df, shape=None,
                                     codes=['000001.SZ'], table='eps')
    assert list(rows.keys()) == ['000001.SZ']
    recs = rows['000001.SZ']
    assert len(recs) == 2                    # 折叠已生效（非 6 条）
    assert [r['eps'] for r in recs] == [1.2, 2.2]   # 末行值
    # (b) 缺键列帧：回落 dropna(how='all') 的二阶行为——全 NaN 行此前
    # 存活到策略，现被回落路径移除（设计 §四⑥）
    df2 = pd.DataFrame({'eps': [1.0, np.nan, 2.0, np.nan],
                        'm_anntime': [1.0, 1.0, 2.0, np.nan]})
    rows2 = ns["_qs_fin_to_rows_hist"](df2, shape=None,
                                      codes=['000001.SZ'], table='eps')
    assert len(rows2['000001.SZ']) == 3      # 仅全 NaN 行被剔（eps=NaN 行存活）
    assert all(any(v == v for v in r.values()) for r in rows2['000001.SZ'])


# ---------- 用例 5：weekly 族消费点不变式（_latest_by_code 逐位一致） ----------

def test_05_weekly_latest_by_code_invariant(ns):
    """折叠前后 weekly 族**真实** _latest_by_code 选码结果逐位一致。

    修订说明：改调真实策略函数（importlib 加载，消除镜像等价物漂移风险），
    并按生产语义**逐码折叠**后回汇——_qs_fin_to_rows_hist 内折叠作用于单码帧；
    初版把双码帧整体折叠（组内 code 列不等 → 跨码收敛），非生产形态。
    """
    strat = _load(WEEKLY, "weekly_strategy_d28_probe")
    latest = strat._latest_by_code
    per = [(a, t, {'or_yoy': v}) for a, t, v in [
        (20210430.0, 20201231.0, 0.11), (20220429.0, 20211231.0, 0.22),
        (20230428.0, 20221231.0, 0.33)]]
    d1 = _synth_daily(per, n_daily=3, code="000001.SZ")
    d2 = _synth_daily(per, n_daily=2, code="000002.SZ")
    d1.index = ["000001.SZ"] * len(d1)          # 策略契约：index=code
    d2.index = ["000002.SZ"] * len(d2)
    raw = pd.concat([d1, d2])                   # 保留 index（消费方按 index 取码）
    folded = pd.concat([
        ns["_qs_hist_fold"](d1.copy(), "growth_ability", "000001.SZ"),
        ns["_qs_hist_fold"](d2.copy(), "growth_ability", "000002.SZ")])
    assert latest(raw, "or_yoy") == latest(folded, "or_yoy"), "weekly 选码结果漂移"
    assert latest(folded, "or_yoy") == {"000001.SZ": 0.33, "000002.SZ": 0.33}


# ---------- 用例 6：CANSLIM 消费点不变式（per-end publ-max 严格 >） ----------

def _canslim_best(df):
    """CANSLIM:406 消费模式：per-end_date publ_date-max（严格 > 取首命中）。"""
    best = {}
    for _, r in df.iterrows():
        ed, p = r['end_date'], r['publ_date']
        if ed not in best or p > best[ed][0]:
            best[ed] = (p, r['eps'])
    return best


def test_06_canslim_publ_max_invariant(ns):
    # E1 两个公告版（重述 P1<P2）；E2 公告日与 P2 相同（同 publ_date 多行）
    df = _synth_daily([
        (20210430.0, 20201231.0, {'eps': 1.0}),
        (20220630.0, 20201231.0, {'eps': 1.5}),   # E1 重述版（同 end 不同 ann）
        (20220630.0, 20211231.0, {'eps': 2.0}),   # E2（publ 与上行相同）
    ], n_daily=3)
    folded = ns["_qs_hist_fold"](df.copy(), 'eps', '000001.SZ')
    assert _canslim_best(df) == _canslim_best(folded)
    # 期望读数自证：E1 取 max publ=20220630 版（1.5）；E2 唯一版（2.0）
    assert _canslim_best(folded) == {20201231.0: (20220630.0, 1.5),
                                     20211231.0: (20220630.0, 2.0)}


# ---------- 用例 7：F-Score 模式不变式（sort end_date 取末行） ----------

def test_07_fscore_last_row_invariant(ns):
    df = _synth_daily([
        (20210430.0, 20201231.0, {'roe': 10.0}),
        (20220429.0, 20211231.0, {'roe': 20.0}),
        (20230428.0, 20221231.0, {'roe': 30.0}),
    ], n_daily=3)
    folded = ns["_qs_hist_fold"](df.copy(), 'profit_ability', '000001.SZ')
    raw_last = df.sort_values('end_date').iloc[-1]
    fold_last = folded.sort_values('end_date').iloc[-1]
    for c in ('roe', 'end_date', 'publ_date', 'm_anntime', 'm_timetag'):
        assert raw_last[c] == fold_last[c]
    assert fold_last['roe'] == 30.0   # 最大 end_date 组末行＝折叠保留行


# ---------- 用例 8：组内值突变预期语义（末行值 + intravar>0） ----------

def test_08_intravariance_takes_last_row_and_reports(ns, capsys):
    df = _synth_daily([
        (20210330.0, 20201231.0, {'eps': 1.0}),
        (20220430.0, 20211231.0, {'eps': [2.0, 2.5, 3.0]}),  # 组内值突变
        (20230428.0, 20221231.0, {'eps': 4.0}),
    ], n_daily=3)
    folded = ns["_qs_hist_fold"](df, 'eps', '000001.SZ')
    # 预期语义声明（非不变式）：同键组内值不等 → 取组内末行值 3.0
    assert folded['eps'].tolist() == [1.0, 3.0, 4.0]
    m = _HISTFOLD_RE.search(capsys.readouterr().out)
    assert m is not None
    assert int(m.group(5)) > 0                  # intravar 哨兵披露组内值突变


# ---------- 用例 9：空帧/零行边界 ----------

def test_09_empty_and_none_inputs_no_raise(ns):
    empty = pd.DataFrame(columns=['eps', 'm_anntime', 'm_timetag'])
    out = ns["_qs_hist_fold"](empty, 'eps', '000001.SZ')
    assert isinstance(out, pd.DataFrame) and len(out) == 0   # 空进空出、不抛错
    assert ns["_qs_hist_fold"](None, 'eps', '000001.SZ') is None
    # 非 DataFrame（无 drop_duplicates 面）→ 原对象直通
    assert ns["_qs_hist_fold"]('not-a-frame', 'eps', None) == 'not-a-frame'
    # 零行帧经行收集路径同样安全
    rows = ns["_qs_fin_to_rows_hist"](empty, shape=None,
                                     codes=['000001.SZ'], table='eps')
    assert rows == {}


# ---------- 用例 10：HISTFOLD 审计行每表一次 + 键恒附/剥离 ----------

class _FakeC(object):
    """get_financial_data duck-typing 替身：记录请求列、返回预制帧。"""

    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def get_financial_data(self, fields, codes, start, end, report_type=None):
        self.calls.append({'fields': list(fields), 'codes': list(codes),
                           'end': end, 'report_type': report_type})
        return self.frame


def test_10_audit_once_key_appended_and_stripped(ns, capsys):
    fake = _FakeC(_synth_daily([
        (20210330.0, 20201231.0, {'s_fa_eps_basic': 1.0}),
        (20220430.0, 20211231.0, {'s_fa_eps_basic': 2.0})], n_daily=3))
    f1 = ns["_qs_get_fundamentals"]('eps', ['000001.SZ'],
                                   fields=['eps'], date=None, C=fake)
    # 键恒附：内部请求列 = 调用方列 + 2 键（幂等追加于表尾）
    got1 = fake.calls[0]['fields']
    assert got1 == ['PERSHAREINDEX.s_fa_eps_basic',
                    'PERSHAREINDEX.m_anntime', 'PERSHAREINDEX.m_timetag']
    # 调用方视角列集不变：辅助键经投影剥离，不进入返回帧
    assert list(f1.columns) == ['eps'] and len(f1) == 2
    # 同表第二次调用（消费方已请求键字段 → 恒附幂等不重复）
    f2 = ns["_qs_get_fundamentals"](
        'eps', ['000001.SZ'], fields=['eps', 'publ_date', 'end_date'],
        date=None, C=fake)
    got2 = fake.calls[1]['fields']
    assert len(got2) == 3 and sorted(got2) == sorted(got1)   # 已含键 → +0
    assert list(f2.columns) == ['eps', 'publ_date', 'end_date']
    out = capsys.readouterr().out
    # 审计行每表恰一次（两次调用仅一行）；字段完整可机检
    hits = _HISTFOLD_RE.findall(out)
    assert len(hits) == 1
    t, code, before, after, iv = hits[0]
    assert (t, code, before, after, iv) == ('eps', '000001.SZ', '6', '2', '0')


# ---------- 用例 11：QSFUNDHIST kept 语义＝折叠后完整行数 ----------

def test_11_qsfundhist_ked_equals_folded_complete_rows(ns, capsys):
    fake = _FakeC(_synth_daily([
        (20210330.0, 20201231.0, {'s_fa_eps_basic': 1.0}),
        (20220430.0, 20211231.0, {'s_fa_eps_basic': 2.0}),
        (20230428.0, 20221231.0, {'s_fa_eps_basic': 3.0}),
        (20240430.0, 20231231.0, {'s_fa_eps_basic': np.nan})], n_daily=3))
    f = ns["_qs_get_fundamentals"]('eps', ['000001.SZ'],
                                  fields=['eps'], date=None, C=fake)
    out = capsys.readouterr().out
    hf = _HISTFOLD_RE.search(out)
    qh = _QSFUNDHIST_RE.search(out)
    assert hf is not None and qh is not None
    # 折叠：12 日频行 → 4 报告期行；NaN-eps 期折叠后仍 1 行
    assert (hf.group(3), hf.group(4)) == ('12', '4')
    # kept＝折叠后完整行数（仅查请求字段——键恒附 +2 列不影响完整行判定）
    assert (qh.group(2), qh.group(3), qh.group(4), qh.group(5)) \
        == ('3', '1', '1', '1')          # kept=3 drop=1 cc=1 n=1
    assert len(f) == 3                   # 返回帧只含完整行
    assert np.isfinite(f['eps']).all()


# ---------- 用例 12：SG-MS-PEG 真实函数前后对照（要件③） ----------

def test_12_sgmspeg_real_roe_mean_before_vs_after_fold(ns):
    strat = _load(STRAT, "sg_ms_peg_strategy_under_hist_fold_test")
    roe_func = strat._roe_mean_from_fin_indicator
    k = strat._ROE_MEAN_MAX_REPORTS
    assert k == 5                          # 前置守卫：金标数学依赖该常量
    tags = [20201231.0, 20211231.0, 20221231.0,
            20231231.0, 20241231.0, 20251231.0]
    anns = [20210331.0, 20220331.0, 20230428.0,
            20240330.0, 20250331.0, 20260331.0]
    roes = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    code = '000002.SZ'
    # 合成含键日频帧（模拟键恒附后真实形态）；末期行数 ≥ k 保证错误口径
    # 「末 k 行全落最新报告期」成立（§五-12 前提）
    df = _synth_daily([(a, t, {'roe': v}) for a, t, v in zip(anns, tags, roes)],
                      n_daily=k)
    df.index = [code] * len(df)            # 策略契约：index=code 重复多行
    before = roe_func(df)
    folded = ns["_qs_hist_fold"](df.copy(), 'profit_ability', code)
    after = roe_func(folded)
    # 折叠前（错误口径实证）：日频末 k 行同属最新报告期 → 均值 ≡ 该期 ROE
    assert before == {code: roes[-1]}
    # 折叠后（本地口径恢复）：末 5 报告期均值
    assert after == {code: float(np.mean(roes[-k:]))}
    # diff 恰源于修正行集：折叠只收敛行数（30→6），roe 值集不变
    assert len(folded) == len(tags)
    assert sorted(folded['roe'].tolist()) == sorted(roes)
    assert before[code] != after[code]     # 60.0 ≠ 40.0：口径差异可观测
