# strategy_ptrade.py - 由 QuantStudio source_import 转换生成
# 来源: strategy.py
# profile: ptrade-default (ptrade_profile_version 1.1.0-source-import)
# 已知差异:
#   (无)
# PTRADE_RUNTIME_UNVERIFIED: 真实券商平台行为未验证，部署前须人工冒烟。


# 断板反包策略_ptrade.py - 由 QuantStudio source_import 转换生成
# 来源: 断板反包策略.py
# profile: ptrade-default (ptrade_profile_version 1.1.0-source-import)
# 已知差异:
# - get_history: get_history 签名 A→B（count-first，PTrade 契约；本地双签名兼容）
# - get_history: get_history 签名 A→B（count-first，PTrade 契约；本地双签名兼容）
# PTRADE_RUNTIME_UNVERIFIED: 真实券商平台行为未验证，部署前须人工冒烟。


# -*- coding: utf-8 -*-
"""断板反包策略（broken_board_reversal）— QuantStudio 本地专用回测策略。

形态（时序严格 T-2 → T-1 → T）：
  T-2 涨停封板（首板或连板均可）
  T-1 断板：不再涨停，收放量阴线（烂板回落/高开低走）
  T   反包：收阳线，收盘价 ≥ T-1 阴线开盘价，量能在 T-1 量 0.8~1.2 倍带内

硬性过滤：无量一字板炸板剔除 / T-1 涨跌幅 [-8%,-3%] / 量能带 / T-2~T 三日站上 MA20 /
流动性（T-1 成交额 ≥ 5000 万元）/ T 日收盘涨停弃买 / 除权窗口弃号（宁缺勿假）。

交易：T 日收盘确认信号并以收盘价买入（close 模式），T+2 日收盘卖出；
最多同时持有 2 只、单只目标仓位 50%（runtime_total_value）；对标基准 000852（中证1000）。

参数冻结（R0 客户裁决，禁止按回测结果回调）：
  VOL_RATIO 0.8/1.2 · DROP_BAND [-8,-3] · NO_VOL_ONEWORD 0.5 · LIQ_AMT 5e7 · HOLD_DAYS 2

targets: quantstudio 本地专用（不声明 PTrade 可移植性；PTrade 转换由 PyQt tab/CLI 承接）。
"""
import numpy as np

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
# 版本自标识（2026-09-02：三次平台验证版本错位根治——diag 行内嵌版本号，平台日志直接可读）
_QS_WRAPPER_VERSION = "20260903-v10.4"
_QS_REQ_PREC_MIN = False
_QS_MINUTE_DIAG_DONE = False
_QS_MINUTE_SYNTH_LOGGED = False
_QS_PREC_CACHE = {}
_QS_LAST_SYNTH_INFO = {}

# v10（2026-09-03 立项方案 data[code] 分钟 bar 捕获与合成）：
# 平台回测分钟 include 双模式均不可安全表达"上一已完成 bar"（False=昨日 bar /
# True=含未来 bar，known-limitation 实证定谳）→ handle_data 回调参数 data[code]
# 是唯一正确的"当前已完成 bar"来源（触发时点=当前 bar 刚完成，天然无未来函数，
# 本地 DataDict 与平台 BarData 语义同构）→ 捕获 data 引用，wrapper 分钟路径
# 返回侧合成"当前 bar"追加至返回体，策略 closes[-1]=今日盘中价，源码零改动。
_QSRuntimeDataState = type("_QSRuntimeDataState", (), {"data": None})


def _qs_capture_data(*rt_args):
    """handle_data 入口捕获 data（及可选 context）引用（O(1)，每回调刷新）。

    v10.3：签名 (context, data) / (data) 双兼容——注入行传 (context, data)；
    context 同时落 _QS_RUNTIME_CTX（订单扩展未注入捕获行时兜底，供合成日期守卫）。
    """
    try:
        if len(rt_args) >= 2:
            _ctx, _dt = rt_args[0], rt_args[1]
        elif rt_args and rt_args[0] is not None and hasattr(rt_args[0], "keys"):
            _ctx, _dt = None, rt_args[0]
        else:
            _ctx, _dt = (rt_args[0], None) if rt_args else (None, None)
        if _dt is not None:
            _QSRuntimeDataState.data = _dt
        if _ctx is not None:
            global _QS_RUNTIME_CTX
            _QS_RUNTIME_CTX = _ctx
    except Exception:
        pass


