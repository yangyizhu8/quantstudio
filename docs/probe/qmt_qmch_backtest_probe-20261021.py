#coding:gbk
# =============================================================================
# QS-QMCH 回测探针（2026-10-21）  仅用于 QMT「回测」入口，勿在交易模式运行
# 覆盖：QMCH-01 撮合价口径(open/close) / QMCH-02 涨跌停可成交性 / QMCH-03 T+1 约束
# 安全闸：未确认回测模式 => 一律不下单，只打印只读信息
# 输出：所有行以 [QMCH_PROBE] 开头，末行 [QMCH_PROBE] VERDICT=...
# =============================================================================
import datetime

GS = {
    'inited': False,
    'ordered': False,
    'order_ref': None,
    'order_code': None,
    'order_vol': 0,
    'order_px': 0.0,
    'bar_open': None,
    'bar_close': None,
    'bar_date': None,
    'fills': [],
    't1_result': 'NOT_RUN',
    'limit_result': 'NOT_RUN',
    'mode_note': 'UNKNOWN',
    'gate': False,
    'bars': 0,
}


def _p(msg):
    print('[QMCH_PROBE] ' + str(msg))


def _has(obj, name):
    try:
        return hasattr(obj, name)
    except Exception:
        return False


def _get(obj, name, default=None):
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _detect_mode(C):
    """识别是否回测模式（多属性探测，任一为 True 即认，均不存在则 UNKNOWN）。"""
    note = []
    gate = False
    for attr in ('do_back_test', 'is_backtest', 'backtest', 'do_backtest'):
        if _has(C, attr):
            v = _get(C, attr)
            note.append('%s=%r' % (attr, v))
            if v is True:
                gate = True
    if not note:
        note.append('NO_MODE_ATTR')
    GS['mode_note'] = ','.join(note)
    GS['gate'] = gate
    return gate


def _bar_prices(C):
    """取当根 bar 的开收盘（多路径兜底）。"""
    o = c = None
    try:
        d = C.get_market_data_ex(['open', 'close'], [C.stock_code], period='1d', count=1)
        if d is not None and len(d) > 0:
            df = d.get(C.stock_code) if isinstance(d, dict) else None
            if df is not None and len(df) > 0:
                o = float(df['open'].iloc[-1])
                c = float(df['close'].iloc[-1])
    except Exception as e:
        _p('bar_prices_ex_fail err=%s' % e)
    if o is None:
        try:
            for f in ('open', 'close'):
                pass
            o = float(C.get_bar_timetag(0)) if False else None
        except Exception:
            pass
    return o, c


def init(C):
    _p('init hook 进入')
    _detect_mode(C)
    _p('mode=%s SAFETY_GATE=%s' % (GS['mode_note'], 'BACKTEST' if GS['gate'] else 'NOT_BACKTEST'))
    if not GS['gate']:
        _p('!!! 未确认回测模式 => 本探针不会下单；如这是交易模式请立即停止策略 !!!')
    try:
        _p('account=%r stock_code=%r' % (_get(C, 'accountid'), _get(C, 'stock_code')))
    except Exception as e:
        _p('init_meta_fail err=%s' % e)
    GS['inited'] = True
    return


def _dump_account(C):
    try:
        accs = get_trade_detail_data(_get(C, 'accountid'), 'STOCK', 'ACCOUNT')
        _p('ACCOUNT n=%d' % (len(accs) if accs else -1))
        if accs:
            a = accs[0]
            for f in ('m_dAvailable', 'm_dAssetBalance', 'm_dInstrumentValue', 'm_strStatus'):
                _p('ACCOUNT %s=%r' % (f, _get(a, f)))
    except Exception as e:
        _p('ACCOUNT fail err=%s' % e)


