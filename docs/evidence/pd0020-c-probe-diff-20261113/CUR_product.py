"\ntech_etf_mvo_rotation.py - agent-authored canonical QuantStudio/PTrade strategy.\n\n科技ETF四维评分MVO轮动策略（design_version 2.0, strategy_id tech_etf_mvo_rotation）\n\n语义（客户 R0/R2.5 已确认）：\n- 固定池：8 只科技细分赛道 ETF 评分，2 只避险 ETF（黄金/国债）仅参与 MVO 对冲；\n- 四维截面评分（252 日，fq='pre'，include=False，信号截止 T-1 收盘）：\n  年化收益率(1x) + 夏普比率(2x) + 胜率(1x) + 最大回撤反向(1x)，百分位归一，Top3 入选；\n- 权重：120 日协方差 MVO（做多-only、满仓、期望收益不低于入选均值），失败回退 Top3 等权；\n- 风控：MACD 死叉标的权重减半；任一入选标的 21 日年化波动率 252 日百分位 > 80% → 总仓位 ×0.5；\n- 周频：run_daily 15:00 调度，ISO 周边界去重，仅每周第一个交易日调仓；\n- 暖机期（历史不足 252 日）持币空仓；缺数据标的 fail-soft 跳过。\n\n平台纪律：\n- 显式 import numpy/pandas；g/log 与公共 API 由平台提供；不使用 MyTT/get_current_data/check_limit/data[code]；\n- get_history 使用 PTrade count-first 便携签名；订单返回值按不透明 ID/None 处理；\n- 所有回调首行调用幂等 _ensure_runtime_state()。\n"
import numpy as np
import pandas as pd
import pickle  # PTRADE-DEPLOY:IMPORT
# PTRADE-DEPLOY:GUARD BEGIN
def _deploy_guard_pass(security, side):
    'PTRADE-DEPLOY 注入护栏（停牌/涨跌停/限价参考/订单幂等，设计文档 §6.3）。\n\n    由 qs-deploy 转换器自动注入，整块带 GUARD 标记、可整体删除；\n    全部检查失败仅拦截下单并告警，绝不抛异常中断策略。\n    '
    try:
        if get_stock_status(security, query_type='HALT').get(security, False):
            log.warning('护栏拦截: %s 停牌/HALT，跳过下单' % security)
            return False
    except Exception:
        pass
    try:
        _flag = check_limit(security)
        if isinstance(_flag, dict):
            _flag = _flag.get(security, 0)
        if side == 'buy' and _flag in (1, 2):
            log.warning('护栏拦截: %s 涨停(check_limit=%s)，拒买' % (security, _flag))
            return False
        if side == 'sell' and _flag in (-1, -2):
            log.warning('护栏拦截: %s 跌停(check_limit=%s)，拒卖' % (security, _flag))
            return False
    except Exception:
        pass
    if is_trade():
        try:
            _snap = get_snapshot(security)
            _px = None
            if isinstance(_snap, dict):
                _inner = _snap.get(security)
                if isinstance(_inner, dict):
                    _px = _inner.get('last_px')
                if _px is None:
                    _px = _snap.get('last_px')
                if _px is None and isinstance(_inner, (int, float)):
                    _px = _inner
            elif isinstance(_snap, (int, float)):
                _px = _snap
            log.info('护栏参考: %s 快照最新价=%s（F8：下单请显式限价，默认最新价报单）' % (security, _px))
        except Exception:
            pass
    try:
        _book = getattr(g, 'state', None)
        if isinstance(_book, dict):
            _key = [str(security), str(side)]
            _done = _book.get('orders_done', [])
            if _key in _done:
                log.warning('护栏拦截: %s %s 当日重复下单，幂等拦截' % (security, side))
                return False
    except Exception:
        pass
    try:
        _pq = getattr(g, 'state', None)
        if isinstance(_pq, dict):
            _stale_ids = _pq.get('qs_prev_orders', [])
            if _stale_ids:
                _pq['qs_prev_orders'] = []
                _stale_open = []
                for _sid in list(_stale_ids):
                    try:
                        _so = get_order(_sid)
                        _sob = None
                        if isinstance(_so, (list, tuple)):
                            if len(_so) > 0:
                                _sob = _so[0]
                        elif _so is not None:
                            _sob = _so
                        _sst = ''
                        if _sob is not None:
                            try:
                                if isinstance(_sob, dict):
                                    _sst = str(_sob.get('status', ''))
                                else:
                                    _sst = str(getattr(_sob, 'status', ''))
                            except Exception:
                                _sst = ''
                        if _sst in ('5', '6', '8', '9'):
                            continue
                        _stale_open.append(_sid)
                    except Exception:
                        continue
                if _stale_open:
                    log.warning('PTRADE-DEPLOY 陈旧挂单巡检: 上一交易日遗留未终态委托 %d 笔 [%s]——策略本次将重新下单，存在双买风险，请人工确认是否需撤单' % (len(_stale_open), ','.join(_stale_open[:10])))
                    if False:
                        for _sid in _stale_open:
                            try:
                                cancel_order(_sid)
                                log.warning('PTRADE-DEPLOY 陈旧挂单已撤: %s' % _sid)
                            except Exception:
                                pass
    except Exception:
        pass
    return True
# PTRADE-DEPLOY:GUARD END
# PTRADE-DEPLOY:DATA_PROXY BEGIN
class _QSDataItem(dict):
    """单标的行情项：同时支持 item['close'] 与 item.close 两种访问形态。"""

    def __getattr__(self, _name):
        try:
            return self[_name]
        except KeyError:
            raise AttributeError(_name)
# PTRADE-DEPLOY:DATA_PROXY END
# PTRADE-DEPLOY:DATA_PROXY BEGIN
class _QSDataProxy(object):
    """交易态 data 惰性代理（PTrade run_daily 不传 data 时的替身）。

    无状态：每次访问实时解析价格，不做缓存。
    取价链与 _qs_px_exec 同源（当日 close 优先 → 前收缓存 → get_history → 0.0）。
    """

    def _resolve(self, security):
        try:
            _px = _qs_px_exec(security)
        except Exception:
            _px = 0.0
        try:
            _px = float(_px)
        except Exception:
            return 0.0
        return _px if _px > 0 else 0.0

    def __contains__(self, security):
        try:
            return self._resolve(security) > 0
        except Exception:
            return False

    def __getitem__(self, security):
        _px = self._resolve(security)
        return _QSDataItem(close=_px, last_px=_px, price=_px, preclose_px=_px)

    def get(self, security, default=None):
        _px = self._resolve(security)
        if _px <= 0 and default is not None:
            return default
        return _QSDataItem(close=_px, last_px=_px, price=_px, preclose_px=_px)

    def __len__(self):
        return 0

    def keys(self):
        return []

    def __iter__(self):
        return iter([])
# PTRADE-DEPLOY:DATA_PROXY END
# PTRADE-DEPLOY:DATA_PROXY BEGIN
def _qs_data_proxy():
    '构造全新代理（无状态，可安全作为默认参数被共享）。'
    return _QSDataProxy()
# PTRADE-DEPLOY:DATA_PROXY END
# PTRADE-DEPLOY:PERSIST BEGIN
def after_trading_end(context):
    try:
        try:
            _q_roll = g.state if isinstance(getattr(g, 'state', None), dict) else None
            if _q_roll is not None:
                _q_roll['qs_prev_orders'] = list(_q_roll.get('qs_cur_orders', []))
                _q_roll['qs_cur_orders'] = []
        except Exception:
            pass
        with open(_PD_NOTEBOOK + 'tech_etf_mvo_rotation_state.pkl', 'wb') as _pf:
            pickle.dump(g.state, _pf, -1)
    except Exception as _pe:
        log.warning('PTRADE-DEPLOY 状态落盘失败: %s' % _pe)
    try:
        _qs_probe_path = _PD_NOTEBOOK + 'QS_PERSIST_PROBE.txt'
        with open(_qs_probe_path, 'w') as _qs_pf:
            _qs_pf.write('probe-ok')
        with open(_qs_probe_path, 'r') as _qs_pf:
            _qs_back = _qs_pf.read()
        log.info('QS_PERSIST_PROBE write_read_back=%s path_len=%d' % (_qs_back == 'probe-ok', len(_PD_NOTEBOOK)))
    except Exception as _qs_pe:
        log.warning('QS_PERSIST_PROBE failed=%s: %s' % (type(_qs_pe).__name__, _qs_pe))
# PTRADE-DEPLOY:PERSIST END
# PTRADE-DEPLOY:PERSIST BEGIN
try:
    _PD_NOTEBOOK = get_research_path()
except Exception:
    _PD_NOTEBOOK = ''
# PTRADE-DEPLOY:PERSIST END
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
    market_value = sum((float(getattr(position, 'market_value', 0) or 0) for position in portfolio.positions.values()))
    return float(portfolio.cash) + market_value
