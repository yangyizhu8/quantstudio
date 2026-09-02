# -*- coding: utf-8 -*-
"""source_import：本地策略源码 → PTrade 代码转换器（source entry 支线 A）。

规格：私募工作文件/QuantStudio本地策略转ptrade模块开发/02-source_import模块规格.md（v1.1）
规则来源：portability_rules.py（单一来源，与 validate_ptrade_portability 共用）

设计要点（审核意见 H2/H3/G1 落实）：
- AST 定位 + 行级文本改写（禁止纯正则全文替换，防误伤字符串/注释）
- 别名表归一化后再匹配（H3）
- REMOVE 分档：档 1 裸语句删行 / 档 2 内嵌改等价字面量 / 档 3 BLOCK（H2）
- FQ 归一化只按 NORMALIZE_RULES 的 grade 执行（G1：dypre→NORMALIZE 有证据、dypost→WARN_KEEP）
- MyTT/A股规则：用到才注入 + 前缀重命名 + 非 1:1 标记（D1）
- 幂等性：INJECTED_MARKER 标记，二次转换零动作
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .portability_rules import (
    DENY_BLOCK,
    DENY_REMOVE,
    DENY_SHIM,
    GET_PRICE_DROP_PARAMS,
    INJECTED_MARKER,
    MYTT_FUNCTIONS,
    ASHARE_RULES_FUNCTIONS,
    NORMALIZE_RULES,
    PTRADE_PROFILE_MARKER,
    PTRADE_REGISTERED_WARN,
)
# ============================================================================
# 数据结构（02 规格 §1）
# ============================================================================


@dataclass
class ConversionAction:
    """一次转换动作的留痕记录。"""

    action_type: str  # REMOVE | DEGRADE | REWRITE | SHIM | NORMALIZE | INJECT | KEEP_COMMENT
    rule_id: str
    api_name: str
    line: int
    severity: str  # BLOCK | WARN | INFO
    old_text: str = ""
    new_text: str = ""
    message: str = ""


@dataclass
class SourceImportResult:
    converted_code: str
    actions: list[ConversionAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    reverse_spec: Optional[dict] = None
    spec_inference_notes: list[str] = field(default_factory=list)


# ============================================================================
# 注入源码常量（helper 提取自 templates/ptrade_daily.py.j2）
# ============================================================================

_PTRADE_HELPERS = '''
{marker}
def _lookup_history_item(history, code):
    if history is None:
        return None
    try:
        return history[code]
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    target = _bare_code(code)
    try:
        keys = history.keys()
    except AttributeError:
        return None
    for key in keys:
        if _bare_code(key) == target:
            try:
                return history[key]
            except Exception:
                return None
    return None


def _extract_history_field(history_item, field):
    if history_item is None:
        return np.asarray([], dtype=object)
    try:
        values = history_item[field]
    except (KeyError, IndexError, TypeError, ValueError):
        return np.asarray([], dtype=object)
    if hasattr(values, 'values'):
        values = values.values
    return np.asarray(values, dtype=object)


def _bare_code(code):
    return str(code).strip().upper().split('.')[0]


def _finite_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _finite_series(values):
    converted = []
    for value in np.asarray(values, dtype=object).reshape(-1):
        number = _finite_float(value)
        converted.append(number if number is not None else np.nan)
    return np.asarray(converted, dtype=float)


def _get_ma(close_array, num):
    if len(close_array) < num:
        return None
    value = close_array[-num:].mean()
    return round(value, 2) if np.isfinite(value) else None


def _portfolio_total_value(context):
    portfolio = context.portfolio
    for name in ('total_value', 'portfolio_value', 'total_asset'):
        value = getattr(portfolio, name, None)
        if value is not None:
            try:
                value = float(value)
                if np.isfinite(value) and value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    market_value = sum(
        float(getattr(position, 'market_value', 0) or 0)
        for position in portfolio.positions.values()
    )
    return float(portfolio.cash) + market_value


def _qs_shape_check(api_name, expected, actual):
    """P-D10 运行时首调形状自检（三道防线③）：返回形态不符 → QS_SHIM_SHAPE_VIOLATION
    显性警报（log 不抛错不阻断），封"平台日后行为漂移"场景。所有注入 wrapper/shim
    首调用点挂接；expected: dataframe / dict / list / df_or_dict。"""
    _ok = False
    try:
        if expected == 'dataframe':
            _ok = hasattr(actual, 'columns') and hasattr(actual, 'index')
        elif expected == 'dict':
            _ok = isinstance(actual, dict)
        elif expected == 'list':
            _ok = isinstance(actual, (list, tuple))
        elif expected == 'df_or_dict':
            _ok = ((hasattr(actual, 'columns') and hasattr(actual, 'index'))
                   or isinstance(actual, dict))
        else:
            _ok = True
    except Exception:
        _ok = False
    if not _ok:
        try:
            log.warning('QS_SHIM_SHAPE_VIOLATION %s expected=%s actual=%s'
                        % (api_name, expected, type(actual).__name__))
        except Exception:
            pass
    return _ok
'''



# 公共 helper（无条件注入，2026-09-01）：行业段（_qs_finance_pool/get_industry）依赖
# _qs_g_obj，但定义原在 fundamentals 段——策略不用 get_fundamentals 时注入缺失
# → 产物调用悬空（PyQt 转 PTrade LOCAL-API-WHITELIST BLOCK）。移为无条件注入。
_QS_COMMON_EXT = '''
{marker_common}
def _qs_g_obj():
    """平台全局 g 安全引用（策略平台 g 为全局 API；本地/测试无 g → None 不炸）。"""
    try:
        return g
    except NameError:
        return None
'''
_QS_HISTORY_WRAPPER = '''
{marker}
# 方向 B（2026-08-13 平台实证）：PTrade get_history 返回 numpy structured array，
# 统一转为 DataFrame（PTrade pandas 1.5.3 可用）。策略代码可用全部 pandas API。
# 字段名中枢（2026-08-13 平台实证 etf_theme_rotation：invalid field ['amount'],
# valid fields 含 'money' 无 'amount'（成交额）；preClose → preclose 前收大小写）：
# 请求时 本地 → PTrade，返回时 PTrade → 本地，双向映射策略代码零改动。
import pandas as _qs_pd
import numpy as _qs_np

# 本地字段名 → PTrade 字段名（请求时映射）
_QS_FIELD_TO_PTRADE = {{
    'amount': 'money',
    'preClose': 'preclose',
}}
# PTrade 列名 → 本地列名（返回时映射，含 datetime→time）
_QS_COL_TO_LOCAL = {{
    'datetime': 'time',
    'money': 'amount',
    'preclose': 'preClose',
}}
# pctChg 可移植（2026-09-01 平台实证回归）：PTrade 合法字段无 pctChg → 请求侧剔除、
# 返回侧由 close/preClose 合成（与本地引擎 aligner 同口径 (close/preClose−1)×100）。
# 标志记录本次请求是否含 pctChg（每次请求先复位，_qs_to_dataframe 消费）。
_QS_REQ_PCT = False

def _qs_to_dataframe(item):
    """structured array / DataFrame → 本地列名（money→amount、preclose→preClose 等）。
    D4-S7（2026-08-27 平台实证）：get_history 可能直接返回带日期 index 的 DataFrame，
    DataFrame 分支同样必须做列名映射，否则策略 _extract_history_field 取列得到空数组。
    """
    _df = None
    if isinstance(item, _qs_np.ndarray) and hasattr(item, 'dtype') and hasattr(item.dtype, 'names'):
        _df = _qs_pd.DataFrame(item)
    elif isinstance(item, _qs_pd.DataFrame):
        _df = item
    if _df is not None:
        # 列名统一映射：PTrade → 本地（datetime→time / money→amount / preclose→preClose）
        _rename = {{k: v for k, v in _QS_COL_TO_LOCAL.items()
                    if k in _df.columns and v not in _df.columns}}
        if _rename:
            _df = _df.rename(columns=_rename)
        # pctChg 合成（2026-09-01 平台实证回归）：请求含 pctChg 且返回无此列时，
        # 由 close/preClose 基列合成 (close/preClose−1)×100；fail-soft（异常/缺基列→不合成）。
        if _QS_REQ_PCT and 'pctChg' not in _df.columns \
                and 'close' in _df.columns and 'preClose' in _df.columns:
            try:
                _prec = _df['preClose'].astype(float)
                _ok = _prec > 0
                _pct = _qs_pd.Series(_qs_np.nan, index=_df.index)
                _pct[_ok] = (_df.loc[_ok, 'close'].astype(float) / _prec[_ok] - 1.0) * 100.0
                _df['pctChg'] = _pct
            except Exception:
                pass
        return _df
    return item

# v3（2026-09-01 平台实证第三轮）：分钟频率平台不支持 preclose 字段（INFO 刷屏实证）
# → 返回侧由日线昨收合成 preClose 列（include=False 日线末行 close = 上一交易日收盘，
# 与本地分钟 preClose 语义一致）；本地/平台返回若已含 preClose → guard 短路零影响。
# QS_MINUTE_DIAG：分钟路径首次调用打一条形状诊断（平台分钟数据可观测性）。
_QS_REQ_PREC_MIN = False
_QS_MINUTE_DIAG_DONE = False
_QS_PREC_CACHE = {{}}


def _qs_bar_date(v):
    """bar 时间 → 'YYYYMMDD'（int/str/datetime 兼容，fail-soft None）。"""
    try:
        if hasattr(v, "strftime"):
            return v.strftime("%Y%m%d")
        _s = v if isinstance(v, str) else str(int(v))
        _s = _s.replace("-", "").replace(" ", "").replace(":", "")
        return _s[:8] or None
    except Exception:
        return None


def _qs_synth_minute_preclose(df, code, fq):
    """分钟 df 无 preClose 列时由日线昨收合成（fail-soft：任一步失败保持原 df）。"""
    if df is None or not isinstance(df, _qs_pd.DataFrame) or "preClose" in df.columns \
            or "close" not in df.columns or len(df) == 0:
        return df
    try:
        _tvals = df["time"].values if "time" in df.columns else df.index.values
        _ds = _qs_bar_date(_tvals[-1])
        if not _ds:
            return df
        _key = (code, _ds)
        if _key not in _QS_PREC_CACHE:
            # 裸字符串形态（平台已实证唯一有效形态；security 关键字形态违反平台契约）
            _dd = _QSHistoryState.orig(2, frequency="1d", field=["close"],
                                       security_list=code, fq=fq, include=False)
            _dd = _qs_to_dataframe(_dd)
            if isinstance(_dd, dict):
                _dd = _dd.get(code)
            if _dd is None or not isinstance(_dd, _qs_pd.DataFrame) \
                    or len(_dd) == 0 or "close" not in _dd.columns:
                return df
            _QS_PREC_CACHE[_key] = float(_dd["close"].iloc[-1])
        df["preClose"] = float(_QS_PREC_CACHE[_key])
    except Exception:
        pass
    return df

# 保存原始 get_history 引用：类属性承载（属性调用不被静态 API 白名单拦截，
# 模块级别名函数 _qs_original_get_history(...) 会被 validate_local_strategy 判 BLOCK）
class _QSHistoryState:
    orig = None

_QSHistoryState.orig = get_history

# 重新绑定 get_history：请求前字段名映射（本地 → PTrade）+ 返回转 DataFrame
def get_history(*args, **kwargs):
    global _QS_REQ_PCT, _QS_REQ_PREC_MIN, _QS_MINUTE_DIAG_DONE
    _field = kwargs.get('field') or kwargs.get('fields')
    _QS_REQ_PCT = False
    _QS_REQ_PREC_MIN = False
    _freq = str(kwargs.get('frequency') or kwargs.get('unit') or '1d')
    _is_minute = _freq in ('1m', '5m', '15m', '30m', '60m',
                           '1min', '5min', '15min', '30min', '60min')
    if _field:
        _is_list = isinstance(_field, list)
        _items = _field if _is_list else [_field]
        _QS_REQ_PCT = 'pctChg' in _items
        # 请求侧剔除平台合成字段 pctChg（PTrade 合法字段无 pctChg，返回侧再合成）；
        # 剔除后为空 → 兜底 ['close']（与 trade_date 门控版同规则）
        _mapped = [_QS_FIELD_TO_PTRADE.get(f, f) for f in _items if f != 'pctChg']
        if not _mapped:
            _mapped = ['close']
        # 平台返回列 = 请求列（2026-09-01 平台实证）：pctChg 剔除后，须注入 preclose 基列
        # 供返回侧由 close/preClose 合成 pctChg（本地引擎返回全列故本地不受影响）
        if _QS_REQ_PCT and 'preclose' not in _mapped:
            _mapped.append('preclose')
        # 分钟频率请求 preclose：请求保持原样（零请求变更），返回侧由日线昨收合成（v3）
        _QS_REQ_PREC_MIN = _is_minute and 'preclose' in _mapped
        if 'field' in kwargs:
            kwargs['field'] = _mapped if _is_list else _mapped[0]
        if 'fields' in kwargs:
            kwargs['fields'] = _mapped if _is_list else _mapped[0]
    # v4（2026-09-01 平台实证第四轮）：平台分钟 get_history 对「security_list 列表 + is_dict=True」
    # 返回空 dict（QS_MINUTE_DIAG keys=0 实证），而「裸字符串 security_list + is_dict=True」
    # 日线已实证有效（第一轮 hist 键正确）→ 与门控版 D4-S7 R1 同构：is_dict 走逐码路径
    # （裸字符串形态），拼 code→DataFrame dict，策略代码零改动。
    _is_dict = bool(kwargs.pop('is_dict', False))
    _secs = kwargs.pop('security_list', None)
    if _secs is None:
        _secs = kwargs.pop('security', None)
    if _secs is None and args and isinstance(args[0], (str, list, tuple)):
        # 本地风格位置 security（策略零改动：get_history(codes, count=3, ...)）
        _secs = args[0]
        args = ()
    if isinstance(_secs, str):
        _secs = [_secs]
    if _is_dict and _secs:
        _out = {{}}
        for _s in _secs:
            _kw = dict(kwargs)
            # 裸字符串形态（平台已实证唯一有效形态；security 关键字形态违反平台契约）
            _kw['security_list'] = _s
            try:
                _item = _QSHistoryState.orig(*args, **_kw)
            except Exception:
                _item = None
            _out[_s] = _qs_to_dataframe(_item)
    elif _secs:
        _kw = dict(kwargs)
        _kw['security_list'] = _secs
        _result = _QSHistoryState.orig(*args, **_kw)
        if isinstance(_result, dict):
            _out = {{k: _qs_to_dataframe(v) for k, v in _result.items()}}
        else:
            _out = _qs_to_dataframe(_result)
    else:
        _result = _QSHistoryState.orig(*args, **kwargs)
        if isinstance(_result, dict):
            _out = {{k: _qs_to_dataframe(v) for k, v in _result.items()}}
        else:
            _out = _qs_to_dataframe(_result)
    if _QS_REQ_PREC_MIN:
        if not _QS_MINUTE_DIAG_DONE:
            _QS_MINUTE_DIAG_DONE = True
            try:
                if isinstance(_out, dict):
                    _k0 = next(iter(_out), None)
                    _df0 = _out.get(_k0)
                    # v5（2026-09-01 平台实证第五轮）：平台分钟逐码返回体可能是 OrderedDict
                    # 形态（非 DataFrame/ndarray）→ 打印其键（字段名）供结构判定
                    if _df0 is not None and hasattr(_df0, 'columns'):
                        _desc = "cols=%s" % (list(_df0.columns),)
                    elif _df0 is not None and hasattr(_df0, 'keys'):
                        try:
                            _desc = "omap_keys=%s" % (list(_df0.keys())[:8],)
                        except Exception:
                            _desc = "type=%s" % type(_df0).__name__
                    else:
                        _desc = "type=%s" % type(_df0).__name__
                    log.info("QS_MINUTE_DIAG keys=%d %s" % (len(_out), _desc))
                else:
                    log.info("QS_MINUTE_DIAG single cols=%s" % (
                        list(_out.columns) if hasattr(_out, 'columns')
                        else type(_out).__name__))
            except Exception:
                pass
        _fq = kwargs.get('fq', 'pre')
        if isinstance(_out, dict):
            for _k in list(_out.keys()):
                _out[_k] = _qs_synth_minute_preclose(_out[_k], _k, _fq)
        elif isinstance(_out, _qs_pd.DataFrame):
            _code0 = kwargs.get('security_list')
            if _code0 is None:
                _code0 = kwargs.get('security')
            if _code0 is None and args:
                _code0 = args[0]
            if isinstance(_code0, (list, tuple)):
                _code0 = _code0[0] if len(_code0) else ''
            _out = _qs_synth_minute_preclose(_out, _code0, _fq)
    _qs_shape_check('get_history', 'df_or_dict', _out)
    return _out
'''

# source_import trade_date 门控扩展（2026-08-19，框架层方案 source_import-ptrade-history-translation-design.md）
# 仅当源策略显式使用 trade_date（本地 provider 合成伪列）时注入本扩展；不使用 trade_date 的
# 策略转换输出与改造前逐字节一致（纯增益）。扩展重定义 _qs_to_dataframe（改名后再合成 trade_date，
# R3 格式以本地实测为准：object、'YYYY-MM-DD'、Asia/Shanghai strftime）与 get_history
# （请求侧剔除合成字段 trade_date、is_dict=True 走逐码路径——契约无关，R1）。
_QS_HISTORY_TRADE_DATE_EXT = '''
{marker}
# [qs-import-generated] trade_date / pctChg 合成 + is_dict 逐码（source_import 门控扩展，2026-08-19/27）
# trade_date 是本地 provider 合成伪列（object 类型 'YYYY-MM-DD' 字符串）；PTrade 无此字段，
# 由返回体的 datetime/time 派生。请求侧剔除合成字段（只传真实字段），返回侧补齐。
# pctChg（涨跌幅百分比）D4-S7 增补（2026-08-27 平台实证：PTrade get_history 合法字段无 pctChg，
# 由 close/preClose 合成 (close/preClose−1)×100）。
_QS_SYNTHETIC_FIELDS = {{'trade_date', 'pctChg'}}
# 合成意图状态（get_history 设置，_qs_to_dataframe 消费；None=全合成，集合=仅请求字段）
_QS_REQUESTED_SYNTH = None


def _qs_synthesize_trade_date(df, requested=True):
    """trade_date 合成（透传优先/合成兜底，D4-S7 M3 裁定）：
    requested 仅作延伸语义标记：默认 True（调用方需要则合成）；
    返回体已有 trade_date（本地形态透传）则原样保留（任何 requested 下均优先生成）。"""
    if 'trade_date' in df.columns:
        return df
    if not requested:
        return df
    src = None
    for _col in ('time', 'datetime'):
        if _col in df.columns:
            src = df[_col]
            break
    # PTrade 返回 DataFrame 时日期常在 index（无 time/datetime 列）
    if src is None:
        idx = df.index
        if hasattr(idx, 'dtype') and _qs_np.issubdtype(idx.dtype, _qs_np.datetime64):
            src = _qs_pd.Series(idx, index=idx)
        elif len(idx) > 0:
            try:
                _ = _qs_pd.to_datetime(idx)
                src = _qs_pd.Series(idx, index=idx)
            except Exception:
                pass
    if src is None:
        return df
    try:
        if hasattr(src, 'dtype') and _qs_np.issubdtype(src.dtype, _qs_np.integer):
            _ts = _qs_pd.to_datetime(src, unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')
        else:
            _ts = _qs_pd.to_datetime(src)
            if getattr(_ts.dt, 'tz', None) is not None:
                _ts = _ts.dt.tz_convert('Asia/Shanghai')
        df['trade_date'] = _ts.dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    return df


def _qs_synthesize_pct_chg(df, requested=True):
    """pctChg 合成（D4-S7，2026-08-27 平台实证：PTrade get_history 合法字段无 pctChg）：
    默认合成（调用方需要 pctChg 时）；仅当 close/preClose 两基列可用时合成 (close/preClose−1)×100。
    fail-soft：异常/缺基列 → 保留空（策略侧按数据不足跳过）。即返回已有 pctChg 则透传保留。"""
    if 'pctChg' in df.columns:
        return df
    if not requested:
        return df
    if 'close' in df.columns and 'preClose' in df.columns:
        try:
            _prec = df['preClose'].astype(float)
            _ok = _prec > 0
            _pct = _qs_pd.Series(_qs_np.nan, index=df.index)
            _pct[_ok] = (df.loc[_ok, 'close'].astype(float) / _prec[_ok] - 1.0) * 100.0
            df['pctChg'] = _pct
        except Exception:
            pass
    return df


def _qs_to_dataframe(item):
    """ndarray / DataFrame 双形态统一：列名映射 + trade_date/pctChg 合成（D4-S7 #1/#3/#4）。
    合成意图由模块状态 _QS_REQUESTED_SYNTH（get_history 设置）决定；单参签名保持模板契约。"""
    _df = None
    if isinstance(item, _qs_np.ndarray) and hasattr(item, 'dtype') and hasattr(item.dtype, 'names'):
        _df = _qs_pd.DataFrame(item)
    elif isinstance(item, _qs_pd.DataFrame):
        _df = item
    if _df is not None:
        # 列名统一映射：PTrade → 本地（datetime→time / money→amount / preclose→preClose）
        _rename = {{k: v for k, v in _QS_COL_TO_LOCAL.items()
                    if k in _df.columns and v not in _df.columns}}
        if _rename:
            _df = _df.rename(columns=_rename)
        # 合成（透传优先：返回已有列则保留；requested 空集=全不合成、None=全合成、集合=仅请求字段）
        _req = _QS_REQUESTED_SYNTH
        _want_td = _req is None or ('trade_date' in _req)
        _want_pct = _req is None or ('pctChg' in _req)
        _df = _qs_synthesize_trade_date(_df, requested=_want_td)
        return _qs_synthesize_pct_chg(_df, requested=_want_pct)
    return item


def get_history(*args, **kwargs):
    global _QS_REQUESTED_SYNTH
    _field = kwargs.get('field') or kwargs.get('fields')
    _requested = []
    if _field:
        _is_list = isinstance(_field, list)
        _items = _field if _is_list else [_field]
        _requested = list(_items)                       # 原始请求字段（含合成字段，供返回侧合成判断）
        _mapped = [_QS_FIELD_TO_PTRADE.get(f, f) for f in _items
                   if f not in _QS_SYNTHETIC_FIELDS]
        if not _mapped:
            _mapped = ['close']
        # 平台返回列 = 请求列（2026-09-01 平台实证）：pctChg 剔除后注入 preclose 基列供返回侧合成
        if 'pctChg' in _requested and 'preclose' not in _mapped:
            _mapped.append('preclose')
        if 'field' in kwargs:
            kwargs['field'] = _mapped if _is_list else _mapped[0]
        if 'fields' in kwargs:
            kwargs['fields'] = _mapped if _is_list else _mapped[0]
    # 合成意图：trade_date 恒合成（本地引擎返回体常含 trade_date，策略可能取未请求的该列——
    # 原生语义依赖；平台无该列则从索引合成）；pctChg 仅当请求含它时合成（需 close/preClose 基列）。
    _want_td = True
    _want_pct = 'pctChg' in _requested
    _QS_REQUESTED_SYNTH = set()
    if _want_td:
        _QS_REQUESTED_SYNTH.add('trade_date')
    if _want_pct:
        _QS_REQUESTED_SYNTH.add('pctChg')
    if not _QS_REQUESTED_SYNTH:
        _QS_REQUESTED_SYNTH = None
    _is_dict = bool(kwargs.pop('is_dict', False))
    _secs = kwargs.pop('security_list', None)
    if _secs is None:
        _secs = kwargs.pop('security', None)
    # D4-S7 #6（2026-08-27 本地引擎实证）：security_list 可能是裸字符串（如 '000852.SS'）
    # 而非 list——逐码路径按可迭代拆分会把字符串逐字符拆（'0','8','5','2','.','S'）。
    # 统一归一为单元素 list（本地/平台契约均按证券列表处理）。
    if isinstance(_secs, str):
        _secs = [_secs]
    if _is_dict and _secs:
        # R1：契约无关——不依赖多标的返回形态，逐码调用（单证券返回风险最小）拼成 dict（code -> df）
        result = {{}}
        for _s in _secs:
            _kw = dict(kwargs)
            _kw['security_list'] = [_s]
            try:
                _item = _QSHistoryState.orig(*args, **_kw)
            except (TypeError, ValueError):
                _kw2 = dict(kwargs)
                _kw2['security'] = _s
                _item = _QSHistoryState.orig(*args, **_kw2)
            result[_s] = _qs_to_dataframe(_item)
        _qs_shape_check('get_history', 'df_or_dict', result)
        return result
    if _secs:
        # 非 dict 模式仍需把 security_list/security 传回原始 API（如单市场 σ 计算）
        _kw = dict(kwargs)
        _kw['security_list'] = _secs
        try:
            _result = _QSHistoryState.orig(*args, **_kw)
        except (TypeError, ValueError):
            _kw2 = dict(kwargs)
            _kw2['security'] = _secs
            _result = _QSHistoryState.orig(*args, **_kw2)
    else:
        _result = _QSHistoryState.orig(*args, **kwargs)
    if isinstance(_result, dict):
        _out = {{k: _qs_to_dataframe(v) for k, v in _result.items()}}
    else:
        _out = _qs_to_dataframe(_result)
    _qs_shape_check('get_history', 'df_or_dict', _out)
    return _out
'''


# source_import 市价单拆单注入（2026-08-22，框架方案 PTrade 平台对齐治理 v4 §3.1 A1）
# 平台实测（测试456/908）：创业板/科创板市价单单笔上限 50,000 股（51,000 拒），沪深主板 ≥86,900 通过；
# 超限 = 整单取消（无订单号、仓位缺口）。拆单注入把 >49,000 股的目标拆成多笔 ≤49,000 股，
# 全板规避上限；本地注入同构 helper → 双端订单序列逐笔一致。
# 设计约束（R3）：
#  ① px 基准写死 = 统一链 ① 层前收（_qs_last_close，A2 提供）；px<=0 时回退下单不拆（与现行为一致）；
#  ② 段级独立现金检查：段额预分配 min(可用现金,目标)/N（vol_regime per_new 预算模型通用化），
#     合计勾稽 Σ(段股数×px) + 预留 ≤ 可用现金（_PX_BUFFER 同类缓冲语义）；
#  ③ 费用前置门已实证：平台最低佣金 ≈5 元/笔（probe_commission 2026-08-22），与本地同构 → 拆单双端对称。
_QS_ORDER_SPLIT_EXT = '''
{marker}
# [qs-import-generated] 市价单拆单注入（source_import 门控扩展，2026-08-22）
# 平台分板市价单上限（创业板/科创板 50,000 股）→ >49,000 股目标自动拆多笔。
# 与本地 ptrade_api._qs_split_order 同构（同常量同算法）→ 双端订单序列逐笔一致。
_QS_MAX_ORDER_SHARES = 49000      # 单笔安全上限（低于创业板/科创板 50,000；主板远高）
_QS_SPLIT_LOT = 100               # A股整手
_QS_SPLIT_PX_BUFFER = 1.0        # 整手可负担预筛缓冲（D4-S6 换算价=当日撮合价后价差归零，
                                 #  审计 R1 裁定 0.95→1.0：消除多笔拆单 5% 系统性低配；仅兜竞对边角）


class _QSOrderRefState:
    """捕获平台原始订单 API（class 属性承载——平台静态白名单识别 class 语句，
    变量持函数引用后调用会被 LOCAL-API-WHITELIST BLOCK，2026-08-22 平台实证）。"""
    target_orig = None
    order_orig = None
    value_orig = None
    target_percent_orig = None
    percent_orig = None


_QSOrderRefState.target_orig = order_target_value
_QSOrderRefState.order_orig = order
_QSOrderRefState.value_orig = order_value
# D4-S7（2026-08-27 平台冒烟取证）：percent API 按策略使用裁剪注入——未注入时
# 赋值引用不存在的名字（NameError，产物加载即崩）。防御式绑定（存在才绑，否则 None）。
try:
    _QSOrderRefState.target_percent_orig = order_target_percent
except NameError:
    _QSOrderRefState.target_percent_orig = None
try:
    _QSOrderRefState.percent_orig = order_percent
except NameError:
    _QSOrderRefState.percent_orig = None


def _qs_split_order(security, value, px, cash_avail=None):
    """把目标金额拆成 ≤_QS_MAX_ORDER_SHARES 股的多笔 order(amount=...)。

    - value: 目标金额（order_target_value 语义）；px: 现价（统一链 ① 层前收；<=0 回退）；
    - cash_avail: 可用现金；None 时以 value 为预算上界（不放大，段额不超目标）。
    - 返回 (order_list, total_shares)；order_list = [(code, amount), ...]；
      无法拆（px<=0 / amount<=0 / 预算不足以买 1 手）返回 ([], 0)，调用方走原路径。
    """
    if px is None or px <= 0 or value is None or value <= 0:
        return [], 0
    n = value / px
    if n <= _QS_MAX_ORDER_SHARES:
        # D4-S7 #9（2026-08-28，与本地 ptrade_api._qs_split_order 同构修复）：
        # 单笔分支同样应用 cash_avail 钳制（此前只在拆单分支生效）。budget=min(cash,value) 无折扣。
        _budget = min(cash_avail, value) if cash_avail is not None else value
        if _budget < value:
            n = _budget / px
        amount = int(n / _QS_SPLIT_LOT) * _QS_SPLIT_LOT
        return ([(security, amount)], amount) if amount > 0 else ([], 0)
    k = int((n + _QS_MAX_ORDER_SHARES - 1) // _QS_MAX_ORDER_SHARES)  # ceil
    # 段额预分配（R3 ②）：目标金额均分 k 段；cash_avail 提供时改按 min(现金,目标)/k
    # 分配（现金不足则收缩段额），并启用合计勾稽 Σ(amount×px) ≤ min(cash,value)×缓冲。
    budget = min(cash_avail, value) if cash_avail is not None else value
    use_buffer = cash_avail is not None
    per = budget / k
    orders = []
    total_cost = 0.0
    for _i in range(k):
        seg_amount = int(per / px / _QS_SPLIT_LOT) * _QS_SPLIT_LOT
        if seg_amount <= 0:
            seg_amount = _QS_SPLIT_LOT
        seg_amount = min(seg_amount, _QS_MAX_ORDER_SHARES)
        cost = seg_amount * px
        if use_buffer and total_cost + cost > budget * _QS_SPLIT_PX_BUFFER:
            # 合计勾稽：超出可用现金缓冲 → 舍去本段（与平台"资金不足降量"同语义）
            break
        orders.append((security, seg_amount))
        total_cost += cost
    return (orders, sum(a for _, a in orders)) if orders else ([], 0)


# 订单 API 拆单包装：order_target_value 系列按统一链 ① 层 px 拆分；
# 策略代码零改动（调用语句不变，由 wrapper 收口）。
#
# P-D12（2026-08-26）：order_target_value 恢复目标市值语义——delta 修复（B1，
# 与本地 ptrade_api._qs_wire_order_target_value 同构）。分支序：清仓保底 →
# px 缺失回退 → 现值/delta → 微调跳过（0.5%，与引擎 min_rebalance_pct 一致）
# → 减仓走原生命令 → 加仓对 delta 拆单 → delta<1 手告警 no-op。
# 现值入口（D1/D6）：get_position(_qs_norm_code(security)).amount × T-1 px
# ——_qs_norm_code 由 P-D11 持仓视图块提供（策略不调 position API 时该块不注入，
# 此处内嵌兜底归一：_qs_norm_code 名字存在性惰性检查）。

_QS_MIN_REBALANCE_PCT = 0.005   # P-D12 D7：与引擎 min_rebalance_pct 默认一致（T10 钉死）


def _qs_pos_amount(security):
    """P-D12 D6：平台侧现值查询——get_position(.SS/.SZ 内联归一).amount（异常视为空仓=D5）。

    自包含归一（不依赖 P-D11 _qs_norm_code——该块可能未注入，跨模板引用会
    被 LOCAL-API-WHITELIST 拦截）；订单场景最小集：5/6/9→.SS 其余→.SZ。"""
    try:
        _s = str(security).strip().upper()
        _bare = _s.split('.', 1)[0]
        _code = _bare + ('.SS' if _bare[:1] in ('5', '6', '9') else '.SZ')
        _pos = get_position(_code)
        return float(getattr(_pos, 'amount', 0) or 0)
    except Exception:
        return 0.0


def _qs_noop_target(security, delta, reason):
    """P-D12：no-op 返回（None 语义=未成交；平台无 Order 对象，log 显式告知）。"""
    try:
        log.warning('QS_NOOP_ORDER api=order_target_value code=%s delta=%.1f'
                    ' reason=%s' % (security, float(delta or 0), reason))
    except Exception:
        pass
    return None


# cash_avail 一次性告警状态（D4-S7 M2 fail-open：取数不可得只告警一次，钳制不生效）
# 注：本模板字符串经 .format 渲染，字面量花括号须双写转义。
_QS_CASH_WARNED_STATE = {{'v': False}}
# D4-S7 #5 接线（ZCode 方案 A，2026-08-27）：handle_data 入口经 _qs_capture_ctx 捕获 context，
# 平台探针实证 context.portfolio.cash=100000 可用（与本地 engine.account.cash 同源）。None=未捕获
# （不注入/注入失败）→ fail-open 兜底路径，行为与 B 方案等同。
_QS_RUNTIME_CTX = None


def _qs_capture_ctx(context):
    """D4-S7 #5：handle_data 入口捕获 context（转换管线注入一行）。"""
    global _QS_RUNTIME_CTX
    _QS_RUNTIME_CTX = context