def _dump_orders(C, tag):
    try:
        ods = get_trade_detail_data(_get(C, 'accountid'), 'STOCK', 'ORDER')
        _p('%s ORDER n=%d' % (tag, len(ods) if ods else -1))
        if ods:
            for o in ods[-3:]:
                _p('%s ORDER code=%r volTraded=%r volOrig=%r status=%r limPx=%r insertTime=%r' % (
                    tag, _get(o, 'm_strInstrumentID'), _get(o, 'm_nVolumeTraded'),
                    _get(o, 'm_nVolumeTotalOriginal'), _get(o, 'm_nOrderStatus'),
                    _get(o, 'm_dLimitPrice'), _get(o, 'm_strInsertTime')))
        return ods
    except Exception as e:
        _p('%s ORDER fail err=%s' % (tag, e))
        return None


def _dump_deals(C, tag):
    try:
        dls = get_trade_detail_data(_get(C, 'accountid'), 'STOCK', 'DEAL')
        _p('%s DEAL n=%d' % (tag, len(dls) if dls else -1))
        out = []
        if dls:
            for d in dls[-3:]:
                rec = {
                    'code': _get(d, 'm_strInstrumentID'),
                    'px': _get(d, 'm_dPrice'),
                    'vol': _get(d, 'm_nVolume'),
                    't': _get(d, 'm_strTradeTime'),
                    'ref': _get(d, 'm_strOrderRef'),
                    'offset': _get(d, 'm_nOffsetFlag'),
                }
                out.append(rec)
                _p('%s DEAL code=%r px=%r vol=%r t=%r ref=%r offset=%r' % (
                    tag, rec['code'], rec['px'], rec['vol'], rec['t'], rec['ref'], rec['offset']))
        return out
    except Exception as e:
        _p('%s DEAL fail err=%s' % (tag, e))
        return []


def _pick_code(C):
    """取一个有日线数据的标的（universe 前若干只中第一个有行情的）。"""
    codes = []
    try:
        codes = list(get_Ashares()) if _has(globals(), 'get_Ashares') else []
    except Exception as e:
        _p('get_Ashares fail err=%s' % e)
    for code in codes[:20]:
        try:
            d = C.get_market_data_ex(['open', 'close', 'preClose'], [code], period='1d', count=2)
            df = d.get(code) if isinstance(d, dict) else None
            if df is not None and len(df) >= 1:
                o = float(df['open'].iloc[-1])
                c = float(df['close'].iloc[-1])
                pc = float(df['preClose'].iloc[-1]) if 'preClose' in df.columns else 0.0
                if o > 0 and c > 0:
                    _p('picked code=%r open=%r close=%r preClose=%r' % (code, o, c, pc))
                    return code, o, c, pc
        except Exception:
            continue
    return None, None, None, None