def _qs_shape_check(api_name, expected, actual):
    'P-D10 运行时首调形状自检（三道防线③）：返回形态不符 → QS_SHIM_SHAPE_VIOLATION\n    显性警报（log 不抛错不阻断），封"平台日后行为漂移"场景。所有注入 wrapper/shim\n    首调用点挂接；expected: dataframe / dict / list / df_or_dict。'
    _ok = False
    try:
        if expected == 'dataframe':
            _ok = hasattr(actual, 'columns') and hasattr(actual, 'index')
        elif expected == 'dict':
            _ok = isinstance(actual, dict)
        elif expected == 'list':
            _ok = isinstance(actual, (list, tuple))
        elif expected == 'df_or_dict':
            _ok = hasattr(actual, 'columns') and hasattr(actual, 'index') or isinstance(actual, dict)
        else:
            _ok = True
    except Exception:
        _ok = False
    if not _ok:
        try:
            log.warning('QS_SHIM_SHAPE_VIOLATION %s expected=%s actual=%s' % (api_name, expected, type(actual).__name__))
        except Exception:
            pass
    return _ok
def _qs_g_obj():
    '平台全局 g 安全引用（策略平台 g 为全局 API；本地/测试无 g → None 不炸）。'
    try:
        return g
    except NameError:
        return None
import pandas as _qs_pd
import numpy as _qs_np
_QS_FIELD_TO_PTRADE = {'amount': 'money', 'preClose': 'preclose'}
_QS_COL_TO_LOCAL = {'datetime': 'time', 'money': 'amount', 'preclose': 'preClose'}
_QS_REQ_PCT = False
def _qs_to_dataframe(item):
    'structured array / DataFrame → 本地列名（money→amount、preclose→preClose 等）。\n    D4-S7（2026-08-27 平台实证）：get_history 可能直接返回带日期 index 的 DataFrame，\n    DataFrame 分支同样必须做列名映射，否则策略 _extract_history_field 取列得到空数组。\n    '
    _df = None
    if isinstance(item, _qs_np.ndarray) and hasattr(item, 'dtype') and hasattr(item.dtype, 'names'):
        _df = _qs_pd.DataFrame(item)
    elif isinstance(item, _qs_pd.DataFrame):
        _df = item
    if _df is not None:
        _rename = {k: v for k, v in _QS_COL_TO_LOCAL.items() if k in _df.columns and v not in _df.columns}
        if _rename:
            _df = _df.rename(columns=_rename)
        if _QS_REQ_PCT and 'pctChg' not in _df.columns and ('close' in _df.columns) and ('preClose' in _df.columns):
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
_QS_WRAPPER_VERSION = '20260903-v10.4'
_QS_REQ_PREC_MIN = False
_QS_MINUTE_DIAG_DONE = False
_QS_MINUTE_SYNTH_LOGGED = False
_QS_PREC_CACHE = {}
_QS_LAST_SYNTH_INFO = {}
_QSRuntimeDataState = type('_QSRuntimeDataState', (), {'data': None})
def _qs_capture_data(*rt_args):
    'handle_data 入口捕获 data（及可选 context）引用（O(1)，每回调刷新）。\n\n    v10.3：签名 (context, data) / (data) 双兼容——注入行传 (context, data)；\n    context 同时落 _QS_RUNTIME_CTX（订单扩展未注入捕获行时兜底，供合成日期守卫）。\n    '
    try:
        if len(rt_args) >= 2:
            _ctx, _dt = (rt_args[0], rt_args[1])
        elif rt_args and rt_args[0] is not None and hasattr(rt_args[0], 'keys'):
            _ctx, _dt = (None, rt_args[0])
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
    'v10：分钟返回体合成"当前已完成 bar"（来源=捕获的 data[code]，本地 DataDict /\n    平台 BarData 双端同构；逐属性 fail-soft 探测，缺失置 NA）。\n\n    去重：返回体末 bar 时间 == data bar 时间 → 已含今日 bar，跳过（本地引擎\n    include 语义正确时自然短路，零行为变化）。\n    根因叙事（第二十一轮三元组定谳，权威口径）：第二十轮假阳性的真根因是\n    平台 BarData.preclose 返回 0.0 伪值（穿透策略 g.prev_close 回退 → lp=0.0 →\n    恒真触发）；data[code] 本身提供**今日** bar（t=2026-07-01 09:31 三元组实证，\n    close=今日盘中价）——早期"昨日 stale bar"假说已被本轮证据推翻。\n    v10.3 日期守卫定位=防御性（防数据面未来任何滞后/漂移形态回归，当前平台\n    实证不触发）；已实证修复=合成行 omit preClose（见 v10.4 注释）。\n    守卫：bar 日期 == 当前交易日（_QS_RUNTIME_CTX.current_dt）才合成；stale bar\n    拒绝（fail-open 返回体保持原样）。返回 (df, flag)，\n    flag ∈ True / False / \'stale-bar\'。\n    '
    if df is None or not isinstance(df, _qs_pd.DataFrame):
        return (df, False)
    _rt = _QSRuntimeDataState.data
    if _rt is None:
        return (df, False)
    try:
        _bar = None
        try:
            _bar = _rt[code]
        except Exception:
            _bare = str(code).split('.')[0]
            for _k in _rt.keys():
                if str(_k).split('.')[0] == _bare:
                    _bar = _rt[_k]
                    break
        if _bar is None:
            return (df, False)
    
        def _attr(obj, names):
            for n in names:
                try:
                    v = getattr(obj, n, None)
                    if v is None and hasattr(obj, 'get'):
                        v = obj.get(n, None)
                    if v is not None:
                        return v
                except Exception:
                    pass
            return None
        _c = _attr(_bar, ('close', 'price'))
        if _c is None:
            return (df, False)
        try:
            _c = float(_c)
        except Exception:
            return (df, False)
        if _c != _c or _c <= 0:
            return (df, False)
        _t = _attr(_bar, ('dt', 'time', 'day_str'))
        _cur_d = None
        try:
            _cdt = getattr(_QS_RUNTIME_CTX, 'current_dt', None)
            if _cdt is not None:
                _cur_d = _qs_bar_date(_cdt)
        except Exception:
            pass
        _bar_d = _qs_bar_date(_t) if _t is not None else None
        if _cur_d and _bar_d and (_bar_d != _cur_d):
            return (df, 'stale-bar')
        if 'time' in df.columns and len(df) > 0:
            try:
                _lt = df['time'].iloc[-1]
                _lt_s = str(int(_lt)) if not isinstance(_lt, str) else _lt.replace('-', '').replace(' ', '').replace(':', '')
                _t_s = str(_t)
                _t_s = _t_s.replace('-', '').replace(' ', '').replace(':', '')
                if _t_s[:8] == _lt_s[:8]:
                    return (df, False)
            except Exception:
                pass
        _row = {'close': _c}
        for _nm, _al in (('open', ('open',)), ('high', ('high',)), ('low', ('low',)), ('volume', ('volume',))):
            _v = _attr(_bar, _al)
            if _v is not None:
                try:
                    _row[_nm] = float(_v)
                except Exception:
                    pass
        if _t is not None:
            _row['time'] = _t
        try:
            _pv = _row.get('preClose')
            _QS_LAST_SYNTH_INFO[code] = (str(_t), _c, _pv)
        except Exception:
            pass
        df = pd_concat_one(df, _row)
        return (df, True)
    except Exception:
        return (df, False)
def pd_concat_one(df, row):
    '向 DataFrame 追加一行（dict），保持列并集；ignore_index。'
    try:
        return _qs_pd.concat([df, _qs_pd.DataFrame([row])], ignore_index=True)
    except Exception:
        return df
def _qs_bar_date(v):
    "bar 时间 → 'YYYYMMDD'（int/str/datetime 兼容，fail-soft None）。"
    try:
        if hasattr(v, 'strftime'):
            return v.strftime('%Y%m%d')
        _s = v if isinstance(v, str) else str(int(v))
        _s = _s.replace('-', '').replace(' ', '').replace(':', '')
        return _s[:8] or None
    except Exception:
        return None
def _qs_synth_minute_preclose(df, code, fq):
    '分钟 df 无 preClose 列时由日线昨收合成（fail-soft：任一步失败保持原 df）。'
    if df is None or not isinstance(df, _qs_pd.DataFrame) or 'preClose' in df.columns or ('close' not in df.columns) or (len(df) == 0):
        return df
    try:
        _tvals = df['time'].values if 'time' in df.columns else df.index.values
        _ds = _qs_bar_date(_tvals[-1])
        if not _ds:
            return df
        _key = (code, _ds)
        if _key not in _QS_PREC_CACHE:
            _dd = _QSHistoryState.orig(2, frequency='1d', field=['close'], security_list=code, fq=fq, include=False)
            _dd = _qs_to_dataframe(_dd)
            if isinstance(_dd, dict):
                _dd = _dd.get(code)
            if _dd is None or not isinstance(_dd, _qs_pd.DataFrame) or len(_dd) == 0 or ('close' not in _dd.columns):
                return df
            _QS_PREC_CACHE[_key] = float(_dd['close'].iloc[-1])
        df['preClose'] = float(_QS_PREC_CACHE[_key])
    except Exception:
        pass
    return df
class _QSHistoryState:
    orig = None