def _qs_synth_minute_bar_from_data(code, df):
    """v10：分钟返回体合成"当前已完成 bar"（来源=捕获的 data[code]，本地 DataDict /
    平台 BarData 双端同构；逐属性 fail-soft 探测，缺失置 NA）。

    去重：返回体末 bar 时间 == data bar 时间 → 已含今日 bar，跳过（本地引擎
    include 语义正确时自然短路，零行为变化）。
    根因叙事（第二十一轮三元组定谳，权威口径）：第二十轮假阳性的真根因是
    平台 BarData.preclose 返回 0.0 伪值（穿透策略 g.prev_close 回退 → lp=0.0 →
    恒真触发）；data[code] 本身提供**今日** bar（t=2026-07-01 09:31 三元组实证，
    close=今日盘中价）——早期"昨日 stale bar"假说已被本轮证据推翻。
    v10.3 日期守卫定位=防御性（防数据面未来任何滞后/漂移形态回归，当前平台
    实证不触发）；已实证修复=合成行 omit preClose（见 v10.4 注释）。
    守卫：bar 日期 == 当前交易日（_QS_RUNTIME_CTX.current_dt）才合成；stale bar
    拒绝（fail-open 返回体保持原样）。返回 (df, flag)，
    flag ∈ True / False / 'stale-bar'。
    """
    if df is None or not isinstance(df, _qs_pd.DataFrame):
        return df, False
    _rt = _QSRuntimeDataState.data
    if _rt is None:
        return df, False
    try:
        _bar = None
        try:
            _bar = _rt[code]
        except Exception:
            _bare = str(code).split(".")[0]
            for _k in _rt.keys():
                if str(_k).split(".")[0] == _bare:
                    _bar = _rt[_k]
                    break
        if _bar is None:
            return df, False
        # 逐属性 fail-soft 探测（本地 BarData 属性 / 平台 BarData 属性 / dict 键）
        def _attr(obj, names):
            for n in names:
                try:
                    v = getattr(obj, n, None)
                    if v is None and hasattr(obj, "get"):
                        v = obj.get(n, None)
                    if v is not None:
                        return v
                except Exception:
                    pass
            return None
        _c = _attr(_bar, ("close", "price"))
        if _c is None:
            return df, False
        try:
            _c = float(_c)
        except Exception:
            return df, False
        if _c != _c or _c <= 0:
            return df, False
        _t = _attr(_bar, ("dt", "time", "day_str"))
        # v10.3 日期守卫：bar 日期 ≠ 当前交易日 → stale bar 拒绝合成（fail-open）
        _cur_d = None
        try:
            _cdt = getattr(_QS_RUNTIME_CTX, "current_dt", None)
            if _cdt is not None:
                _cur_d = _qs_bar_date(_cdt)
        except Exception:
            pass
        _bar_d = _qs_bar_date(_t) if _t is not None else None
        if _cur_d and _bar_d and _bar_d != _cur_d:
            return df, "stale-bar"
        # 去重：返回体末 bar 时间与 data bar 时间一致 → 已含今日 bar，短路
        if "time" in df.columns and len(df) > 0:
            try:
                _lt = df["time"].iloc[-1]
                _lt_s = str(int(_lt)) if not isinstance(_lt, str) else _lt.replace("-", "").replace(" ", "").replace(":", "")
                _t_s = str(_t)
                _t_s = _t_s.replace("-", "").replace(" ", "").replace(":", "")
                if _t_s[:8] == _lt_s[:8]:
                    return df, False  # 末 bar 已是当日（引擎 include 语义正确）→ 短路
            except Exception:
                pass
        _row = {"close": _c}
        for _nm, _al in (("open", ("open",)), ("high", ("high",)),
                         ("low", ("low",)), ("volume", ("volume",))):
            _v = _attr(_bar, _al)
            if _v is not None:
                try:
                    _row[_nm] = float(_v)
                except Exception:
                    pass
        # v10.4（第二十一轮三元组定谳）：**omit preClose**——平台 BarData.preclose 返回
        # 0.0 伪值（preclose=0.0 实证），写入合成行 → prev_close=0.0 穿透 g.prev_close
        # 回退 → lp=0.0 → last_close>=0 恒真 → 无条件假阳性触发。omit 后由 v3 日线
        # 昨收合成统一填充（6.12 → lp=6.73 → 全部候选 last_close < lp → 负向正确；
        # v3 日线窗口双态均安全：06-30 close=6.12 或含今日 07-01 close=6.08 都 ≥ 涨停
        # 基准所需，不会触发）。
        if _t is not None:
            _row["time"] = _t
        # 三元组审计（v10.3，审核定位要求 1）：合成成功时记录 close/preclose/time
        try:
            _pv = _row.get("preClose")
            _QS_LAST_SYNTH_INFO[code] = (str(_t), _c, _pv)
        except Exception:
            pass
        df = pd_concat_one(df, _row)
        return df, True
    except Exception:
        return df, False