def handlebar(C):
    GS['bars'] += 1
    if GS['bars'] > 6:
        return
    _p('==== bar #%d ====' % GS['bars'])
    if GS['bars'] == 1:
        _detect_mode(C)
        _dump_account(C)
        code, o, c, pc = _pick_code(C)
        if code is None:
            _p('NO_CODE 无可用行情标的 => 终止')
            return
        GS['order_code'] = code
        GS['bar_open'], GS['bar_close'] = o, c
        try:
            GS['bar_date'] = str(_get(C, 'bar_timetag'))
        except Exception:
            GS['bar_date'] = 'NA'
        _p('bar_date=%r' % GS['bar_date'])
        # ---- 安全闸：仅在回测模式下单 ----
        if not GS['gate']:
            _p('SAFETY_GATE_BLOCK 不下单（未确认回测模式）')
            return
        px = round(c, 2)
        vol = 100
        try:
            passorder(23, 1101, _get(C, 'accountid'), code, 11, px, vol, C)
            GS['ordered'] = True
            GS['order_px'] = px
            GS['order_vol'] = vol
            _p('passorder BUY sent code=%r px=%r vol=%r (prType=11 限价)' % (code, px, vol))
        except Exception as e:
            _p('passorder fail err=%s' % e)
        return

    # ---- 后续 bar：收集成交并判定 ----
    if GS['ordered'] and not GS['fills']:
        ods = _dump_orders(C, 'bar%d' % GS['bars'])
        dls = _dump_deals(C, 'bar%d' % GS['bars'])
        mine = [d for d in (dls or []) if d.get('code') == GS['order_code'] and d.get('px')]
        GS['fills'] = mine
        if mine:
            fpx = float(mine[0]['px'])
            o, c = GS['bar_open'], GS['bar_close']
            verdict = 'OTHER'
            if o and abs(fpx - o) < 1e-6:
                verdict = 'OPEN'
            elif c and abs(fpx - c) < 1e-6:
                verdict = 'CLOSE'
            elif abs(fpx - GS['order_px']) < 1e-6:
                verdict = 'LIMIT_ORDER_PX'
            _p('QMCH01 fill_px=%r bar_open=%r bar_close=%r order_px=%r => MATCH=%s' % (
                fpx, o, c, GS['order_px'], verdict))
        else:
            _p('QMCH01 fill_px=None（本 bar 无成交，可能未撮合/被拒）')

    # ---- QMCH-03 T+1：当日买入后尝试卖出 ----
    if GS['fills'] and GS['t1_result'] == 'NOT_RUN':
        try:
            passorder(24, 1101, _get(C, 'accountid'), GS['order_code'], 11,
                      GS['order_px'], GS['order_vol'], C)
            GS['t1_result'] = 'SELL_SENT'
            _p('QMCH03 SELL sent（同 bar 卖出刚买量，观察是否成交）')
        except Exception as e:
            GS['t1_result'] = 'SELL_ERR:%s' % e
            _p('QMCH03 SELL fail err=%s' % e)

    if GS['t1_result'] == 'SELL_SENT':
        dls = _dump_deals(C, 'bar%d_t1' % GS['bars'])
        sold = [d for d in (dls or []) if d.get('code') == GS['order_code'] and _get_offset(d)]
        if len(dls) > len(GS['fills']):
            GS['t1_result'] = 'T0_ALLOWED(卖出成交)'
        _p('QMCH03 state=%s' % GS['t1_result'])

    # ---- QMCH-02 涨跌停：找涨跌幅接近 ±9.8% 的标的试单 ----
    if GS['limit_result'] == 'NOT_RUN' and GS['bars'] >= 3:
        try:
            codes = list(get_Ashares())[:60] if _has(globals(), 'get_Ashares') else []
        except Exception:
            codes = []
        for code in codes:
            try:
                d = C.get_market_data_ex(['open', 'close', 'preClose'], [code], period='1d', count=1)
                df = d.get(code) if isinstance(d, dict) else None
                if df is None or len(df) < 1:
                    continue
                c0 = float(df['close'].iloc[-1])
                pc0 = float(df['preClose'].iloc[-1]) if 'preClose' in df.columns else 0.0
                if pc0 <= 0 or c0 <= 0:
                    continue
                chg = c0 / pc0 - 1.0
                if abs(chg) >= 0.098:
                    _p('QMCH02 candidate code=%r chg=%.4f close=%r' % (code, chg, c0))
                    if GS['gate']:
                        try:
                            passorder(23, 1101, _get(C, 'accountid'), code, 11, round(c0, 2), 100, C)
                            GS['limit_result'] = 'LIMIT_BUY_SENT:%s(chg=%.4f)' % (code, chg)
                        except Exception as e:
                            GS['limit_result'] = 'LIMIT_ERR:%s' % e
                    break
            except Exception:
                continue
        if GS['limit_result'] == 'NOT_RUN':
            GS['limit_result'] = 'NO_LIMIT_SAMPLE(样本窗无 ±9.8% 标的)'
        _p('QMCH02 state=%s' % GS['limit_result'])


def _get_offset(rec):
    return True


def stop(C):
    _p('==== end ====')
    _p('VERDICT mode=%s gate=%s bars=%d ordered=%s fills=%d qmch01=%s qmch02=%s qmch03=%s' % (
        GS['mode_note'], GS['gate'], GS['bars'], GS['ordered'], len(GS['fills']),
        ('FILLED' if GS['fills'] else 'NO_FILL'), GS['limit_result'], GS['t1_result']))