_QSHistoryState.orig = get_history
def get_history(*args, **kwargs):
    global _QS_REQ_PCT, _QS_REQ_PREC_MIN, _QS_MINUTE_DIAG_DONE, _QS_MINUTE_SYNTH_LOGGED
    _field = kwargs.get('field') or kwargs.get('fields')
    _QS_REQ_PCT = False
    _QS_REQ_PREC_MIN = False
    _freq = str(kwargs.get('frequency') or kwargs.get('unit') or '1d')
    _is_minute = _freq in ('1m', '5m', '15m', '30m', '60m', '1min', '5min', '15min', '30min', '60min')
    if _field:
        _is_list = isinstance(_field, list)
        _items = _field if _is_list else [_field]
        _QS_REQ_PCT = 'pctChg' in _items
        _mapped = [_QS_FIELD_TO_PTRADE.get(f, f) for f in _items if f != 'pctChg']
        if not _mapped:
            _mapped = ['close']
        if _QS_REQ_PCT and 'preclose' not in _mapped:
            _mapped.append('preclose')
        _QS_REQ_PREC_MIN = _is_minute and 'preclose' in _mapped
        if _QS_REQ_PREC_MIN and 'preclose' in _mapped:
            _mapped.remove('preclose')
        if 'field' in kwargs:
            kwargs['field'] = _mapped if _is_list else _mapped[0]
        if 'fields' in kwargs:
            kwargs['fields'] = _mapped if _is_list else _mapped[0]
    _is_dict = bool(kwargs.pop('is_dict', False))
    _secs = kwargs.pop('security_list', None)
    if _secs is None:
        _secs = kwargs.pop('security', None)
    if _secs is None and args and isinstance(args[0], (str, list, tuple)):
        _secs = args[0]
        args = ()
    if isinstance(_secs, str):
        _secs = [_secs]
    if _is_dict and _secs:
        _out = {}
        for _s in _secs:
            _kw = dict(kwargs)
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
                    if _df0 is not None and hasattr(_df0, 'columns'):
                        _last_t = None
                        _last_c = None
                        _closes = None
                        try:
                            if 'time' in _df0.columns:
                                _last_t = _df0['time'].iloc[-1]
                            if 'close' in _df0.columns:
                                _last_c = _df0['close'].iloc[-1]
                                _closes = [round(float(x), 3) for x in _df0['close'].tolist()]
                        except Exception:
                            pass
                        _desc = 'cols=%s rows=%d last_t=%s last_close=%s closes=%s' % (list(_df0.columns), len(_df0), _last_t, _last_c, _closes)
                    elif _df0 is not None and hasattr(_df0, 'keys'):
                        try:
                            _desc = 'omap_keys=%s' % (list(_df0.keys())[:8],)
                        except Exception:
                            _desc = 'type=%s' % type(_df0).__name__
                    else:
                        _desc = 'type=%s' % type(_df0).__name__
                    log.info('QS_MINUTE_DIAG v=%s code=%s keys=%d %s' % (_QS_WRAPPER_VERSION, _k0, len(_out), _desc))
                    try:
                        _kf = _freq if 'frequency' in kwargs or 'unit' in kwargs else '1m'
                        _ksecs = _k0
                        _probe_log = []
                        for _inc in (False, True):
                            try:
                                _pr = _QSHistoryState.orig(kwargs.get('count', 3), frequency=_kf, field=['close'], security_list=_ksecs, fq=kwargs.get('fq', 'pre'), include=_inc)
                                _pr = _qs_to_dataframe(_pr)
                                if isinstance(_pr, dict):
                                    _pr = _pr.get(_ksecs)
                                if _pr is not None and hasattr(_pr, 'columns'):
                                    if 'time' in _pr.columns:
                                        _ts = [str(x) for x in _pr['time'].tolist()]
                                        _probe_log.append('%s:%s' % (_inc, _ts))
                                    else:
                                        _cl = None
                                        try:
                                            if 'close' in _pr.columns:
                                                _cl = [round(float(x), 3) for x in _pr['close'].tolist()]
                                        except Exception:
                                            pass
                                        _probe_log.append('%s:notime rows=%d closes=%s' % (_inc, len(_pr), _cl))
                                elif _pr is not None and hasattr(_pr, 'keys'):
                                    _probe_log.append('%s:omap_keys=%s' % (_inc, list(_pr.keys())[:8]))
                                else:
                                    _probe_log.append('%s:type=%s' % (_inc, type(_pr).__name__))
                            except Exception as _e:
                                _probe_log.append('%s:exc=%s' % (_inc, str(_e)[:60]))
                        log.info('QSPROBE %s %s' % (_ksecs, ' | '.join(_probe_log)))
                    except Exception:
                        pass
                else:
                    log.info('QS_MINUTE_DIAG single cols=%s' % (list(_out.columns) if hasattr(_out, 'columns') else type(_out).__name__))
            except Exception:
                pass
        _synth_flags = {}
        if isinstance(_out, dict):
            for _k in list(_out.keys()):
                _out[_k], _synth_flags[_k] = _qs_synth_minute_bar_from_data(_k, _out[_k])
        elif isinstance(_out, _qs_pd.DataFrame):
            _code0 = kwargs.get('security_list')
            if _code0 is None:
                _code0 = kwargs.get('security')
            if _code0 is None and args:
                _code0 = args[0]
            if isinstance(_code0, (list, tuple)):
                _code0 = _code0[0] if len(_code0) else ''
            _out, _synth_flags[''] = _qs_synth_minute_bar_from_data(_code0, _out)
        _today_d = None
        try:
            _cdt = getattr(_QS_RUNTIME_CTX, 'current_dt', None)
            if _cdt is not None:
                _today_d = _qs_bar_date(_cdt)
        except Exception:
            pass
        if not _QS_MINUTE_SYNTH_LOGGED or _QS_MINUTE_SYNTH_LOGGED != _today_d:
            _QS_MINUTE_SYNTH_LOGGED = _today_d
            try:
                _trip = []
                for _ck, _cf in _synth_flags.items():
                    _ti = _QS_LAST_SYNTH_INFO.get(_ck)
                    if _ti:
                        _trip.append('%s=%s[close=%s preclose=%s t=%s]' % (_ck, _cf, _ti[1], _ti[2], str(_ti[0])[:19]))
                    else:
                        _trip.append('%s=%s' % (_ck, _cf))
                log.info('QS_MINUTE_SYNTH v=%s %s' % (_QS_WRAPPER_VERSION, ' '.join(_trip) or 'no-codes'))
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
_QS_MAX_ORDER_SHARES = 49000
_QS_SPLIT_LOT = 100
_QS_SPLIT_PX_BUFFER = 1.0
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
class _QSClampHooks:
    iso_avail = None
    iso_fill = None
try:
    _QSOrderRefState.target_percent_orig = order_target_percent
except NameError:
    _QSOrderRefState.target_percent_orig = None
try:
    _QSOrderRefState.percent_orig = order_percent
except NameError:
    _QSOrderRefState.percent_orig = None
