#coding:gbk
# =============================================================================
# QS-QMCH 回测探针 v6（2026-10-22）  仅用于 QMT「回测」入口
# 窗口建议 2023-08-20 ~ 2023-09-08（跨 2023-08-28 印花税切换点）
# A 跌停扩池 300 只 -> 跌停买入成交判定
# B 印花税切换点：切换前/后各一买一卖 -> 卖出费率反推对比
# 面板参数必须与本文件常量一致（输出首部会打印核对项）
# =============================================================================

COMM_RATE = 0.00035      # 佣金率（面板 0.0350）
MIN_COMM = 5.0           # 最低佣金（面板 5.000）
STAMP_SWITCH = '20230828'
SWITCH_STRICT = 1        # 1=切换日当日按新费率（卖出 0.05%）

GS = {
    'bars': 0, 'gate': False, 'mode_note': 'UNKNOWN',
    'account': '', 'account_src': 'NONE',
    'vol': 100, 'avail_seq': [], 'deal_ok': 'NOT_RUN',
    'early_seen': 0, 'early_bought': False, 'early_sell_bar': 0,
    'late_seen': 0, 'late_bought': False, 'late_sell_bar': 0,
    'early': None, 'late': None,
    'dn': 'NOT_RUN', 'dn_px': None, 'dn_code': None, 'dn_base': None,
    'P': {}, 'Q': {},
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


def _pool(C):
    if hasattr(C, 'get_stock_list_in_sector'):
        try:
            return list(C.get_stock_list_in_sector('沪深A股') or [])
        except Exception as e:
            _p('POOL fail err=%s' % e)
    return []


def _order(C, code, side, px, tag):
    try:
        passorder(side, 1101, GS['account'], code, 11, px, GS['vol'], C)
        _p('%s %s sent code=%r px=%r vol=%r'
           % (tag, 'BUY' if side == 23 else 'SELL', code, px, GS['vol']))
        return True
    except Exception as e:
        _p('%s %s fail err=%s' % (tag, 'BUY' if side == 23 else 'SELL', e))
        return False


def _fee_infer(tag, sell_px, net):
    """卖出费率反推：stamp = gross - net - commission（过户费 QMT 侧恒 0，见 FEE-04）"""
    gross = sell_px * GS['vol']
    if net is None:
        _p('%s 无法反推（net 缺失）gross=%.4f' % (tag, gross))
        return None
    comm = max(gross * COMM_RATE, MIN_COMM)
    stamp = gross - net - comm
    rate = stamp / gross if gross else None
    _p('%s gross=%.4f net=%.4f comm=%.4f stamp=%.4f stamp_rate=%.6f'
       % (tag, gross, net, comm, stamp, rate if rate is not None else -1))
    return rate


def _pick_first(C, d, skip):
    for cd in _pool(C):
        if cd in skip:
            continue
        q = _quote(C, cd, d)
        if q and q['c'] and q['c'] > 0:
            return cd, q
    return None, None


def init(C):
    _p('init hook 进入 (v6)')
    _detect_mode(C)
    _p('mode=%s SAFETY_GATE=%s' % (GS['mode_note'], 'BACKTEST' if GS['gate'] else 'NOT_BACKTEST'))
    _p('CONST COMM_RATE=%.5f MIN_COMM=%.2f STAMP_SWITCH=%s（面板须一致：佣金0.0350/最低5.000/卖印花0.1000）'
       % (COMM_RATE, MIN_COMM, STAMP_SWITCH))
    _ensure_account(C)


def handlebar(C):
    GS['bars'] += 1
    if GS['bars'] > 16:
        return
    a = _avail(C)
    GS['avail_seq'].append((GS['bars'], a))
    d = _bar_date(C)
    _p('==== bar #%d ==== date=%s avail=%r' % (GS['bars'], d, a))
    if d == 'UNKNOWN':
        return
    is_early = d < STAMP_SWITCH
    is_switch = (d == STAMP_SWITCH)
    is_late = d > STAMP_SWITCH

    # ---------- B1 切换前：第2根买、第3根卖 ----------
    if is_early:
        GS['early_seen'] += 1
        if GS['early_seen'] == 2 and not GS['early_bought'] and GS['gate']:
            code, q = _pick_first(C, d, ())
            if code:
                px = round(q['c'], 2)
                GS['early'] = {'code': code, 'px': px, 'buy_bar': GS['bars'],
                               'buy_avail': a, 'date': d}
                GS['early_bought'] = True
                _order(C, code, 23, px, 'B_EARLY_BUY')
        elif GS['early_seen'] == 3 and GS['early_bought']:
            e = GS['early']
            q = _quote(C, e['code'], d)
            lo = (q or {}).get('lo') or e['px']
            spx = round(lo, 2)
            e['sell_px'] = spx
            e['sell_base'] = a
            e['sell_bar'] = GS['bars']
            _order(C, e['code'], 24, spx, 'B_EARLY_SELL')

    # ---------- B2 切换后：第1根买、第2根卖 ----------
    if is_late:
        GS['late_seen'] += 1
        if GS['late_seen'] == 1 and not GS['late_bought'] and GS['gate']:
            code, q = _pick_first(C, d, (GS['early'] or {}).get('code'))
            if code:
                px = round(q['c'], 2)
                GS['late'] = {'code': code, 'px': px, 'buy_bar': GS['bars'],
                              'buy_avail': a, 'date': d}
                GS['late_bought'] = True
                _order(C, code, 23, px, 'B_LATE_BUY')
        elif GS['late_seen'] == 2 and GS['late_bought']:
            l = GS['late']
            q = _quote(C, l['code'], d)
            lo = (q or {}).get('lo') or l['px']
            spx = round(lo, 2)
            l['sell_px'] = spx
            l['sell_base'] = a
            l['sell_bar'] = GS['bars']
            _order(C, l['code'], 24, spx, 'B_LATE_SELL')

    # ---------- B 判定：卖出后一根 bar 读净得 ----------
    for key, tag in (('early', 'B_EARLY'), ('late', 'B_LATE')):
        rec = GS[key]
        if not rec or 'sell_bar' not in rec or rec.get('net_done'):
            continue
        if GS['bars'] == rec['sell_bar'] + 1:
            net = (a - rec['sell_base']) if (a is not None and rec['sell_base'] is not None) else None
            rec['net'] = net
            rec['rate'] = _fee_infer(tag + '_SELL', rec.get('sell_px', 0), net)
            rec['net_done'] = True

    # ---------- A 跌停扩池（300 只） ----------
    if GS['bars'] >= 8 and GS['dn'] == 'NOT_RUN':
        if GS.get('dn_base') is None:
            best = None
            for cd in _pool(C)[:300]:
                q = _quote(C, cd, d)
                if not q or not q['c'] or not q['pc'] or q['pc'] <= 0:
                    continue
                chg = q['c'] / q['pc'] - 1.0
                if chg <= -0.098:
                    best = (cd, chg, q['c'])
                    break
            if best is None:
                GS['dn'] = 'NO_LIMIT_DOWN_SAMPLE(前300只当日无<=-9.8%)'
                _p('A state=%s' % GS['dn'])
            else:
                _p('A candidate code=%r chg=%.4f close=%r date=%s' % (best[0], best[1], best[2], d))
                GS['dn_code'] = best[0]
                GS['dn_px'] = round(best[2], 2)
                GS['dn_base'] = a
                if GS['gate']:
                    _order(C, best[0], 23, GS['dn_px'], 'A_DOWNLIMIT_BUY')
        else:
            if GS['dn'] == 'NOT_RUN':
                dlt = (a - GS['dn_base']) if (a is not None) else None
                if dlt is not None and dlt < -1e-6:
                    GS['dn'] = 'DOWNLIMIT_BUY_FILLED'
                    gross = GS['dn_px'] * GS['vol']
                    _p('A 跌停买 CASH delta=%r gross=%.4f implied_fee=%.4f'
                       % (dlt, gross, -dlt - gross))
                else:
                    GS['dn'] = 'DOWNLIMIT_BUY_NO_FILL'
                    _p('A 跌停买 CASH delta=%r（未成交）' % dlt)


def stop(C):
    _p('==== end (v6) ====')
    e, l = GS['early'], GS['late']
    er = e.get('rate') if e else None
    lr = l.get('rate') if l else None
    verdict = 'NOT_RUN'
    if er is not None and lr is not None:
        if abs(er - lr) < 1e-6:
            verdict = 'NO_PERIODIC_STAMP(两侧同为%.4f%%)' % (er * 100)
        else:
            verdict = 'PERIODIC_STAMP(early=%.4f%% late=%.4f%%)' % (er * 100, lr * 100)
    _p('B_VERDICT %s' % verdict)
    _p('A_VERDICT %s' % GS['dn'])
    _p('FEE_DELTA_SEQ %r' % (GS['avail_seq'],))
    _p('VERDICT mode=%s gate=%s acct=%s deal=%s early=%r early_rate=%r '
       'late=%r late_rate=%r stamp=%r dn=%r'
       % (GS['mode_note'], GS['gate'], GS['account_src'], GS['deal_ok'],
          (e or {}).get('date'), er, (l or {}).get('date'), lr, verdict, GS['dn']))