def _qs_px_exec(security):
    """D4-S7 #8（2026-08-27 ZCode 裁定）：订单换算价 = 当日撮合价优先（与本地 D4-S6 ②层镜像）。

    当日取法双端差异（2026-08-27 实证）：
    - 本地引擎 get_history(count=1, include=False) = T-1 前收；include=True = 当日收盘（6.57 实证）；
    - 平台 get_history(count=1) 在 before_trading_start 已返回 T 日 close（P-D9 L872-875 实证，include 语义不同）。
    故优先 include=True（当日），平台不支持时回退 include=False（平台下仍=当日），再退 ① 层前收，
    ≤0 返回 0（调用方 fail-open 原生）。current_price 函数本体零改动。
    """
    for _inc in (True, False):
        try:
            _df = get_history(count=1, frequency="1d", field=["close"],
                              security_list=security, fq="pre", include=_inc)
            if _df is not None and hasattr(_df, "iloc") and len(_df) > 0:
                _c = float(_df.iloc[-1].get("close", 0) or 0)
                if _c > 0:
                    return _c
        except Exception:
            pass
    _v = _qs_last_close_lookup(security)
    if _v and _v > 0:
        return _v
    return 0.0


def _qs_cash_avail():
    """D4-S7 #5：平台可用资金取数（fail-open）。
    优先：_QS_RUNTIME_CTX.portfolio.cash（平台探针实证可用，2026-08-27）；
    次选：平台 API get_positions()/get_position() 四字段探测（available_cash/available/cash/avail_cash）；
    全不可得 → 返回 None（钳制不生效，保持平台现状全额下单）+ 一次性告警 QS_CASH_AVAIL_UNAVAILABLE。
    禁止：取不到禁下单 / 静默逐笔告警 / 引用未登记平台 API（portability BLOCK）。"""
    # 路径 1：注入的 context.portfolio.cash（平台实证）
    try:
        _ctx = _QS_RUNTIME_CTX
        if _ctx is not None:
            _cash = float(getattr(getattr(_ctx, 'portfolio', None), 'cash', 0) or 0)
            if _cash > 0:
                return _cash
    except Exception:
        pass
    # 路径 2：平台 API 探测（兜底）
    _obj = None
    try:
        _obj = get_positions()
    except Exception:
        pass
    if _obj is None:
        try:
            _obj = get_position()          # 部分平台实现 get_position() 无参返回全部持仓
        except Exception:
            pass
    if _obj is not None:
        for _field in ('available_cash', 'available', 'cash', 'avail_cash'):
            try:
                _cash = float(getattr(_obj, _field, 0) or 0)
                if _cash > 0:
                    return _cash
            except Exception:
                continue
        try:
            # 兜底：list/iterable 形态（逐仓位对象找现金字段）
            for _it in _obj:
                for _field in ('available_cash', 'available', 'cash', 'avail_cash'):
                    try:
                        _cash = float(getattr(_it, _field, 0) or 0)
                        if _cash > 0:
                            return _cash
                    except Exception:
                        continue
        except Exception:
            pass
    if not _QS_CASH_WARNED_STATE['v']:
        _QS_CASH_WARNED_STATE['v'] = True
        try:
            log.warning('QS_CASH_AVAIL_UNAVAILABLE api=order cash_avail=unavailable'
                        '（钳制不生效，维持平台全额下单语义——仅告警一次）')
        except Exception:
            pass
    return None


def order_target_value(security, value, *args, **kwargs):
    if value is None:
        return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
    if value == 0:
        return _QSOrderRefState.target_orig(security, 0, *args, **kwargs)
    # P-D13 C4c：下单前退市状态校验（只告警不拦截——平台数据矛盾检测器：
    # 平台曾出现退市日仍可买入的自相矛盾，fall_reversal L66+L70 实证）。
    try:
        _dl = get_stock_status([str(security)], query_type='DELISTING')
        if _dl and _dl.get(str(security), False):
            log.warning('QS_DELIST_ORDER code=%s status=delisted'
                        '（继续下单——平台数据矛盾检测，不吞单）' % security)
    except Exception:
        pass
    # D4-S6（2026-08-27）+ D4-S7 #8（2026-08-27）：换算价 = 当日撮合价语义。
    # 平台无 current_price（NameError 实证，见本模板 current_price 注释）→ 订单换算点
    # 用 _qs_px_exec（当日 close 优先——平台 get_history(count=1) 已返 T 日 close，P-D9 实证；
    #  ① 前收回退；≤0 fail-open 原生），与本地 D4-S6 ② 层（_api.current_price 原语义）镜像。
    _px_exec = _qs_px_exec(security)     # 当日撮合价（D4-S7 #8：不再①优先）
    if _px_exec <= 0:
        return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
    _px = _qs_last_close_lookup(security)  # ① 层：仅用于现值/delta（P-D12 目标市值语义）
    if _px <= 0:
        return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
    _amt = _qs_pos_amount(security)
    _current = _amt * _px
    _delta = value - _current
    if _current > 0 and abs(_delta) / _current < _QS_MIN_REBALANCE_PCT:
        return _qs_noop_target(security, _delta, 'below_rebalance_threshold')
    if _delta <= 0:
        if _current > 0:
            return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
        return _qs_noop_target(security, 0.0, 'already_flat')
    # D4-S7 #5（2026-08-27 ZCode 定谳）：平台可透支 vs 本地现金硬约束 → 模板侧可负担钳制。
    # 系数 1.0（M1：与本地 D4-S6 buffer=1.0 同构，budget=min(cash_avail, value) 无折扣）；
    # fail-open（M2）：cash_avail 取数不可得 → None → 钳制不生效（平台现状全额下单）+ 一次性告警。
    _cash = _qs_cash_avail()
    orders, _tot = _qs_split_order(security, _delta, _px_exec, cash_avail=_cash)
    if not orders:
        try:
            log.warning('QS_ZERO_ORDER api=order_target_value code=%s delta=%.1f'
                        ' px=%.4f one_lot_value=%.1f reason=delta_below_one_lot'
                        % (security, _delta, _px_exec, _px_exec * 100))
        except Exception:
            pass
        return _qs_noop_target(security, _delta, 'delta_below_one_lot')
    _ids = []
    for _code, _amt2 in orders:
        _ids.append(order(_code, _amt2))
    return _ids[-1] if _ids else None


