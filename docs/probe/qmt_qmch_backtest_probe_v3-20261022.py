#coding:gbk
# =============================================================================
# QS-QMCH 回测探针 v3（2026-10-22）  仅用于 QMT「回测」入口
# v3 = v2 + J1..J8：
#   J1 取价按回测当日（不用 count=2 末行）        J2 成交判定改资金差额反推
#   J3 限价未成交则市价单兜底                     J4 涨跌停池走 sector + 当日 pctChg
#   J5 T+1 判据改资金回升                         J6 逐 bar 资金序列（FEE 反推）
#   J7 ACCOUNT 哨兵过滤（DBL_MAX 禁消费）          J8 自检
# 安全闸：未确认回测模式 => 一律不下单
# =============================================================================

DBL_MAX = 1.7976931348623157e308

GS = {
    'bars': 0, 'gate': False, 'mode_note': 'UNKNOWN',
    'account': '', 'account_src': 'NONE', 'set_acc_rc': None,
    'code': None, 'bar_date': None,
    'o': None, 'c': None, 'h': None, 'lo': None, 'pc': None,
    'order_px': 0.0, 'order_vol': 100, 'ordered': False, 'order_mode': 'NONE',
    'avail_seq': [], 'base_avail': None,
    'filled': False, 'fill_px_est': None, 'fill_mode': 'NONE',
    't1': 'NOT_RUN', 'limit': 'NOT_RUN', 'diag_done': False,
}


def _p(m):
    print('[QMCH_PROBE] ' + str(m))


def _get(o, n, d=None):
    try:
        return getattr(o, n, d)
    except Exception:
        return d


def _f(v):
    try:
        return float(v)
    except Exception:
        return None


def _num(v):
    """J7: 哨兵过滤——DBL_MAX/NaN 不当数值。"""
    x = _f(v)
    if x is None:
        return 'N/A'
    if abs(x) > 1e100:
        return 'SENTINEL'
    return x


def _detect_mode(C):
    note, gate = [], False
    for a in ('do_back_test', 'do_backtest', 'is_backtest', 'backtest'):
        if hasattr(C, a):
            v = _get(C, a)
            note.append('%s=%r' % (a, v))
            if v is True:
                gate = True
    GS['mode_note'] = ','.join(note) if note else 'NO_MODE_ATTR'
    GS['gate'] = gate
    return gate


def _bar_date(C):
    """J1: 取回测当根 bar 的日期字符串 YYYYMMDD。"""
    tag = None
    try:
        tag = C.get_bar_timetag(C.barpos)
    except Exception as e:
        _p('bar_date get_bar_timetag fail err=%s' % e)
    if tag is None:
        try:
            tag = C.get_bar_timetag()
        except Exception:
            pass
    d = 'UNKNOWN'
    try:
        t = int(tag)
        if t > 1000000000000:
            import datetime
            d = datetime.datetime.fromtimestamp(t / 1000.0).strftime('%Y%m%d')
        elif t > 19000000:
            d = str(t)[:8]
    except Exception:
        d = 'UNKNOWN'
    _p('BAR_DATE timetag=%r => %s (barpos=%r)' % (tag, d, _get(C, 'barpos')))
    return d


def _day_quote(C, code, date_str):
    """J1: 按回测当日取 OHLC（不用 count 末行）。"""
    try:
        d = C.get_market_data_ex(['open', 'close', 'high', 'low', 'preClose'],
                                 [code], period='1d',
                                 start_time=date_str, end_time=date_str)
        df = d.get(code) if isinstance(d, dict) else None
        if df is None or len(df) < 1:
            _p('DAYQUOTE empty code=%r date=%r' % (code, date_str))
            return None
        r = df.iloc[-1]
        out = {'o': _f(r.get('open')), 'c': _f(r.get('close')),
               'h': _f(r.get('high')), 'lo': _f(r.get('low')),
               'pc': _f(r.get('preClose'))}
        _p('DAYQUOTE code=%r date=%r %r' % (code, date_str, out))
        return out
    except Exception as e:
        _p('DAYQUOTE fail err=%s' % e)
        return None


def _avail(C):
    """J2/J6: 只信 m_dAvailable（回测其余字段是哨兵/半初始化）。"""
    try:
        accs = get_trade_detail_data(GS['account'], 'STOCK', 'ACCOUNT')
        if not accs:
            return None
        v = _f(_get(accs[0], 'm_dAvailable'))
        return v
    except Exception as e:
        _p('avail fail err=%s' % e)
        return None


def _acc_full(C, tag):
    try:
        accs = get_trade_detail_data(GS['account'], 'STOCK', 'ACCOUNT')
        if not accs:
            _p('%s ACCOUNT n=0' % tag)
            return
        a = accs[0]
        _p('%s ACCOUNT avail=%r asset=%s comm=%s status=%r' % (
            tag, _num(_get(a, 'm_dAvailable')), _num(_get(a, 'm_dAssetBalance')),
            _num(_get(a, 'm_dCommission')), _get(a, 'm_strStatus')))
    except Exception as e:
        _p('%s ACCOUNT fail err=%s' % (tag, e))


