# -*- coding: utf-8 -*-
"""因子层诊断单一源（2026-11-06 · D 类主因定位，六步①c6699fc ②审通过）。

设计铁律：**同一段诊断实现两侧共用**——
  - QMT 侧：转换器（source_import_qmt.QmtSourceConverter.convert(diag=True)）把
    ``DIAG_BLOCK`` 原样注入产物（wrapper 遮蔽区之后、策略主体之前）；
  - 本地侧：``scripts/qmt_factor_diag.py`` 用 ``exec_diag_block()``（即
    ``exec(DIAG_BLOCK)``）后调用同一函数名 ``_qs_fact_dbg_emit``；
  ⇒ 两侧执行的是同一份代码，杜绝「诊断自身引入分叉」。

DIAG_BLOCK 内所有字符必须 GBK 可编码（产物物理编码 GBK）：
禁 ``→ ★ ⇒ ⊂ ≠ ≈`` 等非 GBK 字符；只用 ASCII + 常用汉字 + 全角标点。

d0/d1 提取的两侧形态差异（2026-11-06 实施时按实际代码核正，见交付报告）：
  - QMT 侧 ``_qs_fetch_bars`` 返回 ``C.get_market_data_ex`` 的 DataFrame，
    索引即 'YYYYMMDD' 日期串（source_import_qmt.py E1 防御段同款提取法）；
  - 本地侧 ``ptrade_api.get_history`` 把索引重置为负整数（-N..-1），日期只在
    ``trade_date`` 列（duckdb_data_access._post 生成，'YYYY-MM-DD'）。
  ⇒ ``_qs_fact_dbg_d8`` 索引路优先（QMT 侧不变），取不到时按日期列兜底
    （本地侧生效；'time' 毫秒时间戳被 20xx 年份闸自动排除）。
"""

# 11 只对照标的（跨两侧固定）：
#   前 5 只 = 本地独有（QMT 侧为何没有它们）；
#   后 8 只 = QMT 独有（本地侧为何没有它们）。
TARGET_CODES = (
    '002568', '600971', '600228', '300321', '300121',
    '301439', '301557', '603859', '600060', '002577', '001331', '301093', '300214',
)

# 三个对照时点（执行日，YYYYMMDD；include=False 取 D-1 及之前 260 根）
DIAG_TIMES = ('20260601', '20260708', '20260914')

