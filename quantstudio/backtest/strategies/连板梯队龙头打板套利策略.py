# strategy_ptrade.py - 由 QuantStudio source_import 转换生成
# 来源: strategy.py
# profile: ptrade-default (ptrade_profile_version 1.1.0-source-import)
# 已知差异:
# - get_history_batch: get_history_batch() 为本地批量 API，将注入同名 shim（循环单调用）
# - get_history: get_history 签名 A→B（count-first，PTrade 契约；本地双签名兼容）
# - get_history: get_history 签名 A→B（count-first，PTrade 契约；本地双签名兼容）
# PTRADE_RUNTIME_UNVERIFIED: 真实券商平台行为未验证，部署前须人工冒烟。


# -*- coding: utf-8 -*-
"""
连板梯队龙头打板套利策略（lbdt_dalong）.py - agent-authored QuantStudio-only strategy.

Chinese published filename: 连板梯队龙头打板套利策略.py (quantstudio/backtest/strategies/<strategy_name>.py).

design 2.2（R2.5 客户确认 2026-08-31）；minute-bar-v1 / close 撮合 / T+1 引擎强制。
窗口 2026-06-15~2026-08-13（stock_minutes 覆盖，C1-M 裁决）。
四维筛选（量能/板块热度/市场情绪/梯队生态）→ 二进三打板（09:30-10:00 分钟窗口）→ T+1 开板/止损卖出。
"""

STRATEGY_ID = 'lbdt_dalong'
STRATEGY_NAME = '连板梯队龙头打板套利策略'
DESIGN_VERSION = '2.2'

import numpy as np
import pandas as pd

# [qs-import-generated]
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


# [qs-import-generated]
def _qs_g_obj():
    """平台全局 g 安全引用（策略平台 g 为全局 API；本地/测试无 g → None 不炸）。"""
    try:
        return g
    except NameError:
        return None


# [qs-import-generated]
# 方向 B（2026-08-13 平台实证）：PTrade get_history 返回 numpy structured array，
# 统一转为 DataFrame（PTrade pandas 1.5.3 可用）。策略代码可用全部 pandas API。
# 字段名中枢（2026-08-13 平台实证 etf_theme_rotation：invalid field ['amount'],
# valid fields 含 'money' 无 'amount'（成交额）；preClose → preclose 前收大小写）：
# 请求时 本地 → PTrade，返回时 PTrade → 本地，双向映射策略代码零改动。
import pandas as _qs_pd
import numpy as _qs_np

# 本地字段名 → PTrade 字段名（请求时映射）
_QS_FIELD_TO_PTRADE = {
    'amount': 'money',
    'preClose': 'preclose',
}
# PTrade 列名 → 本地列名（返回时映射，含 datetime→time）
_QS_COL_TO_LOCAL = {
    'datetime': 'time',
    'money': 'amount',
    'preclose': 'preClose',
}
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
        _rename = {k: v for k, v in _QS_COL_TO_LOCAL.items()
                    if k in _df.columns and v not in _df.columns}
        if _rename:
            _df = _df.rename(columns=_rename)
        # pctChg 合成（2026-09-01 平台实证回归）：请求含 pctChg 且返回无此列时，
        # 由 close/preClose 基列合成 (close/preClose−1)×100；fail-soft（异常/缺基列→不合成）。
        if _QS_REQ_PCT and 'pctChg' not in _df.columns                 and 'close' in _df.columns and 'preClose' in _df.columns:
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
_QS_PREC_CACHE = {}


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
    if df is None or not isinstance(df, _qs_pd.DataFrame) or "preClose" in df.columns             or "close" not in df.columns or len(df) == 0:
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
            if _dd is None or not isinstance(_dd, _qs_pd.DataFrame)                     or len(_dd) == 0 or "close" not in _dd.columns:
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
        _out = {}
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
            _out = {k: _qs_to_dataframe(v) for k, v in _result.items()}
        else:
            _out = _qs_to_dataframe(_result)
    else:
        _result = _QSHistoryState.orig(*args, **kwargs)
        if isinstance(_result, dict):
            _out = {k: _qs_to_dataframe(v) for k, v in _result.items()}
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