def _ensure_account(C):
    """I2 + J: 回测无 accountid => 兜底；并试 set_account。"""
    for src, v in (('C.accountid', _get(C, 'accountid')),
                   ('C.accountID', _get(C, 'accountID')),
                   ('C.account_id', _get(C, 'account_id'))):
        if v:
            GS['account'] = str(v); GS['account_src'] = src
            _p('ACCOUNT_SRC %s=%r' % (src, GS['account']))
            break
    if not GS['account']:
        GS['account'] = '8881409880'
        GS['account_src'] = "FALLBACK:'8881409880'"
        _p('ACCOUNT_SRC fallback=%r' % GS['account'])
    # J-extra: 试 set_account（若回测支持，DEAL 或许可用）
    if hasattr(C, 'set_account'):
        try:
            rc = C.set_account(GS['account'])
            GS['set_acc_rc'] = rc
            _p('SET_ACCOUNT rc=%r（回测 ContextInfo 有此方法，行为待验）' % rc)
        except Exception as e:
            GS['set_acc_rc'] = 'ERR:%s' % e
            _p('SET_ACCOUNT fail err=%s' % e)
    else:
        _p('SET_ACCOUNT 方法不存在')


def _diag(C):
    if GS['diag_done']:
        return
    GS['diag_done'] = True
    try:
        ks = [k for k in dir(C) if not k.startswith('__')]
    except Exception as e:
        _p('CTX dir fail err=%s' % e); return
    want = ('back', 'account', 'bench', 'stock', 'bar', 'period', 'universe', 'capital')
    hit = [k for k in ks if any(w in k.lower() for w in want)]
    _p('CTX attrs_total=%d matched=%d' % (len(ks), len(hit)))
    for k in hit[:24]:
        s = repr(_get(C, k))
        _p('CTX %s=%s' % (k, s[:64] + ('...' if len(s) > 64 else '')))


def _pool(C):
    """J4: 池子走 sector（回测无 get_Ashares）。"""
    p = []
    if hasattr(C, 'get_stock_list_in_sector'):
        try:
            p = list(C.get_stock_list_in_sector('沪深A股') or [])
            _p('POOL sector n=%d' % len(p))
        except Exception as e:
            _p('POOL sector fail err=%s' % e)
    if not p and 'get_Ashares' in globals():
        try:
            p = list(get_Ashares() or [])
            _p('POOL get_Ashares n=%d' % len(p))
        except Exception as e:
            _p('POOL get_Ashares fail err=%s' % e)
    return p


def _order(C, code, price, tag):
    """J3: 限价优先；prType=11。返回是否发出。"""
    try:
        passorder(23, 1101, GS['account'], code, 11, price, GS['order_vol'], C)
        _p('%s passorder LIMIT sent code=%r px=%r vol=%r' % (tag, code, price, GS['order_vol']))
        return True
    except Exception as e:
        _p('%s passorder LIMIT fail err=%s' % (tag, e))
        return False


def _order_market(C, code, tag):
    """J3 兜底：市价单 prType=5（若引擎支持）。"""
    try:
        passorder(23, 1101, GS['account'], code, 5, 0, GS['order_vol'], C)
        _p('%s passorder MARKET(prType=5) sent code=%r vol=%r' % (tag, code, GS['order_vol']))
        return True
    except Exception as e:
        _p('%s passorder MARKET fail err=%s' % (tag, e))
        return False


def init(C):
    _p('init hook 进入 (v3)')
    _detect_mode(C)
    _p('mode=%s SAFETY_GATE=%s' % (GS['mode_note'], 'BACKTEST' if GS['gate'] else 'NOT_BACKTEST'))
    if not GS['gate']:
        _p('!!! 须在回测入口运行；当前非回测 => 不下单 !!!')
    _diag(C)
    _ensure_account(C)


