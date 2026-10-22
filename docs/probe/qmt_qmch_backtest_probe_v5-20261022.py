#coding:gbk
# =============================================================================
# QS-QMCH 回测探针 v5（2026-10-22）  仅用于 QMT「回测」入口
# 目标：补齐 QMCH-03(T+1) / QMCH-01(市价单口径) / QMCH-02(跌停买入)
# L1 同 bar 买后立即卖 -> 次日看现金 => 判 T+0 / T+1
# L2 市价单(prType=5) 买入 -> 资金差 + 隐含费用表
# L3 跌停样本限价买入 -> 资金差（跌停卖出不可控，登记 NOT_TESTABLE）
# L4 下单后同 bar 再读现金 => 探测引擎撮合时点
# =============================================================================

GS = {
    'bars': 0, 'gate': False, 'mode_note': 'UNKNOWN',
    'account': '', 'account_src': 'NONE',
    'A': None, 'A_q': None, 'A_px': None, 'A_base': None, 'A_same_bar': None,
    'B': None, 'B_q': None, 'B_px': None, 'B_base': None,
    'X': None, 'X_q': None, 'X_px': None, 'X_base': None,
    'vol': 100, 'avail_seq': [], 'deal_ok': 'NOT_RUN',
    't1': 'NOT_RUN', 'mkt': 'NOT_RUN', 'dn': 'NOT_RUN', 'sameday': 'NOT_RUN',
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
    tag = None
    try:
        tag = C.get_bar_timetag(C.barpos)
    except Exception as e:
        _p('BAR_DATE fail err=%s' % e)
    d = 'UNKNOWN'
    try:
        t = int(tag)
        if t > 1000000000000:
            import datetime
            d = datetime.datetime.fromtimestamp(t / 1000.0).strftime('%Y%m%d')
        elif t > 19000000:
            d = str(t)[:8]
    except Exception:
        pass
    return d


def _quote(C, code, date_str):
    try:
        d = C.get_market_data_ex(['open', 'close', 'high', 'low', 'preClose'],
                                 [code], period='1d',
                                 start_time=date_str, end_time=date_str)
        df = d.get(code) if isinstance(d, dict) else None
        if df is None or len(df) < 1:
            return None
        r = df.iloc[-1]
        q = {'o': _f(r.get('open')), 'c': _f(r.get('close')),
             'h': _f(r.get('high')), 'lo': _f(r.get('low')), 'pc': _f(r.get('preClose'))}
        return q if (q['c'] and q['c'] > 0) else None
    except Exception as e:
        _p('QUOTE fail code=%r err=%s' % (code, e))
        return None


def _avail(C):
    try:
        accs = get_trade_detail_data(GS['account'], 'STOCK', 'ACCOUNT')
        if not accs:
            return None
        return _f(_get(accs[0], 'm_dAvailable'))
    except Exception as e:
        _p('avail fail err=%s' % e)
        return None


def _deal_probe(C, tag):
    try:
        dls = get_trade_detail_data(GS['account'], 'STOCK', 'DEAL')
        n = len(dls) if dls else 0
        if n > 0:
            GS['deal_ok'] = 'AVAILABLE'
            _p('%s DEAL n=%d' % (tag, n))
        else:
            if GS['deal_ok'] != 'AVAILABLE':
                GS['deal_ok'] = 'EMPTY'
    except Exception as e:
        GS['deal_ok'] = 'ERR'


def _ensure_account(C):
    for src, v in (('C.accountid', _get(C, 'accountid')),
                   ('C.accountID', _get(C, 'accountID')),
                   ('C.account_id', _get(C, 'account_id'))):
        if v:
            GS['account'] = str(v)
            GS['account_src'] = src
            break
    if not GS['account']:
        GS['account'] = '8881409880'
        GS['account_src'] = 'FALLBACK8881409880'
    _p('ACCOUNT_SRC %s=%r' % (GS['account_src'], GS['account']))
    if hasattr(C, 'set_account'):
        try:
            _p('SET_ACCOUNT rc=%r' % (C.set_account(GS['account']),))
        except Exception as e:
            _p('SET_ACCOUNT fail err=%s' % e)


def _pool(C):
    if hasattr(C, 'get_stock_list_in_sector'):
        try:
            return list(C.get_stock_list_in_sector('沪深A股') or [])
        except Exception as e:
            _p('POOL fail err=%s' % e)
    return []


def _implied(gross, vol, q, order_px):
    rows = []
    for nm, px in (('ORDER_PX', order_px),
                   ('CLOSE', (q or {}).get('c')),
                   ('OPEN', (q or {}).get('o')),
                   ('HIGH', (q or {}).get('h')),
                   ('LOW', (q or {}).get('lo'))):
        if px:
            rows.append('%s:%.4f' % (nm, gross - vol * px))
    return 'gross=%.4f implied_fee{%s}' % (gross, ', '.join(rows))


def _order(C, code, side, px, tag, prtype=11):
    try:
        passorder(side, 1101, GS['account'], code, prtype, px, GS['vol'], C)
        _p('%s %s sent code=%r px=%r prType=%r vol=%r'
           % (tag, 'BUY' if side == 23 else 'SELL', code, px, prtype, GS['vol']))
        return True
    except Exception as e:
        _p('%s %s fail err=%s' % (tag, 'BUY' if side == 23 else 'SELL', e))
        return False


def init(C):
    _p('init hook 进入 (v5)')
    _detect_mode(C)
    _p('mode=%s SAFETY_GATE=%s' % (GS['mode_note'], 'BACKTEST' if GS['gate'] else 'NOT_BACKTEST'))
    _ensure_account(C)


def handlebar(C):
    GS['bars'] += 1
    if GS['bars'] > 8:
        return
    a = _avail(C)
    GS['avail_seq'].append((GS['bars'], a))
    d = _bar_date(C)
    _p('==== bar #%d ==== date=%s avail=%r' % (GS['bars'], d, a))
    _deal_probe(C, 'bar%d' % GS['bars'])

    # ---------- L1: bar1 买 A 后同 bar 立即卖 A ----------
    if GS['bars'] == 1:
        GS['A_base'] = a
        for cd in _pool(C):
            q = _quote(C, cd, d)
            if not q or not q['c'] or not q['h']:
                continue
            px = round(min(q['h'], q['c'] * 1.004), 2)
            if px <= 0:
                continue
            GS.update({'A': cd, 'A_q': q, 'A_px': px})
            _p('L1A code=%r close=%r high=%r => limit=%r' % (cd, q['c'], q['h'], px))
            if not GS['gate']:
                _p('SAFETY_GATE_BLOCK')
                return
            _order(C, cd, 23, px, 'L1A_BUY')
            same = _avail(C)
            GS['A_same_bar'] = same
            _p('L4 同bar_买入后 avail=%r => sameday=%s'
               % (same, 'VISIBLE' if (same is not None and a is not None and abs(same - a) > 1e-6) else 'INVISIBLE'))
            GS['sameday'] = ('VISIBLE' if (same is not None and a is not None and abs(same - a) > 1e-6) else 'INVISIBLE')
            _order(C, cd, 24, px, 'L1A_SELL_SAMEBAR')
            GS['t1'] = 'SELL_SENT_SAMEBAR'
            return

    # ---------- L1 判定：bar2 ----------
    if GS['bars'] == 2 and GS['t1'] == 'SELL_SENT_SAMEBAR':
        base = GS['A_base']
        cost = GS['vol'] * GS['A_px']
        if base is not None and a is not None:
            dlt = a - base
            only_buy = -cost - 5.0
            both = -10.0
            _p('L1 T1 判定 base=%r avail=%r delta=%r 参照:只有买成交=%r 买卖都成交=%r'
               % (base, a, dlt, only_buy, both))
            if abs(dlt - both) <= 1.0:
                GS['t1'] = 'T0_BOTH_FILLED'
            elif abs(dlt - only_buy) <= 1.0:
                GS['t1'] = 'T1_ENFORCED_SELL_REJECTED'
            else:
                GS['t1'] = 'UNDETERMINED(delta=%r)' % dlt
            _p('L1 T1 结论=%s' % GS['t1'])

    # ---------- L2: bar3 市价买 B ----------
    if GS['bars'] == 3:
        for cd in _pool(C):
            if cd == GS['A']:
                continue
            q = _quote(C, cd, d)
            if not q or not q['c']:
                continue
            GS.update({'B': cd, 'B_q': q, 'B_px': None, 'B_base': a})
            _p('L2B code=%r close=%r（市价单，无委托价）' % (cd, q['c']))
            if GS['gate']:
                _order(C, cd, 23, 0, 'L2B_MARKET', prtype=5)
            return

    # ---------- L2 判定：bar4 ----------
    if GS['bars'] == 4 and GS['B'] and GS['B_base'] is not None and a is not None:
        dlt = a - GS['B_base']
        if dlt < -1e-6:
            GS['mkt'] = 'FILLED'
            _p('L2 市价买 CASH delta=%r => %s' % (dlt, _implied(-dlt, GS['vol'], GS['B_q'], None)))
        else:
            GS['mkt'] = 'NO_FILL'
            _p('L2 市价买 CASH delta=%r（未成交）' % dlt)

    # ---------- L3: bar5 跌停样本买入 ----------
    if GS['bars'] == 5 and GS['dn'] == 'NOT_RUN':
        for cd in _pool(C)[:60]:
            q = _quote(C, cd, d)
            if not q or not q['c'] or not q['pc'] or q['pc'] <= 0:
                continue
            chg = q['c'] / q['pc'] - 1.0
            if chg <= -0.098:
                px = round(q['c'], 2)
                GS.update({'X': cd, 'X_q': q, 'X_px': px, 'X_base': a})
                _p('L3 candidate code=%r chg=%.4f close=%r preClose=%r date=%s'
                   % (cd, chg, q['c'], q['pc'], d))
                if GS['gate']:
                    _order(C, cd, 23, px, 'L3_DOWNLIMIT_BUY')
                return
        GS['dn'] = 'NO_LIMIT_DOWN_SAMPLE(前60只当日无 <=-9.8%)'
        _p('L3 state=%s' % GS['dn'])

    # ---------- L3 判定：bar6 ----------
    if GS['bars'] == 6 and GS['X'] and GS['X_base'] is not None and a is not None:
        dlt = a - GS['X_base']
        if dlt < -1e-6:
            GS['dn'] = 'DOWNLIMIT_BUY_FILLED'
            _p('L3 跌停买 CASH delta=%r => %s' % (dlt, _implied(-dlt, GS['vol'], GS['X_q'], GS['X_px'])))
        else:
            if GS['dn'] == 'NOT_RUN':
                GS['dn'] = 'DOWNLIMIT_BUY_NO_FILL'
            _p('L3 跌停买 CASH delta=%r（未成交）' % dlt)


def stop(C):
    _p('==== end (v5) ====')
    _p('FEE_DELTA_SEQ %r' % (GS['avail_seq'],))
    _p('VERDICT mode=%s gate=%s acct=%s deal=%s sameday=%s t1=%s mkt=%s dn=%s '
       'A=%r A_px=%r B=%r X=%r'
       % (GS['mode_note'], GS['gate'], GS['account_src'], GS['deal_ok'], GS['sameday'],
          GS['t1'], GS['mkt'], GS['dn'], GS['A'], GS['A_px'], GS['B'], GS['X']))
