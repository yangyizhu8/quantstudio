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

def _qs_to_dataframe(item):
    """structured array → DataFrame；已是 DataFrame/其他类型则原样返回。"""
    if isinstance(item, _qs_np.ndarray) and hasattr(item, 'dtype') and hasattr(item.dtype, 'names'):
        df = _qs_pd.DataFrame(item)
        # 列名统一映射：PTrade → 本地（datetime→time / money→amount / preclose→preClose）
        _rename = {{k: v for k, v in _QS_COL_TO_LOCAL.items()
                    if k in df.columns and v not in df.columns}}
        if _rename:
            df = df.rename(columns=_rename)
        return df
    return item

# 保存原始 get_history 引用：类属性承载（属性调用不被静态 API 白名单拦截，
# 模块级别名函数 _qs_original_get_history(...) 会被 validate_local_strategy 判 BLOCK）
class _QSHistoryState:
    orig = None

_QSHistoryState.orig = get_history

# 重新绑定 get_history：请求前字段名映射（本地 → PTrade）+ 返回转 DataFrame
def get_history(*args, **kwargs):
    _field = kwargs.get('field') or kwargs.get('fields')
    if _field:
        _is_list = isinstance(_field, list)
        _items = _field if _is_list else [_field]
        _mapped = [_QS_FIELD_TO_PTRADE.get(f, f) for f in _items]
        if 'field' in kwargs:
            kwargs['field'] = _mapped if _is_list else _mapped[0]
        if 'fields' in kwargs:
            kwargs['fields'] = _mapped if _is_list else _mapped[0]
    _result = _QSHistoryState.orig(*args, **kwargs)
    if isinstance(_result, dict):
        return {{k: _qs_to_dataframe(v) for k, v in _result.items()}}
    return _qs_to_dataframe(_result)
'''

# source_import trade_date 门控扩展（2026-08-19，框架层方案 source_import-ptrade-history-translation-design.md）
# 仅当源策略显式使用 trade_date（本地 provider 合成伪列）时注入本扩展；不使用 trade_date 的
# 策略转换输出与改造前逐字节一致（纯增益）。扩展重定义 _qs_to_dataframe（改名后再合成 trade_date，
# R3 格式以本地实测为准：object、'YYYY-MM-DD'、Asia/Shanghai strftime）与 get_history
# （请求侧剔除合成字段 trade_date、is_dict=True 走逐码路径——契约无关，R1）。
_QS_HISTORY_TRADE_DATE_EXT = '''
{marker}
# [qs-import-generated] trade_date 合成 + is_dict 逐码（source_import 门控扩展，2026-08-19）
# trade_date 是本地 provider 合成伪列（object 类型 'YYYY-MM-DD' 字符串）；PTrade 无此字段，
# 由返回体的 datetime/time 派生。请求侧剔除该合成字段（只传真实字段），返回侧补齐。
_QS_SYNTHETIC_FIELDS = {{'trade_date'}}


def _qs_synthesize_trade_date(df):
    if 'trade_date' in df.columns:
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


def _qs_to_dataframe(item):
    if isinstance(item, _qs_np.ndarray) and hasattr(item, 'dtype') and hasattr(item.dtype, 'names'):
        df = _qs_pd.DataFrame(item)
        _rename = {{k: v for k, v in _QS_COL_TO_LOCAL.items()
                    if k in df.columns and v not in df.columns}}
        if _rename:
            df = df.rename(columns=_rename)
        # R3：先改名（datetime→time），再合成 trade_date
        return _qs_synthesize_trade_date(df)
    if isinstance(item, _qs_pd.DataFrame):
        return _qs_synthesize_trade_date(item)
    return item


def get_history(*args, **kwargs):
    _field = kwargs.get('field') or kwargs.get('fields')
    if _field:
        _is_list = isinstance(_field, list)
        _items = _field if _is_list else [_field]
        _mapped = [_QS_FIELD_TO_PTRADE.get(f, f) for f in _items
                   if f not in _QS_SYNTHETIC_FIELDS]
        if not _mapped:
            _mapped = ['close']
        if 'field' in kwargs:
            kwargs['field'] = _mapped if _is_list else _mapped[0]
        if 'fields' in kwargs:
            kwargs['fields'] = _mapped if _is_list else _mapped[0]
    _is_dict = bool(kwargs.pop('is_dict', False))
    _secs = kwargs.pop('security_list', None)
    if _secs is None:
        _secs = kwargs.pop('security', None)
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
        return {{k: _qs_to_dataframe(v) for k, v in _result.items()}}
    return _qs_to_dataframe(_result)
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
_QS_SPLIT_PX_BUFFER = 0.95        # 整手可负担预筛缓冲（执行价上浮 5% 仍可建仓）


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
_QSOrderRefState.target_percent_orig = order_target_percent
_QSOrderRefState.percent_orig = order_percent


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
        # 不超限：保持单笔（含整手取整，语义与不注入版一致）
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


def order_target_value(security, value, *args, **kwargs):
    _px = _qs_last_close_lookup(security)
    orders, _tot = _qs_split_order(security, value, _px)
    if not orders:
        return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
    _ids = []
    for _code, _amt in orders:
        _ids.append(order(_code, _amt))
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
    """order_value 拆单包装：金额语义 → 统一链 ① 层 px 拆（与 order_target_value 同链路）。"""
    _px = _qs_last_close_lookup(security)
    orders, _tot = _qs_split_order(security, value, _px)
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
                 active_only: bool = True):
        self.strategy_id = strategy_id
        self.inject_helpers = inject_helpers
        self.verbose = verbose
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
        if _source_uses_order_api(code):
            blocks.append(_QS_ORDER_SPLIT_EXT.format(marker=INJECTED_MARKER))
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
        return self._insert_before_first_def(code, injected)

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
    return result
'''
        if name == "get_fundamentals_batch":
            return f'''{INJECTED_MARKER}
def get_fundamentals_batch(security_list, table='valuation', fields=None,
                           date=None, is_dataframe=True, **kwargs):
    """SHIM: 本地批量 API → 循环单调用（PTrade 兼容；返回 dict[code→DataFrame]）"""
    result = {{}}
    for code in security_list:
        try:
            df = get_fundamentals(code, table, fields=fields, date=date,
                                  is_dataframe=is_dataframe)
            result[code] = df
        except Exception as exc:
            log.warning('get_fundamentals_batch skip %s: %s' % (code, exc))
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
    )
    return conv.convert(source_code, source_path=str(path))