# [qs-import-generated]
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
_QS_CASH_WARNED_STATE = {'v': False}
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

    缓存格式 {bare_code: (day, close)}：返回当日记录的最近日均线 close；
    跨日由记录 hook 的 stamp 校验自动失效（PIT 纪律）。
    """
    try:
        cache = _QSLastCloseState.cache or {}
        v = cache.get(_qs_bare(str(code)), 0.0)
        if isinstance(v, (tuple, list)) and len(v) == 2:
            return float(v[1])
        if v and v > 0:
            return float(v)
    except Exception:
        pass
    return 0.0


class _QSLastCloseState:
    cache = None  # {code: (day, close)}；stamp = 最近记录交易日（PIT：跨日失效）
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
                _QSLastCloseState.cache = {}
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


# [qs-import-generated]
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

# 转换期从策略源码烘焙的行业码集（() 占位，渲染后替换）
_QS_INDUSTRY_CODES = ()


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
    """平台替代：返回本地 get_industry 契约（{'sw_l1': {'industry_code': ...}}）。
    - 池有效：code ∈ 池 → 首个策略行业码（∈ 策略剔除集 → 剔）；否则 → '999999'
      （非金融哨兵，∉ 策略行业集 → 不剔，fail-open）；
    - 池无效：回退原生 get_industry（如有）；无 → '999999' 不剔 + 一次性告警
      QS_INDUSTRY_UNAVAILABLE（RD-3 降级登记，防本地 fail-closed 全剔空仓）。"""
    _g = _qs_g_obj()
    _pool, _valid = _qs_finance_pool()
    if _valid:
        if str(code) in _pool:
            _hit = _QS_INDUSTRY_CODES[0] if _QS_INDUSTRY_CODES else '999999'
            return {'sw_l1': {'industry_code': _hit}}
        return {'sw_l1': {'industry_code': '999999'}}
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
    return {'sw_l1': {'industry_code': '999999'}}


# [qs-import-generated]
# [qs-import-generated] P-D13 数据层审计注入（C1a/C1b/C3a，2026-08-27）
# C1b：北交所过滤开关（默认 False 保持平台全 A 行为；对齐本地口径时设 True）
_QS_EXCLUDE_BSE = False


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

# [qs-import-generated]
def get_history_batch(security_list, count, unit='1d', fields=None, fq='pre',
                      include=False, is_dict=True, **kwargs):
    """SHIM: 本地批量 API → 循环单调用（PTrade 兼容；返回 code→DataFrame 字典）。

    与原生 B1 实现语义一致：is_dict=True 的 get_history 返回 code→DataFrame 字典，
    此处解包出 DataFrame 再按 code 组装（T5 修复：禁止把整个 CodeDict 存入 result）。"""
    result = {}
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



# ---- 参数（客户指定常规值，零寻优）----
MAX_HOLDINGS = 2
POSITION_WEIGHT = 0.50
STOP_LOSS_PCT = 0.95
VOL_RATIO_GE2 = 1.20
VOL_RATIO_5D = 1.50
SECTOR_ZT_MIN = 3
SECTOR_RANK_TOP = 3
MKT_ZT_MIN = 40
PROMO_RATE_MIN = 0.15
ZP_RATE_MAX = 0.35
MAX_LADDER_MIN = 2          # 最高连板高度 >= 2（2026-08-31 客户裁决 3->2；弱市空仓纪律保留）
PLAY_END = "10:00"


def _ensure_runtime_state():
    """幂等创建 g 字段（任何 callback 首行调用；hasattr 保护不重置既有状态）。"""
    for k, v in [("prepped", False), ("candidates", []), ("sentiment_ok", False),
                 ("port", {}), ("pending_reb", []), ("day_did", ""),
                 ("trade_days", []), ("zt_counts", {"total": 0, "promo": 0.0,
                                                    "zp_rate": 0.0, "max_ladder": 0}),
                 ("sector_zt", {}), ("sector_gain", {}), ("prev_close", {}),
                 ("day_codes", [])]:
        if not hasattr(g, k):
            setattr(g, k, v)


def _extract_history_field(history_item, field, dtype=float):
    """get_history(is_dict=True) 返回项字段归一（DataFrame / structured array / recarray 兼容）。"""
    if history_item is None:
        return None
    try:
        col = history_item[field]
    except Exception:
        return None
    if col is None:
        return None
    if hasattr(col, "values"):
        col = col.values
    return np.asarray(col, dtype=dtype)


def _limit_threshold(code, is_st=False):
    """涨停阈值近似：ST 5%、688/689 与 300/301 20%、北交(8/4/92) 30%、其余 10%。"""
    c6 = code.split(".")[0] if code else ""
    if is_st:
        return 1.05
    if c6.startswith(("688", "689", "300", "301")):
        return 1.20
    if c6.startswith(("8", "4", "92")):
        return 1.30
    return 1.10


def _is_limit_up(pct_chg, code, is_st=False):
    if pct_chg is None or pct_chg != pct_chg:
        return False
    return pct_chg + 0.01 >= (_limit_threshold(code, is_st) - 1.0) * 100.0


def _limit_price(prev_close, code, is_st=False):
    if prev_close is None or prev_close != prev_close:
        return None
    return round(float(prev_close) * _limit_threshold(code, is_st), 2)


def _is_one_line(row):
    o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
    if None in (o, h, l, c) or any(x != x for x in (o, h, l, c)):
        return False
    return abs(float(o) - float(h)) < 1e-6 and abs(float(h) - float(l)) < 1e-6 \
        and abs(float(l) - float(c)) < 1e-6


def _ind_l1(ind):
    """get_industry 返回 {'sw_l1': {'industry_code': '801780', ...}}（2026-09-01 探针实证）。
    安全取 L1 行业码；非预期结构返回 None（该码不参与板块滤网）。"""
    try:
        if not isinstance(ind, dict):
            return None
        sw = ind.get("sw_l1") or {}
        code = sw.get("industry_code") if isinstance(sw, dict) else None
        return str(code) if code else None
    except Exception:
        return None


def initialize(context):
    """首语句 _ensure_runtime_state（状态安全不依赖 initialize 成功）；
    真实 PTrade 可能在 initialize 抛错后继续回调——全部 g 字段由 guard 兜底。"""
    _ensure_runtime_state()
    set_benchmark("000300.SS")
    # 引擎以独立 globals 执行 handle_data：模块级 str 常量不可见（PLAY_START NameError
    # 教训，2026-09-01）→ 打板窗口边界走 g 字段
    g.play_start = "09:31"
    g.play_end = "10:00"


def before_trading_start(context, data):
    _ensure_runtime_state()
    g.prepped = False
    g.day_did = ""
    g.pending_reb = []
    g.candidates = []


def handle_data(context, data):
    _qs_capture_ctx(context)
    _ensure_runtime_state()
    today = str(context.current_dt.date())
    now = context.current_dt.time().strftime("%H:%M")

    if not g.prepped:
        _screen_market(context, today)

    # 分钟打板窗口 09:31-10:00（T-1 已筛候选；未封板即打板失败放弃）
    if g.sentiment_ok and g.play_start <= now <= g.play_end and g.day_did != today:
        _try_play_window(context, today, now)
        if now >= g.play_end:
            g.day_did = today

    # T+1 起持仓每 bar 监控（开板=炸板/断板 → 卖出；止损 -5%）
    _holdings_monitor(context, today, now)

    if now >= "15:00":
        _emit_portfolio_audit(context, today)


def _screen_market(context, today):
    """T-1 收盘态全市场日线聚合（当日内一次）：情绪/晋级率/炸板率/板块热度/候选二进三。"""
    g.prepped = True
    try:
        all_codes = sorted(get_Ashares() or [])
    except Exception:
        all_codes = []
    if not all_codes:
        g.sentiment_ok = False
        return
    g.day_codes = all_codes
    try:
        hist = get_history_batch(all_codes, count=8, unit="1d",
                                 fields=["open", "high", "low", "close",
                                         "volume", "pctChg"],
                                 fq="pre", include=False) or {}
    except Exception:
        return
    tbl = {}
    for c, item in hist.items():
        clc = _extract_history_field(item, "close")
        if clc is None or len(clc) < 4:
            continue
        tbl[c] = {
            "clc": clc,
            "vol": _extract_history_field(item, "volume"),
            "pct": _extract_history_field(item, "pctChg"),
            "opn": _extract_history_field(item, "open"),
            "hig": _extract_history_field(item, "high"),
            "low": _extract_history_field(item, "low"),
        }
    if not tbl:
        g.sentiment_ok = False
        return

    zt_lst, zt_t2 = [], []
    promo_up, promo_base = 0, 0
    zp_up, zp_base = 0, 0
    for c, r in tbl.items():
        pct = r["pct"]
        up_t1 = len(pct) >= 2 and _is_limit_up(pct[-1], c)
        up_t2 = len(pct) >= 3 and _is_limit_up(pct[-2], c)
        if up_t1:
            zt_lst.append(c)
        if up_t2:
            zt_t2.append(c)
            promo_base += 1
            if up_t1:
                promo_up += 1
        hi, cl = r["hig"], r["clc"]
        if hi is not None and len(hi) >= 2 and cl is not None and len(cl) >= 2:
            prev_c = float(cl[-2])
            if prev_c == prev_c:
                r["limit_price"] = _limit_price(prev_c, c)
                if float(hi[-1]) >= r["limit_price"] - 1e-6:
                    zp_base += 1
                    if not _is_limit_up(pct[-1], c):
                        zp_up += 1
    g.zt_counts = {"total": len(zt_lst),
                   "promo": (promo_up / promo_base) if promo_base else 0.0,
                   "zp_rate": (zp_up / zp_base) if zp_base else 0.0,
                   "max_ladder": _max_ladder(tbl)}
    g.sentiment_ok = bool(
        g.zt_counts["total"] > MKT_ZT_MIN
        and g.zt_counts["promo"] > PROMO_RATE_MIN
        and g.zt_counts["zp_rate"] < ZP_RATE_MAX
        and g.zt_counts["max_ladder"] >= MAX_LADDER_MIN)
    _emit_screen(today, 0, "ok" if g.sentiment_ok else "no")
    if not g.sentiment_ok:
        return

    # 板块热度：对 T-1 涨停股取 SW2021 L1 归属（仅涨停股集合，≤数百次）
    g.sector_zt, g.sector_gain = {}, {}
    for c in zt_lst:
        try:
            ind = get_industry(c) or {}
        except Exception:
            ind = {}
        l1 = _ind_l1(ind)
        if not l1:
            continue
        g.sector_zt[l1] = g.sector_zt.get(l1, 0) + 1
        pct = tbl[c]["pct"]
        if pct is not None and len(pct):
            g.sector_gain[l1] = g.sector_gain.get(l1, 0.0) + float(pct[-1])
    for k in g.sector_gain:
        g.sector_gain[k] = g.sector_gain[k] / max(1, g.sector_zt.get(k, 0))
    top3 = [k for k, _ in sorted(g.sector_gain.items(), key=lambda kv: -kv[1])[:SECTOR_RANK_TOP]]

    g.candidates = []
    for c, r in tbl.items():
        pct, v = r["pct"], r["vol"]
        if pct is None or v is None or len(pct) < 4 or len(v) < 6:
            continue
        if not (_is_limit_up(pct[-2], c) and _is_limit_up(pct[-1], c)):
            continue
        if not (float(v[-2]) > 0 and float(v[-1]) / float(v[-2]) > VOL_RATIO_GE2):
            continue
        avg5 = float(np.nanmean(v[-6:-1]))
        if avg5 <= 0 or float(v[-1]) < avg5 * VOL_RATIO_5D:
            continue
        try:
            ind = get_industry(c) or {}
        except Exception:
            ind = {}
        l1 = _ind_l1(ind)
        if not l1 or g.sector_zt.get(l1, 0) < SECTOR_ZT_MIN or l1 not in top3:
            continue
        one = {"open": float(r["opn"][-1]) if r["opn"] is not None and len(r["opn"]) else None,
               "high": float(r["hig"][-1]) if r["hig"] is not None and len(r["hig"]) else None,
               "low": float(r["low"][-1]) if r["low"] is not None and len(r["low"]) else None,
               "close": float(r["clc"][-1])}
        if _is_one_line(one):
            continue
        g.candidates.append(c)
    # 公共 status API：ST 剔除（数据近似 AP-8 之外，公共签名兜底）
    try:
        st_map = get_stock_status(g.candidates, query_type="ST") or {}
        g.candidates = [c for c in g.candidates if not st_map.get(c, False)]
    except Exception:
        pass
    _emit_screen(today, len(g.candidates), "ok")


def _max_ladder(tbl):
    best = 0
    for c, r in tbl.items():
        pct = r.get("pct")
        if pct is None:
            continue
        streak = 0
        for p in pct[::-1]:
            if _is_limit_up(p, c):
                streak += 1
            else:
                break
        best = max(best, streak)
    return best



def _portfolio_total_value(context):
    """runtime 总资产（sizing_mode=runtime_total_value）：portfolio.total_value 优先，
    兼容 total_asset / portfolio_value；取不到回退 cash。"""
    p = getattr(context, "portfolio", None)
    if p is not None:
        for f in ("total_value", "total_asset", "portfolio_value"):
            v = getattr(p, f, None)
            if v is not None:
                try:
                    fv = float(v)
                except Exception:
                    continue
                if fv == fv and fv > 0:
                    return fv
    return float(getattr(context, "cash", 0.0) or 0.0)

def _try_play_window(context, today, now):
    """打板窗口：候选中上一已完成分钟 bar 封板（close ≥ 涨停价近似）→ 买入（每码每日一次）。"""
    codes = [c for c in g.candidates if c not in g.port and c not in g.pending_reb]
    if not codes or len(g.port) >= MAX_HOLDINGS:
        return
    try:
        mh = get_history(3, frequency='1m', field=['close', 'preClose'], security_list=codes, fq='pre', include=False, is_dict=True) or {}
    except Exception:
        return
    for c in codes:
        if len(g.port) >= MAX_HOLDINGS:
            break
        item = mh.get(c)
        closes = _extract_history_field(item, "close")
        pres = _extract_history_field(item, "preClose")
        if closes is None or len(closes) == 0:
            continue
        last_close = float(closes[-1])
        prev_close = None
        if pres is not None and len(pres) and float(pres[-1]) == float(pres[-1]):
            prev_close = float(pres[-1])
        if prev_close is None:
            prev_close = g.prev_close.get(c)
        if prev_close is None:
            continue
        lp = _limit_price(prev_close, c)
        if lp is None or last_close + 1e-6 < lp:
            continue  # 未封板 → 打板失败，放弃
        eq = _portfolio_total_value(context)
        target = eq * POSITION_WEIGHT
        if target <= 100:
            continue
        g.pending_reb.append(c)
        g.port[c] = {"buy_date": today, "cost": last_close, "value": target}
        order_target_value(c, target)
        log.info("QS_REBALANCE_AUDIT rebalance_id=lbdt-%s-%s date=%s selected=%d "
                 "tradable=%d sell_submitted=0 buy_submitted=1" % (
                     today.replace("-", ""), str(context.current_dt.time()).replace(":", ""),
                     today, len(g.port), len(g.port)))


def _holdings_monitor(context, today, now):
    """T+1 起每 bar：bar close < 涨停价（开板）或 < 成本×0.95（止损）→ 卖出。"""
    for c in list(g.port.keys()):
        p = g.port[c]
        if today <= str(p.get("buy_date") or ""):
            continue
        try:
            mh = get_history(2, frequency='1m', field=['close', 'preClose'], security_list=c, fq='pre', include=False, is_dict=True) or {}
        except Exception:
            continue
        item = mh.get(c)
        closes = _extract_history_field(item, "close")
        pres = _extract_history_field(item, "preClose")
        if closes is None or len(closes) == 0:
            continue
        last_close = float(closes[-1])
        cost = float(p.get("cost") or 0.0)
        if cost <= 0 or last_close != last_close:
            continue
        if last_close < cost * STOP_LOSS_PCT:
            order_target_value(c, 0)
            del g.port[c]
            log.info("QS_REBALANCE_AUDIT rebalance_id=lbdt-%s-%s date=%s selected=%d "
                     "tradable=%d sell_submitted=1 buy_submitted=0" % (
                         today.replace("-", ""), str(context.current_dt.time()).replace(":", ""),
                         today, len(g.port), len(g.port)))
            continue
        if pres is not None and len(pres) and float(pres[-1]) == float(pres[-1]):
            lp = _limit_price(float(pres[-1]), c)
            if lp is not None and last_close + 1e-6 < lp:
                order_target_value(c, 0)  # 开板（炸板/断板）→ 卖出
                del g.port[c]
                log.info("QS_REBALANCE_AUDIT rebalance_id=lbdt-%s-%s date=%s selected=%d "
                         "tradable=%d sell_submitted=1 buy_submitted=0" % (
                             today.replace("-", ""), str(context.current_dt.time()).replace(":", ""),
                             today, len(g.port), len(g.port)))


def _emit_screen(today, n_cand, state):
    log.info("QS_SCREEN_AUDIT date=%s candidates=%d sentiment=%s promo=%.3f "
             "zp_rate=%.3f max_ladder=%d mkt_zt=%d" % (
                 today, n_cand, state, g.zt_counts["promo"],
                 g.zt_counts["zp_rate"], g.zt_counts["max_ladder"],
                 g.zt_counts["total"]))


def _emit_portfolio_audit(context, today):
    cash = float(getattr(context, "cash", 0.0) or 0.0)
    eq = float(getattr(getattr(context, "portfolio", None), "total_value", None) or cash)
    ratio = (cash / eq) if eq else 0.0
    log.info("QS_PORTFOLIO_AUDIT rebalance_id=lbdt-%s date=%s positions=%d "
             "cash_ratio=%.4f gross_exposure=%.4f" % (
                 today.replace("-", ""), today, len(g.port), ratio, 1.0 - ratio))


def after_trading_end(context, data):
    _ensure_runtime_state()
    today = str(context.current_dt.date())
    _emit_portfolio_audit(context, today)