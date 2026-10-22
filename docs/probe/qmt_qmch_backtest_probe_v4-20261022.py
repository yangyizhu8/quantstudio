#coding:gbk
# =============================================================================
# QS-QMCH 回测探针 v4（2026-10-22）  仅用于 QMT「回测」入口
# v4 = v3 + K1..K6
#   K1 撮合口径判别：A 挂 close*1.004（<=high）买入；B 挂 close*0.996（>=low）买入
#      判读法：基线变体下买入佣金=最低佣金 5 元（1166*0.035% < 5）
#      => 隐含费用表中恰等于 5 的那一行 = 真实成交价假设
#   K2 涨跌停扫描每次重取 bar 日期（修 v3 的日期错配）
#   K3 set_account 后每 bar 复测 DEAL
#   K4 T+1 复验改用限价卖出
#   K5 BUY/SELL 现金原值 + 隐含费用表
#   K6 自检（先数后断言）
# 变体B（佣金1%/最低0/卖印花1%）用于解费率方程
# =============================================================================

GS = {
    'bars': 0, 'gate': False, 'mode_note': 'UNKNOWN',
    'account': '', 'account_src': 'NONE', 'set_acc_rc': None,
    'bar_date': None, 'diag_done': False,
    'A': None, 'A_px': None, 'A_q': None, 'A_base': None, 'A_out': None,
    'B': None, 'B_px': None, 'B_q': None, 'B_base': None, 'B_out': None,
    'vol': 100, 'avail_seq': [], 'deal_ok': 'NOT_RUN',
    't1': 'NOT_RUN', 'limit': 'NOT_RUN',
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
    _p('BAR_DATE timetag=%r => %s barpos=%r' % (tag, d, _get(C, 'barpos')))
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


def _acc(C, tag):
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


def _deal_probe(C, tag):
    try:
        dls = get_trade_detail_data(GS['account'], 'STOCK', 'DEAL')
        n = len(dls) if dls else 0
        if n > 0:
            GS['deal_ok'] = 'AVAILABLE'
            d = dls[-1]
            _p('%s DEAL n=%d last px=%r vol=%r comm=%r' % (
                tag, n, _num(_get(d, 'm_dPrice')), _get(d, 'm_nVolume'),
                _num(_get(d, 'm_dCommission'))))
        else:
            if GS['deal_ok'] != 'AVAILABLE':
                GS['deal_ok'] = 'EMPTY'
            _p('%s DEAL n=0' % tag)
    except Exception as e:
        GS['deal_ok'] = 'ERR'
        _p('%s DEAL fail err=%s' % (tag, e))


def _ensure_account(C):
    for src, v in (('C.accountid', _get(C, 'accountid')),
                   ('C.accountID', _get(C, 'accountID')),
                   ('C.account_id', _get(C, 'account_id'))):
        if v:
            GS['account'] = str(v)
            GS['account_src'] = src
            _p('ACCOUNT_SRC %s=%r' % (src, GS['account']))
            break
    if not GS['account']:
        GS['account'] = '8881409880'
        GS['account_src'] = 'FALLBACK8881409880'
        _p('ACCOUNT_SRC fallback=%r' % GS['account'])
    if hasattr(C, 'set_account'):
        try:
            rc = C.set_account(GS['account'])
            GS['set_acc_rc'] = rc
            _p('SET_ACCOUNT rc=%r' % rc)
        except Exception as e:
            GS['set_acc_rc'] = 'ERR:%s' % e
            _p('SET_ACCOUNT fail err=%s' % e)


def _diag(C):
    if GS['diag_done']:
        return
    GS['diag_done'] = True
    try:
        ks = [k for k in dir(C) if not k.startswith('__')]
    except Exception as e:
        _p('CTX dir fail err=%s' % e)
        return
    want = ('back', 'account', 'bench', 'stock', 'bar', 'period', 'universe', 'capital')
    hit = [k for k in ks if any(w in k.lower() for w in want)]
    _p('CTX attrs_total=%d matched=%d' % (len(ks), len(hit)))


def _pool(C):
    if hasattr(C, 'get_stock_list_in_sector'):
        try:
            p = list(C.get_stock_list_in_sector('沪深A股') or [])
            _p('POOL sector n=%d' % len(p))
            return p
        except Exception as e:
            _p('POOL sector fail err=%s' % e)
    if 'get_Ashares' in globals():
        try:
            p = list(get_Ashares() or [])
            _p('POOL get_Ashares n=%d' % len(p))
            return p
        except Exception as e:
            _p('POOL get_Ashares fail err=%s' % e)
    return []


def _implied(delta_neg, vol, q, order_px):
    gross = delta_neg
    rows = []
    for nm, px in (('ORDER_PX', order_px),
                   ('CLOSE', (q or {}).get('c')),
                   ('OPEN', (q or {}).get('o')),
                   ('HIGH', (q or {}).get('h')),
                   ('LOW', (q or {}).get('lo'))):
        if px:
            rows.append('%s:%.4f' % (nm, gross - vol * px))
    return 'gross=%.4f implied_fee{%s}' % (gross, ', '.join(rows))


def _buy(C, code, px, tag):
    try:
        passorder(23, 1101, GS['account'], code, 11, px, GS['vol'], C)
        _p('%s BUY LIMIT sent code=%r px=%r vol=%r' % (tag, code, px, GS['vol']))
        return True
    except Exception as e:
        _p('%s BUY fail err=%s' % (tag, e))
        return False


def _sell(C, code, px, tag):
    try:
        passorder(24, 1101, GS['account'], code, 11, px, GS['vol'], C)
        _p('%s SELL LIMIT sent code=%r px=%r vol=%r' % (tag, code, px, GS['vol']))
        return True
    except Exception as e:
        _p('%s SELL fail err=%s' % (tag, e))
        return False


def init(C):
    _p('init hook 进入 (v4)')
    _detect_mode(C)
    _p('mode=%s SAFETY_GATE=%s' % (GS['mode_note'], 'BACKTEST' if GS['gate'] else 'NOT_BACKTEST'))
    _diag(C)
    _ensure_account(C)


def handlebar(C):
    GS['bars'] += 1
    if GS['bars'] > 8:
        return
    a = _avail(C)
    GS['avail_seq'].append((GS['bars'], a))
    d = _bar_date(C)
    GS['bar_date'] = d
    _p('==== bar #%d ==== date=%s avail=%r' % (GS['bars'], d, a))
    _deal_probe(C, 'bar%d' % GS['bars'])

    if GS['bars'] == 1:
        GS['A_base'] = a
        pool = _pool(C)
        for cd in pool[:20]:
            q = _quote(C, cd, d)
            if not q or not q['c'] or not q['h']:
                continue
            px = round(min(q['h'], q['c'] * 1.004), 2)
            if px <= 0:
                continue
            GS.update({'A': cd, 'A_q': q, 'A_px': px})
            _p('K1A code=%r close=%r high=%r low=%r => limit=%r' % (cd, q['c'], q['h'], q['lo'], px))
            if GS['gate']:
                _buy(C, cd, px, 'K1A')
            else:
                _p('SAFETY_GATE_BLOCK')
            break
        return

    if GS['bars'] == 2 and GS['A'] and GS['A_base'] is not None and a is not None:
        delta = a - GS['A_base']
        if delta < -1e-6:
            GS['A_out'] = -delta
            _p('K1A CASH delta=%r => %s' % (delta, _implied(-delta, GS['vol'], GS['A_q'], GS['A_px'])))
        else:
            _p('K1A CASH delta=%r（未成交或无变化）' % delta)

    if GS['bars'] == 3 and GS['A'] and not GS['B']:
        pool = _pool(C)
        for cd in pool[:20]:
            if cd == GS['A']:
                continue
            q = _quote(C, cd, d)
            if not q or not q['c'] or not q['lo']:
                continue
            px = round(max(q['lo'], q['c'] * 0.996), 2)
            if px <= 0:
                continue
            GS.update({'B': cd, 'B_q': q, 'B_px': px, 'B_base': a})
            _p('K1B code=%r close=%r low=%r high=%r => limit=%r' % (cd, q['c'], q['lo'], q['h'], px))
            if GS['gate']:
                _buy(C, cd, px, 'K1B')
            break
        return

    if GS['bars'] == 4 and GS['B'] and GS['B_base'] is not None and a is not None:
        delta = a - GS['B_base']
        if delta < -1e-6:
            GS['B_out'] = -delta
            _p('K1B CASH delta=%r => %s' % (delta, _implied(-delta, GS['vol'], GS['B_q'], GS['B_px'])))
        else:
            _p('K1B CASH delta=%r（未成交或无变化）' % delta)

    if GS['bars'] == 5 and GS['A'] and GS['t1'] == 'NOT_RUN':
        q = _quote(C, GS['A'], d) or GS['A_q'] or {}
        px = round(q.get('lo') or GS['A_px'], 2)
        GS['t1'] = 'SELL_SENT'
        _p('K4 T1 卖出基准 avail=%r px=%r' % (a, px))
        if GS['gate']:
            _sell(C, GS['A'], px, 'K4T1')
        return

    if GS['bars'] == 6 and GS['t1'] == 'SELL_SENT':
        pre = None
        for i in range(len(GS['avail_seq']) - 1, -1, -1):
            if GS['avail_seq'][i][0] == 5:
                pre = GS['avail_seq'][i][1]
                break
        if pre is not None and a is not None:
            up = a - pre
            GS['t1'] = ('T0_ALLOWED(up=%.4f)' % up) if up > 1e-6 else ('T1_ENFORCED(up=%.4f)' % up)
            _p('K4 T1 结果 avail5=%r avail6=%r up=%r => %s' % (pre, a, up, GS['t1']))

    if GS['bars'] == 6 and GS['limit'] == 'NOT_RUN':
        pool = _pool(C)
        best = None
        for cd in pool[:60]:
            q = _quote(C, cd, d)
            if not q or not q['c'] or not q['pc'] or q['pc'] <= 0:
                continue
            chg = q['c'] / q['pc'] - 1.0
            if abs(chg) >= 0.098:
                best = (cd, chg, q['c'], q['pc'])
                break
        if best is None:
            GS['limit'] = 'NO_LIMIT_SAMPLE(前60只当日无9.8pct)'
        else:
            _p('K2 candidate code=%r chg=%.4f close=%r preClose=%r date=%s' % (
                best[0], best[1], best[2], best[3], d))
            if GS['gate']:
                ok = _buy(C, best[0], round(best[2], 2), 'K2')
                GS['limit'] = ('LIMIT_SENT:%s' % best[0]) if ok else 'LIMIT_ERR'
        _p('K2 state=%s' % GS['limit'])


def stop(C):
    _p('==== end (v4) ====')
    _p('FEE_DELTA_SEQ %r' % (GS['avail_seq'],))
    _p('K1A_COST %r' % (GS['A_out'],))
    _p('K1B_COST %r' % (GS['B_out'],))
    _p('VERDICT mode=%s gate=%s account_src=%s set_acc_rc=%r deal=%s '
       'A=%r A_px=%r A_cost=%r B=%r B_px=%r B_cost=%r qmch02=%s qmch03=%s'
       % (GS['mode_note'], GS['gate'], GS['account_src'], GS['set_acc_rc'], GS['deal_ok'],
          GS['A'], GS['A_px'], GS['A_out'], GS['B'], GS['B_px'], GS['B_out'],
          GS['limit'], GS['t1']))