def pd_concat_one(df, row):
    """向 DataFrame 追加一行（dict），保持列并集；ignore_index。"""
    try:
        return _qs_pd.concat([df, _qs_pd.DataFrame([row])], ignore_index=True)
    except Exception:
        return df


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
    global _QS_REQ_PCT, _QS_REQ_PREC_MIN, _QS_MINUTE_DIAG_DONE, _QS_MINUTE_SYNTH_LOGGED
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
        # 分钟频率请求 preclose：v7（2026-09-02 平台实证第七轮 omap_keys=[] 定谳）——
        # 平台分钟含不支持字段 preclose → 静默返回空 OrderedDict（日线含不支持字段则抛错
        # skip）→ 分钟请求侧必须剥离 preclose（仅发 close），preClose 由 v3 日线昨收合成补回
        _QS_REQ_PREC_MIN = _is_minute and 'preclose' in _mapped
        if _QS_REQ_PREC_MIN and 'preclose' in _mapped:
            _mapped.remove('preclose')
        # v9（2026-09-03 用户评审）：v8 曾将分钟 include 改写 True——存未来函数风险
        # （平台 include=True 语义未实证：若返回当日含当前时点后 bar → 未来函数），
        # **回退改写**（主请求保持 include=False，行为保守无风险）；
        # 平台 include 语义改由 QSPROBE 探针实证（仅首调观测，双形态各一次）。
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
                        # v7.2/v7.3：末 bar 时间戳 + close 全数组（判定平台分钟 include 语义：
                        # closes 序列=今日盘中价 → 语义正常；=昨日尾盘价 → include 错位实锤，
                        # 由本地 stock_minutes 真实行情对照定谳）
                        _last_t = None
                        _last_c = None
                        _closes = None
                        try:
                            if 'time' in _df0.columns:
                                _last_t = _df0['time'].iloc[-1]
                            if 'close' in _df0.columns:
                                _last_c = _df0['close'].iloc[-1]
                                _closes = [round(float(x), 3)
                                           for x in _df0['close'].tolist()]
                        except Exception:
                            pass
                        _desc = "cols=%s rows=%d last_t=%s last_close=%s closes=%s" % (
                            list(_df0.columns), len(_df0), _last_t, _last_c, _closes)
                    elif _df0 is not None and hasattr(_df0, 'keys'):
                        try:
                            _desc = "omap_keys=%s" % (list(_df0.keys())[:8],)
                        except Exception:
                            _desc = "type=%s" % type(_df0).__name__
                    else:
                        _desc = "type=%s" % type(_df0).__name__
                    log.info("QS_MINUTE_DIAG v=%s code=%s keys=%d %s" % (
                        _QS_WRAPPER_VERSION, _k0, len(_out), _desc))
                    # v9 QSPROBE：分钟 include 语义双形态探针（仅首调一次、纯观测、不参与
                    # 策略逻辑）——include=False 与 include=True 各发一次同参调用，打印返回
                    # bars 的时间戳范围与列名，实证平台分钟 include 语义（含未来?/含今日?）。
                    try:
                        _kf = _freq if 'frequency' in kwargs or 'unit' in kwargs else '1m'
                        _ksecs = _k0
                        _probe_log = []
                        for _inc in (False, True):
                            try:
                                _pr = _QSHistoryState.orig(
                                    kwargs.get('count', 3), frequency=_kf,
                                    field=['close'], security_list=_ksecs,
                                    fq=kwargs.get('fq', 'pre'), include=_inc)
                                _pr = _qs_to_dataframe(_pr)
                                if isinstance(_pr, dict):
                                    _pr = _pr.get(_ksecs)
                                if _pr is not None and hasattr(_pr, 'columns'):
                                    if 'time' in _pr.columns:
                                        _ts = [str(x) for x in _pr['time'].tolist()]
                                        _probe_log.append("%s:%s" % (_inc, _ts))
                                    else:
                                        # v9.1：补 close 值（无 time 列时靠值对照本地行情
                                        # 判定 bar 归属——今日盘中价 vs 昨日封板价）
                                        _cl = None
                                        try:
                                            if 'close' in _pr.columns:
                                                _cl = [round(float(x), 3)
                                                       for x in _pr['close'].tolist()]
                                        except Exception:
                                            pass
                                        _probe_log.append(
                                            "%s:notime rows=%d closes=%s" % (
                                                _inc, len(_pr), _cl))
                                elif _pr is not None and hasattr(_pr, 'keys'):
                                    _probe_log.append("%s:omap_keys=%s" % (
                                        _inc, list(_pr.keys())[:8]))
                                else:
                                    _probe_log.append("%s:type=%s" % (
                                        _inc, type(_pr).__name__))
                            except Exception as _e:
                                _probe_log.append("%s:exc=%s" % (_inc, str(_e)[:60]))
                        log.info("QSPROBE %s %s" % (_ksecs, " | ".join(_probe_log)))
                    except Exception:
                        pass
                else:
                    log.info("QS_MINUTE_DIAG single cols=%s" % (
                        list(_out.columns) if hasattr(_out, 'columns')
                        else type(_out).__name__))
            except Exception:
                pass
        # v10：分钟返回体合成"当前已完成 bar"（来源=捕获的 data[code]，双端同构；
        # 平台回测 include=False 只返回昨日 bar / True 含未来 bar——known-limitation）
        _synth_flags = {}
        if isinstance(_out, dict):
            for _k in list(_out.keys()):
                _out[_k], _synth_flags[_k] = _qs_synth_minute_bar_from_data(
                    _k, _out[_k])
        elif isinstance(_out, _qs_pd.DataFrame):
            _code0 = kwargs.get('security_list')
            if _code0 is None:
                _code0 = kwargs.get('security')
            if _code0 is None and args:
                _code0 = args[0]
            if isinstance(_code0, (list, tuple)):
                _code0 = _code0[0] if len(_code0) else ''
            _out, _synth_flags[''] = _qs_synth_minute_bar_from_data(
                _code0, _out)
        # v10.4.1：SYNTH 打点按日一次（审核前置 1——逐日 watchlist 观测）
        _today_d = None
        try:
            _cdt = getattr(_QS_RUNTIME_CTX, "current_dt", None)
            if _cdt is not None:
                _today_d = _qs_bar_date(_cdt)
        except Exception:
            pass
        if not _QS_MINUTE_SYNTH_LOGGED or _QS_MINUTE_SYNTH_LOGGED != _today_d:
            _QS_MINUTE_SYNTH_LOGGED = _today_d
            try:
                # 三元组审计（v10.3 审核定位要求 1）：每码合成行 time/close/preclose——
                # 定谳"合成 close 错 / 涨停基准错 / 平台 stale bar"三假说
                _trip = []
                for _ck, _cf in _synth_flags.items():
                    _ti = _QS_LAST_SYNTH_INFO.get(_ck)
                    if _ti:
                        _trip.append("%s=%s[close=%s preclose=%s t=%s]" % (
                            _ck, _cf, _ti[1], _ti[2], str(_ti[0])[:19]))
                    else:
                        _trip.append("%s=%s" % (_ck, _cf))
                log.info("QS_MINUTE_SYNTH v=%s %s" % (
                    _QS_WRAPPER_VERSION, " ".join(_trip) or "no-codes"))
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
# [qs-import-generated] trade_date / pctChg 合成 + is_dict 逐码（source_import 门控扩展，2026-08-19/27）
# trade_date 是本地 provider 合成伪列（object 类型 'YYYY-MM-DD' 字符串）；PTrade 无此字段，
# 由返回体的 datetime/time 派生。请求侧剔除合成字段（只传真实字段），返回侧补齐。
# pctChg（涨跌幅百分比）D4-S7 增补（2026-08-27 平台实证：PTrade get_history 合法字段无 pctChg，
# 由 close/preClose 合成 (close/preClose−1)×100）。
_QS_SYNTHETIC_FIELDS = {'trade_date', 'pctChg'}
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
        _rename = {k: v for k, v in _QS_COL_TO_LOCAL.items()
                    if k in _df.columns and v not in _df.columns}
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
        result = {}
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
        _out = {k: _qs_to_dataframe(v) for k, v in _result.items()}
    else:
        _out = _qs_to_dataframe(_result)
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
# [qs-import-generated] filter_stock_by_status('ST') 退市风险兜底注入（P-D9，2026-08-22）
# 平台 'ST' 仅官方 ST 标记 → 补本地同款 is_delisting_risk（close<1，当日 T close）。
# fail-open：取数失败 log.warning + 保持平台原生结果（与本地 except: return result 同语义）。
# 性能（A 条三级）：批量优先（多码一次 get_history）→ 幸存者兜底（仅对原生过滤幸存池判）
# → 当日缓存复用（探针实证批量 8 码 0.007s vs 逐码 8 次 0.018s）。
_QSFilterStatusState = type("_QSFilterStatusState", (), {"orig": None, "cache": None})
_QSFilterStatusState.orig = filter_stock_by_status
_QS_FILTER_DELISTING_THRESHOLD = 1.0  # 面值退市线（元）