_QS_SPLIT_DEPTH = 0
_QS_CASH_EPOCH_RESET = False
def _qs_split_order(security, value, px, cash_avail=None):
    '把目标金额拆成 ≤_QS_MAX_ORDER_SHARES 股的多笔 order(amount=...)。\n\n    - value: 目标金额（order_target_value 语义）；px: 现价（统一链 ① 层前收；<=0 回退）；\n    - cash_avail: 可用现金；None 时以 value 为预算上界（不放大，段额不超目标）。\n    - 返回 (order_list, total_shares)；order_list = [(code, amount), ...]；\n      无法拆（px<=0 / amount<=0 / 预算不足以买 1 手）返回 ([], 0)，调用方走原路径。\n    '
    if px is None or px <= 0 or value is None or (value <= 0):
        return ([], 0)
    n = value / px
    if n <= _QS_MAX_ORDER_SHARES:
        _budget = min(cash_avail, value) if cash_avail is not None else value
        if _budget < value:
            n = _budget / px
        amount = int(n / _QS_SPLIT_LOT) * _QS_SPLIT_LOT
        return ([(security, amount)], amount) if amount > 0 else ([], 0)
    k = int((n + _QS_MAX_ORDER_SHARES - 1) // _QS_MAX_ORDER_SHARES)
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
            break
        orders.append((security, seg_amount))
        total_cost += cost
    return (orders, sum((a for _, a in orders))) if orders else ([], 0)
_QS_MIN_REBALANCE_PCT = 0.005
def _qs_pos_amount(security):
    'P-D12 D6：平台侧现值查询——get_position(.SS/.SZ 内联归一).amount（异常视为空仓=D5）。\n\n    自包含归一（不依赖 P-D11 _qs_norm_code——该块可能未注入，跨模板引用会\n    被 LOCAL-API-WHITELIST 拦截）；订单场景最小集：5/6/9→.SS 其余→.SZ。'
    try:
        _s = str(security).strip().upper()
        _bare = _s.split('.', 1)[0]
        _code = _bare + ('.SS' if _bare[:1] in ('5', '6', '9') else '.SZ')
        _pos = get_position(_code)
        return float(getattr(_pos, 'amount', 0) or 0)
    except Exception:
        return 0.0
def _qs_noop_target(security, delta, reason):
    'P-D12：no-op 返回（None 语义=未成交；平台无 Order 对象，log 显式告知）。'
    try:
        log.warning('QS_NOOP_ORDER api=order_target_value code=%s delta=%.1f reason=%s' % (security, float(delta or 0), reason))
    except Exception:
        pass
    return None
def _qs_idempotent_admit(security, side):
    'PD-0018 F2：逻辑下单入口幂等判定 + 登记（一次）。\n\n    键 = [str(security), str(side)]（逻辑单级，与护栏幂等段只读判定同键同构）；\n    已存在 ⇒ QS_NOOP_ORDER 告警 + False；不存在 ⇒ 登记 + True。登记只发生在\n    逻辑入口（拆单循环内不再逐笔判定/登记 ⇒ 拆单 k 笔不自撞；order() 直调\n    路径由本函数补上幂等——2026-11-06 前该路径完全无幂等保护的既有盲区闭合）。\n    fail-open：状态不可得/异常 ⇒ 放行不吞单；异常告警带文本（Q042：禁空吞）。'
    try:
        _key = [str(security), str(side)]
        _st = getattr(g, 'state', None)
        if not isinstance(_st, dict):
            return True
        _done = _st.get('orders_done', [])
        if not isinstance(_done, list):
            _done = []
        if _key in _done:
            try:
                log.warning('QS_NOOP_ORDER code=%s side=%s reason=duplicate_idempotent' % (security, side))
            except Exception:
                pass
            return False
        _done.append(_key)
        _st['orders_done'] = _done
        return True
    except Exception as _e:
        try:
            log.warning('QS_IDEMPOTENT_ADMIT_ERROR code=%s side=%s err=%s' % (security, side, _e))
        except Exception:
            pass
        return True
_QS_CASH_WARNED_STATE = {'v': False}
_QS_CASH_REJECT_COUNT = [0]
_QS_CLAMP_UNARMED_COUNT = [0]
_QS_LEDGER_NEG_COUNT = [0]
_QS_LEDGER_NEG_WARNED = {}
_QS_LEDGER_NO_PX_WARNED = {}
_QS_RUNTIME_CTX = None
def _qs_capture_ctx(context):
    'D4-S7 #5：handle_data 入口捕获 context（转换管线注入一行）。'
    global _QS_RUNTIME_CTX
    _QS_RUNTIME_CTX = context
def _qs_register_split_order(security, order_id):
    "PD-0017 C：把拆单产生的**每一笔**委托号登记进策略审计台账 + 框架自持注册表。\n\n    背景（2026-11-02 模拟盘实测）：`order_target_value` 拆成 k 笔后只把末笔\n    `_ids[-1]` 交回策略，策略 `submit_target_value_order` 也只登记末笔 ⇒\n    `g.pending_order_audit` 漏记 k-1 笔，`audit_pending_orders` **永不核验**它们\n    （当时 15 笔挂单中 14 笔未进审计）。\n\n    登记对象：\n      1. 策略台账 `g.pending_order_audit`（字段与策略自登记同构；该审计函数对委托字段\n         访问全部用 `.get()`，故补最小 dict 安全，策略随后登记末笔会覆盖为更完整字段）；\n      2. 框架自持 `g.state['qs_cur_orders']`（供 PD-0017 D 的陈旧挂单巡检消费：\n         `after_trading_end` 落盘时滚动进 `qs_prev_orders`）。\n\n    全程受保护：无 `g` / 台账非 dict / 状态不可写时静默跳过，**绝不抛异常影响下单**。\n    不改订单序列、不改返回值 ⇒ 与本地引擎订单序列逐笔一致的同构约束不被破坏。\n    "
    _oid = None
    try:
        if order_id is not None:
            _oid = str(order_id)
    except Exception:
        _oid = None
    if not _oid:
        return
    try:
        _book = getattr(g, 'pending_order_audit', None)
        if isinstance(_book, dict) and _oid not in _book:
            _book[_oid] = {'security': str(security), 'asset_name': '', 'stage': 'split', 'action': 'auto', 'audit_count': 0}
    except Exception:
        pass
    try:
        _st = getattr(g, 'state', None)
        if isinstance(_st, dict):
            _cur = _st.get('qs_cur_orders', [])
            if not isinstance(_cur, list):
                _cur = []
            if _oid not in _cur:
                _cur.append(_oid)
            _st['qs_cur_orders'] = _cur
    except Exception:
        pass
def _qs_px_exec(security):
    'D4-S7 #8（2026-08-27 ZCode 裁定）：订单换算价 = 当日撮合价优先（与本地 D4-S6 ②层镜像）。\n\n    当日取法双端差异（2026-08-27 实证）：\n    - 本地引擎 get_history(count=1, include=False) = T-1 前收；include=True = 当日收盘（6.57 实证）；\n    - 平台 get_history(count=1) 在 before_trading_start 已返回 T 日 close（P-D9 L872-875 实证，include 语义不同）。\n    故优先 include=True（当日），平台不支持时回退 include=False（平台下仍=当日），再退 ① 层前收，\n    ≤0 返回 0（调用方 fail-open 原生）。current_price 函数本体零改动。\n    '
    for _inc in (True, False):
        try:
            _df = get_history(count=1, frequency='1d', field=['close'], security_list=security, fq='pre', include=_inc)
            if _df is not None and hasattr(_df, 'iloc') and (len(_df) > 0):
                _c = float(_df.iloc[-1].get('close', 0) or 0)
                if _c > 0:
                    return _c
        except Exception:
            pass
    _v = _qs_last_close_lookup(security)
    if _v and _v > 0:
        return _v
    return 0.0
def _qs_cash_avail():
    'D4-S7 #5：平台可用资金取数（fail-open）。\n    优先：_QS_RUNTIME_CTX.portfolio.cash（平台探针实证可用，2026-08-27）；\n    次选：平台 API get_positions()/get_position() 四字段探测（available_cash/available/cash/avail_cash）；\n    全不可得 → 返回 None（钳制不生效，保持平台现状全额下单）+ 一次性告警 QS_CASH_AVAIL_UNAVAILABLE。\n    禁止：取不到禁下单 / 静默逐笔告警 / 引用未登记平台 API（portability BLOCK）。'
    try:
        _ctx = _QS_RUNTIME_CTX
        if _ctx is not None:
            _cash = float(getattr(getattr(_ctx, 'portfolio', None), 'cash', 0) or 0)
            if _cash > 0:
                return _cash
    except Exception:
        pass
    _obj = None
    try:
        _obj = get_positions()
    except Exception:
        pass
    if _obj is None:
        try:
            _obj = get_position()
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
            log.warning('QS_CASH_AVAIL_UNAVAILABLE api=order cash_avail=unavailable（钳制不生效，维持平台全额下单语义——仅告警一次）')
        except Exception:
            pass
    return None
def _qs_cash_budget(security):
    "PD-0018 F3(a)：拆单可用预算（跨标的累计扣减，替代裸 _qs_cash_avail）。\n\n    平台无按股东账号取可用资金的接口（§9.2 实证：沪A A222087012 / 深A 0028389333\n    分账、资金不可互用），context.portfolio.cash 为全账户合并口径 ⇒ 退化为保守\n    预算：budget = _qs_cash_avail()（合并口径实时可用，已不含 T+1 卖出所得）\n    − g.state['qs_cash_reserved']（本次运行已承诺额，跨标的累计）。绝不静默\n    使用全账户口径——每次取数打 QS_CASH_BUDGET basis 日志明示口径与市场位。"
    global _QS_CASH_EPOCH_RESET
    _mkt = 'SZ'
    try:
        _b = str(security).strip().upper().split('.', 1)[0]
        _mkt = 'SH' if _b[:1] in ('5', '6', '9') else 'SZ'
    except Exception:
        pass
    if not _QS_CASH_EPOCH_RESET:
        _QS_CASH_EPOCH_RESET = True
        try:
            _st0 = getattr(g, 'state', None)
            if isinstance(_st0, dict):
                _st0['qs_cash_reserved'] = 0.0
                try:
                    log.info('QS_CASH_BUDGET basis=epoch_reset market=%s（本进程首次取预算：作废跨日残留 qs_cash_reserved）' % _mkt)
                except Exception:
                    pass
        except Exception:
            pass
    _cash = _qs_cash_avail()
    _res = 0.0
    try:
        _st = getattr(g, 'state', None)
        if isinstance(_st, dict):
            _res = float(_st.get('qs_cash_reserved', 0.0) or 0.0)
    except Exception as _e:
        _res = 0.0
        try:
            log.warning('QS_CASH_BUDGET market=%s reserved_read_error=%s' % (_mkt, _e))
        except Exception:
            pass
    if _cash is None:
        try:
            log.info('QS_CASH_BUDGET basis=unavailable market=%s reserved=%.1f（现金取数不可得，钳制不生效）' % (_mkt, _res))
        except Exception:
            pass
        return None
    try:
        log.info('QS_CASH_BUDGET basis=market_pool_conservative market=%s cash_avail=%.1f reserved=%.1f budget=%.1f（合并口径现金−跨标的已承诺；无按股东账号接口）' % (_mkt, _cash, _res, _cash - _res))
    except Exception:
        pass
    return _cash - _res
def _qs_check_order_state(order_id):
    "PD-0018 F3(b)：单笔受理核验（三态）。返回 (state, px)，绝不抛异常。\n\n    平台依据（Context7 /kay-ou/ptradeapi）：get_order(order_id) → Order\n    （order_id/filled/status）；get_all_orders 不能查待报/未报 ⇒「已报」不可由\n    平台直接证明 ⇒ 三态保守判定：\n      'final_ok'      终态非废单（str(status) ∈ 5/6/9——F6 归一：int/str 版本兼容）\n      'final_reject'  终态废单（str(status) == '8'）\n      'pending'       平台可见但未终态（已报未终态 / status 不可读）\n      'missing'       平台未出现（None/异常/空——未受理或未暴露待报态，保守告警）"
    try:
        _od = get_order(order_id)
        if _od is None:
            return ('missing', None)
        _ob = _od
        if isinstance(_od, (list, tuple)):
            _ob = _od[0] if len(_od) > 0 else None
        if _ob is None:
            return ('missing', None)
        _stt = ''
        try:
            if isinstance(_ob, dict):
                _stt = str(_ob.get('status', ''))
            else:
                _stt = str(getattr(_ob, 'status', ''))
        except Exception:
            _stt = ''
        _px = None
        try:
            _raw = _ob.get('price') if isinstance(_ob, dict) else getattr(_ob, 'price', None)
            if _raw is not None and float(_raw) > 0:
                _px = float(_raw)
        except Exception:
            _px = None
        if _stt in ('5', '6', '8', '9'):
            return ('final_reject' if _stt == '8' else 'final_ok', _px)
        return ('pending', _px)
    except Exception as _e:
        try:
            log.warning('QS_ACCEPT_CHECK_ERROR order=%s err=%s' % (order_id, _e))
        except Exception:
            pass
        return ('missing', None)
def _qs_reserve_cash(security, shares, px):
    'PD-0018 F3(a)：子委托提交后按承诺额累加 qs_cash_reserved（价×数量）。\n\n    px = 受理核验读到的委托/成交价，读不到时由调用方传拆单换算价 _px_exec 兜底。\n    只记账不改下单序列；异常告警带文本（Q042），绝不影响下单主流程。'
    try:
        _amt = float(shares or 0) * float(px or 0)
        if _amt <= 0:
            return
        _st = getattr(g, 'state', None)
        if isinstance(_st, dict):
            _st['qs_cash_reserved'] = _qs_ledger_nn('qs_cash_reserved', float(_st.get('qs_cash_reserved', 0.0) or 0.0) + _amt, security)
    except Exception as _e:
        try:
            log.warning('QS_CASH_RESERVE_ERROR code=%s err=%s' % (security, _e))
        except Exception:
            pass
def _qs_ledger_nn(field, value, security):
    "PD-2026-0020 §12 期1 b-2/B4（②审）：账本字段非负不变量守卫。\n\n    账本字段（reserved / cash_base / pos 三类）任何更新算出 < 0 时不静默钳零：\n    ①保留 0 作为计算值（防负值进一步传染预算/隔夜市值等下游计算）；\n    ②QS_LEDGER_NEGATIVE log.error 按 (field, code) 一次（防刷屏；计数仍逐次累加）；\n    ③计入 _QS_LEDGER_NEG_COUNT 供 RECONCILE 汇总。\n    本文件账本字段 = g.state['qs_cash_reserved']；cash_base/pos 属隔离台账\n    （isolation_template，本批不改），由隔离批按同名守卫同类接入。\n    返回守卫后的值；value 非数值时原样返回（不放大异常面）。"
    try:
        _v = float(value)
    except (TypeError, ValueError):
        return value
    if _v >= 0:
        return _v
    _QS_LEDGER_NEG_COUNT[0] += 1
    try:
        _key = (str(field), str(security))
        if not _QS_LEDGER_NEG_WARNED.get(_key):
            _QS_LEDGER_NEG_WARNED[_key] = True
            log.error('QS_LEDGER_NEGATIVE field=%s value=%s code=%s' % (field, _v, security))
    except Exception:
        pass
    return 0.0
def _qs_ledger_release(security, amount, px=None, record_iso=True):
    "PD-2026-0020 §12 期1 b-1/B3（②审·P1 关键缺口）：卖出成交后账本回写钩子。\n\n    缺口：qs_cash_reserved 只在买入受理时累加（_qs_reserve_cash），卖出从不\n    回补 ⇒ 调仓「卖旧买新」日 reserved 只增不减，预算被永久性挤占。\n    语义：卖出（amount < 0）⇒ reserved = max(0, reserved − |amount| × px)\n    （经 _qs_ledger_nn 守卫：算出 <0 不静默钳零，QS_LEDGER_NEGATIVE 可见），\n    使后续 _qs_cash_budget()（= 现金 − qs_cash_reserved）与隔离版\n    _qs_iso_cash_avail()（= cash_base − reserved）均能反映额度回升。\n    px 取不到（≤0，含 _qs_px_exec 昨收链兜底后仍 ≤0）⇒ 不改 reserved\n    （保守 fail-closed：不虚增额度）+ QS_LEDGER_RELEASE_NO_PX 告警一次/代码。\n\n    双层结构：\n    ① record_iso=True 时 try 调 _qs_iso_record_fill（仅隔离产物在位；globals()\n       探测——非隔离产物无此名自然跳过，不触发 NameError/白名单拦截）：\n       隔离台账 pos/reserved 由它记账。order() 包装路径必须传 record_iso=False\n       ——隔离 TAIL 正式批已把 order_orig 包成 _qs_iso_order_orig（受理即记账），\n       再记即双重。order_value 卖出路径（value_orig 未被隔离包装）保留 True。\n    ② 通用账本 g.state['qs_cash_reserved'] 按公式释放——隔离模式下预算已被\n       _qs_cash_budget 接管（不读此字段），此处为影子记账，与 _qs_reserve_cash\n       在隔离模式仍写此字段同口径。\n    隔离版覆盖生效方式：若后续隔离 TAIL 定义同名 _qs_ledger_release（模块尾\n    后注入者胜，Python 模块级名字后者定义覆盖），本通用版整体被覆盖，各调用\n    点在调用期解析全局名 ⇒ 自动走隔离版。绝不抛异常影响下单主流程。"
    if record_iso:
        try:
            if _QSClampHooks.iso_fill is not None:
                _QSClampHooks.iso_fill(security, amount, px)
        except Exception:
            pass
    try:
        if amount is None or float(amount) >= 0:
            return
    except (TypeError, ValueError):
        return
    _px = None
    try:
        _px = None if px is None else float(px)
    except (TypeError, ValueError):
        _px = None
    if _px is None or _px <= 0:
        _px = _qs_px_exec(security)
    if _px is None or _px <= 0:
        try:
            _ck = str(security)
            if not _QS_LEDGER_NO_PX_WARNED.get(_ck):
                _QS_LEDGER_NO_PX_WARNED[_ck] = True
                log.warning('QS_LEDGER_RELEASE_NO_PX code=%s（取不到估值价 ⇒ 保守不释放额度）' % security)
        except Exception:
            pass
        return
    try:
        _st = getattr(g, 'state', None)
        if isinstance(_st, dict):
            _res = float(_st.get('qs_cash_reserved', 0.0) or 0.0)
            _st['qs_cash_reserved'] = _qs_ledger_nn('qs_cash_reserved', _res - abs(float(amount)) * _px, security)
    except Exception as _e:
        try:
            log.warning('QS_LEDGER_RELEASE_ERROR code=%s err=%s' % (security, _e))
        except Exception:
            pass
def _qs_report_acceptance(api, security, order_ids):
    'PD-0018 F3(b)：一次逻辑下单的受理核验收尾（部分失败必须可见——新增检测型）。\n\n    三态分列：已受理 n / 拒单 m / 待核验 k（含平台未出现）；存在拒单或待核验 ⇒\n    log.warning 分列，全终态非废单 ⇒ log.info；平台未出现逐笔 warning。存在\n    非终态或未出现时绝不打印「提交状态=成功」（2026-11-06 静默少建 ≈649 万、\n    框架却报成功——Q051「静默兜底谎报」教训：查不到禁兜底成成功）。只读核验。'
    if not order_ids:
        return
    _ok = []
    _rej = []
    _pend = []
    _miss = []
    for _oid in order_ids:
        _st, _ = _qs_check_order_state(_oid)
        if _st == 'final_ok':
            _ok.append(_oid)
        elif _st == 'final_reject':
            _rej.append(_oid)
        else:
            _pend.append(_oid)
            if _st == 'missing':
                _miss.append(_oid)
    for _oid in _miss:
        try:
            log.warning('QS_ORDER_MISSING api=%s code=%s order=%s（平台未出现：未受理或未暴露待报态——保守计失败，禁止兜底成成功）' % (api, security, _oid))
        except Exception:
            pass
    try:
        if _rej or _pend:
            log.warning('QS_ORDER_ACCEPT api=%s code=%s total=%d accepted=%d rejected=%d pending=%d（部分失败：拒单=%s 待核验=%s）' % (api, security, len(order_ids), len(_ok), len(_rej), len(_pend), ','.join((str(_o) for _o in _rej)), ','.join((str(_o) for _o in _pend))))
        else:
            log.info('QS_ORDER_ACCEPT api=%s code=%s total=%d accepted=%d rejected=0 pending=0（全部已受理终态）' % (api, security, len(order_ids), len(_ok)))
    except Exception:
        pass
def order_target_value(security, value, *args, **kwargs):
    if value is None:
        return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
    if value == 0:
        return _QSOrderRefState.target_orig(security, 0, *args, **kwargs)
    try:
        _dl = get_stock_status([str(security)], query_type='DELISTING')
        if _dl and _dl.get(str(security), False):
            log.warning('QS_DELIST_ORDER code=%s status=delisted（继续下单——平台数据矛盾检测，不吞单）' % security)
    except Exception:
        pass
    _px_exec = _qs_px_exec(security)
    if _px_exec <= 0:
        return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
    _px = _qs_last_close_lookup(security)
    if _px <= 0:
        return _QSOrderRefState.target_orig(security, value, *args, **kwargs)
    _amt = _qs_pos_amount(security)
    _current = _amt * _px
    _delta = value - _current
    if _current > 0 and abs(_delta) / _current < _QS_MIN_REBALANCE_PCT:
        return _qs_noop_target(security, _delta, 'below_rebalance_threshold')
    if _delta <= 0:
        if _current > 0:
            _tid = _QSOrderRefState.target_orig(security, value, *args, **kwargs)
            if _tid is not None:
                _qs_ledger_release(security, _delta / _px_exec, _px_exec)
            return _tid
        return _qs_noop_target(security, 0.0, 'already_flat')
    if not _qs_idempotent_admit(security, 'auto'):
        return _qs_noop_target(security, _delta, 'duplicate_idempotent')
    _cash = _qs_cash_budget(security)
    if _cash is None or _cash <= 0:
        try:
            log.error('QS_CASH_BUDGET_ZERO api=%s code=%s budget=%s reason=%s' % ('order_target_value', security, _cash, 'unavailable' if _cash is None else 'zero_budget'))
        except Exception:
            pass
        _QS_CASH_REJECT_COUNT[0] += 1
        return None
    orders, _tot = _qs_split_order(security, _delta, _px_exec, cash_avail=_cash)
    if not orders:
        try:
            log.warning('QS_ZERO_ORDER api=order_target_value code=%s delta=%.1f px=%.4f one_lot_value=%.1f reason=delta_below_one_lot' % (security, _delta, _px_exec, _px_exec * 100))
        except Exception:
            pass
        return _qs_noop_target(security, _delta, 'delta_below_one_lot')
    _ids = []
    global _QS_SPLIT_DEPTH
    _QS_SPLIT_DEPTH += 1
    try:
        for _code, _amt2 in orders:
            _id2 = _QSOrderRefState.order_wrapped(_code, _amt2)
            if _id2 is None:
                try:
                    log.warning('QS_SPLIT_MISS api=order_target_value code=%s shares=%s reason=order_returned_none' % (_code, _amt2))
                except Exception:
                    pass
                continue
            _ids.append(_id2)
            _qs_register_split_order(_code, _id2)
    finally:
        _QS_SPLIT_DEPTH -= 1
    _qs_report_acceptance('order_target_value', security, _ids)
    return _ids[-1] if _ids else None
def _qs_budget_clamp(security, amount):
    'PD-2026-0020 §12 期1 甲-order（②审·P0）：买入按虚拟额度折算可买股数。\n\n    注意：本函数所在模板会被 .format() 渲染 ⇒ **禁止使用花括号字面量**（dict/set），\n    一律用 tuple 返回。\n\n    背景（11-10 实机取证）：策略走 order(security, amount) 数量版，自己用真实持仓算\n    delta ⇒ 隔离层原持仓侧介入点（_qs_pos_amount 覆盖 / target_orig 阻断）从未被触达\n    （PD-2026-0021）。唯一可行介入点就是 order() 包装本身。\n\n    语义（§12.2）：仅买入(amount>0)且隔离启用时折算；可买=floor(额度/px/100)*100，\n    amount=min(amount,可买)；卖出一律不钳（G1/D1 教训）；px 缺失则不钳(fail-open)\n    并计入未武装计数（B2）。\n    返回 (数量, applied, orig, clamp, avail, px)。\n    '
    _no = (amount, False, amount, None, None, None)
    try:
        if amount is None or amount <= 0:
            return _no
        if _QSClampHooks.iso_avail is None:
            return _no
        avail = float(_QSClampHooks.iso_avail())
        px = _qs_px_exec(security)
        if px is None or px <= 0:
            try:
                _QS_CLAMP_UNARMED_COUNT[0] += 1
                log.warning('QS_CLAMP_UNARMED code=%s amount=%s reason=px_unavailable' % (security, amount))
            except Exception:
                pass
            return (amount, False, amount, None, avail, px)
        if avail <= 0:
            return (0, True, amount, 0, avail, px)
        lot = int(_QS_SPLIT_LOT) if _QS_SPLIT_LOT else 100
        can_buy = int(avail / px / lot) * lot
        if amount <= can_buy:
            return (amount, False, amount, can_buy, avail, px)
        return (can_buy, True, amount, can_buy, avail, px)
    except Exception:
        return _no
def order(security, amount, *args, **kwargs):
    global _QS_SPLIT_DEPTH
    if _QS_SPLIT_DEPTH <= 0:
        if not _qs_idempotent_admit(security, 'auto'):
            try:
                log.warning('QS_NOOP_ORDER api=order code=%s reason=duplicate_idempotent' % security)
            except Exception:
                pass
            return None
    amount, _cl_applied, _cl_orig, _cl_can, _cl_avail, _cl_px = _qs_budget_clamp(security, amount)
    if _cl_applied:
        try:
            log.info('QS_CLAMP_ORDER api=order code=%s orig=%s can_buy=%s avail=%s px=%s' % (security, _cl_orig, _cl_can, _cl_avail, _cl_px))
        except Exception:
            pass
        if amount is None or amount <= 0:
            try:
                log.error('QS_CLAMP_ORDER_REJECT api=order code=%s orig=%s avail=%s' % (security, _cl_orig, _cl_avail))
            except Exception:
                pass
            return None
    _ids_o = []
    if amount is None or amount <= 0 or amount <= _QS_MAX_ORDER_SHARES:
        _id0 = _QSOrderRefState.order_orig(security, amount, *args, **kwargs)
        _qs_register_split_order(security, _id0)
        if _id0 is not None:
            _ids_o.append(_id0)
            _ost0, _opx0 = _qs_check_order_state(_id0)
            _qs_reserve_cash(security, amount, _opx0 if _opx0 is not None else _qs_px_exec(security))
            if amount is not None and amount < 0:
                _qs_ledger_release(security, amount, _opx0 if _opx0 is not None else _qs_px_exec(security), record_iso=False)
        if _QS_SPLIT_DEPTH <= 0:
            _qs_report_acceptance('order', security, _ids_o)
        return _id0
    k = int((amount + _QS_MAX_ORDER_SHARES - 1) // _QS_MAX_ORDER_SHARES)
    per = int(amount // k / _QS_SPLIT_LOT) * _QS_SPLIT_LOT
    if per <= 0:
        _id1 = _QSOrderRefState.order_orig(security, amount, *args, **kwargs)
        _qs_register_split_order(security, _id1)
        if _id1 is not None:
            _ids_o.append(_id1)
            _ost1, _opx1 = _qs_check_order_state(_id1)
            _qs_reserve_cash(security, amount, _opx1 if _opx1 is not None else _qs_px_exec(security))
        if _QS_SPLIT_DEPTH <= 0:
            _qs_report_acceptance('order', security, _ids_o)
        return _id1
    _id = None
    _placed = 0
    for _i in range(k):
        _seg = per if _i < k - 1 else amount - _placed
        _seg = int(_seg / _QS_SPLIT_LOT) * _QS_SPLIT_LOT
        if _seg <= 0:
            break
        _id = _QSOrderRefState.order_orig(security, _seg, *args, **kwargs)
        _qs_register_split_order(security, _id)
        if _id is not None:
            _ids_o.append(_id)
            _ost2, _opx2 = _qs_check_order_state(_id)
            _qs_reserve_cash(security, _seg, _opx2 if _opx2 is not None else _qs_px_exec(security))
        _placed += _seg
    if _QS_SPLIT_DEPTH <= 0:
        _qs_report_acceptance('order', security, _ids_o)
    return _id if _id is not None else _QSOrderRefState.order_orig(security, amount, *args, **kwargs)
_QSOrderRefState.order_wrapped = order
def order_value(security, value, *args, **kwargs):
    'order_value 拆单包装：金额语义 → ② 层换算价（D4-S6/D4-S7 #8 修复，与 order_target_value 同链路）。\n\n    PD-0018 F2：幂等判定上移逻辑入口一次（判定失败不进入拆单）；\n    PD-0018 F3：预算改 _qs_cash_budget（跨标的累计）＋受理核验收尾（部分失败可见）。'
    if not _qs_idempotent_admit(security, 'auto'):
        try:
            log.warning('QS_NOOP_ORDER api=order_value code=%s reason=duplicate_idempotent' % security)
        except Exception:
            pass
        return None
    _px_exec = _qs_px_exec(security)
    if _px_exec <= 0:
        return _QSOrderRefState.value_orig(security, value, *args, **kwargs)
    _cash = _qs_cash_budget(security)
    try:
        _qs_ov_buy = value is not None and float(value) > 0
    except (TypeError, ValueError):
        _qs_ov_buy = False
    if _qs_ov_buy and (_cash is None or _cash <= 0):
        try:
            log.error('QS_CASH_BUDGET_ZERO api=%s code=%s budget=%s reason=%s' % ('order_value', security, _cash, 'unavailable' if _cash is None else 'zero_budget'))
        except Exception:
            pass
        _QS_CASH_REJECT_COUNT[0] += 1
        return None
    orders, _tot = _qs_split_order(security, value, _px_exec, cash_avail=_cash)
    if not orders:
        if value is not None and value < 0:
            _vid = _QSOrderRefState.value_orig(security, value, *args, **kwargs)
            if _vid is not None:
                _qs_ledger_release(security, value / _px_exec, _px_exec)
            return _vid
        return _QSOrderRefState.value_orig(security, value, *args, **kwargs)
    _ids = []
    global _QS_SPLIT_DEPTH
    _QS_SPLIT_DEPTH += 1
    try:
        for _code, _amt in orders:
            _idv = _QSOrderRefState.order_wrapped(_code, _amt)
            if _idv is None:
                try:
                    log.warning('QS_SPLIT_MISS api=order_value code=%s shares=%s reason=order_returned_none' % (_code, _amt))
                except Exception:
                    pass
                continue
            _ids.append(_idv)
    finally:
        _QS_SPLIT_DEPTH -= 1
    _qs_report_acceptance('order_value', security, _ids)
    return _ids[-1] if _ids else None
def order_target_percent(security, percent, *args, **kwargs):
    'order_target_percent 包装：比例语义的目标仓——无法在模板内可靠取组合总值\n    （依赖运行时 context），保守回退原 API（平台 percent 单按当前市值折算，超限风险低，\n    与 px=0 回退同语义）；保留入口覆盖保证双端 API 集合一致。'
    return _QSOrderRefState.target_percent_orig(security, percent, *args, **kwargs)
def order_percent(security, percent, *args, **kwargs):
    'order_percent 包装：同 order_target_percent，回退原 API（见上）。'
    return _QSOrderRefState.percent_orig(security, percent, *args, **kwargs)
def _qs_last_close_lookup(code):
    '统一链 ① 层（前收）：优先 _qs_last_close 框架缓存（A2 由 get_history 链记录）。\n\n    缓存格式 {bare_code: (day, close)}：返回当日记录的最近日均线 close；\n    跨日由记录 hook 的 stamp 校验自动失效（PIT 纪律）。\n    '
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
    cache = None
    stamp = None
def _qs_record_trade_day(df):
    "从 get_history 返回体最后一行推断交易日（'YYYY-MM-DD'）。\n\n    优先 trade_date 列（扩展合成列），其次 time/datetime，最后 index。\n    "
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
    '调用前一版 get_history（_QSHistoryChainState.prev），提取最近一根已完成日线\n    close 写入缓存（PIT 纪律）。'
    result = _QSHistoryChainState.prev(*args, **kwargs)
    try:
        unit = kwargs.get('frequency') or kwargs.get('unit') or '1d'
        if str(unit) != '1d':
            return result
        fqv = kwargs.get('fq')
        if fqv is None or str(fqv) not in ('pre',):
            return result
        day = _qs_record_trade_day(result if not isinstance(result, dict) else next(iter(result.values()), None))
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
            if df is not None and hasattr(df, 'iloc') and (len(df) > 0):
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
                        sec = kwargs.get('security_list') or kwargs.get('security') or (args[0] if args else None)
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
    '统一链 current_price（PTrade 转换侧）。\n\n    平台实证（2026-08-22 冒烟）：**current_price 不是真实 PTrade API**——模块加载期\n    引用即 NameError（平台仅注入 order/get_history/get_trade_days 等标准 API；此前\n    策略运行 current_price 返回 0 正是 NameError 被 try/except 吞掉）。故本侧统一链\n    只含 ① 前收（框架缓存）→ ③ get_history 兜底；本地 QuantStudio 侧另有模块级\n    注入的统一链（含 ② 原 API 语义，ptrade_api 内）。返回 0 = 不可得（与旧行为一致）。\n    '
    v = _qs_last_close_lookup(security)
    if v and v > 0:
        return v
    try:
        df = get_history(count=1, frequency='1d', field=['close'], security_list=security, fq='pre', include=False)
        if df is not None and hasattr(df, 'iloc') and (len(df) > 0):
            c = float(df.iloc[-1].get('close', 0) or 0)
            if c > 0:
                return c
    except Exception:
        pass
    return 0.0
STRATEGY_ID = 'tech_etf_mvo_rotation'
DESIGN_VERSION = '2.0'
def _ensure_runtime_state():
    'Idempotently create every g field used by any callback.'
    if not hasattr(g, 'tech_pool'):
        g.tech_pool = ['515000.SS', '512760.SS', '515880.SS', '512720.SS', '159995.SZ', '588000.SS', '515050.SS', '512480.SS']
    if not hasattr(g, 'hedge_pool'):
        g.hedge_pool = ['518880.SS', '511260.SS']
    if not hasattr(g, 'lookback'):
        g.lookback = 252
    if not hasattr(g, 'mvo_lookback'):
        g.mvo_lookback = 120
    if not hasattr(g, 'top_n'):
        g.top_n = 3
    if not hasattr(g, 'w_ann'):
        g.w_ann = 1.0
    if not hasattr(g, 'w_sharpe'):
        g.w_sharpe = 2.0
    if not hasattr(g, 'w_win'):
        g.w_win = 1.0
    if not hasattr(g, 'w_mdd'):
        g.w_mdd = 1.0
    if not hasattr(g, 'vol_window'):
        g.vol_window = 21
    if not hasattr(g, 'vol_pct_threshold'):
        g.vol_pct_threshold = 0.8
    if not hasattr(g, 'vol_scale'):
        g.vol_scale = 0.5
    if not hasattr(g, 'macd_scale'):
        g.macd_scale = 0.5
    if not hasattr(g, 'last_rebalance_week'):
        g.last_rebalance_week = None
def _bare(code):
    '提取裸 6 位代码，跨 .SS/.SZ/.XSHG/.SH 等后缀比较。'
    s = str(code)
    digits = ''
    for ch in s:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return digits[:6]
def _get_close(code, count):
    '便携取前复权收盘价序列（np.array，升序）；失败/空返回 None。'
    try:
        hist = get_history(count, frequency='1d', field=['close'], security_list=code, fq='pre', include=False)
    except Exception as e:
        log.warning('get_history 失败 %s: %s' % (code, e))
        return None
    try:
        if hist is None:
            return None
        if isinstance(hist, dict):
            obj = None
            for k in hist:
                obj = hist[k]
                break
            if obj is None:
                return None
            hist = obj
        if hasattr(hist, 'columns'):
            if 'close' not in list(hist.columns):
                return None
            arr = np.asarray(hist['close'])
        else:
            arr = hist
        arr = np.asarray(arr, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) == 0:
            return None
        return arr
    except Exception as e:
        log.warning('历史解析失败 %s: %s' % (code, e))
        return None
def _daily_returns(close):
    '日收益序列。'
    prev = close[:-1]
    nxt = close[1:]
    mask = prev > 0
    if mask.sum() < 2:
        return None
    return (nxt[mask] - prev[mask]) / prev[mask]
def _pct_rank(values, v):
    'v 在 values 中的百分位（0~1），截面相对打分用。'
    n = len(values)
    if n <= 1:
        return 1.0
    less = 0
    for x in values:
        if x < v:
            less += 1
    return float(less) / float(n - 1)
def _four_factor_metrics(close):
    '四维指标：年化收益、夏普、胜率、最大回撤绝对值。数据不足返回 None。'
    if close is None or len(close) < g.lookback:
        return None
    window = close[-(g.lookback + 1):]
    rets = _daily_returns(window)
    if rets is None or len(rets) < g.lookback - 2:
        return None
    n = len(rets)
    start_px = window[0]
    end_px = window[-1]
    if start_px <= 0:
        return None
    ann_ret = (end_px / start_px) ** (252.0 / n) - 1.0
    mu = float(np.mean(rets))
    sigma = float(np.std(rets))
    sharpe = mu / sigma * float(np.sqrt(252.0)) if sigma > 1e-12 else 0.0
    win_rate = float((rets > 0).sum()) / float(n)
    nav = np.cumprod(1.0 + rets)
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1.0
    mdd = abs(float(np.min(dd)))
    return {'ann': ann_ret, 'sharpe': sharpe, 'win': win_rate, 'mdd': mdd}
def _ema(arr, n):
    '便携 EMA（pandas ewm, adjust=False），返回 np.array。'
    return pd.Series(arr).ewm(span=n, adjust=False).mean().values
def _macd_dead_cross(close):
    'MACD(12,26,9) 死叉判定：DIF 上穿后下穿 DEA（最新两根）。'
    if close is None or len(close) < 35:
        return False
    try:
        dif = _ema(close, 12) - _ema(close, 26)
        dea = _ema(dif, 9)
        return bool(dif[-2] >= dea[-2] and dif[-1] < dea[-1])
    except Exception:
        return False
def _vol_percentile(close):
    '最新 21 日年化波动率在其 252 日历史序列中的百分位（0~1）；不足返回 None。'
    if close is None or len(close) < g.lookback:
        return None
    rets = _daily_returns(close[-(g.lookback + 1):])
    if rets is None or len(rets) < g.vol_window + 20:
        return None
    try:
        s = pd.Series(rets)
        vol = s.rolling(g.vol_window).std() * float(np.sqrt(252.0))
        vol = vol.values
        vol = vol[np.isfinite(vol)]
        if len(vol) < 20:
            return None
        latest = vol[-1]
        hist = vol[:-1]
        return float((hist < latest).sum()) / float(len(hist))
    except Exception:
        return None
def _mvo_weights(codes, closes):
    '均值方差优化（做多-only、满仓、期望收益不低于入选科技均值）。\n\n    codes: 入选科技 + 可用避险资产；closes: {code: close_array}\n    返回 {code: weight}；失败返回 None（调用方回退等权）。\n    '
    try:
        m = g.mvo_lookback
        ret_cols = []
        valid = []
        for c in codes:
            cl = closes.get(c)
            if cl is None or len(cl) < m + 1:
                continue
            r = _daily_returns(cl[-(m + 1):])
            if r is None or len(r) < m - 2:
                continue
            ret_cols.append(r[-m:])
            valid.append(c)
        k = len(valid)
        if k == 0:
            return None
        min_len = min((len(r) for r in ret_cols))
        ret_cols = [r[-min_len:] for r in ret_cols]
        R = np.vstack(ret_cols)
        mu = np.mean(R, axis=1)
        cov = np.cov(R)
        cov = cov + np.eye(k) * 1e-08
        inv = np.linalg.inv(cov)
        ones = np.ones(k)
        w_gmv = inv.dot(ones) / float(ones.dot(inv).dot(ones))
        tech_idx = [i for i, c in enumerate(valid) if c in list(g.tech_pool)]
        if len(tech_idx) == 0:
            return None
        mu_min = float(np.mean(mu[tech_idx]))
        w = w_gmv
        if float(mu.dot(w_gmv)) < mu_min:
            best = int(np.argmax(mu))
            w_ret = np.zeros(k)
            w_ret[best] = 1.0
            chosen = None
            for step in range(1, 21):
                alpha = step / 20.0
                cand = (1.0 - alpha) * w_gmv + alpha * w_ret
                if float(mu.dot(cand)) >= mu_min:
                    chosen = cand
                    break
            if chosen is not None:
                w = chosen
        w = np.clip(w, 0.0, None)
        total = float(w.sum())
        if total <= 1e-12 or not np.isfinite(total):
            return None
        w = w / total
        return {valid[i]: float(w[i]) for i in range(k)}
    except Exception as e:
        log.warning('MVO 求解失败，回退等权: %s' % e)
        return None
def initialize(context):
    '设基准、初始化状态、注册周频调仓调度。'
    # PTRADE-DEPLOY:PERSIST BEGIN
    try:
        with open(_PD_NOTEBOOK + 'tech_etf_mvo_rotation_state.pkl', 'rb') as _pf:
            _saved_state = pickle.load(_pf)
        if not isinstance(_saved_state, dict) or _saved_state.get('version') != 1:
            log.warning('PTRADE-DEPLOY 持久化状态版本不匹配，回退默认状态')
            _saved_state = None
    except Exception:
        _saved_state = None
    g.state = _saved_state if _saved_state is not None else {'version': 1}
    try:
        if isinstance(g.state, dict):
            g.state['orders_done'] = []
    except Exception:
        pass
    # PTRADE-DEPLOY:PERSIST END
    _ensure_runtime_state()
    set_benchmark('000300.SS')
    log.info('[%s] 初始化完成: 科技池%d只 避险池%d只 top%d 回看%d日' % (STRATEGY_ID, len(g.tech_pool), len(g.hedge_pool), g.top_n, g.lookback))
    # PTRADE-DEPLOY:MOUNT BEGIN
    run_daily(context, _deploy_daily_exec, time='14:50')
    # PTRADE-DEPLOY:MOUNT END
# PTRADE-DEPLOY:MOUNT BEGIN
def _deploy_daily_exec(context, data=_qs_data_proxy()):
    '每日 15:00 被调度；ISO 周变化时执行评分+MVO+风控+调仓。'
    _ensure_runtime_state()
    dt = context.current_dt
    try:
        if hasattr(dt, 'isocalendar'):
            iso = dt.isocalendar()
            week_key = (int(iso[0]), int(iso[1]))
        else:
            week_key = (int(pd.Timestamp(str(dt)[:10]).isocalendar()[0]), int(pd.Timestamp(str(dt)[:10]).isocalendar()[1]))
    except Exception:
        week_key = str(dt)[:10]
    if g.last_rebalance_week == week_key:
        return
    g.last_rebalance_week = week_key
    closes = {}
    for code in list(g.tech_pool) + list(g.hedge_pool):
        cl = _get_close(code, g.lookback + 1)
        if cl is not None and len(cl) >= g.lookback:
            closes[code] = cl
        else:
            log.info('历史不足/缺数据，跳过: %s' % code)
    tech_available = [c for c in g.tech_pool if c in closes]
    if len(tech_available) == 0:
        log.info('[%s] %s 暖机期或数据不可用，本期持币不交易' % (STRATEGY_ID, str(dt)[:10]))
        return
    metrics = {}
    for code in tech_available:
        m = _four_factor_metrics(closes[code])
        if m is not None:
            metrics[code] = m
    if len(metrics) == 0:
        log.info('[%s] %s 指标计算为空，本期持币不交易' % (STRATEGY_ID, str(dt)[:10]))
        return
    anns = [m['ann'] for m in metrics.values()]
    sharpes = [m['sharpe'] for m in metrics.values()]
    wins = [m['win'] for m in metrics.values()]
    mdds = [m['mdd'] for m in metrics.values()]
    scored = []
    for code, m in metrics.items():
        score = g.w_ann * _pct_rank(anns, m['ann']) + g.w_sharpe * _pct_rank(sharpes, m['sharpe']) + g.w_win * _pct_rank(wins, m['win']) + g.w_mdd * (1.0 - _pct_rank(mdds, m['mdd']))
        scored.append((code, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    selected = [c for c, s in scored[:g.top_n]]
    log.info('[%s] %s 评分排名: %s' % (STRATEGY_ID, str(dt)[:10], ', '.join(('%s=%.3f' % (c, s) for c, s in scored))))
    mvo_codes = list(selected)
    for hc in g.hedge_pool:
        if hc in closes:
            mvo_codes.append(hc)
    weights = _mvo_weights(mvo_codes, closes)
    if weights is None:
        weights = {c: 1.0 / len(selected) for c in selected}
        log.info('[%s] MVO 回退等权: %s' % (STRATEGY_ID, str(selected)))
    for code in selected:
        if weights.get(code, 0.0) > 0.0 and _macd_dead_cross(closes[code]):
            weights[code] = weights[code] * g.macd_scale
            log.info('[%s] %s MACD死叉，权重减半 -> %.3f' % (STRATEGY_ID, code, weights[code]))
    vol_trigger = False
    for code in selected:
        pct = _vol_percentile(closes[code])
        if pct is not None and pct > g.vol_pct_threshold:
            vol_trigger = True
            log.info('[%s] %s 21日波动率百分位 %.2f > %.2f，触发降仓' % (STRATEGY_ID, code, pct, g.vol_pct_threshold))
    if vol_trigger:
        for code in list(weights.keys()):
            weights[code] = weights[code] * g.vol_scale
        log.info('[%s] 组合总仓位收紧至 %.0f%%' % (STRATEGY_ID, g.vol_scale * 100))
    weights = {c: w for c, w in weights.items() if w > 0.0001}
    target_bares = set((_bare(c) for c in weights.keys()))
    total_value = float(context.portfolio.portfolio_value)
    try:
        positions = context.portfolio.positions
        held_keys = list(positions.keys())
    except Exception:
        held_keys = []
    for key in held_keys:
        if _bare(key) not in target_bares:
            try:
                amount = float(getattr(positions[key], 'amount', 0))
            except Exception:
                amount = 0.0
            if amount > 0:
                # PTRADE-DEPLOY:GUARD BEGIN
                if _deploy_guard_pass(None, 'sell'):
                    oid = order_target_value(security=key, value=0.0)
                else:
                    log.warning('PTRADE-DEPLOY 护栏拦截下单: %s (%s)' % (None, 'sell'))
                # PTRADE-DEPLOY:GUARD END
                if oid is None:
                    log.warning('[%s] 清仓委托失败: %s' % (STRATEGY_ID, key))
                else:
                    log.info('[%s] 清仓: %s' % (STRATEGY_ID, key))
    for (code, w) in weights.items():
        target_value = total_value * w
        if target_value < 1000.0:
            continue
        # PTRADE-DEPLOY:GUARD BEGIN
        if _deploy_guard_pass(None, 'auto'):
            oid = order_target_value(security=code, value=target_value)
        else:
            log.warning('PTRADE-DEPLOY 护栏拦截下单: %s (%s)' % (None, 'auto'))
        # PTRADE-DEPLOY:GUARD END
        if oid is None:
            log.warning('[%s] 委托失败: %s -> %.0f' % (STRATEGY_ID, code, target_value))
        else:
            log.info('[%s] 调仓: %s 目标权重 %.3f 市值 %.0f' % (STRATEGY_ID, code, w, target_value))
# PTRADE-DEPLOY:MOUNT END