# 唯一诊断实现（源码字符串）。r-string：内部不得出现三连单引号。
# 注入产物时 strip("\n") 后作为独立 parts 段（不进任何模板串常量）。
DIAG_BLOCK = r'''
# ==== 因子层诊断（仅诊断产物注入；常规产物不含本段）====================
_QS_FACTDBG_CODES = frozenset([
    '002568', '600971', '600228', '300321', '300121',
    '301439', '301557', '603859', '600060', '002577', '001331', '301093', '300214',
])


def _qs_fact_dbg_num(x):
    try:
        import math
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return 'nan'
        return ('%.4f' % v)
    except Exception:
        return 'nan'


def _qs_fact_dbg_d8(v):
    """提取 8 位 YYYYMMDD；非日期形态（负整数索引 / 毫秒时间戳）返回空串。"""
    try:
        s = ''.join(ch for ch in str(v) if ch.isdigit())
    except Exception:
        return ''
    if len(s) < 8 or s[:2] != '20':
        return ''
    return s[:8]


def _qs_fact_dbg_row(code, df):
    """对一个 code 的历史 DataFrame 产出一行诊断（两侧共用）。

    d0/d1：索引路优先（QMT 侧索引即日期串）；索引非日期（本地侧为负整数）
    时按日期列兜底（trade_date 优先；time 为毫秒时间戳，被 20xx 年份闸排除）。
    """
    try:
        import numpy as np
        if df is None or len(df) == 0:
            return ('QS_QMT_FACTDBG code=%s n=0 d0=none d1=none c1=nan c5=nan c60=nan '
                    'c251=nan h260=nan v5=nan v20=nan v60=nan zeros=0 nans=0'
                    % (code,))
        # 注意：不可写 `getattr(df, 'columns', []) or []`——pandas Index 的真值
        # 判断抛 ValueError（truth value of an Index is ambiguous），会被外层
        # except 吞掉并把「有数据」伪装成 err=ValueError（2026-11-06 实测）。
        try:
            cols = set(df.columns)
        except Exception:
            cols = set()
        def col(name):
            if name not in cols:
                return None
            try:
                return np.asarray(df[name].values, dtype=float)
            except Exception:
                return None
        close = col('close')
        high = col('high')
        volume = col('volume')
        n = len(df)
        def tail(arr, k, how='last'):
            if arr is None or len(arr) == 0:
                return None
            kk = min(k, len(arr))
            if how == 'last':
                return float(arr[-kk])
            return float(np.nanmean(arr[-kk:]))
        def mean_tail(arr, k):
            if arr is None or len(arr) == 0:
                return None
            kk = min(k, len(arr))
            v = np.nanmean(arr[-kk:])
            try:
                if np.isnan(v):
                    return None
            except Exception:
                return None
            return float(v)
        d0 = d1 = ''
        try:
            idx = list(df.index)
            if idx:
                d0 = _qs_fact_dbg_d8(idx[0])
                d1 = _qs_fact_dbg_d8(idx[-1])
        except Exception:
            d0 = d1 = ''
        if not (d0 and d1):
            # 本地侧形态：索引非日期 -> 按日期列兜底（只补缺失端，不覆盖已得值）
            for cn in ('trade_date', 'date', 'day', 'dt', 'time'):
                if d0 and d1:
                    break
                if cn not in cols:
                    continue
                try:
                    cv = df[cn]
                    if not d0:
                        d0 = _qs_fact_dbg_d8(cv.iloc[0])
                    if not d1:
                        d1 = _qs_fact_dbg_d8(cv.iloc[-1])
                except Exception:
                    pass
        d0 = d0 or 'none'
        d1 = d1 or 'none'
        zeros = 0
        nans = 0
        if close is not None:
            for v in close:
                try:
                    if v != v:
                        nans += 1
                    elif v == 0:
                        zeros += 1
                except Exception:
                    pass
        return ('QS_QMT_FACTDBG code=%s n=%d d0=%s d1=%s c1=%s c5=%s c60=%s c251=%s '
                'h260=%s v5=%s v20=%s v60=%s zeros=%d nans=%d'
                % (code, n, d0, d1,
                   _qs_fact_dbg_num(tail(close, 1)),
                   _qs_fact_dbg_num(tail(close, 5)),
                   _qs_fact_dbg_num(tail(close, 60)),
                   _qs_fact_dbg_num(tail(close, 251)),
                   _qs_fact_dbg_num(tail(high, 260)),
                   _qs_fact_dbg_num(mean_tail(volume, 5)),
                   _qs_fact_dbg_num(mean_tail(volume, 20)),
                   _qs_fact_dbg_num(mean_tail(volume, 60)),
                   zeros, nans))
    except Exception as exc:
        return ('QS_QMT_FACTDBG code=%s n=-1 d0=none d1=none c1=nan c5=nan c60=nan '
                'c251=nan h260=nan v5=nan v20=nan v60=nan zeros=0 nans=0 err=%s'
                % (code, type(exc).__name__))


def _qs_fact_dbg_emit(codes, hist):
    """对命中 TARGET 的 code 逐个打印诊断行（两侧共用）。"""
    try:
        if not isinstance(hist, dict):
            return
        for code in list(hist.keys()):
            key = str(code).split('.')[0]
            if key not in _QS_FACTDBG_CODES:
                continue
            try:
                print(_qs_fact_dbg_row(key, hist.get(code)))
            except Exception:
                pass
    except Exception:
        pass


try:
    import pandas as _qs_fdbg_pd
except Exception:
    _qs_fdbg_pd = None
if _qs_fdbg_pd is not None:
    try:
        _qs_fdbg_pd._qs_fact_dbg_orig_gh = get_history
    except Exception:
        pass
    else:
        def get_history(*a, **kw):
            r = _qs_fdbg_pd._qs_fact_dbg_orig_gh(*a, **kw)
            try:
                _qs_fact_dbg_emit(a[0] if a else kw.get('codes'), r)
            except Exception:
                pass
            return r
    try:
        _qs_fdbg_pd._qs_fact_dbg_orig_ghb = get_history_batch
    except Exception:
        pass
    else:
        def get_history_batch(*a, **kw):
            r = _qs_fdbg_pd._qs_fact_dbg_orig_ghb(*a, **kw)
            try:
                _qs_fact_dbg_emit(a[0] if a else kw.get('codes'), r)
            except Exception:
                pass
            return r
# ==== 因子层诊断 终 ======================================================
'''


def exec_diag_block() -> dict:
    """本地侧入口：exec 同一份 DIAG_BLOCK，返回其命名空间。

    调用方用法（强制同源，不得另写本地实现）::

        ns = exec_diag_block()
        with contextlib.redirect_stdout(buf):
            ns['_qs_fact_dbg_emit'](codes, hist)
    """
    ns: dict = {}
    exec(DIAG_BLOCK, ns)
    return ns


def emit_for_hist(codes, hist) -> dict:
    """本地侧便捷入口：exec 同一份 DIAG_BLOCK 后调用同一 _qs_fact_dbg_emit。"""
    ns = exec_diag_block()
    ns['_qs_fact_dbg_emit'](codes, hist)
    return ns