def _qs_status_prefetch_closes(codes):
    """批量预取多码 close（一次 get_history 多码调用，探针实证 8 码 0.007s）→ 写入缓存。

    平台返回形态：DataFrame（code/close 或 time/close 列）。fail-open：异常/空返回不写缓存。
    缓存命中短路：codes 全部已缓存 → 跳过批量（同日重复调用零取数）。
    """
    cache = _QSFilterStatusState.cache
    if cache is None:
        cache = {}
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
        cache = {}
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


# [qs-import-generated]
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
# n=248 条；官方稳定映射转换期固化；模板自包含——平台无 quantstudio 可导入）。
_QS_BSE_LEGACY = {'430017', '430047', '430090', '430139', '430198', '430300', '430418', '430425', '430476', '430478', '430489', '430510', '430556', '430564', '430685', '430718', '830779', '830799', '830809', '830832', '830839', '830879', '830896', '830946', '830964', '830974', '831010', '831039', '831087', '831152', '831167', '831175', '831195', '831278', '831304', '831305', '831370', '831396', '831445', '831526', '831627', '831641', '831689', '831726', '831768', '831832', '831834', '831855', '831856', '831906', '831961', '832000', '832023', '832089', '832110', '832145', '832149', '832171', '832175', '832225', '832278', '832419', '832469', '832471', '832491', '832522', '832566', '832651', '832662', '832735', '832786', '832802', '832876', '832885', '832978', '832982', '833030', '833075', '833171', '833230', '833266', '833284', '833346', '833394', '833427', '833429', '833454', '833455', '833509', '833523', '833533', '833575', '833580', '833751', '833781', '833819', '833873', '833914', '833943', '834014', '834021', '834033', '834058', '834062', '834261', '834407', '834415', '834475', '834599', '834639', '834682', '834765', '834770', '834950', '835174', '835179', '835184', '835185', '835207', '835237', '835305', '835368', '835438', '835508', '835579', '835640', '835670', '835857', '835892', '835985', '836077', '836149', '836208', '836221', '836239', '836247', '836260', '836263', '836270', '836395', '836414', '836419', '836422', '836433', '836504', '836547', '836675', '836699', '836717', '836720', '836807', '836826', '836871', '836892', '836942', '836957', '836961', '837006', '837023', '837046', '837092', '837174', '837212', '837242', '837344', '837403', '837592', '837663', '837748', '837821', '838030', '838163', '838171', '838227', '838262', '838275', '838402', '838670', '838701', '838810', '838837', '838924', '838971', '839167', '839273', '839371', '839493', '839680', '839719', '839725', '839729', '839790', '839792', '839946', '870199', '870204', '870299', '870357', '870436', '870508', '870656', '870726', '870866', '870976', '871245', '871263', '871396', '871478', '871553', '871634', '871642', '871694', '871753', '871857', '871970', '871981', '872190', '872351', '872374', '872392', '872541', '872808', '872895', '872925', '872931', '872953', '873001', '873122', '873132', '873152', '873167', '873169', '873223', '873305', '873339', '873527', '873570', '873576', '873593', '873665', '873679', '873690', '873693', '873703', '873706', '873726', '873806', '873833'}


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
        return {}
    try:
        items = list(raw.items())
    except AttributeError:
        raise ValueError("QS_POS_VIEW_VIOLATION get_positions 返回非 dict（%s）"
                         % type(raw).__name__)
    out = {}
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
        return {tgt: out[tgt]} if tgt in out else {}
    _qs_shape_check("get_positions", "dict", out)
    return out