def order(security, amount, *args, **kwargs):
    if amount is None or amount <= 0 or amount <= _QS_MAX_ORDER_SHARES:
        return _QSOrderRefState.order_orig(security, amount, *args, **kwargs)
    # 直接 order() 超限：同样拆（amount 已是股数，无需 px）；末段整手对齐。
    k = int((amount + _QS_MAX_ORDER_SHARES - 1) // _QS_MAX_ORDER_SHARES)
    per = int((amount // k) / _QS_SPLIT_LOT) * _QS_SPLIT_LOT
    if per <= 0:
        return _QSOrderRefState.order_orig(security, amount, *args, **kwargs)
    _id = None
    _placed = 0
    for _i in range(k):
        _seg = per if _i < k - 1 else amount - _placed
        _seg = int(_seg / _QS_SPLIT_LOT) * _QS_SPLIT_LOT
        if _seg <= 0:
            break
        _id = _QSOrderRefState.order_orig(security, _seg, *args, **kwargs)
        _placed += _seg
    return _id if _id is not None else _QSOrderRefState.order_orig(security, amount, *args, **kwargs)


def order_value(security, value, *args, **kwargs):
    """order_value 拆单包装：金额语义 → ② 层换算价（D4-S6/D4-S7 #8 修复，与 order_target_value 同链路）。"""
    _px_exec = _qs_px_exec(security)     # 当日撮合价（D4-S7 #8：不再①优先）
    if _px_exec <= 0:
        return _QSOrderRefState.value_orig(security, value, *args, **kwargs)
    # D4-S7 #5：现金钳制（与 order_target_value 同链，M1 系数 1.0 / M2 fail-open）
    _cash = _qs_cash_avail()
    orders, _tot = _qs_split_order(security, value, _px_exec, cash_avail=_cash)
    if not orders:
        return _QSOrderRefState.value_orig(security, value, *args, **kwargs)
    _ids = []
    for _code, _amt in orders:
        _ids.append(order(_code, _amt))
    return _ids[-1] if _ids else None


def order_target_percent(security, percent, *args, **kwargs):
    """order_target_percent 包装：比例语义的目标仓——无法在模板内可靠取组合总值
    （依赖运行时 context），保守回退原 API（平台 percent 单按当前市值折算，超限风险低，
    与 px=0 回退同语义）；保留入口覆盖保证双端 API 集合一致。"""
    return _QSOrderRefState.target_percent_orig(security, percent, *args, **kwargs)


def order_percent(security, percent, *args, **kwargs):
    """order_percent 包装：同 order_target_percent，回退原 API（见上）。"""
    return _QSOrderRefState.percent_orig(security, percent, *args, **kwargs)


def _qs_last_close_lookup(code):
    """统一链 ① 层（前收）：优先 _qs_last_close 框架缓存（A2 由 get_history 链记录）。

    缓存格式 {{bare_code: (day, close)}}：返回当日记录的最近日均线 close；
    跨日由记录 hook 的 stamp 校验自动失效（PIT 纪律）。
    """
    try:
        cache = _QSLastCloseState.cache or {{}}
        v = cache.get(_qs_bare(str(code)), 0.0)
        if isinstance(v, (tuple, list)) and len(v) == 2:
            return float(v[1])
        if v and v > 0:
            return float(v)
    except Exception:
        pass
    return 0.0


class _QSLastCloseState:
    cache = None  # {{code: (day, close)}}；stamp = 最近记录交易日（PIT：跨日失效）
    stamp = None


def _qs_record_trade_day(df):
    """从 get_history 返回体最后一行推断交易日（'YYYY-MM-DD'）。

    优先 trade_date 列（扩展合成列），其次 time/datetime，最后 index。
    """
    if df is None or not hasattr(df, 'iloc') or len(df) == 0:
        return ''
    try:
        r = df.iloc[-1]
        for col in ('trade_date', 'time', 'datetime'):
            if col in df.columns:
                v = r.get(col)
                if v is None:
                    continue
                s = str(v)
                if s and s != 'nan':
                    return s[:10]
        idx = df.index
        if len(idx) > 0:
            s = str(idx[-1])
            if s and s != 'nan':
                return s[:10]
    except Exception:
        pass
    return ''


def _qs_history_record_core(args, kwargs):
    """调用前一版 get_history（_QSHistoryChainState.prev），提取最近一根已完成日线
    close 写入缓存（PIT 纪律）。"""
    result = _QSHistoryChainState.prev(*args, **kwargs)
    try:
        unit = kwargs.get('frequency') or kwargs.get('unit') or '1d'
        if str(unit) != '1d':
            return result
        fqv = kwargs.get('fq')
        if fqv is None or str(fqv) not in ('pre',):
            return result
        day = _qs_record_trade_day(result if not isinstance(result, dict)
                                   else next(iter(result.values()), None))
        if not day:
            return result
        if _QSLastCloseState.stamp != day:
            if _QSLastCloseState.cache is None:
                _QSLastCloseState.cache = {{}}
            else:
                _QSLastCloseState.cache.clear()
            _QSLastCloseState.stamp = day
        cache = _QSLastCloseState.cache
        if isinstance(result, dict):
            for code, df in result.items():
                if df is None or not hasattr(df, 'iloc') or len(df) == 0:
                    continue
                try:
                    v = float(df.iloc[-1].get('close', 0))
                except Exception:
                    v = 0.0
                if v and v > 0:
                    cache[_qs_bare(str(code))] = (day, v)
        else:
            df = result
            if df is not None and hasattr(df, 'iloc') and len(df) > 0:
                try:
                    codes = df['code'].values if 'code' in df.columns else None
                except Exception:
                    codes = None
                if codes is not None:
                    for i in range(len(df)):
                        try:
                            v = float(df.iloc[i].get('close', 0))
                        except Exception:
                            v = 0.0
                        if v and v > 0:
                            cache[_qs_bare(str(codes[i]))] = (day, v)
                else:
                    try:
                        v = float(df.iloc[-1].get('close', 0))
                    except Exception:
                        v = 0.0
                    if v and v > 0:
                        sec = (kwargs.get('security_list') or kwargs.get('security')
                               or (args[0] if args else None))
                        if sec is not None:
                            if isinstance(sec, (list, tuple)):
                                sec = sec[0]
                            cache[_qs_bare(str(sec))] = (day, v)
    except Exception:
        pass
    return result


def _qs_bare(code):
    return str(code).strip().upper().split('.')[0]


class _QSHistoryChainState:
    """前一版 get_history 引用（class 属性承载——平台白名单识别 class，2026-08-22）。"""
    prev = None


_QSHistoryChainState.prev = get_history


def get_history(*args, **kwargs):
    return _qs_history_record_core(args, kwargs)


def current_price(security):
    """统一链 current_price（PTrade 转换侧）。

    平台实证（2026-08-22 冒烟）：**current_price 不是真实 PTrade API**——模块加载期
    引用即 NameError（平台仅注入 order/get_history/get_trade_days 等标准 API；此前
    策略运行 current_price 返回 0 正是 NameError 被 try/except 吞掉）。故本侧统一链
    只含 ① 前收（框架缓存）→ ③ get_history 兜底；本地 QuantStudio 侧另有模块级
    注入的统一链（含 ② 原 API 语义，ptrade_api 内）。返回 0 = 不可得（与旧行为一致）。
    """
    v = _qs_last_close_lookup(security)
    if v and v > 0:
        return v
    try:
        df = get_history(count=1, frequency="1d", field=["close"],
                         security_list=security, fq="pre", include=False)
        if df is not None and hasattr(df, "iloc") and len(df) > 0:
            c = float(df.iloc[-1].get("close", 0) or 0)
            if c > 0:
                return c
    except Exception:
        pass
    return 0.0
'''


# ============================================================================
# P-D9 注入模板：filter_stock_by_status('ST') 转换语义一致化（2026-08-22，方案 v3）
# 平台实证（probe_pd9_filter_ptrade.py，测试456 2026-08-22 05:15）：
#   - 平台 'ST' 仅官方 ST 标记（文档 L5556）→ 退市整理期仙股（无 ST 标记）留池；
#   - 平台 get_history(count=1) 在 before_trading_start 已返回 T 日 close（E 时点差不存在，
#     与本地 attach_day 同日快照同值）；
#   - circ_mv/total_mv 平台 get_fundamentals 不可得（KeyError）→ market_cap 分支降级
#     price-only（DB 实测本地 market_cap 触发零样本，差异面=零）；
#   - 批量 get_history 8 码 0.007s → 幸存者兜底可行。
# 语义：本地 filter_stock_by_status('ST') = is_st OR is_delisting_risk（ptrade_api.py:877-878 锚）。
# 对齐：转换侧补 is_delisting_risk = close<1（当日 T close）剔除，fail-open 与本地一致。
# ============================================================================
_QS_FILTER_STATUS_EXT = '''
{marker}
# [qs-import-generated] filter_stock_by_status('ST') 退市风险兜底注入（P-D9，2026-08-22）
# 平台 'ST' 仅官方 ST 标记 → 补本地同款 is_delisting_risk（close<1，当日 T close）。
# fail-open：取数失败 log.warning + 保持平台原生结果（与本地 except: return result 同语义）。
# 性能（A 条三级）：批量优先（多码一次 get_history）→ 幸存者兜底（仅对原生过滤幸存池判）
# → 当日缓存复用（探针实证批量 8 码 0.007s vs 逐码 8 次 0.018s）。
_QSFilterStatusState = type("_QSFilterStatusState", (), {{"orig": None, "cache": None}})
_QSFilterStatusState.orig = filter_stock_by_status
_QS_FILTER_DELISTING_THRESHOLD = 1.0  # 面值退市线（元）


def _qs_status_prefetch_closes(codes):
    """批量预取多码 close（一次 get_history 多码调用，探针实证 8 码 0.007s）→ 写入缓存。

    平台返回形态：DataFrame（code/close 或 time/close 列）。fail-open：异常/空返回不写缓存。
    缓存命中短路：codes 全部已缓存 → 跳过批量（同日重复调用零取数）。
    """
    cache = _QSFilterStatusState.cache
    if cache is None:
        cache = {{}}
        _QSFilterStatusState.cache = cache
    if not codes:
        return
    try:
        missing = [c for c in codes if c not in cache]
    except Exception:
        missing = list(codes)
    if not missing:
        return
    try:
        df = get_history(count=1, frequency="1d", field=["close"],
                         security_list=list(missing), fq="pre", include=False)
    except Exception:
        return
    if df is None or not hasattr(df, "iloc") or len(df) == 0:
        return
    try:
        if "code" in getattr(df, "columns", []):
            vals = df["code"].values
            for i in range(len(df)):
                try:
                    v = float(df.iloc[i].get("close", 0) or 0)
                except Exception:
                    continue
                if v > 0:
                    cache[str(vals[i])] = v
        else:
            # 无 code 列（单码形状）：从 kwargs 无法回填多码 → 跳过（由逐码路径兜底）
            pass
    except Exception:
        pass


def _qs_status_history_close(code):
    """取单码当日 close（before_trading_start 平台已可返回 T close；批量缓存优先）。"""
    cache = _QSFilterStatusState.cache
    if cache is None:
        cache = {{}}
        _QSFilterStatusState.cache = cache
    try:
        if code in cache:
            return cache[code]
    except Exception:
        pass
    try:
        df = get_history(count=5, frequency="1d", field=["close"],
                         security_list=code, fq="pre", include=False)
        if df is not None and hasattr(df, "iloc") and len(df) > 0:
            v = float(df.iloc[-1].get("close", 0) or 0)
            if v > 0:
                cache[code] = v
                return v
    except Exception:
        pass
    return None


def _qs_is_delisting_risk(code):
    """退市风险兜底（P-D9，price 分支）：当日 close < 1 元 → 剔除。

    与本地 aligner 的 is_delisting_risk（close<1）同构；circ_mv 分支平台不可得
    （probe 实证 KeyError）且本地零触发 → 降级 price-only（残余差异已登记 P-D9）。
    取数失败返回 False（fail-open，与本地 except: return result 一致）。
    """
    try:
        v = _qs_status_history_close(code)
        if v is None:
            return False
        return v < _QS_FILTER_DELISTING_THRESHOLD
    except Exception:
        return False


def filter_stock_by_status(stocks, filter_type=None, query_date=None, *args, **kwargs):
    """平台原生过滤后，'ST' 语义补退市风险兜底（close<1 剔除）→ 与本地候选池一致。

    A 条三级：① 先批量预取幸存池 close（一次多码 get_history）→ ② 逐码判定（命中缓存
    不再取数；批量未覆盖的码由单码路径兜底）→ ③ 当日缓存（跨调用复用）。
    """
    result = _QSFilterStatusState.orig(stocks, filter_type, query_date, *args, **kwargs)
    try:
        ft = filter_type if filter_type is not None else ["ST", "HALT", "DELISTING"]
        if "ST" in ft:
            _qs_status_prefetch_closes(result)   # ① 批量预取（幸存者兜底：仅对 result 判）
            result = [c for c in result if not _qs_is_delisting_risk(c)]  # ② 判定
    except Exception as exc:
        log.warning("P-D9 filter fallback failed (keep native result): %s" % (exc,))
    _qs_shape_check('filter_stock_by_status', 'list', result)
    return result
'''


# source_import 日期归一化注入（2026-08-22，框架方案 PTrade 平台对齐治理 v4 §3.1 A3）
# 平台实测（测试456/908）：PTrade get_trade_days() 无 end_date 返回全量日历（含未来），
# 且日期格式混用 YYYYMMDD / datetime.date / 'YYYY-MM-DD'；本地 get_trade_days 已实现
# 'YYYY-MM-DD' ndarray + 缺省过滤至当前回测日（ptrade_api.py:1486-1497，不改）。
# A3 = PTrade 转换侧注入：格式归一 + 未来过滤（与本地同语义），策略层零自维护兜底。
# 补充：get_stock_info 返回的 listed_date 一并归一化（本地 'YYYY-MM-DD' string 契约）。
_QS_DATE_NORM_EXT = '''
{marker}
# [qs-import-generated] 交易日历/上市日归一化注入（source_import 门控扩展，2026-08-22）
# get_trade_days：PTrade 无 end_date 返回全量日历（含未来）+ 格式混用 → 归一 'YYYY-MM-DD' + <= today 过滤
# get_stock_info(listed_date)：统一 'YYYY-MM-DD'（与本地契约一致）
class _QSDateNormState:
    """原始 get_trade_days/get_stock_info 引用。

    class 属性承载（平台白名单识别 class 语句）。捕获时机 = 本模板 def 之前
    （此刻 get_trade_days/get_stock_info 仍是平台注入原函数，def 之后会被包装
    覆盖——若在 def 后惰性 globals() 解析会拿到包装自身 → 递归，2026-08-22 实证）。
    get_trade_days/get_stock_info 是真实 PTrade API（冒烟调用成功），顶层引用安全；
    非平台 API（如 current_price）才必须在顶层避免引用（见 A2 说明）。
    """
    orig_days = None
    orig_info = None


_QSDateNormState.orig_days = get_trade_days
_QSDateNormState.orig_info = get_stock_info


def _qs_norm_date_str(value):
    """把 PTrade 各种日期返回（str/date/datetime/YYYYMMDD）统一成 'YYYY-MM-DD'。"""
    if value is None:
        return ""
    if hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day'):
        return "%04d-%02d-%02d" % (value.year, value.month, value.day)
    text = str(value)
    if len(text) == 10 and text[4] == '-' and text[7] == '-':
        return text
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 8:
        return "%s-%s-%s" % (digits[:4], digits[4:6], digits[6:8])
    return text


def get_trade_days(start_date=None, end_date=None, count=None, *args, **kwargs):
    """本地同语义包装：'YYYY-MM-DD' 列表 + 缺省 end_date 时过滤至当前回测日。"""
    result = _QSDateNormState.orig_days(start_date, end_date, count, *args, **kwargs)
    days = [_qs_norm_date_str(x) for x in result] if result is not None else []
    today = _qs_today_str()
    if end_date is None and today:
        days = [d for d in days if d and d <= today]
    _qs_shape_check('get_trade_days', 'list', days)
    return days


def get_stock_info(stocks, field=None, *args, **kwargs):
    """listed_date 归一化包装（'YYYY-MM-DD'，与本地契约一致）。"""
    result = _QSDateNormState.orig_info(stocks, field, *args, **kwargs)
    if field is None or 'listed_date' in (field if isinstance(field, (list, tuple)) else [field]):
        try:
            if isinstance(result, dict):
                for code in result:
                    rec = result[code]
                    if rec is not None and hasattr(rec, 'get'):
                        rec['listed_date'] = _qs_norm_date_str(rec.get('listed_date'))
        except Exception:
            pass
    _qs_shape_check('get_stock_info', 'dict', result)
    return result


def _qs_today_str():
    """当前回测日（'YYYY-MM-DD'）。

    优先取 A2 统一链的最近交易日 stamp（由 get_history 数据流推导，PIT 正确）；
    无记录时返回 ''（不过滤——保持与平台原始行为一致，避免错误截断）。
    """
    try:
        s = _QSLastCloseState.stamp
        if s:
            return str(s)[:10]
    except Exception:
        pass
    try:
        ctx = _QSContextHolder.ctx
        dt = getattr(ctx, 'current_dt', None)
        if dt is not None:
            return str(dt.date() if hasattr(dt, 'date') else dt)[:10]
    except Exception:
        pass
    return ''


class _QSContextHolder:
    ctx = None


def _qs_ctx_holder():
    return _QSContextHolder.ctx


def _qs_set_context(ctx):
    _QSContextHolder.ctx = ctx
'''

# P-D10 get_fundamentals 契约对齐注入（2026-08-22，框架方案 docs/p-d10-gf-contract-design.md）
# 平台探针实证（probe_gf_contract_ptrade.py，测试123 2026-08-22）：
#   U1: 平台 get_fundamentals 原生接受 list —— 单次调用返回 index=股票代码 的合并 DataFrame；
#   U2: 返回 index=code ✓；end_date/publ_date 为 object 'YYYY-MM-DD' 字符串（本地为数值时间戳，
#       策略 _latest_by_code 以 dtype=float 强转+数值排序 → 必须归一为 YYYYMMDD 数值，否则 ValueError）；
#   U3: 平台返回固定列集（fields 不做列过滤）→ 按请求 fields 列筛选（本地 ptrade_api available 同构）；
#       请求字段不在返回列集 → 平台吞错返回空 df → 识别为 QS_SHIM_FIELD_MISSING 显性失败（必查项①，
#       不得当正常空处理）；date 'YYYYMMDD'/'YYYY-MM-DD'/None 均可用；is_dataframe kwarg 三形态同值（透传）。
# 注入模板铁律（既有三条）：class 属性承载 / def 前捕获 / 非平台 API 顶层禁引
# （get_fundamentals 是真实平台 API，def 前捕获安全）。批量 shim 走本 wrapper（DF 拼装，本地 B1 契约）。
_QS_FUNDAMENTALS_EXT = '''
{marker}
# [qs-import-generated] get_fundamentals 契约对齐包装（P-D10，2026-08-22；
# v8.3 整合：B6c range+PIT 主路径 / B8 缺列种子短路 / P-A2 eps 口径映射，2026-08-31）
# list 原生单调用 + 列筛选 + end_date/publ_date 数值归一 + 形状自检（QS_SHIM_SHAPE_VIOLATION）
import pandas as _qs_pd
import numpy as _qs_np


# P-A2 eps 口径保真常量（单一来源，v8.3 整合——旧版独立 _QS_FIDELITY_EPS_EXT 另含
# def get_fundamentals，注入在后 → 同名覆盖本 wrapper 全部修复（B6c/seeds/PIT），
# 平台复验 v8.0~v8.2 总根因）。此处直接烘焙，产物始终只有一个 get_fundamentals。
_QS_FIDELITY_EPS_BASIS = '{eps_basis}'
_QS_FIDELITY_EPS_FIELD_MAP = {{'passthrough': {{}}, 'basic': {{'eps': 'basic_eps'}},
                               'diluted': {{'eps': 'diluted_eps'}}}}
_QS_FIDELITY_EPS_FIELD_MAP_REV = {{
    plat: loc for _basis, _m in _QS_FIDELITY_EPS_FIELD_MAP.items() for loc, plat in _m.items()}}
if _QS_FIDELITY_EPS_BASIS not in ('passthrough', 'basic', 'diluted'):
    raise RuntimeError('_QS_FIDELITY_EPS_BASIS=%r 非法（允许 passthrough/basic/diluted）'
                       % (_QS_FIDELITY_EPS_BASIS,))


class _QSFundState:
    orig = None


_QSFundState.orig = get_fundamentals


# 本地→平台 字段名映射（P-D10 探针二/三结论：growth_ability 表平台自有命名。
# or_yoy → operating_revenue_grow_rate 数值对照实证：000001.SZ/600000.SS @2026-03-31
# 平台值 == 本地 or_yoy（4.6516 / 1.4176，Δ=0.0000，同百分点单位同符号）。
# np_yoy 不映射（600000 Δ=0.72pct 口径差，无策略消费）；映射只翻译列名，不代理本地契约。
_QS_GF_FIELD_MAP = {{'or_yoy': 'operating_revenue_grow_rate'}}
_QS_GF_FIELD_MAP_REV = {{v: k for k, v in _QS_GF_FIELD_MAP.items()}}


def _qs_norm_report_date(value):
    """'YYYY-MM-DD' / YYYYMMDD → epoch 毫秒（end_date 契约 v8.7）或 YYYYMMDD float（publ_date）。
    本地策略以 np.datetime64(int(end_date),'ms') 消费（F-Score L86/92）与
    pd.to_datetime(..., unit='ms', utc=True)（CANSLIM L391）——end_date 必须 epoch 毫秒，
    数值 YYYYMMDD（20250331）会被当作 1970+20250331ms→1970-01-01 垃圾日期 → prev
    同月日匹配崩 → 同比项恒加分 → fscore 虚高（v8.6 实跑 166 vs 探针复算 79 的残留根因）。"""
    try:
        import datetime as _qs_dt
        text = str(value).strip()
        if not text or text.lower() in ('nan', 'none', 'nat'):
            return None
        if len(text) == 10 and text[4] == '-' and text[7] == '-':
            _t = text[:4] + text[5:7] + text[8:10]
        else:
            _t = text
        if _t.isdigit() and len(_t) == 8:
            _d = _qs_dt.datetime(int(_t[:4]), int(_t[4:6]), int(_t[6:8]))
            return _d.replace(tzinfo=_qs_dt.timezone.utc).timestamp() * 1000.0
        if _t.isdigit():
            # 已是 epoch 毫秒（10+ 位）或本地原值 → 原样
            return float(_t)
        return None
    except Exception:
        return value


def _qs_norm_publ_date(value):
    """publ_date → YYYYMMDD float（PIT 过滤比较用，策略不消费）。"""
    try:
        text = str(value).strip()
        if len(text) == 10 and text[4] == '-' and text[7] == '-':
            return float(text[:4] + text[5:7] + text[8:10])
        if len(text) == 8 and text.isdigit():
            return float(text)
    except Exception:
        pass
    return value


def _qs_norm_fund_dates(df):
    """日期列归一（v8.7 契约：end_date → epoch 毫秒；publ_date → YYYYMMDD float）。"""
    try:
        if 'end_date' in df.columns:
            df['end_date'] = [_qs_norm_report_date(x) for x in df['end_date']]
    except Exception:
        pass
    try:
        if 'publ_date' in df.columns:
            df['publ_date'] = [_qs_norm_publ_date(x) for x in df['publ_date']]
    except Exception:
        pass
    return df


def _qs_fund_select_fields(df, fields, table=None):
    """按请求 fields 列筛选（本地 ptrade_api.py:763-768 available 同构）。
    请求字段不在返回列集 → QS_SHIM_FIELD_MISSING 一次性显性警报 + 登记 (table,field) gap
    （B8：平台缺列确认后短路，不再逐次告警/平台调用刷屏——v8 平台 total_share 60+ 次/日
    = 每次回退平台调用）；全部缺失 → 1 行 NaN 契约 DataFrame（P-D10 v1.2，⑦ 降级 RD-1）。"""
    if not fields:
        return df
    field_list = [fields] if isinstance(fields, str) else list(fields)
    available = [f for f in field_list if f in df.columns]
    missing = [f for f in field_list if f not in df.columns]
    _gaps = _qs_gf_known_gaps()
    for _f in missing:
        if table and (table, _f) in _gaps:
            continue  # 已登记缺口 → 不再告警
        log.warning('QS_SHIM_FIELD_MISSING get_fundamentals table=%s field=%s '
                    '(requested but absent in platform return; contract gap)'
                    % (table or '?', _f))
        if table:
            _qs_gf_mark_gap(table, _f)
    if available:
        _out = df[available]
        if missing:
            # P-D10 v1.2：部分缺列 → 保留 available 值 + 缺失列补 NaN（策略 KeyError 免疫，
            # NaN 数学比较恒 False → ⑦ 类判定自然降级；v7 _qs_fund_fields_merge 同款语义）
            _out = _out.copy()
            for _f in missing:
                _out[_f] = float('nan')
        return _out
    # 全部字段缺失 → 平台吞错返回空 df（growth_ability 无 or_yoy 实证）：
    # P-D10 v1.2 缺列降级语义（2026-08-31，B8/B2-⑦）：返回 1 行 NaN 契约 DataFrame，
    # 而非空 columns-frame——策略"无数据加分"分支（如 ⑦ ts_now is None）不再误加分，
    # NaN 比较恒 False → 平台降级不判（RD-1）；列契约保留，策略 KeyError 免疫；
    # 完整性过滤仍自然剔除（NaN 参与数学比较恒 False）。显性警报先行（下方循环）。
    _nan = _qs_pd.DataFrame([[float('nan')] * len(field_list)], columns=field_list)
    return _nan


def _qs_frame_to_contract(df, secs, fields, table):
    """平台原始返回 → 本地契约：index=code 防御 + 字段名逆翻译 + 列筛选 + 日期数值归一 + 空行为。"""
    if df is None or not hasattr(df, 'columns') or not hasattr(df, 'index'):
        return _qs_fund_select_fields(_qs_pd.DataFrame(), fields, table)
    # 字段名逆翻译（平台列名 → 本地列名；growth_ability 表 + 映射命中才 rename）
    if table == 'growth_ability':
        try:
            df = df.rename(columns=_QS_GF_FIELD_MAP_REV)
        except Exception:
            pass
    # P-A2 返回逆翻译（v8.3 整合进统一 wrapper）：平台 basic_eps/diluted_eps 列 →
    # 本地 eps/diluted_eps 名（策略无感知）；常量由 _QS_FIDELITY_EPS_EXT 提供。
    if table == 'eps' and _QS_FIDELITY_EPS_BASIS != 'passthrough':
        try:
            df = df.rename(columns=_QS_FIDELITY_EPS_FIELD_MAP_REV)
        except Exception:
            pass
    if not len(df):
        return _qs_fund_select_fields(df, fields, table)
    _idx = df.index
    is_code_index = False
    try:
        _s = str(_idx[0])
        is_code_index = bool(_s) and ('.' in _s or (_s.isdigit() and len(_s) >= 5))
    except Exception:
        is_code_index = False
    if not is_code_index:
        # 防御路径：平台返回 RangeIndex 等非代码 index → 用 code 列/行序重建（探针实证平台返回 code index）
        if 'code' in df.columns:
            df = df.set_index(df['code'].astype(str))
            df = df.drop(columns=['code'])
        elif len(df) == len(secs) and df.index.equals(_qs_pd.RangeIndex(len(df))):
            df = df.copy()
            df.index = [str(c) for c in secs]
    df = _qs_norm_fund_dates(df)
    return _qs_fund_select_fields(df, fields, table)


def get_fundamentals(security, table='valuation', fields=None, date=None,
                     is_dataframe=True, start_year=None, end_year=None,
                     report_types=None, *args, **kwargs):
    """本地契约包装：list 原生单调用 → DataFrame(index=code, columns=fields)。

    返回结构与本地 get_fundamentals（ptrade_api.py:698-772 / 1421-1442）一致：
    index=ptrade_code、columns=fields（筛选后）、end_date/publ_date 数值归一、
    空 → 1 行 NaN 契约 DataFrame(columns=fields) 不抛错（P-D10 v1.2，缺列降级）。
    防御路径：list 调用失败 → 逐码循环退化。
    本地→平台字段名映射：请求 fields 按 _QS_GF_FIELD_MAP 翻译（or_yoy→operating_revenue_grow_rate），
    返回列名逆翻译回本地名（_qs_frame_to_contract 内 rename）；策略按本地列名消费。

    B6 平台契约（2026-08-28，文档 L2335）：date 与 start_year/end_year 互斥。
    date+range 并存（本地 provider 支持）→ 平台 date-only 双查询拆分（date / date-1年），
    concat 返回多期；探针 P1（2026-08-31）：平台原生 start_year 多期返回
    index=multi2(end_date, secu_code) 击穿本地 index=code 契约 → 并存绝不透传 start_year。
    仅 start_year/end_year（无 date）→ 原样透传（平台原生多期，P1 实证 12 行）。
    B9/B10（2026-08-31）：平台无 get_fundamentals_batch → 批量 = list 模式
    （P-D10 实证 500 码 0.05s）；_qs_gf_maybe_prefetch 在 g.universe 全池就绪后
    自动触发当月两期批量预取（破逐股流控卡死），失败静默回退逐股。"""
    _secs = security if isinstance(security, (list, tuple)) else [security]
    _field_list = [fields] if isinstance(fields, str) else (list(fields) if fields else None)
    # P-A2 eps 口径（2026-08-24，v8.3 整合）：eps 表且 basis 命中 → eps→basic_eps/diluted_eps
    # 请求翻译；其余走 _QS_GF_FIELD_MAP。常量由 _QS_FIDELITY_EPS_EXT（注入在后，仅常量）
    # 提供，函数体运行时解析（调用时不要求定义顺序）。
    if _field_list is not None and table == 'eps' and _QS_FIDELITY_EPS_BASIS != 'passthrough':
        _m = _QS_FIDELITY_EPS_FIELD_MAP.get(_QS_FIDELITY_EPS_BASIS, {{}})
        _plat_fields = [_m.get(f, f) for f in _field_list]
    else:
        _plat_fields = ([_QS_GF_FIELD_MAP.get(f, f) for f in _field_list]
                        if _field_list is not None else None)
    # B6c 分派（2026-08-31 18:20，v8.1 平台复验二轮修正）：date 与 start_year/end_year 并存。
    # 主路径 = 平台原生 range 多期透传（探针 P1 实证 start_year/end_year → 12 期季报齐全、
    # index=multi2(end_date,secu_code)、含 publ_date 列、PIT 可复现 9/12）→ multi2 拍平 →
    # publ_date<=date PIT 过滤（对齐本地『ann_date<=date 最新已披露』）→ 本地 _latest_statement
    # 自取 cur(最新期)+prev(年-1 同月日) 两行。
    # 否决 date-only 双查询（v8/v8.1 实证）：平台 date 查询为披露时点语义——date=2025-03-31
    # 时 2025 一季报未披露 → 平台返回 2024-12-31，恒拿不到『cur 期年-1 同月日』期 →
    # prev 恒 None → 同比项恒加分 → fscore_pass=166 稳定虚高（vs 本地 97）。
    # multi2 由 _qs_multi_flat 拍平（审计 B-1 裁定击穿本地 index=code 契约 → wrapper 层修复，
    # 不透传多级 index 给策略）。
    if (date is not None) and (start_year is not None or end_year is not None):
        try:
            _qs_gf_progress(_secs, table, 'range:%s' % date)
            # v8.8 B6c range 缓存读：命中免平台调用；miss 触发全池分组预取
            _qs_gf_maybe_prefetch_range(_secs, table, fields, date, start_year, end_year)
            _ckey = _qs_gf_range_cache_key(table, fields)
            _cdf = None
            if _secs is not None and (not isinstance(_secs, (list, tuple)) or len(_secs) == 1):
                # v8.8：策略有多种传参形态（单码 str 或单元素 list）→ 单码一律走缓存
                _hit_code = _secs[0] if isinstance(_secs, (list, tuple)) else _secs
                _cdf = _qs_gf_range_cache_hit(_hit_code, table, fields)
            if _cdf is not None and report_types is None:
                # v8.8 缓存命中：预取 df 已是 contract 后多期形态（与逐码路径同构）
                _qs_shape_check('get_fundamentals', 'dataframe', _cdf)
                return _cdf
            _raw = _QSFundState.orig(_secs, table, fields=_plat_fields,
                                     start_year=start_year, end_year=end_year,
                                     is_dataframe=is_dataframe, *args, **kwargs)
            _raw = _qs_multi_flat(_raw)
            # v8.5 PIT 位置修复（2026-08-31 五次复验实证 fscore_pass=4）：PIT 过滤必须在
            # _qs_frame_to_contract（字段筛选）之前——策略请求 fields 不含 publ_date，
            # 放后面则 publ_date 已被丢弃 → 过滤恒不生效 → 平台 range 返回的未披露期
            # （如 2026-06-30 中报，8 月底才披露）未被剔除 → cur 取错期（本地 2026-03-31
            # vs 平台 2026-06-30）→ 数值/阈值判定全偏（fscore_pass 166→4）。
            _raw = _qs_pit_filter(_raw, date)
            _df = _qs_frame_to_contract(_raw, _secs, fields, table)
            if report_types is not None:
                _df = _qs_filter_report_types(_df, report_types)
            if len(_df):
                _qs_shape_check('get_fundamentals', 'dataframe', _df)
                # v8.8：QS_GF_META 审计已退役（边界复核定论，噪音移除）
                return _df
        except Exception as _exc:
            log.warning('GF-RANGE-FAILOPEN get_fundamentals date+range %s: %s'
                        % (type(_exc).__name__, _exc))
        # 回退（B6b 保底）：date-only 双查询（cur + curED 年-1同月日窗口），平台披露时点
        # 语义下命中率有限，仅防主路径异常时给出两期近似。
        try:
            _cur_raw = _QSFundState.orig(_secs, table, fields=_plat_fields, date=date,
                                         is_dataframe=is_dataframe, *args, **kwargs)
            _cur = _qs_frame_to_contract(_cur_raw, _secs, fields, table)
            _prev_date = _qs_prev_window_date(_cur, date)
            _prev_raw = _QSFundState.orig(_secs, table, fields=_plat_fields, date=_prev_date,
                                          is_dataframe=is_dataframe, *args, **kwargs)
            _prev = _qs_frame_to_contract(_prev_raw, _secs, fields, table)
            _parts = [_f for _f in (_cur, _prev) if _f is not None and len(_f)]
            if not _parts:
                _df = _qs_fund_select_fields(_qs_pd.DataFrame(), fields, table)
            else:
                _df = _qs_pd.concat(_parts)
                if report_types is not None:
                    _df = _qs_filter_report_types(_df, report_types)
            if len(_df):
                _qs_shape_check('get_fundamentals', 'dataframe', _df)
                return _df
        except Exception as _exc:
            log.warning('GF-RANGE-FAILOPEN2 get_fundamentals date+range %s: %s'
                        % (type(_exc).__name__, _exc))
    # B8 gap 短路（2026-08-31 v8 平台复验修正）：请求字段全部已确认缺列（g 缓存）→
    # 直接返回 1 行 NaN 契约（免平台调用 + 免告警刷屏——v8 实证 total_share 60+ 次/日）。
    try:
        if _field_list and set(_field_list) <= _qs_gf_gap_shortcut(table, _field_list):
            _df = _qs_pd.DataFrame([[float('nan')] * len(_field_list)], columns=_field_list)
            _qs_shape_check('get_fundamentals', 'dataframe', _df)
            return _df
    except Exception:
        pass
    # B9/B10：平台自动批量预取（纯增益；无池/失败静默回退逐股）
    try:
        _qs_gf_maybe_prefetch(_secs, table, _field_list, date)
    except Exception:
        pass
    # 预取缓存命中（单码）→ 免平台调用（破流控）；未命中回退下方平台调用
    if len(_secs) == 1:
        try:
            _cached = _qs_gf_auto_cache_get(_secs[0], table, date, _field_list)
            if _cached is not None:
                _qs_shape_check('get_fundamentals', 'dataframe', _cached)
                return _cached
        except Exception:
            pass
    try:
        _qs_gf_progress(_secs, table, date)
        _df = _QSFundState.orig(_secs, table, fields=_plat_fields, date=date,
                                is_dataframe=is_dataframe, *args, **kwargs)
        _df = _qs_frame_to_contract(_df, _secs, fields, table)
    except Exception as exc:
        log.warning('GF-FAILOPEN get_fundamentals list-call %s: %s' % (type(exc).__name__, exc))
        _frames = []
        for _code in _secs:
            try:
                _one = _QSFundState.orig(_code, table, fields=_plat_fields, date=date,
                                         is_dataframe=is_dataframe, *args, **kwargs)
                if _one is None or not hasattr(_one, 'columns'):
                    continue
                _one = _qs_frame_to_contract(_one, [_code], fields, table)
                if len(_one) > 0:
                    _frames.append(_one)
            except Exception as exc2:
                log.warning('GF-FAILOPEN get_fundamentals %s %s: %s' % (_code, type(exc2).__name__, exc2))
        if not _frames:
            _df = _qs_pd.DataFrame(columns=[fields] if isinstance(fields, str)
                                   else (fields or []))
        else:
            _df = _qs_pd.concat(_frames)
    _qs_shape_check('get_fundamentals', 'dataframe', _df)
    return _df


def _qs_gf_progress(secs, table, date):
    """v8.7b 可观测性：wrapper 平台原语调用进度日志（定位 v8.7 平台卡死点，
    2026-08-31）——每表每月首 3 次 + 每 50 次打一行 QS_GF_CALL（table/date/序号）。
    卡住时最后一条日志即平台挂起的死调用。"""
    try:
        _g = _qs_g_obj()
        _n = 0
        if _g is not None:
            _c = getattr(_g, '_qs_gf_call_n', {{}})
            _k = '%s|%s' % (table, str(date)[:7])
            _n = _c.get(_k, 0) + 1
            _c[_k] = _n
            _g._qs_gf_call_n = _c
        if _n <= 1 or _n % 200 == 0:
            log.info('QS_GF_CALL n=%d table=%s date=%s secs=%d'
                     % (_n, table, str(date)[:10], len(secs)))
    except Exception:
        pass


def _qs_prev_window_date(cur_df, date):
    """B6b 回退路径平台同比窗口日期：cur 最新 end_date（epoch 毫秒契约，v8.7）还原
    'YYYY-MM-DD' 后反推（cur 年-1 + 同月日）。注：平台 date 查询为披露时点语义，
    该窗口常取不到期（主路径已改 range 多期 + PIT）。"""
    try:
        if cur_df is not None and len(cur_df) and 'end_date' in cur_df.columns:
            _ser = cur_df['end_date'].dropna()
            if len(_ser):
                _max = float(max(float(x) for x in _ser))
                _s = str(_qs_np.datetime64(int(_max), 'ms'))[:10]
                return str(int(_s[:4]) - 1) + _s[4:]
    except Exception:
        pass
    return str(int(str(date)[:4]) - 1) + str(date)[4:]

def _qs_multi_flat(df):
    """平台 range 多期返回 index=multi2(end_date, secu_code) → 普通行拍平：
    按 index 重建 end_date / code 列后 reset（探针 P1 实证，审计 B-1 裁定击穿本地
    index=code 契约 → wrapper 层修复，不透传多级 index）。非 MultiIndex 原样返回。"""
    if df is None or not hasattr(df, 'index'):
        return df
    try:
        if df.index.nlevels < 2:
            return df
    except Exception:
        return df
    try:
        _idx = list(df.index)
        _out = df.copy()
        _out['end_date'] = [str(_x[0]) for _x in _idx]
        if 'code' not in _out.columns and 'secu_code' not in _out.columns:
            _out['code'] = [(_x[1] if len(_x) > 1 else None) for _x in _idx]
        _out = _out.reset_index(drop=True)
        return _out
    except Exception:
        return df


def _qs_pit_filter(df, date):
    """平台 range 全期行过滤（对齐本地『ann_date<=date 最新已披露』）。PIT 判据
    （v8.6，2026-08-31 P5-7 实证）：
      1) 值域兜底：非 end_date/publ_date 数值列全 NaN（未披露占位，如 2026-06-30 中报
         range 返回 NaN 行）→ 剔除（不问 publ_date——平台 list+range 模式 publ_date
         全空，P5-1 empty=18 实证，仅靠 publ_date 无法剔占位期）
      2) publ_date 有值且 > date → 剔除；缺失/空串 → 不据此剔除
    关键：空串绝不能 astype(float)（P5-1 ValueError）→ 逐行安全比对。原始 BUG 链路：
    空串 ValueError → wrapper FAILOPEN/整表放行 → NaN 占位期被 _latest_statement 取为
    cur → fscore 实跑 3（P5-7 平台复算 79 的差异源，RD-4）。"""
    if df is None or not len(df) or 'publ_date' not in df.columns:
        return df
    try:
        import re as _qs_re
        _dn = _qs_re.sub(r'\\D', '', str(date))[:8]
        if not _dn.isdigit() or len(_dn) != 8:
            return df
        _dn = float(_dn)
        _val_cols = [c for c in df.columns if c not in ("end_date", "publ_date", "code", "secu_code")]
        keep = []
        for _i in range(len(df)):
            _row = df.iloc[_i]
            # 1) 值域兜底：数值列全 NaN → 未披露占位剔除
            _all_nan = True
            for _c in _val_cols:
                _v = _row.get(_c)
                try:
                    _vs = str(_v).strip() if _v is not None else ""
                    if _vs and _vs.lower() not in ("nan", "none", "nat"):
                        _all_nan = False
                        break
                except Exception:
                    _all_nan = False
                    break
            if _all_nan:
                keep.append(False)
                continue
            # 2) publ_date 判据
            _s = str(_row.get('publ_date') or "").strip()
            if not _s or _s.lower() in ("nan", "none", "nat"):
                keep.append(True)          # 空/缺失 → 不据此剔除（平台 list+range 全空）
                continue
            _num = _qs_re.sub(r'\\D', '', _s)
            if _num.isdigit() and len(_num) >= 8:
                keep.append(float(_num[:8]) <= _dn)
            else:
                keep.append(True)
        return df[[bool(b) for b in keep]]
    except Exception:
        return df

# B8 缺列种子（探针 P2 实证，2026-08-31）：balance/income/valuation × 8 净资产/股本字段
# 平台全 EMPTY（KeyError not in index）→ 首调即短路（免平台调用免告警刷屏，v8/v8.1 实证
# total_share 60+ 次/日）。运行时动态 gap（_qs_gf_mark_gap）追加合并。
_QS_GF_GAP_SEEDS = set()
for _qs_gap_t in ('balance', 'income', 'valuation'):
    for _qs_gap_f in ('total_equity', 'total_hldr_eqy', 'total_hldr_eqy_excl_min_int',
                      'total_hldr_eqy_inc_min_int', 'total_share', 'total_shares',
                      'capital_reserve', 'share_capital'):
        _QS_GF_GAP_SEEDS.add((_qs_gap_t, _qs_gap_f))


def _qs_gf_known_gaps():
    """已知缺列集 = 探针实证种子 ∪ 运行时登记（g._qs_gf_field_gaps）；无 g/未登记 → 种子集。"""
    _gaps = set(_QS_GF_GAP_SEEDS)
    _g = _qs_g_obj()
    if _g is not None:
        _gaps.update(getattr(_g, '_qs_gf_field_gaps', None) or set())
    return _gaps


def _qs_gf_mark_gap(table, field):
    """登记缺列（一次性告警 + 短路依据；B8 平台缺列确认后不再逐次平台调用/刷屏）。"""
    _g = _qs_g_obj()
    if _g is None or not table or not field:
        return
    _gaps = getattr(_g, '_qs_gf_field_gaps', None) or set()
    _gaps.add((table, field))
    _g._qs_gf_field_gaps = _gaps


def _qs_gf_gap_shortcut(table, field_list):
    """已确认缺列命中集（约束 wrapper：命中全部请求字段 → 直接 NaN 行短路）。
    注：模板经 .format 渲染，禁止 set 推导式字面量（花括号括 f 遍历式会被当作占位符）。"""
    _gaps = _qs_gf_known_gaps()
    if not _gaps or not field_list:
        return set()
    _hit = set()
    for _f in field_list:
        if (table, _f) in _gaps:
            _hit.add(_f)
    return _hit


_QS_REPORT_TYPE_MD = {{1: '0331', 2: '0630', 3: '0930', 4: '1231'}}


def _qs_filter_report_types(df, report_types):
    """B-1 report_types 多期过滤（2026-08-31 探针 P1：平台 report_types 为 start_year 模式
    参数，date 模式不可传）→ 双查询返回后按 end_date 月日过滤（1/2/3/4 → 0331/0630/0930/1231）。
    v8.7：end_date 为 epoch 毫秒契约——经 np.datetime64 还原 'MMDD'（YYYYMMDD 数值切片 [4:8]
    对 ms epoch 失效，2026-08-31 v8.6 复验后修复）。"""
    try:
        _md = _QS_REPORT_TYPE_MD.get(int(report_types))
        if _md and df is not None and len(df) and 'end_date' in df.columns:
            def _mm(_v):
                try:
                    _t = str(_qs_np.datetime64(int(float(_v)), 'ms'))
                    return _t[5:7] + _t[8:10]
                except Exception:
                    return ''
            _ser = df['end_date'].map(_mm)
            return df[_ser == _md]
    except Exception:
        pass
    return df


def _qs_gf_pit_filter(df, date):
    """B-1 publ_date PIT 过滤（2026-08-31 探针 P1：publ_date≤date 过滤 12 行→9 行可复现）：
    返回 publ_date 数值 ≤ date 数值 的行。本地 provider / 平台 date 模式均已 PIT，
    本 helper 供多期查询/审计/未来显式语义使用。v8.7：日期归一用 _qs_norm_publ_date
    （YYYYMMDD 比较语义）——_qs_norm_report_date 现为 end_date 契约（epoch 毫秒），
    混用会把 publ_date（YYYYMMDD）误判（2026-08-31）。"""
    if df is None or not len(df) or date is None or 'publ_date' not in df.columns:
        return df
    try:
        _d = float(_qs_norm_publ_date(str(date)))
        _ok = df['publ_date'].map(
            lambda _x: (float(_qs_norm_publ_date(_x)) if _x == _x else _d) <= _d)
        return df[_ok]
    except Exception:
        return df


# v8.8（2026-08-31 批复）：大池分组预取 + B6c range 缓存——噪音/慢双修。
# 平台单码 range（fscore 249 只 × 3 表 + ROE）≈1000 次往返 ≈15 分钟；
# 3 码/组 list（P5-7 实证安全）→ ~332 次 ≈3 倍提速；行为等价（缓存同源同值）。
def _qs_gf_range_cache_key(table, flds):
    """B6c range 缓存 key 规范化：预取与命中必须同构（end_date/publ_date 恒入键，
    否则 预取 key(含日期列) ≠ 命中 key(仅请求列) → 恒 miss）。v8.8。"""
    _n = ["end_date", "publ_date"] + [f for f in (flds or [])
                                      if f not in ("end_date", "publ_date")]
    return "%s|%s" % (table, ",".join(sorted(_n)))


def _qs_gf_range_cache_hit(code, table, flds):
    """B6c range 预取缓存读：命中返回 contract 后多期 df（同单码 B6c 产物），
    miss/无缓存 → None（调用方回退平台调用）。v8.8。"""
    try:
        _g = _qs_g_obj()
        _c = getattr(_g, "_qs_gf_range_cache", None)
        if not _c:
            return None
        return _c.get(_qs_gf_range_cache_key(table, flds), {{}}).get(str(code))
    except Exception:
        return None


def _qs_gf_maybe_prefetch_range(secs, table, field_list, date, start_year, end_year):
    """B6c range 全池分组预取（v8.8，替代 >32 整体 SKIP）：
    首个 range 调用触发该表全池 3 码/组 list range 预取 → _qs_gf_range_cache；
    后续同表同字段单码 B6c 命中缓存免平台调用。幂等按 (table,月,字段族)；
    失败逐组 continue + 整表标 done（fail-open 回退逐股，防再挂）。
    调用形态与探针 P5-7 分组 3 码实证一致（83 组跑通）。"""
    _g = _qs_g_obj()
    _pool = None
    try:
        _pool = getattr(_g, "universe", None) or getattr(_g, "candidates", None)
    except Exception:
        _pool = None
    if not _pool or len(_pool) <= 1:
        return
    _mkey = str(date)[:7]
    _k = "%s|%s|%s" % (table, _mkey, "|".join(sorted(field_list or [])))
    _done = dict(getattr(_g, "_qs_gf_range_prefetched", {{}}))
    if _done.get(_k):
        _g._qs_gf_range_prefetched = _done
        return
    _flds = ["end_date", "publ_date"] + [f for f in (field_list or []) if f not in ("end_date", "publ_date")]
    _plat = _plat_fields_of(_flds)
    _cache = getattr(_g, "_qs_gf_range_cache", None)
    if _cache is None:
        _cache = {{}}
        _g._qs_gf_range_cache = _cache
    _ckey = _qs_gf_range_cache_key(table, _flds)
    _by_key = _cache.setdefault(_ckey, {{}})
    import numpy as _qs_np8
    for _i in range(0, len(_pool), 3):
        _grp = list(_pool[_i:_i + 3])
        try:
            _raw = _QSFundState.orig(_grp, table, fields=_plat,
                                    start_year=start_year, end_year=end_year,
                                    is_dataframe=True)
        except Exception:
            continue
        if _raw is None or not len(_raw):
            continue
        try:
            _dfp = _qs_multi_flat(_raw)
            _dfp = _qs_pit_filter(_dfp, date)
            _dfp = _qs_frame_to_contract(_dfp, _grp, _flds, table)
            for _c2 in _grp:
                _sub = _dfp[_dfp.index == _c2] if len(_dfp) else None
                if _sub is not None and len(_sub):
                    _by_key[str(_c2)] = _sub.copy()
        except Exception:
            continue
    _done[_k] = 1
    _g._qs_gf_range_prefetched = _done
    log.info("QS_GF_PREFETCH_RANGE date=%s table=%s pool=%d groups=%d" % (
        str(date)[:10], table, len(_pool), (len(_pool) + 2) // 3))


def _qs_gf_maybe_prefetch(secs, table, field_list, date):
    """B9/B10 自动批量预取（2026-08-31，平台无 get_fundamentals_batch → list 模式，
    P-D10 实证 500 码 0.05s FULL；破逐股 1488 次/日流控卡死）：
    策略在 g.universe/g.candidates 设全池后，首个单码/小批 get_fundamentals 触发
    该表当月两期（date / date-1年）全池 list 预取 → g._qs_gf_cache；后续单码命中
    _qs_gf_auto_cache_get 免平台调用。纯增益：无池/失败静默回退逐股，不改变契约返回。
    缓存按月幂等（g._qs_gf_prefetched[table] == 月 key）。"""
    _g = _qs_g_obj()
    _pool = None
    try:
        _pool = getattr(_g, 'universe', None) or getattr(_g, 'candidates', None)
    except Exception:
        _pool = None
    if not _pool or date is None:
        return
    _dkey = str(date)[:10]
    _mkey = _dkey[:7]
    _done = getattr(_g, '_qs_gf_prefetched', {{}})
    if _done.get(table) == _mkey:
        return
    if isinstance(secs, (list, tuple)) and len(secs) > 32:
        return  # 已是批量调用 → 无需预取
    # v8.4 平台卡死修复（2026-08-31 四次复验）：大池禁止批量 list 预取——fscore
    # universe=300 时首个 ROE 单码触发 profit_ability 全池 list（2 期×300 码），
    # 平台该表 list 批量未获探针实证（P-D10 仅 income/valuation 500 码 0.05s）、
    # 实测挂起（日志停在 QS_GF_PREFETCH 后）。>32 码跳过批量预取 → 回退逐股
    # （v7/v8.2 覆盖版同路径可跑完 07-01）；池 ≤32（小池策略）保留批量预取。
    # SKIP 按月幂等（_done[table]=_mkey），避免后续单码每次重复打 SKIP 日志。
    if len(_pool) > 32:
        log.info('QS_GF_PREFETCH_SKIP date=%s table=%s pool=%d（>32 不批量预取，逐股回退）'
                 % (_dkey, table, len(_pool)))
        _done[table] = _mkey
        _g._qs_gf_prefetched = _done
        return
    _mkey = _dkey[:7]
    _done = getattr(_g, '_qs_gf_prefetched', {{}})
    if _done.get(table) == _mkey:
        return
    _cache = getattr(_g, '_qs_gf_cache', None)
    if _cache is None:
        _cache = {{}}
        _g._qs_gf_cache = _cache
    _prev_date = str(int(_dkey[:4]) - 1) + _dkey[4:]
    _flds = ['end_date'] + [f for f in (field_list or []) if f != 'end_date']
    for _dk in (_dkey, _prev_date):
        try:
            _raw = _QSFundState.orig(_pool, table, fields=_plat_fields_of(_flds),
                                     date=_dk, is_dataframe=True)
            _df = _qs_frame_to_contract(_raw, _pool, _flds, table)
        except Exception as _e:
            log.warning('QS_GF_PREFETCH table=%s date=%s error=%s（回退逐股）'
                        % (table, _dk, repr(_e)[:60]))
            continue
        if _df is None or not len(_df):
            continue
        _by_code = _cache.setdefault(table, {{}})
        for _code in _pool:
            try:
                _r = _df.loc[_code] if _code in _df.index else None
                if _r is None:
                    continue
                if getattr(_r, 'ndim', 1) > 1:
                    _r = _r.sort_values('end_date').iloc[-1]
                _by_code.setdefault(str(_code), {{}})[_dk] = _r
            except Exception:
                continue
    _done[table] = _mkey
    _g._qs_gf_prefetched = _done
    log.info('QS_GF_PREFETCH date=%s table=%s pool=%d' % (_dkey, table, len(_pool)))


def _qs_gf_auto_cache_get(code, table, date, field_list):
    """单码预取缓存读（B9）：date 视角最近一期单行 → 本地契约 df(index=[code], cols=fields)。
    未命中/无缓存 → None（调用方回退平台调用）。"""
    _g = _qs_g_obj()
    _cache = getattr(_g, '_qs_gf_cache', None)
    if not _cache:
        return None
    _by_code = _cache.get(table)
    if not _by_code:
        return None
    _rows = _by_code.get(str(code))
    if not _rows:
        return None
    _dk = str(date)[:10]
    _cand = [(_k, _sr) for (_k, _sr) in _rows.items() if _k and str(_k)[:10] == _dk]
    if not _cand:
        return None
    _cand.sort(key=lambda _p: float(_p[1].get('end_date') or 0))
    _sr = _cand[-1][1]
    _flds = list(field_list) if field_list else [c for c in _sr.index if c != 'end_date']
    _out = _qs_pd.DataFrame([[float(_sr.get(f)) for f in _flds]], columns=_flds)
    _out.index = [code]
    return _out


def _plat_fields_of(flds):
    """请求字段集 → 平台字段集（_QS_GF_FIELD_MAP 翻译；batch/list 预取同构）。"""
    return [_QS_GF_FIELD_MAP.get(f, f) for f in (flds or [])]


def _qs_equity_probe(code, date):
    """B-2 净资产/总股本可用性一次性探测（2026-08-31 探针 P2：字段族全缺失）：
    g 缓存一次判定；⑦ 平台恒降级由 NaN 缺列自动达成（RD-1），本函数供审计/策略显式用。"""
    _g = _qs_g_obj()
    if getattr(_g, '_qs_equity_checked', False):
        return bool(getattr(_g, '_qs_equity_usable', False))
    if _g is not None:
        _g._qs_equity_checked = True
    _usable = False
    try:
        _eqt = _QSFundState.orig([code], 'balance_statement', fields=['total_equity'],
                                 date=date, is_dataframe=True)
        _clean = _qs_frame_to_contract(_eqt, [code], ['total_equity'], 'balance_statement')
        if _clean is not None and len(_clean) and 'total_equity' in _clean.columns:
            _usable = bool(_clean['total_equity'].notna().any())
    except Exception:
        _usable = False
    if _g is not None:
        _g._qs_equity_usable = _usable
    log.info('QS_EQUITY_PROBE date=%s equity_usable=%s' % (date, _usable))
    return _usable
'''

# ===== B1/B7 get_industry 平台替代（2026-08-31，双端行业剔除对齐） =====
# 平台探针 P4（probe v2.1b）：get_industry 不可用（LOCAL_ONLY）、
# get_industry_stocks('480000.XBHS') 银行 42 只有效（480000 裸码/801780 全形态无效）→
# 反向金融池双码方案（v7 产物实证 finance_pool=121 = 银行42+非银79）。
# 行业码集 _QS_INDUSTRY_CODES 转换期从策略源码烘焙（6 位数字引号字面量，如
# ('801780','801790','480000','490000')）——框架层通用，任意剔除行业语义自动生效。
# 语义差异登记 RD-3：本地 fail-closed（无法确认行业→剔除）vs 平台 fail-open
# （池外/池无效→非金融哨兵 999999→不剔），防平台全剔空仓（B1 初始 300 只根因）。
_QS_INDUSTRY_EXT = '''
{marker}
# [qs-import-generated] get_industry 平台替代包装（B1/B7，2026-08-31）
class _QSIndustryState:
    """平台 get_industry_stocks / get_industry 引用（class 属性承载过平台
    LOCAL-API-WHITELIST；模块级变量持函数引用再调用会被 BLOCK，2026-08-22 实证同款）。"""
    get_industry_stocks_orig = None
    get_industry_orig = None


_QSIndustryState.get_industry_stocks_orig = get_industry_stocks
try:
    _QSIndustryState.get_industry_orig = get_industry
except Exception:
    _QSIndustryState.get_industry_orig = None

# 转换期从策略源码烘焙的行业码集（__QS_INDUSTRY_CODES__ 占位，渲染后替换）
_QS_INDUSTRY_CODES = __QS_INDUSTRY_CODES__


def _qs_finance_pool():
    """懒构建反向金融池（行业码双码尝试：裸/.XBHS/.XBKS；命中即停）；g 缓存一次。
    返回 (pool, valid)。valid=False → 下游回退原生 get_industry + fail-open。"""
    _g = _qs_g_obj()
    _pool = getattr(_g, '_qs_finance_pool', None)
    if _pool is not None:
        return _pool, bool(getattr(_g, '_qs_finance_pool_valid', False))
    _pool = []
    _got = False
    for _ind in _QS_INDUSTRY_CODES:
        for _c in (_ind, _ind + '.XBHS', _ind + '.XBKS'):
            try:
                _members = _QSIndustryState.get_industry_stocks_orig(_c) or []
            except Exception:
                continue
            if _members:
                _got = True
                for _m in _members:
                    if _m not in _pool:
                        _pool.append(_m)
                break
    if _g is not None:
        _g._qs_finance_pool = _pool
        _g._qs_finance_pool_valid = _got
    log.info('QS_INDUSTRY_POOL industries=%s pool_size=%d valid=%s'
             % (','.join(_QS_INDUSTRY_CODES), len(_pool), _got))
    return _pool, _got


def get_industry(code):
    """平台替代：返回本地 get_industry 契约（{{'sw_l1': {{'industry_code': ...}}}}）。
    - 池有效：code ∈ 池 → 首个策略行业码（∈ 策略剔除集 → 剔）；否则 → '999999'
      （非金融哨兵，∉ 策略行业集 → 不剔，fail-open）；
    - 池无效：回退原生 get_industry（如有）；无 → '999999' 不剔 + 一次性告警
      QS_INDUSTRY_UNAVAILABLE（RD-3 降级登记，防本地 fail-closed 全剔空仓）。"""
    _g = _qs_g_obj()
    _pool, _valid = _qs_finance_pool()
    if _valid:
        if str(code) in _pool:
            _hit = _QS_INDUSTRY_CODES[0] if _QS_INDUSTRY_CODES else '999999'
            return {{'sw_l1': {{'industry_code': _hit}}}}
        return {{'sw_l1': {{'industry_code': '999999'}}}}
    if _QSIndustryState.get_industry_orig is not None:
        try:
            _ind = _QSIndustryState.get_industry_orig(code)
            if _ind:
                return _ind
        except Exception:
            pass
    _flag = '_qs_industry_warned'
    if _g is not None and not getattr(_g, _flag, False):
        _g._qs_industry_warned = True
        log.warning('QS_INDUSTRY_UNAVAILABLE get_industry 平台不可用且行业池无效'
                    '（行业剔除降级 fail-open，登记 RD-3）')
    return {{'sw_l1': {{'industry_code': '999999'}}}}
'''

# ===== P-D11 持仓视图归一（2026-08-26，P-POS 平台实证 docs/evidence/pd11-pos-probe-20260826.md） =====
# 平台实证契约事实（F1~F7）：
#   F1 get_positions() 键 = XSHG/XSHE 四位后缀，而 get_Ashares() = .SS/.SZ 两位
#      （同平台双后缀体系并存）→ 策略侧裸串匹配持仓键恒 False（CANSLIM basis=-1.0000、
#      周频三层 tier1 静默、fall_reversal positions 虚报的共同根因，双端对齐分析闭环）；
#   F2 Position.sid = '.SS' 两位形（输出键归一主锚点，恒存在）；
#   F4 残影行：卖出当日 amount=0 行仍在（次日清理）→ amount>0 过滤；
#   F5 get_position('.SH') 平台内部崩溃（NoneType.asset）、裸码返回 cost_basis=None
#      空壳 → 输入必须归一；未持仓返回 amount=0 空仓对象（与本地契约一致）；
#   F3 字段集与本地 ptrade_api.Position 同构（缺 avg_cost → cost_basis 别名；
#      含费口径差 F7 已登记，本注入只对形状不动数值语义）。
# 设计：docs/pd11-position-view-normalization-design.md（v1.2 审定冻结）。
# 归一规则 = 本地权威 security_code_rules.exchange() 五分支序逐字镜像
# （BSE 精确表(920*/legacy 表) → 后缀 → 可转债段 → 5/6/9|0/1/2/3 前缀 → SS 兜底），
# 等价性由 tests/test_pd11_position_view.py T11 差分测试永久钉死；
# _QS_BSE_LEGACY 为转换期烘焙快照（__QS_BSE_SET__/__QS_BSE_N__ 占位，渲染后替换）。
_QS_POSITION_VIEW_EXT = '''
{marker}
# [qs-import-generated] 持仓视图归一注入（P-D11，2026-08-26）
# 输出契约 = 本地 ptrade_api.Position：键 .SS/.SZ/.BJ、无残影行、
# sid/amount/enable_amount/cost_basis/last_sale_price/market_value + avg_cost 别名。
class _QSPositionState:
    """平台原始持仓 API 引用（class 属性承载过平台 LOCAL-API-WHITELIST，
    模块级变量持函数引用再调用会被 BLOCK，2026-08-22 平台实证同款）。"""
    get_positions_orig = None
    get_position_orig = None


_QSPositionState.get_positions_orig = get_positions
_QSPositionState.get_position_orig = get_position

# 北交所存量映射烘焙快照（来源 security_code_rules.BSE_LEGACY_TO_920，
# n=__QS_BSE_N__ 条；官方稳定映射转换期固化；模板自包含——平台无 quantstudio 可导入）。
_QS_BSE_LEGACY = __QS_BSE_SET__


def _qs_norm_code(code):
    """代码归一 → .SS/.SZ/.BJ；本地权威 exchange() 分支序逐字镜像（T11 钉死）：
    ① BSE 精确表（startswith 920 或 ∈ legacy 表）——权威同序，优先于后缀判定
    ② 入参后缀（.SH/.SS/.XSHG→SS；.SZ/.XSHE→SZ；.BJ/.XBJ/.XBSE→BJ）
    ③ 可转债段（110/111/113/118→SS；123/127/128→SZ）
    ④ 前缀（5/6/9→SS；0/1/2/3→SZ；startswith 语义，空串安全）
    ⑤ SS 兜底（权威 unknown 回退=SH）。"""
    s = str(code).strip().upper()
    bare = s.split(".", 1)[0]
    if bare.startswith("920") or bare in _QS_BSE_LEGACY:
        return bare + ".BJ"
    if "." in s:
        suf = s.split(".", 1)[1]
        if suf in ("SH", "SS", "XSHG"):
            return bare + ".SS"
        if suf in ("SZ", "XSHE"):
            return bare + ".SZ"
        if suf in ("BJ", "XBJ", "XBSE"):
            return bare + ".BJ"
        # 未知后缀：剥除走裸码规则
    if bare.startswith(("110", "111", "113", "118")):
        return bare + ".SS"
    if bare.startswith(("123", "127", "128")):
        return bare + ".SZ"
    if bare.startswith(("5", "6", "9")):
        return bare + ".SS"
    if bare.startswith(("0", "1", "2", "3")):
        return bare + ".SZ"
    return bare + ".SS"


def _qs_pos_sid_key(pos, raw_key):
    """输出键归一：主锚 pos.sid（实证 '.SS' 形）；缺失/异常回退 _qs_norm_code(raw_key)。"""
    sid = getattr(pos, "sid", None)
    if sid:
        s = str(sid).upper()
        if s.endswith((".SS", ".SZ", ".BJ")):
            return s
        return _qs_norm_code(s)
    return _qs_norm_code(raw_key)


class _QSPositionView:
    """平台 Position → 本地 ptrade_api.Position 契约视图（透传 + avg_cost 别名）。"""

    def __init__(self, p, key):
        self._p = p
        self._key = key

    def __getattr__(self, name):
        p = object.__getattribute__(self, "_p")
        if name == "sid":
            return object.__getattribute__(self, "_key")
        if name == "avg_cost":
            # 平台无 avg_cost → cost_basis 别名（含费口径差 F7 已登记，不动数值语义）
            return getattr(p, "cost_basis", 0.0)
        if name in ("amount", "enable_amount"):
            return getattr(p, name, 0)
        return getattr(p, name, 0.0)


def get_positions(security=None):
    """键归一 + 残影过滤（amount>0）+ 契约视图；平台返回形态漂移 fail-loud。"""
    raw = _QSPositionState.get_positions_orig()
    if raw is None:
        return {{}}
    try:
        items = list(raw.items())
    except AttributeError:
        raise ValueError("QS_POS_VIEW_VIOLATION get_positions 返回非 dict（%s）"
                         % type(raw).__name__)
    out = {{}}
    for k, p in items:
        try:
            amt = float(getattr(p, "amount", 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt <= 0:
            continue
        key = _qs_pos_sid_key(p, k)
        out[key] = _QSPositionView(p, key)
    if security is not None:
        tgt = _qs_norm_code(security)
        return {{tgt: out[tgt]}} if tgt in out else {{}}
    _qs_shape_check("get_positions", "dict", out)
    return out


def get_position(security):
    """输入归一（防 .SH 平台崩溃/裸码空壳）+ 契约视图；空仓语义 amount=0（F5）。"""
    code = _qs_norm_code(security)
    p = _QSPositionState.get_position_orig(code)
    return _QSPositionView(p, code)
'''

# P-D11 渲染：{marker} format + BSE 烘焙快照替换（占位符在 format 之后 replace，
# 避免 248 条映射字面量进入 format 花括号扫描域）。


def _bse_legacy_bare_codes() -> frozenset:
    """权威 BSE legacy 裸码集（security_code_rules.BSE_LEGACY_TO_920 键集）。

    fail-loud：同包内权威文件不可读属环境损坏，静默烘焙空集会让 BJ 判定
    退化为仅 920* 前缀（违反 v1.2 审定「精确表优先」），故显性报错。"""
    try:
        from quantstudio.backtest.libs.security_code_rules import BSE_LEGACY_TO_920
    except Exception as exc:  # pragma: no cover - 环境损坏防护
        raise ValueError(
            "P-D11 BSE 权威映射不可导入（security_code_rules）： %r" % (exc,))
    if not BSE_LEGACY_TO_920:
        raise ValueError(
            "P-D11 BSE 权威映射为空（bse_legacy_code_mapping.json 缺失/损坏）"
            "—— 禁止烘焙空集（fail-loud，见 pd11 设计 v1.2 必改②）")
    return frozenset(str(k).split(".")[0] for k in BSE_LEGACY_TO_920)


def _render_position_view_ext(marker: str) -> str:
    """P-D11 模板渲染：format(marker) → 替换 BSE 烘焙字面量与条数注记。"""
    codes = sorted(_bse_legacy_bare_codes())
    literal = "{%s}" % ", ".join("'%s'" % c for c in codes)
    return (_QS_POSITION_VIEW_EXT.format(marker=marker)
            .replace("__QS_BSE_SET__", literal)
            .replace("__QS_BSE_N__", str(len(codes))))


def _render_industry_ext(marker: str, industry_codes: tuple[str, ...]) -> str:
    """B1 模板渲染：format(marker) → 替换 _QS_INDUSTRY_CODES 烘焙行业码字面量（tuple）。

    industry_codes 为空 → 烘焙空 tuple（wrapper 池空 → fail-open + RD-3 告警，
    转换器另有 warnings 条目；不抛错，保持转换可用）。
    注意：必须渲染为 tuple（模板内 _QS_INDUSTRY_CODES[0] 下标、','.join 保序），
    渲染为 set 会运行 TypeError。"""
    literal = "(%s)" % ", ".join("'%s'" % c for c in industry_codes)
    return (_QS_INDUSTRY_EXT.format(marker=marker)
            .replace("__QS_INDUSTRY_CODES__", literal))


# ===== P-D13 C1a/C1b/C3a：宇宙差审计 + 北交所过滤 + 窗口审计（2026-08-27） =====
# C1a 板块统计：get_Ashares 返回后输出 QS_ASHARES_BREAKDOWN（定位 322 北交所差）。
# C1b exclude_bse：_QS_EXCLUDE_BSE 常量（转换期 CLI --exclude-bse 烘焙，默认 False
# ——P-D9 纪律：本地语义权威；对齐验证需显式 opt-in）。
# C3a 窗口审计：get_history(count≥100) 返回前输出 QS_HISTORY_WINDOW
# （vol_regime q 0.9333 vs 0.9000 单点故障定位器）。
_QS_DATA_AUDIT_EXT = '''
{marker}
# [qs-import-generated] P-D13 数据层审计注入（C1a/C1b/C3a，2026-08-27）
# C1b：北交所过滤开关（默认 False 保持平台全 A 行为；对齐本地口径时设 True）
_QS_EXCLUDE_BSE = {exclude_bse}


class _QSAsharesRefState:
    """原始 get_Ashares 引用（class 属性承载——白名单兼容）。"""
    orig = None


_QSAsharesRefState.orig = get_Ashares


def get_Ashares(date=None):
    """P-D13 包装：板块统计（QS_ASHARES_BREAKDOWN）+ exclude_bse 过滤。"""
    _codes = _QSAsharesRefState.orig(date)
    try:
        _bse = [c for c in _codes
                if str(c).split('.', 1)[0].startswith('920')
                or str(c).split('.', 1)[0] in _QS_BSE_LEGACY]
        if _QS_EXCLUDE_BSE and _bse:
            _codes = [c for c in _codes if c not in set(_bse)]
        log.info('QS_ASHARES_BREAKDOWN total=%d bse=%d non_bse=%d exclude_bse=%s'
                 % (len(_codes), len(_bse), len(_codes) - len(_bse),
                    _QS_EXCLUDE_BSE))
    except Exception:
        pass
    return _codes
'''


def _render_data_audit_ext(marker: str, exclude_bse: bool = False) -> str:
    """P-D13 C1a/C1b/C3a 模板渲染。"""
    return _QS_DATA_AUDIT_EXT.format(marker=marker,
                                     exclude_bse='True' if exclude_bse else 'False')


# ===== P-A2 PTrade 保真模式：eps 口径双端映射（2026-08-24，探针乙实证） =====
# 平台 eps 表监听（24 列）中与本地 eps 语义相关的字段 + 数值对照（PIT @2026-06-30）：
#   code    本地eps  平台basic_eps  平台eps  平台diluted_eps  本地diluted_eps
#   000001  0.67     0.67          0.75     0.67             0.67
#   600000  0.52     0.52          0.54     0.52             0.52
# → 本地 eps 语义 == 平台 basic_eps/diluted_eps（逐位一致）；平台默认 eps 是加权口径
#   （+12%/+3.8% 偏差源，P-D10 归因核对）。
# fidelity_eps_basis（产物侧固化常量，平台运行时零环境变量）决定 get_fundamentals
# 请求 eps 表时向平台请求的列，使平台产物向本地正确语义锚收敛：
#   'passthrough'（默认）：请求平台 eps 列（现状，容忍平台加权口径差异）；
#   'basic'：请求平台 basic_eps 列（本地 eps 语义）；
#   'diluted'：请求平台 diluted_eps 列（本地 diluted_eps 语义）。
# 平台返回的 basic_eps/diluted_eps 列经逆翻译回本地 'eps'/'diluted_eps' 名。
# v8.3 整合（2026-08-31）：常量 + 请求/返回翻译全部并入 _QS_FUNDAMENTALS_EXT（唯一
# wrapper、唯一 get_fundamentals 定义）——旧版 _QS_FIDELITY_EPS_EXT 独立 def 在后注入
# 同名覆盖 B6c/seeds/PIT 全部修复（平台复验 v8.0~v8.2 总根因）已删除；eps_basis 由
# 转换器经 _QS_FUNDAMENTALS_EXT.format(eps_basis=...) 烘焙进产物。

# 档 2 表达式内嵌的等价字面量（H2）：本地函数 → PTrade 语义等价字面量
_REWRITE_LITERALS: dict[str, str] = {
    "set_backtest": "None",
    "is_trade": "False",
}

# 行情字段名映射中枢在 _QS_HISTORY_WRAPPER 内（请求本地→PTrade / 返回 PTrade→本地）
# DENY_REMOVE 中允许档 2 改写的函数；其余 DENY_REMOVE 档 2 → BLOCK
_REMOVE_ALLOW_INLINE: frozenset[str] = frozenset(_REWRITE_LITERALS.keys())


def _source_uses_trade_date(source: str) -> bool:
    """门控：源策略是否显式使用本地合成伪列 trade_date（而非调用它在 PTrade 上会被拒）。

    判定 = 源码文本出现 trade_date 字面量（get_history 的 field 列表或
    _extract_history_field(..., 'trade_date') 提取调用）。用双/单引号两种字面量探测，
    覆盖策略源码两种写法；不使用则注入旧 wrapper（输出与改造前逐字节一致）。
    """
    return "'trade_date'" in source or '"trade_date"' in source


_ORDER_APIS = ("order_target_value", "order_target_percent", "order_value",
               "order_percent", "order")


def _source_uses_order_api(source: str) -> bool:
    """门控：源策略是否调用任一订单 API（是 → 注入拆单 wrapper）。

    判定 = AST 调用名匹配（import 语句除外），避免字符串字面量误伤。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # 无法解析时退化文本探测（只在调用语境出现才命中）
        return any(("(%s" % name) in source for name in _ORDER_APIS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _ORDER_APIS:
                return True
            if isinstance(fn, ast.Attribute) and fn.attr in _ORDER_APIS:
                return True
    return False


_DATE_APIS = ("get_trade_days", "get_stock_info")


def _source_uses_date_api(source: str) -> bool:
    """门控：源策略调用 get_trade_days / get_stock_info → 注入 A3 归一化包装。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return any(("(%s" % name) in source for name in _DATE_APIS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _DATE_APIS:
                return True
            if isinstance(fn, ast.Attribute) and fn.attr in _DATE_APIS:
                return True
    return False


def _source_uses_filter_status(source: str) -> bool:
    """门控（P-D9）：源策略调用 filter_stock_by_status → 注入退市风险兜底包装。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "filter_stock_by_status(" in source
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "filter_stock_by_status":
                return True
            if isinstance(fn, ast.Attribute) and fn.attr == "filter_stock_by_status":
                return True
    return False


_POSITION_APIS = ("get_positions", "get_position")


def _source_uses_position_api(source: str) -> bool:
    """门控（P-D11）：源策略调用 get_positions / get_position → 注入持仓视图归一。

    判定 = AST 调用名匹配（import/字符串字面量不触发），与 _source_uses_order_api
    同款；退化路径只在调用语境出现才命中。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return any(("(%s" % name) in source for name in _POSITION_APIS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _POSITION_APIS:
                return True
            if isinstance(fn, ast.Attribute) and fn.attr in _POSITION_APIS:
                return True
    return False


def _source_uses_ashares_api(source: str) -> bool:
    """门控（P-D13）：源策略调用 get_Ashares → 注入数据层审计（板块统计/过滤）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "get_Ashares(" in source
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "get_Ashares":
                return True
            if isinstance(fn, ast.Attribute) and fn.attr == "get_Ashares":
                return True
    return False


def _source_uses_fundamentals(source: str) -> bool:
    """门控（P-D10）：源策略调用 get_fundamentals / get_fundamentals_batch →
    注入契约对齐包装（list 单调用 + 列筛选 + 日期归一 + 形状自检 + B6 双查询分派 + 批量预取）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ("get_fundamentals(" in source) or ("get_fundamentals_batch(" in source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name in ("get_fundamentals", "get_fundamentals_batch"):
                return True
    return False


def _source_uses_industry_api(source: str) -> bool:
    """门控（B1）：源策略调用 get_industry → 注入平台替代包装（反向金融池双码 + fail-open）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "get_industry(" in source
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "get_industry":
                return True
            if isinstance(fn, ast.Attribute) and fn.attr == "get_industry":
                return True
    return False


def _extract_industry_codes(source: str) -> tuple[str, ...]:
    """从策略源码提取行业码集（6 位数字引号字面量，如 ('801780','801790','480000','490000')
    的行业剔除判定集）→ 烘焙进 _QS_INDUSTRY_EXT 的 _QS_INDUSTRY_CODES。

    AST 遍历全部字符串常量：纯 6 位数字即候选（行业码惯用 6 位；'000001' 类股票代码
    一般 6/7 位带后缀不命中；日期/数字串有引号极少）。空 → 空元组（转换器告警，
    行业剔除降级 fail-open 登记 RD-3）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    codes: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.strip()
            if len(v) == 6 and v.isdigit() and v not in codes:
                codes.append(v)
    return tuple(codes)

# ============================================================================
# 工具
# ============================================================================

_LIFECYCLE = ("initialize", "before_trading_start", "handle_data", "after_trading_end")
_BLOCK_API_NO_FUNCTION = frozenset({"load_research_signals", "get_trades_file",
                                    "convert_position_from_csv", "SharedCostModel"})


def _line_of(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 1))


def _analyze_aliases(tree: ast.AST) -> dict[str, str]:
    """H3：构建 import 别名映射（别名 → 原名）。"""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
    return aliases


def _apply_replacements(src: str, replacements: list[tuple[int, int, int, int, str]]) -> str:
    """按 (start_line, start_col, end_line, end_col, new_text)（1-based 行号、0-based 列）
    从后往前应用替换，避免行号/偏移漂移。"""
    if not replacements:
        return src
    lines = src.splitlines(keepends=True)
    offsets: list[int] = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln)

    def abs_pos(line: int, col: int) -> int:
        return offsets[line - 1] + col

    ordered = sorted(replacements, key=lambda r: -abs_pos(r[0], r[1]))
    for sl, sc, el, ec, new in ordered:
        s, e = abs_pos(sl, sc), abs_pos(el, ec)
        src = src[:s] + new + src[e:]
    return src


def _is_bare_expr_stmt(node: ast.AST, tree: ast.AST) -> bool:
    """该 Call 节点是否直接作为裸表达式语句（档 1 判定）。"""
    parent = None
    for n in ast.walk(tree):
        for child in ast.iter_child_nodes(n):
            if child is node:
                parent = n
                break
        if parent is not None:
            break
    return isinstance(parent, ast.Expr)


# ============================================================================
# 转换器
# ============================================================================


class SourceConverter:
    def __init__(self, *, strategy_id: Optional[str] = None, inject_helpers: bool = True,
                 verbose: bool = True,
                 etf_pool_start_date: Optional[str] = None,
                 db_path: Optional[str] = None,
                 etf_type: str = "equity",
                 active_only: bool = True,
                 fidelity_eps_basis: str = "basic",
                 exclude_bse: bool = False):
        self.strategy_id = strategy_id
        self.inject_helpers = inject_helpers
        self.verbose = verbose
        # P-A2：产物侧 eps 口径保真映射（P-D13 D2 审计通过 2026-08-27：默认
        # basic——探针三实证 basic_eps == 本地 eps Δ=0.0000；passthrough 显式
        # 可指定向后兼容。D8：行为变化纳入合并基线重验）。
        self._fidelity_eps_basis = fidelity_eps_basis
        # P-D13 C1b：北交所过滤旗标（CLI --exclude-bse，默认 False=P-D9 语义权威）
        self._exclude_bse = exclude_bse
        self.actions: list[ConversionAction] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self._set_backtest_body: Optional[str] = None  # T5: set_backtest 函数体（供调用点内联）
        # 07 规格：ETF 动态池 FREEZE 固化
        self._etf_pool_start_date = etf_pool_start_date
        self._db_path = db_path
        self._etf_type = etf_type
        self._active_only = active_only
        self._freeze_calls: list[ast.Call] = []
        self._etf_pool_block: Optional[str] = None
        self._etf_pool_meta: dict[str, Any] = {}
        self._etf_frozen = False
        self.coverage: dict[str, Any] = {
            "api_calls_seen": 0, "denylist_hits": 0, "normalized_params": 0,
            "injected_helpers": [], "fq_warn_kept": [], "aliases_seen": {},
            "mytt_used": [], "ashare_used": [], "inject_libs": [],
        }
        self._replacements: list[tuple[int, int, int, int, str]] = []
        self._fq_injected_nodes: set[int] = set()  # P3-1：fq 被注入/归一化的 node id
        self._mytt_needed: set[str] = set()
        self._ashare_needed: set[str] = set()
        self._need_shim: set[str] = set()
        self._need_helpers = False
        self._sklearn_used = False

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def convert(self, source_code: str, source_path: Optional[str] = None) -> SourceImportResult:
        # N1：BOM 已在调用侧用 utf-8-sig 处理；此处兜底
        if source_code.startswith("\ufeff"):
            source_code = source_code.lstrip("\ufeff")
            self.warnings.append("源文件含 BOM（已剥离，读取应使用 utf-8-sig）")

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            self.errors.append(f"SyntaxError: {e}")
            return self._result(source_code)

        self._tree = tree
        self._src = source_code
        self._lines = source_code.splitlines(keepends=True)
        self._aliases = _analyze_aliases(tree)
        self.coverage["aliases_seen"] = {
            k: v for k, v in self._aliases.items() if v not in ("numpy", "pandas")
        }

        # 1) 生命周期扫描 + set_backtest FunctionDef 删除（H2）
        self._handle_lifecycle(tree)

        # 2) sklearn 检测（N2：WARN + PTRADE_RUNTIME_UNVERIFIED，不 BLOCK）
        self._check_third_party(tree)

        # 3) AST 全量扫描（别名归一化后匹配）
        self._scan_calls(tree)

        # 3a) 代码后缀规范化（聚宽风格 XSHG/XSHE → PTrade SS/SZ，AST 字符串常量级）
        self._normalize_code_suffixes(tree)

        # 3a2) PTrade 契约合规改写（get_Ashares 日期 / get_history 签名 B /
        #       set_benchmark 后缀 / get_stock_status 关键字）
        self._normalize_ptrade_contract_calls(tree)

        # 3b) ETF FREEZE 档（07 规格 §2）：get_etf_list_local → 静态池固化
        self._freeze_etf_pool()

        # 4) 应用文本改写（从后往前）
        converted = _apply_replacements(source_code, self._replacements)

        # 5) 注入（helper / shim / MyTT / A股规则）
        if self.inject_helpers:
            converted = self._inject_all(converted)

        # 6) 头部
        converted = self._build_header(source_path) + converted

        # 7) 转换后语法自检
        try:
            ast.parse(converted)
        except SyntaxError as e:
            self.errors.append(f"转换产物 SyntaxError（转换器 bug）: {e}")

        return self._result(converted)

    def _result(self, code: str) -> SourceImportResult:
        self.coverage["denylist_hits"] = sum(
            1 for a in self.actions if a.action_type in ("REMOVE", "REWRITE", "SHIM", "BLOCK")
        )
        return SourceImportResult(
            converted_code=code,
            actions=self.actions,
            warnings=self.warnings,
            errors=self.errors,
            coverage=self.coverage,
        )

    def _find_parent(self, node: ast.AST) -> Optional[ast.AST]:
        """在语法树中查找 node 的父节点。"""
        for n in ast.walk(self._tree):
            for child in ast.iter_child_nodes(n):
                if child is node:
                    return n
        return None

    # ------------------------------------------------------------------
    # 生命周期 + set_backtest
    # ------------------------------------------------------------------
    def _handle_lifecycle(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if name == "set_backtest":
                    sl, el = node.lineno, node.end_lineno
                    # T5 修复：set_backtest 函数体可能含真实配置调用（set_limit_mode/
                    # set_commission/set_slippage 等）。模块级提升时机过早（引擎未 attach
                    # 时 set_commission 被忽略，实测默认 0.00035 未改为 0.00015）。
                    # 正确做法：函数体存入 self._set_backtest_body，由调用点内联
                    # （保持原 initialize 执行时机）；函数定义删除。
                    body_src = ""
                    for stmt in node.body:
                        seg = ast.get_source_segment(self._src, stmt) or ""
                        if seg:
                            body_src += "\n" + seg
                    stripped_body = body_src.strip()
                    if stripped_body and stripped_body != "pass":
                        lines = stripped_body.splitlines()
                        indent = len(lines[0]) - len(lines[0].lstrip())
                        self._set_backtest_body = "\n".join(
                            (ln[indent:] if len(ln) >= indent else ln.lstrip())
                            for ln in lines)
                        self.actions.append(ConversionAction(
                            action_type="REWRITE", rule_id="DENY-SET_BACKTEST-LIFT",
                            api_name="set_backtest", line=sl, severity="WARN",
                            old_text=f"def set_backtest() 定义（行 {sl}-{el}）",
                            new_text=self._set_backtest_body[:80],
                            message="set_backtest 定义已删除，函数体配置调用将由调用点内联"
                                    "（保留 set_limit_mode/set_commission 语义与执行时机）"))
                    self._replacements.append(
                        (sl, 0, el, len(self._lines[el - 1].rstrip("\r\n")), ""))
                    if stripped_body and stripped_body != "pass":
                        self.actions.append(ConversionAction(
                            action_type="REMOVE", rule_id="DENY-SET_BACKTEST",
                            api_name="set_backtest", line=sl, severity="INFO",
                            old_text=f"def set_backtest() 定义（行 {sl}-{el}）",
                            message="本地自创 API 定义已删除（真实 PTrade 无此函数）"))
                    elif stripped_body == "pass":
                        self.actions.append(ConversionAction(
                            action_type="REMOVE", rule_id="DENY-SET_BACKTEST",
                            api_name="set_backtest", line=sl, severity="INFO",
                            old_text=f"def set_backtest() 定义（行 {sl}-{el}）",
                            message="本地自创 API 定义已整体删除（空函数体，真实 PTrade 无此函数）"))
                elif name not in _LIFECYCLE:
                    # 策略自定义函数：保留（函数体内部调用在 _scan_calls 处理）
                    pass

    def _line_start_abs(self, line: int) -> int:
        pos = 0
        for i in range(line - 1):
            pos += len(self._lines[i])
        return pos

    def _line_end_abs(self, line: int) -> int:
        return self._line_start_abs(line) + len(self._lines[line - 1].rstrip("\r\n"))

    # ------------------------------------------------------------------
    # sklearn / 第三方
    # ------------------------------------------------------------------
    def _check_third_party(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "sklearn" in node.module:
                self._sklearn_used = True
                self.warnings.append(
                    "检测到 sklearn 依赖（行 %d）：PTrade 平台可用性未验证，"
                    "记 PTRADE_RUNTIME_UNVERIFIED，不 BLOCK" % node.lineno)

    # ------------------------------------------------------------------
    # AST 调用扫描（核心）
    # ------------------------------------------------------------------
    def _scan_calls(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            raw = ""
            f = node.func
            if isinstance(f, ast.Name):
                raw = f.id
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                raw = f.attr  # 形如 obj.func() —— 仅按属性名匹配（g.foo 等）
            if not raw:
                continue
            name = self._aliases.get(raw, raw)  # H3：别名归一化
            if name in ("numpy", "pandas"):
                continue
            self.coverage["api_calls_seen"] += 1

            if name in _BLOCK_API_NO_FUNCTION:
                self._block_call(node, name)
            elif name == "get_etf_list_local":
                # 07 规格：FREEZE 档（非 REMOVE）——收集调用点，由 _freeze_etf_pool 处理
                self._freeze_calls.append(node)
            elif name in DENY_REMOVE:
                self._remove_call(node, name)
            elif name in DENY_SHIM:
                self._need_shim.add(name)
                self.actions.append(ConversionAction(
                    action_type="SHIM", rule_id="DENY-SHIM", api_name=name,
                    line=_line_of(node), severity="WARN",
                    message=f"{name}() 为本地批量 API，将注入同名 shim（循环单调用）"))
            elif name in MYTT_FUNCTIONS:
                self._mytt_needed.add(name)
                self.coverage["mytt_used"].append(name)
            elif name in ASHARE_RULES_FUNCTIONS:
                self._ashare_needed.add(name)
                self.coverage["ashare_used"].append(name)
            elif name in PTRADE_REGISTERED_WARN:
                # KEEP-WARN：保留调用；参数级归一化/删除单独处理
                self._normalize_call(node, name)
            elif name in ("get_history", "get_price"):
                # get_history 的 fq 归一化与签名 A→B 改写由 _normalize_ptrade_contract_calls
                # 统一处理（同一调用内避免替换区域重叠）；get_price 仍走参数删除
                if name == "get_price":
                    self._normalize_call(node, name)

    # ------------------------------------------------------------------
    # DENY_REMOVE 分档（H2）
    # ------------------------------------------------------------------
    def _remove_call(self, node: ast.Call, name: str) -> None:
        line = _line_of(node)
        # T5 修复：set_backtest 调用点内联函数体（配置调用保留原执行时机）。
        # 原语义：initialize 内 `if not is_trade(): set_backtest()` 恒执行 → 配置生效。
        if name == "set_backtest" and self._set_backtest_body:
            replacement = ""
            if _is_bare_expr_stmt(node, self._tree):
                # 档 1：裸语句 → 内联配置（保持调用点缩进）
                raw = self._lines[line - 1]
                indent = raw[: len(raw) - len(raw.lstrip())]
                inlined = "\n".join(indent + ln for ln in self._set_backtest_body.splitlines())
                replacement = inlined + "\n"
                self.actions.append(ConversionAction(
                    action_type="REWRITE", rule_id="DENY-SET_BACKTEST-INLINE",
                    api_name="set_backtest", line=line, severity="WARN",
                    old_text=f"{name}(...) 调用",
                    new_text=inlined[:80],
                    message="set_backtest() 调用已内联为配置调用"
                            "（set_limit_mode/set_commission 保留原执行时机）"))
            else:
                # 档 2：内嵌表达式 → None（配置调用丢弃，记 WARN）
                self._replacements.append((
                    node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                    "None"))
                self.actions.append(ConversionAction(
                    action_type="REWRITE", rule_id="DENY-SET_BACKTEST-INLINE",
                    api_name="set_backtest", line=line, severity="WARN",
                    old_text=f"{name}(...)",
                    new_text="None",
                    message="set_backtest() 内嵌于表达式，改写为 None；"
                            "其函数体内的配置调用未保留（请人工核对）"))
            self._replacements.append(
                (line, 0, line, len(self._lines[line - 1].rstrip("\r\n")), replacement))
            return
        if _is_bare_expr_stmt(node, self._tree):
            # 档 1：裸表达式语句 → 删整行（含缩进）；若父复合语句体仅此一条，
            # 替换为 pass 保缩进（防 "if x:\n  set_backtest()" 删除后空块语法错误）
            replacement = ""
            parent = self._find_parent(node)
            if isinstance(parent, ast.Expr):
                gp = self._find_parent(parent)
                body = getattr(gp, "body", None)
                if isinstance(body, list) and len(body) == 1 and body[0] is parent:
                    raw = self._lines[line - 1]
                    indent = raw[: len(raw) - len(raw.lstrip())]
                    replacement = indent + "pass\n"
            self._replacements.append((line, 0, line, len(self._lines[line - 1].rstrip("\r\n")),
                                       replacement))
            self.actions.append(ConversionAction(
                action_type="REMOVE", rule_id=f"DENY-{name.upper()}", api_name=name,
                line=line, severity="INFO",
                old_text=f"{name}(...) 裸语句",
                message=f"{name}() 为本地扩展 API，整行已删除"
                        + ("（父块置 pass）" if replacement else "")))
        elif name in _REMOVE_ALLOW_INLINE:
            # 档 2：表达式内嵌 → 等价字面量
            literal = _REWRITE_LITERALS[name]
            self._replacements.append((
                node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, literal))
            self.actions.append(ConversionAction(
                action_type="REWRITE", rule_id=f"DENY-{name.upper()}-INLINE", api_name=name,
                line=line, severity="WARN",
                old_text=f"{name}(...)",
                new_text=literal,
                message=f"{name}() 内嵌于表达式，改写为等价字面量 {literal}"))
        else:
            # 档 3：无法确定等价语义 → BLOCK
            self._block_call(node, name)

    def _block_call(self, node: ast.Call, name: str) -> None:
        line = _line_of(node)
        self.errors.append(f"BLOCK: {name}()（行 {line}）无法自动转换，需人工改用 PTrade 等价数据源")
        self.actions.append(ConversionAction(
            action_type="BLOCK", rule_id=f"BLOCK-{name.upper()}", api_name=name,
            line=line, severity="BLOCK",
            old_text=f"{name}(...)",
            message=f"{name}() 无 PTrade 自动替代，转换失败（交人工）"))

    # ------------------------------------------------------------------
    # 参数归一化（G1：只按 grade 执行）
    # ------------------------------------------------------------------
    def _normalize_call(self, node: ast.Call, name: str) -> None:
        for kw in node.keywords:
            if kw.arg is None:
                continue
            param = kw.arg
            # 1) 参数删除表（get_price 的 panel/fill_paused/skip_paused）
            if name == "get_price" and param in GET_PRICE_DROP_PARAMS:
                self._replacements.append((
                    kw.lineno, kw.col_offset, kw.end_lineno, kw.end_col_offset, ""))
                self.actions.append(ConversionAction(
                    action_type="NORMALIZE", rule_id="NORM-GETPRICE-PARAM",
                    api_name=name, line=_line_of(node), severity="INFO",
                    old_text=f"{param}=...",
                    message=f"get_price 不支持的参数 {param} 已删除（本地与 PTrade 均不消费）"))
                self.coverage["normalized_params"] += 1
                continue
            # 2) NORMALIZE_RULES（fq 等）
            for api, p, old_v, new_v, rule_id, grade in NORMALIZE_RULES:
                if api != name or p != param:
                    continue
                val = kw.value
                if isinstance(val, ast.Constant) and str(val.value).lower() == str(old_v).lower():
                    if grade == "NORMALIZE":
                        self._replacements.append((
                            val.lineno, val.col_offset, val.end_lineno, val.end_col_offset,
                            repr(new_v)))
                        self.actions.append(ConversionAction(
                            action_type="NORMALIZE", rule_id=rule_id, api_name=name,
                            line=_line_of(node), severity="INFO",
                            old_text=f"{param}={old_v}", new_text=f"{param}={new_v}",
                            message=f"G1 证据（provider 同分支）：{old_v}≡{new_v}，已归一化"))
                        self.coverage["normalized_params"] += 1
                    else:  # WARN_KEEP
                        self.coverage["fq_warn_kept"].append(f"{name}:{old_v}")
                        self.actions.append(ConversionAction(
                            action_type="KEEP_COMMENT", rule_id=rule_id, api_name=name,
                            line=_line_of(node), severity="WARN",
                            old_text=f"{param}={old_v}",
                            message=f"{param}={old_v} 保留原值：本地 {old_v} 与 {new_v} 语义不等价"
                                    f"（G1），该策略不进入 1:1 复刻清单"))
                    break

    # ------------------------------------------------------------------
    # PTrade 契约合规改写（2026-08-12，fall_reversal 平台零交易根因 4 处）
    # ------------------------------------------------------------------
    # 契约证据（skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json）：
    # - get_Ashares: notes "date uses YYYYmmdd when supplied"（示例 get_Ashares('20260724')）
    # - get_history: count-first（示例 get_history(60, frequency='1d', field=['close'],
    #   security_list='600000.SS', fq='pre', include=False, is_dict=True)）
    # - set_benchmark: 带后缀（示例 set_benchmark('000300.SS')）
    # - get_stock_status: 关键字 stocks/query_type/query_date（示例 query_type='ST',
    #   query_date='20260724'）
    # 本地等价性（改写产物在本地引擎语义不变）：
    # - get_history 双签名：本地首参 int → count-first（PR4 接受 frequency/field/security_list）
    # - set_benchmark：本地 bare_code 剥离后缀
    # - get_Ashares：本地 _end_ms → pd.Timestamp('YYYYmmdd') 可解析
    # - get_stock_status：本地签名 (stocks, query_type='ST', query_date=None) 关键字兼容
    # 幂等：count-first 形态（首参 int 常量或 count/frequency/security_list 关键字）跳过。
    # fq 归一化（NORMALIZE 档）并入签名改写（同一调用内避免替换区域重叠）。

    def _normalize_ptrade_contract_calls(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not isinstance(f, ast.Name):
                continue
            name = self._aliases.get(f.id, f.id)
            # P3-1：对 get_history/get_price 注入 fq='pre'（必须在签名改写之前）
            # PTrade 默认 fq=不复权（实证 2026-08-14），不注入会导致两端信号不一致
            if name in ("get_history", "get_price"):
                self._inject_fq_pre(node)
                # get_price 不走 _rewrite_history_signature，需在此创建 replacement
                if name == "get_price" and id(node) in self._fq_injected_nodes:
                    new_text = ast.unparse(node)
                    self._replacements.append(
                        (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, new_text))
            if name == "get_Ashares":
                self._rewrite_asharess_date(node)
            elif name == "get_history":
                self._rewrite_history_signature(node)
            elif name == "set_benchmark":
                self._rewrite_benchmark_suffix(node)
            elif name == "get_stock_status":
                self._rewrite_stock_status_keywords(node)
        # 独立 pass：X['col'].values 是 Attribute 模式（非 Call），单独遍历
        self._rewrite_values_access(tree)

    # ---- P3-1：fq='pre' 注入（确保 PTrade 端信号前复权一致）----
    def _inject_fq_pre(self, node: ast.Call) -> None:
        """对没有 fq 参数的 get_history/get_price 注入 fq='pre'。

        PTrade 平台 get_history 默认 fq=不复权（实证 2026-08-14），
        本地引擎默认 fq='pre'（前复权）。不注入会导致两端信号不一致。
        - 无 fq 参数 → 注入 fq='pre'
        - fq='none' → 归一化为 fq=None（PTrade fq='none' 返回空数据）
        - fq='pre' / fq=None → 不变
        """
        modified = False
        has_fq = False
        for kw in node.keywords:
            if kw.arg == "fq":
                has_fq = True
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str) \
                        and kw.value.value.lower() == "none":
                    kw.value = ast.Constant(value=None)
                    self.actions.append(ConversionAction(
                        action_type="NORMALIZE", rule_id="NORM-FQ-NONE",
                        api_name="get_history", line=_line_of(node), severity="WARN",
                        old_text="fq='none'", new_text="fq=None",
                        message="fq='none' PTrade 不支持（返回空），归一化为 fq=None"))
                    self.coverage["normalized_params"] += 1
                    modified = True
                break
        if not has_fq:
            node.keywords.append(ast.keyword(arg="fq", value=ast.Constant(value="pre")))
            self.actions.append(ConversionAction(
                action_type="INJECT", rule_id="INJECT-FQ-PRE",
                api_name="get_history", line=_line_of(node), severity="INFO",
                old_text="(no fq)", new_text="fq='pre'",
                message="P3-1: PTrade 默认 fq=不复权，注入 fq='pre' 确保信号前复权一致"))
            self.coverage["normalized_params"] += 1
            modified = True
        if modified:
            self._fq_injected_nodes.add(id(node))

    # ---- 修复 1：get_Ashares(date) 日期格式 YYYY-MM-DD → YYYYmmdd ----
    def _rewrite_asharess_date(self, node: ast.Call) -> None:
        arg = None
        if node.args:
            arg = node.args[0]
        else:
            for kw in node.keywords:
                if kw.arg == "date":
                    arg = kw.value
        if arg is None:
            return  # get_Ashares() 无参：平台默认当天
        new_text = None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "-" in arg.value:
            new_text = repr(arg.value.replace("-", ""))
        elif (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "strftime" and arg.args
                and isinstance(arg.args[0], ast.Constant)
                and arg.args[0].value == "%Y-%m-%d"):
            new_text = f"{ast.unparse(arg.func)}('%Y%m%d')"
        elif (isinstance(arg, ast.IfExp) and isinstance(arg.test, ast.Call)
                and isinstance(arg.test.func, ast.Name)
                and arg.test.func.id == "isinstance"):
            return  # 已包装（幂等：二次转换不重复包装）
        elif (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "strftime" and arg.args
                and isinstance(arg.args[0], ast.Constant)
                and "-" not in str(arg.args[0].value)):
            return  # 已是 YYYYmmdd 形态（幂等：strftime('%Y%m%d') 不再改写/包装）
        else:
            expr = ast.unparse(arg)
            new_text = (f"({expr}.replace('-', '') if isinstance({expr}, str) "
                        f"else {expr}.strftime('%Y%m%d'))")
        self._replacements.append(
            (arg.lineno, arg.col_offset, arg.end_lineno, arg.end_col_offset, new_text))
        self.actions.append(ConversionAction(
            action_type="NORMALIZE", rule_id="NORM-ASHARES-DATE",
            api_name="get_Ashares", line=_line_of(node), severity="WARN",
            old_text=ast.unparse(arg), new_text=new_text,
            message="get_Ashares date 改为 YYYYmmdd（PTrade 契约；本地 pd.Timestamp 兼容解析）"))
        self.coverage["normalized_params"] += 1

    # ---- 修复 2：get_history 签名 A（security-first）→ B（count-first）----
    def _rewrite_history_signature(self, node: ast.Call) -> None:
        kw_names = {kw.arg for kw in node.keywords if kw.arg}
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, int):
            # P3-1：签名 B 不改写签名，但 fq 可能已被 _inject_fq_pre 注入 → 创建 replacement
            if id(node) in self._fq_injected_nodes:
                new_text = ast.unparse(node)
                self._replacements.append(
                    (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, new_text))
            return  # count-first（首参 int）：已符合 PTrade 契约，不动（include 不映射）
        if "security_list" in kw_names or "frequency" in kw_names:
            # P3-1：同上，签名 B 关键字形态
            if id(node) in self._fq_injected_nodes:
                new_text = ast.unparse(node)
                self._replacements.append(
                    (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, new_text))
            return  # count-first 关键字形态（security_list/frequency 为 B 独有）：不动
        if not node.args and not ("security" in kw_names or "unit" in kw_names
                                  or "fields" in kw_names):
            return  # get_history() 无参或无法判定：跳过
        # 其余（含 security/unit/fields 任一关键字，或位置参数非 int）→ 签名 A，改写
        # fq 归一化（NORMALIZE 档）——count-first 形态也在此处理（见 _scan_calls 改动）
        fq = ast.Constant(value="pre")
        include = ast.Constant(value=False)
        is_dict = ast.Constant(value=False)
        for kw in node.keywords:
            if kw.arg == "fq":
                fq = kw.value
            elif kw.arg == "include":
                include = kw.value
            elif kw.arg == "is_dict":
                is_dict = kw.value
        # include 不映射（2026-08-13 第二次实测确认：PTrade include 语义与本地一致，
        # True=含当天、False=前一交易日/前一 bar——两端不需要任何映射）。
        # include 保持原值（默认 False = 本地默认值，与 PTrade 默认语义一致）。
        if isinstance(fq, ast.Constant) and isinstance(fq.value, str):
            for api, p, old_v, new_v, rule_id, grade in NORMALIZE_RULES:
                if api == "get_history" and p == "fq" \
                        and str(fq.value).lower() == str(old_v).lower() \
                        and grade == "NORMALIZE":
                    fq = ast.Constant(value=new_v)
                    self.actions.append(ConversionAction(
                        action_type="NORMALIZE", rule_id=rule_id, api_name="get_history",
                        line=_line_of(node), severity="INFO",
                        old_text=f"fq={old_v}", new_text=f"fq={new_v}",
                        message=f"G1 证据（provider 同分支）：{old_v}≡{new_v}，已归一化"))
                    self.coverage["normalized_params"] += 1
                    break
        # 签名 A 提取：位置 (security, count, unit, fields) 或关键字
        def take(index, key, default):
            if node.args and len(node.args) > index:
                return node.args[index]
            for kw in node.keywords:
                if kw.arg == key:
                    return kw.value
            return default
        security = take(0, "security", None)
        count = take(1, "count", None)
        unit = take(2, "unit", ast.Constant(value="1d"))
        fields = take(3, "fields", None)
        if security is None or count is None:
            return  # 参数不全：不改写（交给校验器）
        sec_expr = security
        if isinstance(security, ast.List) and len(security.elts) == 1:
            sec_expr = security.elts[0]  # 单只列表拆包为标量（PTrade 契约示例形态）
        new_call = ast.Call(
            func=ast.Name(id="get_history"),
            args=[count],
            keywords=[
                ast.keyword(arg="frequency", value=unit),
                ast.keyword(arg="security_list", value=sec_expr),
                ast.keyword(arg="fq", value=fq),
                ast.keyword(arg="include", value=include),
                ast.keyword(arg="is_dict", value=is_dict),
            ])
        if fields is not None:
            new_call.keywords.insert(1, ast.keyword(arg="field", value=fields))
        new_text = ast.unparse(new_call)
        self._replacements.append(
            (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, new_text))
        self.actions.append(ConversionAction(
            action_type="REWRITE", rule_id="NORM-GETHISTORY-SIG",
            api_name="get_history", line=_line_of(node), severity="WARN",
            old_text=ast.unparse(node), new_text=new_text,
            message="get_history 签名 A→B（count-first，PTrade 契约；本地双签名兼容）"))
        self.coverage["normalized_params"] += 1

    # ---- 修复 4：set_benchmark 裸码补后缀 ----
    def _rewrite_benchmark_suffix(self, node: ast.Call) -> None:
        if not node.args:
            return
        arg = node.args[0]
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            return
        code = arg.value
        if not re.fullmatch(r"\d{6}", code):
            return
        # 指数优先（set_benchmark 语义=基准指数；000xxx 与深市个股代码重叠，
        # 静态无法区分 → 按指数处理。契约示例 set_benchmark('000300.SS')）：
        # 000xxx → .SS（上证指数系列：上证指数/沪深300/中证系列）
        # 399xxx → .SZ（深证指数系列）
        # 其余 6 位裸码 → security_code_rules.normalize_to_ptrade（股票/ETF 规则）
        if re.fullmatch(r"000\d{3}", code):
            new_text = repr(f"{code}.SS")
        elif re.fullmatch(r"399\d{3}", code):
            new_text = repr(f"{code}.SZ")
        else:
            from quantstudio.backtest.libs.security_code_rules import normalize_to_ptrade
            new_text = repr(normalize_to_ptrade(code))
        self._replacements.append(
            (arg.lineno, arg.col_offset, arg.end_lineno, arg.end_col_offset, new_text))
        self.actions.append(ConversionAction(
            action_type="NORMALIZE", rule_id="NORM-BENCHMARK-SUFFIX",
            api_name="set_benchmark", line=_line_of(node), severity="WARN",
            old_text=repr(code), new_text=new_text,
            message=f"set_benchmark 裸码 {code} 补后缀（PTrade 契约；本地 bare_code 剥离等价）"))
        self.coverage["normalized_params"] += 1

    # ---- 修复 5：get_stock_status 位置传参 → 关键字 query_type ----
    def _rewrite_stock_status_keywords(self, node: ast.Call) -> None:
        kw_names = {kw.arg for kw in node.keywords if kw.arg}
        if "query_type" not in kw_names and len(node.args) >= 2:
            qtype = node.args[1]
            new_text = f"query_type={ast.unparse(qtype)}"
            self._replacements.append(
                (qtype.lineno, qtype.col_offset, qtype.end_lineno, qtype.end_col_offset,
                 new_text))
            self.actions.append(ConversionAction(
                action_type="REWRITE", rule_id="NORM-STOCKSTATUS-KW",
                api_name="get_stock_status", line=_line_of(node), severity="WARN",
                old_text=ast.unparse(qtype), new_text=new_text,
                message="get_stock_status 位置传参改为关键字 query_type=（PTrade 契约）"))
            self.coverage["normalized_params"] += 1
        # query_date：策略已有该关键字且值为含 '-' 常量 → 转 YYYYmmdd；无则不注入
        for kw in node.keywords:
            if kw.arg == "query_date" and isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, str) and "-" in kw.value.value:
                new_text = repr(kw.value.value.replace("-", ""))
                self._replacements.append(
                    (kw.value.lineno, kw.value.col_offset,
                     kw.value.end_lineno, kw.value.end_col_offset, new_text))
                self.actions.append(ConversionAction(
                    action_type="NORMALIZE", rule_id="NORM-STOCKSTATUS-DATE",
                    api_name="get_stock_status", line=_line_of(node), severity="WARN",
                    old_text=repr(kw.value.value), new_text=new_text,
                    message="get_stock_status query_date 改为 YYYYmmdd（PTrade 契约）"))
                self.coverage["normalized_params"] += 1

    # ---- 修复 6：行情字段 `.values` 访问归一化（返回类型兼容）----
    # 证据（2026-08-13 fall_reversal 平台第二次报错）：
    # - PTrade get_history 返回 numpy structured_array/recarray（非 pandas DataFrame），
    #   平台日志：AttributeError: 'numpy.ndarray' object has no attribute 'values'
    # - 源策略 `df['close'].values` 是 pandas DataFrame 专属写法，直接透传必崩
    # - 契约档案 get_history.return_contract.normalization 要求数值使用前归一化：
    #   "np.asarray(item[field], dtype=float).reshape(-1) or an equivalent
    #    hasattr(values, 'values') guarded helper"
    # 改写：X['col'].values → np.asarray(X['col'])（保持 dtype 语义，两边通用）
    #   - pandas DataFrame：np.asarray(Series) 等价 Series.values（保持 dtype）
    #   - numpy structured array：np.asarray(ndarray) 恒等（保持 dtype）
    # 不匹配场景（安全）：
    #   - .values() 方法调用（dict.values() 等）：node 是 Call 而非 Attribute
    #   - 非字符串下标（x[0].values 等）：下标限定 str 常量或 str 列表
    # 幂等：改写后无 `[...].values` 形态，二次转换不重复处理。
    def _rewrite_values_access(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != "values":
                continue
            sub = node.value
            if not isinstance(sub, ast.Subscript):
                continue
            if isinstance(sub.slice, ast.Constant) and isinstance(sub.slice.value, str):
                pass  # 单列 X['col'].values
            elif isinstance(sub.slice, ast.List) and sub.slice.elts and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in sub.slice.elts):
                pass  # 多列 X[['a','b']].values
            else:
                continue
            new_text = f"np.asarray({ast.unparse(sub)})"
            self._replacements.append(
                (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                 new_text))
            self.actions.append(ConversionAction(
                action_type="NORMALIZE", rule_id="NORM-HIST-VALUES",
                api_name="get_history", line=_line_of(node), severity="WARN",
                old_text=ast.unparse(node), new_text=new_text,
                message="行情字段 .values 访问改为 np.asarray(...)（PTrade get_history 返回"
                        " structured array 非 DataFrame；契约 return_contract.normalization）"))
            self.coverage["normalized_params"] += 1

    # ------------------------------------------------------------------
    # 代码后缀规范化（聚宽风格 XSHG/XSHE/SH → PTrade SS/SZ）
    # ------------------------------------------------------------------
    # 证据（2026-08-11 评估结论 B）：
    # - security_code_rules.py:156,201 —— PTrade 目标输出规范后缀为 .SS（SH/SS/XSHG 同组）
    # - ptrade-profile-contract.md —— "策略代码后缀"为 PTrade 渲染检查项
    # - 本地 index_daily code 为 bare 格式，.SH/.SS 经 bare_code 归一化后等价
    # - T5 逐位断言用于验证规范化后回测数值逐位一致
    _CODE_SUFFIX_RE = re.compile(r"^(\d{6})\.(XSHG|XSHE|SH)$")
    _CODE_SUFFIX_MAP = {"XSHG": "SS", "XSHE": "SZ", "SH": "SS"}

    def _normalize_code_suffixes(self, tree: ast.AST) -> None:
        """把字符串常量中的 6 位代码 XSHG/XSHE 后缀规范化为 SS/SZ（PTrade 约定）。

        背景：本地策略可用聚宽风格后缀（本地引擎 bare_code 规范化可跑，T5 证实）；
        PTrade 公共契约用 .SS/.SZ/.BJ，且 validate_local_strategy 对 XSHG/XSHE
        字符串常量 BLOCK（PORTFOLIO-POSITIONS-EXACT-MATCH）。转换时规范化，
        产物才可通过校验并在 PTrade 平台使用。仅匹配精确 code 形态（6 位数字+后缀），
        不误伤注释/日志文本。
        """
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            val = node.value
            if "." not in val:
                continue
            new_val = self._CODE_SUFFIX_RE.sub(
                lambda m: f"{m.group(1)}.{self._CODE_SUFFIX_MAP[m.group(2)]}", val)
            if new_val == val:
                continue
            self._replacements.append((
                node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                repr(new_val)))
            self.actions.append(ConversionAction(
                action_type="NORMALIZE", rule_id="NORM-CODE-SUFFIX",
                api_name="code_suffix", line=_line_of(node), severity="INFO",
                old_text=val,
                new_text=new_val,
                message=f"代码后缀规范化：{val} → {new_val}（聚宽 XSHG/XSHE → PTrade SS/SZ）"))
            self.coverage["normalized_params"] += 1

    # ------------------------------------------------------------------
    # ETF FREEZE 档（07 规格 §2）：get_etf_list_local → 静态池固化
    # ------------------------------------------------------------------
    def _freeze_etf_pool(self) -> None:
        if not self._freeze_calls:
            return
        # 幂等（07 §6 测试 17）：产物已有静态池 → 不再 FREEZE
        if "ETF_POOL_STATIC" in self._src:
            self.coverage["idempotent_skip"] = True
            return
        if not self._etf_pool_start_date:
            self.errors.append(
                "策略使用 get_etf_list_local，需提供 --etf-pool-start-date 才能固化静态池")
            for node in self._freeze_calls:
                self.actions.append(ConversionAction(
                    action_type="BLOCK", rule_id="FREEZE-MISSING-START-DATE",
                    api_name="get_etf_list_local", line=_line_of(node), severity="BLOCK",
                    old_text="get_etf_list_local(...)",
                    message="需提供 --etf-pool-start-date 才能固化静态池"))
            return
        # 边界检测（07 §2.2 步骤 5）：len(pool) 直接参与时变判断 → BLOCK
        for node in self._freeze_calls:
            parent = self._find_parent(node)
            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) \
                    and parent.func.id == "len":
                self.errors.append(
                    "该策略依赖动态池时变性（len(pool) 直接计算），不适合转 PTrade（无法静态固化）")
                self.actions.append(ConversionAction(
                    action_type="BLOCK", rule_id="FREEZE-LEN-TIMEVARY",
                    api_name="get_etf_list_local", line=_line_of(node), severity="BLOCK",
                    message="len(get_etf_list_local(...)) 依赖池子大小随时间变化，无法静态固化"))
                return
        # 快照查询（前置检查 + DATA_BLOCKED，07 §2.2 步骤 4a）
        pool, meta = self._query_etf_snapshot()
        if pool is None:
            return  # errors 已记录 DATA_BLOCKED
        # 后缀转换（07 §2.3）：.SH → .SS 等 PTrade 约定
        from ..backtest.libs.security_code_rules import normalize_to_ptrade
        ptrade_pool = [normalize_to_ptrade(c) for c in pool]
        # 注入静态池定义（07 §2.2 步骤 4e）
        pool_literal = ",\n        ".join(f'"{c}"' for c in ptrade_pool)
        n = len(ptrade_pool)
        m = meta.get("new_listed_excluded", [])
        k = meta.get("delisted_included", [])
        lines = [
            f"{INJECTED_MARKER}",
            f"# PTrade 静态 ETF 池（起始日 {self._etf_pool_start_date} 快照，共 {n} 只）",
            "# 本地版用 get_etf_list_local 动态池，PTrade 版固化为静态",
            f"# 不含起始日后新上市（{len(m)} 只：{('、'.join(m[:10]) + ('...' if len(m) > 10 else '')) if m else '无'}）",
            f"# 仍含起始日后退市（{len(k)} 只：{('、'.join(k[:10]) + ('...' if len(k) > 10 else '')) if k else '无'}；撮合拒单不影响持仓）",
            "ETF_POOL_STATIC = [",
            pool_literal,
            "]",
        ]
        self._etf_pool_block = "\n".join(lines) + "\n\n"
        self._etf_frozen = True
        self._etf_pool_meta = meta
        # 调用点替换为 ETF_POOL_STATIC（07 §2.2 步骤 4f）
        for node in self._freeze_calls:
            self._replacements.append((
                node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                "ETF_POOL_STATIC"))
            self.actions.append(ConversionAction(
                action_type="FREEZE", rule_id="FREEZE-STATIC-POOL",
                api_name="get_etf_list_local", line=_line_of(node), severity="WARN",
                old_text="get_etf_list_local(...)",
                new_text="ETF_POOL_STATIC",
                message=f"get_etf_list_local() 已固化为静态池 ETF_POOL_STATIC"
                        f"（起始日 {self._etf_pool_start_date} 快照，{n} 只）"))
        # 提示文案（07 §2.4）
        self.warnings.append(
            f"PTrade 版基于回测起始日 {self._etf_pool_start_date} 的 ETF 池快照生成，共 {n} 只。\n"
            f"- 不含起始日后新上市的 ETF（{len(m)} 只）：{('、'.join(m[:10]) + ('...' if len(m) > 10 else '')) if m else '无'}\n"
            f"- 仍含起始日后退市的 ETF（{len(k)} 只）：{('、'.join(k[:10]) + ('...' if len(k) > 10 else '')) if k else '无'}。\n"
            f"  本地 PIT 版会在其退市后自动剔除；PTrade 静态版保留但撮合拒单，实际不持仓。\n"
            f"  如需完全对齐本地版，可手动从池中移除。\n"
            f"- 重要：在 PTrade 平台运行此代码时，回测起始日期不得早于 {self._etf_pool_start_date}。")

    def _query_etf_snapshot(self) -> tuple[Optional[list[str]], dict[str, Any]]:
        """07 §2.2 步骤 4a-4c：前置检查 + PIT 快照 + 差异计算。

        Returns (pool, meta)；pool=None 表示 DATA_BLOCKED（errors 已记录）。
        """
        import duckdb
        import pandas as pd
        db_path = Path(self._db_path) if self._db_path else Path("data/quantstudio.db")
        if not db_path.exists():
            self.errors.append(f"DATA_BLOCKED: db_path 不存在: {db_path}")
            return None, {}
        try:
            conn = duckdb.connect(str(db_path), read_only=True)
        except Exception as e:
            self.errors.append(f"DATA_BLOCKED: 无法打开 {db_path}: {e}")
            return None, {}
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'").fetchall()}
            missing = {"etf_basic", "etf_daily"} - tables
            if missing:
                self.errors.append(
                    f"DATA_BLOCKED: 缺表 {sorted(missing)}（请运行 scripts/sync_etf_basic.py 后重试）")
                return None, {}
            start_ms = int(pd.Timestamp(self._etf_pool_start_date).value // 10**6)
            # 全量元数据（供差异计算）
            rows = conn.execute(
                "SELECT code, list_date, delist_date, etf_type, is_cross_border "
                "FROM etf_basic").fetchall()
            # 起始日 PIT 快照（07 §1.2 SQL 语义 + data_access equity 过滤）
            type_pred = ("e.etf_type = ? AND COALESCE(e.is_cross_border, FALSE) = FALSE"
                         if self._etf_type == "equity"
                         else ("e.etf_type = ?" if self._etf_type != "all" else "TRUE"))
            # 参数顺序必须与 SQL 谓词出现顺序一致：
            # list_date(1) + EXISTS(1) + active(1) + type(1)
            params: list[Any] = [start_ms, start_ms]
            if self._active_only:
                params.append(start_ms)
            if self._etf_type != "all":
                params.append(self._etf_type)
            active_pred = ("(e.delist_date IS NULL OR e.delist_date > ?)" if self._active_only
                           else "TRUE")
            sql = f"""
                SELECT e.code FROM etf_basic e
                WHERE e.list_date IS NOT NULL
                  AND e.list_date <= ?
                  AND EXISTS (SELECT 1 FROM etf_daily d WHERE d.code = e.code AND d.time <= ?)
                  AND {active_pred}
                  AND {type_pred}
                ORDER BY e.code
            """
            pool = sorted(r[0] for r in conn.execute(sql, params).fetchall())
        except Exception as e:
            self.errors.append(f"DATA_BLOCKED: etf_basic 查询失败: {e}")
            return None, {}
        finally:
            conn.close()
        if not pool:
            self.errors.append(
                f"DATA_BLOCKED: 起始日 {self._etf_pool_start_date} 的 ETF 快照为空"
                f"（etf_basic/etf_daily 数据不足，不得退化为全 ETF 兜底）")
            return None, {}
        # 差异计算（07 §2.2 步骤 4c）
        pool_set = set(pool)
        new_listed = sorted(c for c, ld, dd, et, cb in rows
                            if ld is not None and ld > start_ms)
        delisted = sorted(c for c, ld, dd, et, cb in rows
                          if c in pool_set and dd is not None and dd > start_ms)
        meta = {"new_listed_excluded": new_listed, "delisted_included": delisted}
        return pool, meta

    # ------------------------------------------------------------------
    # 注入（helper / shim / MyTT / A股规则 / ETF 静态池）
    # ------------------------------------------------------------------
    def _inject_all(self, code: str) -> str:
        # 幂等：产物已有注入标记则跳过（测试 9）
        if INJECTED_MARKER in code:
            self.coverage["idempotent_skip"] = True
            return code
        blocks: list[str] = []
        # ETF 静态池（07 规格：FREEZE 固化产物，放在注入块最前）
        if self._etf_pool_block:
            blocks.append(self._etf_pool_block)
            self.coverage["injected_helpers"].append("ETF_POOL_STATIC")
        # helper 总是注入（防御函数，模板同款）
        blocks.append(_PTRADE_HELPERS.format(marker=INJECTED_MARKER))
        blocks.append(_QS_COMMON_EXT.format(marker_common=INJECTED_MARKER))
        self.coverage["injected_helpers"].append("_qs_g_obj")
        self.coverage["injected_helpers"].extend(
            ["_lookup_history_item", "_extract_history_field", "_bare_code",
             "_finite_float", "_finite_series", "_get_ma", "_portfolio_total_value"])
        # 方向 B：get_history 返回类型统一（structured array → DataFrame）。
        # 注入顺序：helper → wrapper → shim → 策略 def →
        #  - wrapper 在 shim 之前 → shim 内部调 get_history 已是 wrapper 版本 → 返回 DataFrame
        #  - wrapper 在策略 def 之前 → 策略 handle_data 调 get_history 已是 wrapper 版本
        blocks.append(_QS_HISTORY_WRAPPER.format(marker=INJECTED_MARKER))
        self.coverage["injected_helpers"].append("get_history_wrapper")
        # trade_date 门控扩展：仅当源策略显式使用 trade_date 时追加（旧 wrapper 保持逐字节不变）
        if _source_uses_trade_date(code):
            blocks.append(_QS_HISTORY_TRADE_DATE_EXT.format(marker=INJECTED_MARKER))
            self.coverage["injected_helpers"].append("trade_date_synth")
        # 市价单拆单门控扩展（A1）：仅当源策略调用订单 API 时注入（无订单 = 逐字节不变）
        _order_ext_injected = False
        if _source_uses_order_api(code):
            blocks.append(_QS_ORDER_SPLIT_EXT.format(marker=INJECTED_MARKER))
            _order_ext_injected = True
            self.coverage["injected_helpers"].extend(
                ["order_split", "_qs_split_order", "order_target_value_wrapper"])
        # 日期归一化门控扩展（A3）：仅当源策略调用 get_trade_days/get_stock_info 时注入
        if _source_uses_date_api(code):
            blocks.append(_QS_DATE_NORM_EXT.format(marker=INJECTED_MARKER))
            self.coverage["injected_helpers"].extend(
                ["date_norm", "get_trade_days_wrapper", "get_stock_info_wrapper"])
        # P-D9 退市风险兜底门控扩展：仅当源策略调用 filter_stock_by_status 时注入
        if _source_uses_filter_status(code):
            blocks.append(_QS_FILTER_STATUS_EXT.format(marker=INJECTED_MARKER))
            self.coverage["injected_helpers"].extend(
                ["filter_status_norm", "_qs_is_delisting_risk",
                 "filter_stock_by_status_wrapper"])
        # P-D10 契约对齐门控扩展：源策略调用 get_fundamentals/get_fundamentals_batch 时注入
        # （在 shim 之前 → shim 内部调 get_fundamentals 已是 wrapper 版本 → 返回本地契约 DataFrame）
        # v8.3 整合：eps 常量与映射烘焙进 _QS_FUNDAMENTALS_EXT（唯一 wrapper、唯一
        # get_fundamentals 定义）——旧独立 _QS_FIDELITY_EPS_EXT def 覆盖（v8.0~v8.2
        # 平台复验总根因）已删除，此处不再二次注入。
        if _source_uses_fundamentals(code):
            blocks.append(_QS_FUNDAMENTALS_EXT.format(
                marker=INJECTED_MARKER, eps_basis=self._fidelity_eps_basis))
            self.coverage["injected_helpers"].extend(
                ["fundamentals_wrapper", "_qs_norm_fund_dates",
                 "_qs_fund_select_fields", "get_fundamentals_wrapper",
                 "_qs_gf_maybe_prefetch", "_qs_gf_auto_cache_get",
                 "_qs_gf_pit_filter", "_qs_equity_probe",
                 "_qs_multi_flat", "_qs_pit_filter", "_QS_GF_GAP_SEEDS"])
        # B1 get_industry 平台替代门控扩展：源策略调用 get_industry 时注入
        # （反向金融池双码 + fail-open；行业码集从策略源码烘焙，框架层通用）
        if _source_uses_industry_api(code):
            _ind_codes = _extract_industry_codes(code)
            blocks.append(_render_industry_ext(INJECTED_MARKER, _ind_codes))
            self.coverage["injected_helpers"].extend(
                ["industry_wrapper", "_qs_finance_pool", "get_industry_wrapper"])
            if not _ind_codes:
                self.warnings.append(
                    "B1 行业码集未从策略源码提取到（无 6 位数字引号字面量）——"
                    "get_industry 平台替代池为空，行业剔除降级 fail-open（RD-3 登记）；"
                    "请确认策略行业剔除判定含 6 位行业码字面量")
        # P-D11 持仓视图归一门控扩展：源策略调用 get_positions/get_position 时注入
        # （键 .SS/.SZ/.BJ 归一 + 残影过滤 + 本地 Position 契约视图；设计 v1.2 审定）
        if _source_uses_position_api(code):
            blocks.append(_render_position_view_ext(INJECTED_MARKER))
            self.coverage["injected_helpers"].extend(
                ["position_view", "_qs_norm_code", "_qs_pos_sid_key",
                 "_QSPositionView", "get_positions_wrapper",
                 "get_position_wrapper"])
        # P-D13 数据层审计门控扩展：源策略调用 get_Ashares 时注入
        # （C1a 板块统计 + C1b exclude_bse + C3a 窗口审计；设计审计通过 2026-08-27）
        if _source_uses_ashares_api(code):
            blocks.append(_render_data_audit_ext(
                INJECTED_MARKER, exclude_bse=self._exclude_bse))
            self.coverage["injected_helpers"].extend(
                ["data_audit", "QS_ASHARES_BREAKDOWN", "exclude_bse"])
        for shim_name in sorted(self._need_shim):
            blocks.append(self._shim_source(shim_name))
            self.coverage["injected_helpers"].append(f"shim:{shim_name}")
        if self._mytt_needed:
            mytt_src = self._extract_lib_functions("MyTT", self._mytt_needed, "_mytt_")
            if mytt_src:
                blocks.append(mytt_src)
                self.coverage["inject_libs"].append("MyTT")
                self.coverage["injected_helpers"].extend(
                    f"_mytt_{n}" for n in sorted(self._mytt_needed))
                self.warnings.append(
                    "已注入 MyTT 函数（_mytt_ 前缀，用到才注入）：%s；"
                    "该策略不在 1:1 复刻承诺内（MyTT 在 PTrade 平台行为需验证）"
                    % ", ".join(sorted(self._mytt_needed)))
        if self._ashare_needed:
            ashare_src = self._extract_lib_functions("ashare", self._ashare_needed, "_ashare_")
            if ashare_src:
                blocks.append(ashare_src)
                self.coverage["inject_libs"].append("shared_ashare_rules")
                self.coverage["injected_helpers"].extend(
                    f"_ashare_{n}" for n in sorted(self._ashare_needed))
                self.warnings.append(
                    "已注入 A股规则函数（_ashare_ 前缀）：%s；"
                    "该策略不在 1:1 复刻承诺内"
                    % ", ".join(sorted(self._ashare_needed)))
        if not blocks:
            return code
        injected = "\n".join(blocks) + "\n\n"
        out = self._insert_before_first_def(code, injected)
        # D4-S7 #5 接线（ZCode 方案 A，2026-08-27）：订单扩展注入时，在 handle_data 入口
        # 注入 _qs_capture_ctx(context)。机械安全：仅当存在 def handle_data 且签名含 context
        # 时注入一行；任何不匹配（无 handle_data / 签名异常 / 多重歧义）→ 不注入 + 告警，
        # 钳制沿用 fail-open（与不接线等价的现状），禁止半注入。
        if _order_ext_injected:
            out = self._inject_handle_data_capture(out)
        return out

    def _inject_handle_data_capture(self, code: str) -> str:
        """D4-S7 #5：在 handle_data 入口注入 _qs_capture_ctx(context)（方案 A 机械安全门控）。"""
        import re as _re
        # 匹配 def handle_data(context, data): 或带任意参数但首参位 context 的签名
        _m = _re.search(
            r'^def handle_data\(\s*(context)\s*[,)]', code, _re.MULTILINE)
        if not _m:
            self.warnings.append(
                "D4-S7 #5: handle_data 入口捕获未注入（无 def handle_data(context,...)"
                "——钳制沿用 fail-open，QS_CASH_AVAIL_UNAVAILABLE 一次性告警不变）")
            return code
        # 定位函数体首行（def 行后第一个缩进行）
        _def_end = code.index('\n', _m.start())
        _body_prefix = '\n'
        _body = code[_def_end + 1:]
        _m2 = _re.match(r'(\s+)\S', _body)
        if not _m2:
            self.warnings.append(
                "D4-S7 #5: handle_data 函数体为空/异常——不注入（fail-open）")
            return code
        _indent = _m2.group(1)
        _capture_line = _indent + '_qs_capture_ctx(context)\n'
        # 幂等：已有捕获行则跳过
        if '_qs_capture_ctx(context)' in _body.split('\n', 1)[0]:
            return code
        out = code[:_def_end + 1] + _capture_line + _body
        self.coverage["injected_helpers"].append("handle_data_capture_ctx")
        return out

    def _shim_source(self, name: str) -> str:
        if name == "get_history_batch":
            return f'''{INJECTED_MARKER}
def get_history_batch(security_list, count, unit='1d', fields=None, fq='pre',
                      include=False, is_dict=True, **kwargs):
    """SHIM: 本地批量 API → 循环单调用（PTrade 兼容；返回 code→DataFrame 字典）。

    与原生 B1 实现语义一致：is_dict=True 的 get_history 返回 code→DataFrame 字典，
    此处解包出 DataFrame 再按 code 组装（T5 修复：禁止把整个 CodeDict 存入 result）。"""
    result = {{}}
    for code in security_list:
        try:
            df_dict = get_history(count, frequency=unit, field=fields,
                                  security_list=code, fq=fq, include=include,
                                  is_dict=True)
            if isinstance(df_dict, dict):
                for k, df in df_dict.items():
                    result[k] = df
        except Exception as exc:
            log.warning('get_history_batch skip %s: %s' % (code, exc))
    _qs_shape_check('get_history_batch', 'dict', result)
    return result
'''
        if name == "get_fundamentals_batch":
            return f'''{INJECTED_MARKER}
def get_fundamentals_batch(security_list, table='valuation', fields=None,
                           date=None, is_dataframe=True, **kwargs):
    """SHIM: 本地批量 API → 平台原生 list 单调用（P-D10）。

    返回合并 DataFrame（index=code, columns=fields），与本地 B1 契约
    （ptrade_api.py:1421-1442）逐字段一致；委托 get_fundamentals wrapper
    （列筛选 + end_date/publ_date 数值归一 + QS_SHIM_FIELD_MISSING 显性警报）。"""
    result = get_fundamentals(security_list, table, fields=fields, date=date,
                              is_dataframe=is_dataframe, **kwargs)
    _qs_shape_check('get_fundamentals_batch', 'dataframe', result)
    return result
'''
        return ""

    def _extract_lib_functions(self, lib: str, needed: set[str], prefix: str) -> str:
        """从 MyTT.py / shared_ashare_rules.py 提取用到的函数源码（含递归依赖），
        统一加 prefix 前缀（含函数体内互相调用，D1 约束②）。"""
        if lib == "MyTT":
            lib_path = Path(__file__).resolve().parents[1] / "backtest" / "libs" / "MyTT.py"
        else:
            lib_path = Path(__file__).resolve().parents[1] / "backtest" / "libs" / "shared_ashare_rules.py"
        try:
            src = lib_path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:
            self.errors.append(f"注入库读取失败: {lib_path}")
            return ""
        funcs: dict[str, ast.FunctionDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                funcs[node.name] = node
        # 递归收集依赖（ashare 依赖 security_code_rules 的 4 个判定函数）
        todo = set(needed)
        collected: set[str] = set()
        while todo:
            name = todo.pop()
            if name in collected or name not in funcs:
                continue
            collected.add(name)
            body_src = ast.get_source_segment(src, funcs[name]) or ""
            # 递归依赖：函数体内调用的同库函数名
            for m in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body_src):
                if m in funcs and m not in collected and m != name:
                    todo.add(m)
        # ashare 附加依赖：从 security_code_rules.py 提取判定函数（跨文件依赖）
        if lib == "ashare":
            if collected & {"get_price_limit_pct", "is_price_limit_blocked"}:
                for extra in ("is_bse_market", "is_chinext_market", "is_star_market", "is_st_stock"):
                    if extra not in collected:
                        collected.add(extra)
        # 提取源码（按文件顺序）并重命名：函数名 + 函数体内调用名
        order = [n for n in funcs if n in collected]
        parts: list[str] = []
        for n in order:
            seg = ast.get_source_segment(src, funcs[n]) or ""
            seg = re.sub(rf"\b{n}\b", f"{prefix}{n}", seg, count=1)
            # 函数体内对同库已收集函数的调用加前缀
            for dep in collected:
                seg = re.sub(rf"\b{dep}\s*\(", f"{prefix}{dep}(", seg)
            parts.append(seg)
        # ashare：security_code_rules 的 4 个判定函数（无前缀，避免与平台 API 冲突即可；
        # 但需保证其内部无本地依赖——security_code_rules 为纯代码判定，安全）
        if lib == "ashare" and collected & {"is_bse_market", "is_chinext_market",
                                              "is_star_market", "is_st_stock"}:
            scr_path = Path(__file__).resolve().parents[1] / "backtest" / "libs" / "security_code_rules.py"
            try:
                scr_src = scr_path.read_text(encoding="utf-8")
                scr_tree = ast.parse(scr_src)
                for node in ast.walk(scr_tree):
                    if isinstance(node, ast.FunctionDef) and node.name in collected:
                        seg = ast.get_source_segment(scr_src, node) or ""
                        parts.append(seg)
            except Exception:
                self.errors.append(f"security_code_rules 提取失败: {scr_path}")
        header = f"{INJECTED_MARKER}\n# 注入自 {lib_path.name}（{prefix}前缀，仅本策略用到的函数）\n"
        return header + "\n\n".join(parts) + "\n"

    def _insert_before_first_def(self, code: str, block: str) -> str:
        """最后一个 import 之后、第一个 def 之前插入（02 规格 §2 步骤 7 细化）。"""
        lines = code.splitlines(keepends=True)
        last_import = -1
        first_def = -1
        for i, ln in enumerate(lines):
            stripped = ln.lstrip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                last_import = i
            if stripped.startswith("def ") or stripped.startswith("async def "):
                first_def = i
                break
        if first_def == -1:
            return code + "\n" + block
        insert_at = first_def if last_import < 0 else last_import + 1
        lines.insert(insert_at, block)
        return "".join(lines)

    # ------------------------------------------------------------------
    # 头部
    # ------------------------------------------------------------------
    def _build_header(self, source_path: Optional[str]) -> str:
        src_name = Path(source_path).name if source_path else "unknown"
        sid = self.strategy_id or (
            src_name.replace(".py", "").replace("_quantstudio", "") if source_path else "strategy")
        diffs = []
        for a in self.actions:
            if a.severity in ("WARN", "BLOCK"):
                diffs.append(f"# - {a.api_name}: {a.message}\n")
        fq_note = ""
        if self.coverage["fq_warn_kept"]:
            fq_note = ("\n# 注意：fq 参数存在 WARN_KEEP 项（%s），该策略不满足 1:1 复刻"
                       % ", ".join(self.coverage["fq_warn_kept"]))
        mytt_note = ""
        if self.coverage["inject_libs"]:
            mytt_note = ("\n# 注意：已注入本地库（%s），该策略不在 1:1 复刻承诺内"
                         % ", ".join(self.coverage["inject_libs"]))
        return (
            f"# {sid}_ptrade.py - 由 QuantStudio source_import 转换生成\n"
            f"# 来源: {src_name}\n"
            f"# profile: {PTRADE_PROFILE_MARKER} (ptrade_profile_version 1.1.0-source-import)\n"
            f"# 已知差异:\n"
            + ("".join(diffs) if diffs else "#   (无)\n")
            + f"# PTRADE_RUNTIME_UNVERIFIED: 真实券商平台行为未验证，部署前须人工冒烟。\n"
            + fq_note + mytt_note + "\n\n"
        )


# ============================================================================
# 公开 API
# ============================================================================


def convert_source(
    source_path: str | Path,
    *,
    strategy_id: str | None = None,
    inject_helpers: bool = True,
    verbose: bool = True,
    etf_pool_start_date: str | None = None,   # 07 规格：ETF 静态池固化起始日 "YYYY-MM-DD"
    db_path: str | Path | None = None,        # 07 规格：查 etf_basic 的库路径（默认 data/quantstudio.db）
    etf_type: str = "equity",
    active_only: bool = True,
    exclude_bse: bool = False,                # P-D13 C1b：北交所过滤（对齐平台口径）
) -> SourceImportResult:
    """把本地策略 .py 转换为 PTrade 代码。不写盘（写盘由编排层负责）。

    etf_pool_start_date：策略含 get_etf_list_local 时必须提供，否则 BLOCK
    （07-ETF动态池固化补充规格.md §2）。
    """
    path = Path(source_path)
    # N1：统一 utf-8-sig（BOM 文件兼容，小市值策略ptrade.py 实锤）
    try:
        source_code = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            source_code = path.read_text(encoding="gbk")
        except UnicodeDecodeError as e:
            result = SourceImportResult(converted_code="", errors=[f"文件编码无法识别: {e}"])
            return result
    if strategy_id is None:
        strategy_id = path.stem.replace("_quantstudio", "")
    conv = SourceConverter(
        strategy_id=strategy_id, inject_helpers=inject_helpers, verbose=verbose,
        etf_pool_start_date=etf_pool_start_date,
        db_path=str(db_path) if db_path else None,
        etf_type=etf_type, active_only=active_only,
        exclude_bse=exclude_bse,
    )
    return conv.convert(source_code, source_path=str(path))