def handlebar(C):
    GS['bars'] += 1
    if GS['bars'] > 6:
        return
    a = _avail(C)
    GS['avail_seq'].append((GS['bars'], a))
    _p('==== bar #%d ==== avail=%r' % (GS['bars'], a))

    if GS['bars'] == 1:
        GS['base_avail'] = a
        _acc_full(C, 'bar1')
        GS['bar_date'] = _bar_date(C)
        pool = _pool(C)
        code = None
        for cd in pool[:20]:
            q = _day_quote(C, cd, GS['bar_date'])
            if q and q['c'] and q['c'] > 0 and q['o'] and q['o'] > 0:
                code, qq = cd, q
                GS.update({'o': qq['o'], 'c': qq['c'], 'h': qq['h'],
                           'lo': qq['lo'], 'pc': qq['pc']})
                _p('picked code=%r open=%r close=%r high=%r low=%r preClose=%r'
                   % (cd, qq['o'], qq['c'], qq['h'], qq['lo'], qq['pc']))
                break
        if code is None:
            _p('NO_CODE 当日取价全失败（见 DAYQUOTE 行）')
            return
        GS['code'] = code
        if not GS['gate']:
            _p('SAFETY_GATE_BLOCK 不下单')
            return
        px = round(GS['c'], 2)
        if _order(C, code, px, 'bar1'):
            GS['ordered'] = True
            GS['order_px'] = px
            GS['order_mode'] = 'LIMIT@day_close'
        return

    # ---- J2: 资金差额反推成交 ----
    if GS['ordered'] and not GS['filled'] and GS['base_avail'] is not None and a is not None:
        delta = a - GS['base_avail']
        _p('CASH_DELTA bar%d delta=%r（负=现金减少=可能成交）' % (GS['bars'], delta))
        if delta < -1e-6:
            est = (-delta) / float(GS['order_vol'])
            GS['filled'] = True
            GS['fill_px_est'] = est
            GS['fill_mode'] = 'CASH_INFER'
            _p('QMCH01 fill_est_px=%.6f（含费，估算）open=%r close=%r high=%r low=%r order_px=%r'
               % (est, GS['o'], GS['c'], GS['h'], GS['lo'], GS['order_px']))
            tag = 'OTHER'
            for nm, v in (('OPEN', GS['o']), ('CLOSE', GS['c']),
                          ('HIGH', GS['h']), ('LOW', GS['lo'])):
                if v and abs(est - v) / max(v, 1e-9) < 0.01:
                    tag = 'NEAR_' + nm
                    break
            _p('QMCH01 MATCH=%s（1%% 容差内，估算口径）' % tag)

    # ---- J3 兜底：bar3 仍未成交 => 市价单 ----
    if GS['bars'] == 3 and GS['ordered'] and not GS['filled']:
        if _order_market(C, GS['code'], 'bar3'):
            GS['order_mode'] = 'MARKET@prType5'

    # ---- J5: T+1 判据（资金回升） ----
    if GS['filled'] and GS['t1'] == 'NOT_RUN':
        try:
            passorder(24, 1101, GS['account'], GS['code'], 5, 0, GS['order_vol'], C)
            GS['t1'] = 'SELL_SENT'
            _p('QMCH03 SELL(market) sent 100 股（同 bar，看资金是否回升）')
        except Exception as e:
            GS['t1'] = 'SELL_ERR:%s' % e
            _p('QMCH03 SELL fail err=%s' % e)
    if GS['t1'] == 'SELL_SENT' and len(GS['avail_seq']) >= 2:
        prev = GS['avail_seq'][-2][1]
        if prev is not None and a is not None and a > prev + 1e-6:
            GS['t1'] = 'T0_ALLOWED (现金回升 %r)' % round(a - prev, 4)
        _p('QMCH03 state=%s' % GS['t1'])

    # ---- J4: 涨跌停样本（sector 池 + 当日 pctChg） ----
    if GS['limit'] == 'NOT_RUN' and GS['bars'] >= 3:
        pool = _pool(C)
        if not pool:
            GS['limit'] = 'NO_POOL'
            _p('QMCH02 state=%s' % GS['limit'])
            return
        best = None
        for cd in pool[:60]:
            q = _day_quote(C, cd, GS['bar_date'])
            if not q or not q['c'] or not q['pc'] or q['pc'] <= 0:
                continue
            chg = q['c'] / q['pc'] - 1.0
            if abs(chg) >= 0.098:
                best = (cd, chg, q['c'])
                break
        if best is None:
            GS['limit'] = 'NO_LIMIT_SAMPLE(前60只当日无 ±9.8%)'
            _p('QMCH02 state=%s' % GS['limit'])
        else:
            _p('QMCH02 candidate code=%r chg=%.4f close=%r' % best)
            if GS['gate']:
                ok = _order(C, best[0], round(best[2], 2), 'qmch02')
                GS['limit'] = 'LIMIT_SENT:%s' % (best[0],) if ok else 'LIMIT_ERR'
            _p('QMCH02 state=%s' % GS['limit'])


def stop(C):
    _p('==== end (v3) ====')
    _p('FEE_DELTA_SEQ %r' % (GS['avail_seq'],))
    if len(GS['avail_seq']) >= 2:
        first = GS['avail_seq'][0][1]
        last = GS['avail_seq'][-1][1]
        if first is not None and last is not None:
            _p('FEE_DELTA first=%r last=%r diff=%r' % (first, last, round(last - first, 6)))
    _p('VERDICT mode=%s gate=%s account_src=%s set_acc_rc=%r bar_date=%r bars=%d '
       'ordered=%s order_mode=%s filled=%s fill_mode=%s fill_est=%r qmch02=%s qmch03=%s'
       % (GS['mode_note'], GS['gate'], GS['account_src'], GS['set_acc_rc'], GS['bar_date'],
          GS['bars'], GS['ordered'], GS['order_mode'], GS['filled'], GS['fill_mode'],
          GS['fill_px_est'], GS['limit'], GS['t1']))