def get_position(security):
    """输入归一（防 .SH 平台崩溃/裸码空壳）+ 契约视图；空仓语义 amount=0（F5）。"""
    code = _qs_norm_code(security)
    p = _QSPositionState.get_position_orig(code)
    return _QSPositionView(p, code)



def initialize(context):
    _ensure_runtime_state()
    set_benchmark(INDEX_CODE)
    log.info('断板反包策略 initialized: benchmark=%s hold_days=%d max_holdings=%d '
             'per_position=%.2f' % (INDEX_CODE, HOLD_DAYS, MAX_HOLDINGS,
                                    PER_POSITION_WEIGHT))


def before_trading_start(context, data):
    _ensure_runtime_state()
    members = get_index_stocks(INDEX_CODE)          # 回测注入当日，PIT 月度 complete 快照
    members = sorted(members) if members else []
    # 状态硬过滤（执行层边界，非形态规则）：默认 ST/HALT/DELISTING
    filtered = filter_stock_by_status(members)
    g.universe = sorted(filtered) if filtered else []
    log.debug('universe %d -> %d after status filter' % (len(members), len(g.universe)))


def handle_data(context, data):
    _qs_capture_ctx(context)
    _ensure_runtime_state()
    today = context.current_dt.strftime('%Y-%m-%d')
    funnel = {'scanned': 0, 'e0': 0, 'e1': 0, 'e2': 0, 'e3': 0, 'e4': 0, 'e5': 0,
              'e6': 0, 'e7': 0, 'e8': 0, 'e9': 0}

    # ---- 市场基准日（000852 指数前两交易日，停牌缺口防御锚）----
    mkt = get_history(2, frequency='1d', field=['trade_date'], security_list=INDEX_CODE, fq='pre', include=False, is_dict=True)
    mkt_values = list(mkt.values()) if mkt else []
    if not mkt_values:
        return
    mkt_td = _extract_history_field(mkt_values[0], 'trade_date', dtype=str)
    if mkt_td.shape[0] < 2:
        return
    t1_date, t2_date = str(mkt_td[-1])[:10], str(mkt_td[-2])[:10]

    # ---- 批量取史（前复权信号基准；include=False 截至 T-1）----
    hist = get_history(HIST_COUNT, frequency='1d', field=FIELDS, security_list=g.universe, fq='pre', include=False, is_dict=True)
    if not hist:
        return

    candidates = []
    for code in g.universe:
        if code in g.holdings:                       # E10 已持仓忽略（不加仓）
            continue
        item = hist.get(code)
        if item is None:
            continue
        funnel['scanned'] += 1
        try:
            arr_open = _extract_history_field(item, 'open')      # front（前复权）
            arr_high = _extract_history_field(item, 'high')      # front
            arr_close = _extract_history_field(item, 'close')    # front
            arr_vol = _extract_history_field(item, 'volume')     # raw
            arr_amt = _extract_history_field(item, 'amount')     # raw（元）
            arr_pct = _extract_history_field(item, 'pctChg')     # 百分比
            arr_prec = _extract_history_field(item, 'preClose')  # raw
            arr_td = _extract_history_field(item, 'trade_date', dtype=str)
        except Exception:
            continue
        # ---- E0 数据完整性：≥22 根、日期对齐市场基准日、T 日 bar 有效 ----
        if arr_close.shape[0] < 22:
            continue
        bar = data[code]
        if (str(arr_td[-1])[:10] != t1_date or str(arr_td[-2])[:10] != t2_date
                or bar.volume <= 0 or bar.close <= 0 or bar.open <= 0):
            continue
        if np.isnan(arr_close[-21:]).any() or np.isnan(arr_pct[-2:]).any():
            continue
        funnel['e0'] += 1

        pct2, pct1 = float(arr_pct[-2]), float(arr_pct[-1])
        vol2, vol1 = float(arr_vol[-2]), float(arr_vol[-1])
        amt1 = float(arr_amt[-1])
        prec1 = float(arr_prec[-1])
        limit = _limit_pct(code)

        # ---- E1 T-2 涨停封板（pctChg ≥ 板块幅度 − 0.1pct，收盘封板近似）----
        if not (pct2 >= limit * 100.0 - 0.1):
            continue
        funnel['e1'] += 1

        # ---- E2 T-1 断板放量阴线：跌幅带 + 收阴 + 较涨停日放量 ----
        if not (DROP_MIN_PCT <= pct1 <= DROP_MAX_PCT
                and arr_close[-1] < arr_open[-1]
                and vol1 > vol2):
            continue
        funnel['e2'] += 1

        # ---- E3 无量一字板炸板剔除 ----
        f1 = float(arr_close[-1]) / (prec1 * (1.0 + pct1 / 100.0))   # T-1 复权因子
        close_raw1 = prec1 * (1.0 + pct1 / 100.0)                    # T-1 原始收盘
        open_raw1 = float(arr_open[-1]) / f1                         # T-1 原始开盘
        high_limit1 = round(prec1 * (1.0 + limit), 2)                # T-1 涨停价
        if (open_raw1 >= high_limit1 - 0.01
                and close_raw1 < high_limit1
                and vol1 < NO_VOL_ONEWORD_RATIO * vol2):
            continue
        funnel['e3'] += 1

        # ---- E4 T 反包阳线：收盘 ≥ 开盘 且 收盘 ≥ T-1 开盘（原始价）----
        if not (bar.close >= bar.open and bar.close >= open_raw1):
            continue
        funnel['e4'] += 1

        # ---- E5 T 量能带 [0.8, 1.2] × volume(T-1) ----
        if not (VOL_RATIO_MIN * vol1 <= bar.volume <= VOL_RATIO_MAX * vol1):
            continue
        funnel['e5'] += 1

        # ---- E6 MA20 三日过滤（前复权收盘、含当日滚动）----
        closes = arr_close[-21:]                                     # T-21..T-1
        ma_t2 = float(np.mean(closes[:20]))                          # T-21..T-2
        ma_t1 = float(np.mean(closes[1:]))                           # T-20..T-1
        # T 为除权日时 E9 已弃号；无除权则 front 连续：close_front(T)=close_front(T-1)×close_T/preClose_T
        close_front_t = float(closes[-1]) * bar.close / bar.preclose
        ma_t0 = (float(np.sum(closes[2:])) + close_front_t) / 20.0   # T-19..T
        if not (float(closes[-2]) > ma_t2 and float(closes[-1]) > ma_t1
                and close_front_t > ma_t0):
            continue
        funnel['e6'] += 1

        # ---- E7 流动性：T-1 成交额 ≥ 5000 万元 ----
        if not (amt1 >= LIQ_AMT_MIN):
            continue
        funnel['e7'] += 1

        # ---- E8 T 日收盘涨停 → 放弃买入（买不进按现实模拟）----
        if bar.close >= bar.high_limit - 0.001:
            continue
        funnel['e8'] += 1

        # ---- E9 除权防御（宁缺勿假）：量比/反包比较跨除权即弃 ----
        f2 = float(arr_close[-2]) / (float(arr_prec[-2]) * (1.0 + pct2 / 100.0))
        if (abs(f2 - f1) > F_TOL
                or abs(bar.preclose - close_raw1) > 0.001 * close_raw1):
            continue
        funnel['e9'] += 1

        # ---- 完全反包标记（排序优先），进入候选 ----
        fully_covered = bar.close >= float(arr_high[-2]) / f1        # T 收 ≥ T-1 最高
        candidates.append((0 if fully_covered else 1, -amt1, code))

    # ---- 排序：完全反包优先 → T-1 成交额降序 → code 升序（确定性）----
    candidates.sort()

    # ---- 先卖（T+2 到期，close 即时撮合，卖出款同周期可用）----
    g.rebalance_seq += 1
    rid = 'bbrev-%s-%04d' % (today.replace('-', ''), g.rebalance_seq)
    sell_submitted = 0
    for code in sorted(g.holdings.keys()):
        if g.holdings[code]['days_held'] >= HOLD_DAYS:
            order_target_value(code, 0)
            sell_submitted += 1
            pos = get_position(code)
            if getattr(pos, 'amount', 0) <= 0:
                g.holdings.pop(code, None)             # X3 对账：已清仓移除账本

    # ---- 后买（最多补满 2 只，runtime_total_value × 0.5；P1 设计契约）----
    # D4-S6 框架修复已落定（ptrade_api 接线层换算价=②层当日撮合价，2026-08-27），
    # order_target_value 现按当日收盘精确核算，回归标准实现（不再需要显式股数自保）。
    buy_submitted = 0
    slots = MAX_HOLDINGS - len(g.holdings)
    for _rank, _neg_amt, code in candidates:
        if buy_submitted >= slots:
            break
        target_value = context.portfolio.total_value * PER_POSITION_WEIGHT
        order_target_value(code, target_value)
        buy_submitted += 1
        g.holdings[code] = {'buy_dt': today, 'days_held': 0}
        pos = get_position(code)
        if getattr(pos, 'amount', 0) <= 0:              # 受理未成交（如边界拒单）回滚账本
            g.holdings.pop(code, None)
            buy_submitted -= 1

    # ---- 审计行（R5 部署不变量 + 信号漏斗，rebalance_id 1:1）----
    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
             'sell_submitted=%d buy_submitted=%d'
             % (rid, today, len(candidates), funnel['e9'],
                sell_submitted, buy_submitted))
    tv = context.portfolio.total_value
    cash_ratio = context.portfolio.cash / tv if tv > 0 else 0.0
    gross = context.portfolio.market_value / tv if tv > 0 else 0.0
    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
             'cash_ratio=%.4f gross_exposure=%.4f'
             % (rid, today, len(g.holdings), cash_ratio, gross))
    log.info('QS_SIGNAL_AUDIT date=%s scanned=%d e0=%d e1=%d e2=%d e3=%d e4=%d '
             'e5=%d e6=%d e7=%d e8=%d e9=%d signals=%d buys=%d sells=%d'
             % (today, funnel['scanned'], funnel['e0'], funnel['e1'], funnel['e2'],
                funnel['e3'], funnel['e4'], funnel['e5'], funnel['e6'], funnel['e7'],
                funnel['e8'], funnel['e9'], len(candidates), buy_submitted,
                sell_submitted))


def after_trading_end(context, data):
    _ensure_runtime_state()
    for code in sorted(g.holdings.keys()):
        g.holdings[code]['days_held'] += 1
        pos = get_position(code)
        if getattr(pos, 'amount', 0) <= 0:              # 外部强平/退市对账清理
            g.holdings.pop(code, None)
