#coding:gbk
# =============================================================================
# QS-QMCH 回测探针 v2（2026-10-22）  仅用于 QMT「回测」入口（start back test mode）
# 覆盖：QMCH-01 撮合价口径 / QMCH-02 涨跌停可成交性 / QMCH-03 T+1
#       + FEE 计费反推（佣金/印花税/过户费 净额）+ 上下文/账户自诊断
# 安全闸：未确认回测模式 => 一律不下单（只读打印）
# 输出：所有行以 [QMCH_PROBE] 开头，末行 VERDICT=...
# 与 v1 差异：修 A1 假阴性（globals 判定错误）、加账户兜底、加 FEE 字段、加上下文诊断
# =============================================================================

GS = {
    'bars': 0,
    'gate': False,
    'mode_note': 'UNKNOWN',
    'account': None,
    'account_src': 'NONE',
    'code': None,
    'bar_open': None,
    'bar_close': None,
    'bar_high': None,
    'bar_low': None,
    'order_px': 0.0,
    'order_vol': 0,
    'ordered': False,
    'fills': [],
    't1': 'NOT_RUN',
    'limit': 'NOT_RUN',
    'acc_before': None,
    'acc_after': None,
    'diag_done': False,
}


def _p(msg):
    print('[QMCH_PROBE] ' + str(msg))


def _get(obj, name, default=None):
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _f(v):
    try:
        return float(v)
    except Exception:
        return None


def _in_globals(name):
    """I1: 正确的全局名判定（v1 用 _has(globals(),...) 恒 False 是假阴性根因）。"""
    try:
        return name in globals()
    except Exception:
        return False


def _detect_mode(C):
    note = []
    gate = False
    for attr in ('do_back_test', 'do_backtest', 'is_backtest', 'backtest'):
        if hasattr(C, attr):
            v = _get(C, attr)
            note.append('%s=%r' % (attr, v))
            if v is True:
                gate = True
    GS['mode_note'] = ','.join(note) if note else 'NO_MODE_ATTR'
    GS['gate'] = gate
    return gate


def _diagnose_ctx(C):
    """I3: 反查 ContextInfo 真实字段面（一次性把入口/账户/基准面看清）。"""
    if GS['diag_done']:
        return
    GS['diag_done'] = True
    keys = []
    try:
        keys = [k for k in dir(C) if not k.startswith('__')]
    except Exception as e:
        _p('CTX dir() fail err=%s' % e)
        return
    want = ('back', 'account', 'bench', 'stock', 'bar', 'period', 'universe', 'capital')
    hit = [k for k in keys if any(w in k.lower() for w in want)]
    _p('CTX attrs_total=%d matched=%d' % (len(keys), len(hit)))
    for k in hit[:40]:
        v = _get(C, k)
        s = repr(v)
        if len(s) > 70:
            s = s[:70] + '...'
        _p('CTX %s=%s' % (k, s))


def _ensure_account(C):
    """I2: 回测模式不注入 accountid => 逐级兜底，并打印实际使用值（可复核）。"""
    chain = [('C.accountid', _get(C, 'accountid')),
             ('C.accountID', _get(C, 'accountID')),
             ('C.account_id', _get(C, 'account_id'))]
    for src, v in chain:
        if v:
            GS['account'] = str(v)
            GS['account_src'] = src
            _p('ACCOUNT_SRC %s=%r' % (src, GS['account']))
            return GS['account']
    for fb in ('8881409880', ''):
        GS['account'] = fb
        GS['account_src'] = 'FALLBACK:%r' % fb
        _p('ACCOUNT_SRC fallback=%r（回测未注入 accountid，使用兜底值）' % fb)
        return GS['account']
    GS['account_src'] = 'UNAVAILABLE'
    _p('ACCOUNT_ID_UNAVAILABLE 全部兜底失败')
    return ''


def _acc_snap(C, tag):
    """I5: 账户资金快照（供 FEE 反推）。"""
    snap = {}
    try:
        accs = get_trade_detail_data(GS['account'], 'STOCK', 'ACCOUNT')
        if not accs:
            _p('%s ACCOUNT n=0' % tag)
            return snap
        a = accs[0]
        for f in ('m_dAvailable', 'm_dAssetBalance', 'm_dInstrumentValue',
                  'm_dCommission', 'm_dPositionProfit', 'm_strStatus'):
            snap[f] = _get(a, f)
        _p('%s ACCOUNT %s' % (tag, ' '.join('%s=%r' % (k, v) for k, v in snap.items())))
    except Exception as e:
        _p('%s ACCOUNT fail err=%s' % (tag, e))
    return snap


def _dump_deals(C, tag):
    """I5: 成交明细 + 手续费字段（佣金两个拼写都打，供变体核对）。"""
    out = []
    try:
        dls = get_trade_detail_data(GS['account'], 'STOCK', 'DEAL')
        _p('%s DEAL n=%d' % (tag, len(dls) if dls else -1))
        for d in (dls or [])[-5:]:
            rec = {
                'code': _get(d, 'm_strInstrumentID'),
                'px': _f(_get(d, 'm_dPrice')),
                'vol': _get(d, 'm_nVolume'),
                'amt': _f(_get(d, 'm_dTradeAmount')),
                'comm': _f(_get(d, 'm_dCommission')),
                'comm_alias': _f(_get(d, 'm_dComssion')),
                'close_profit': _f(_get(d, 'm_dCloseProfit')),
                't': _get(d, 'm_strTradeTime'),
                'ref': _get(d, 'm_strOrderRef'),
                'offset': _get(d, 'm_nOffsetFlag'),
            }
            out.append(rec)
            _p('%s DEAL code=%r px=%r vol=%r amt=%r comm=%r comm_alias=%r offset=%r t=%r'
               % (tag, rec['code'], rec['px'], rec['vol'], rec['amt'],
                  rec['comm'], rec['comm_alias'], rec['offset'], rec['t']))
    except Exception as e:
        _p('%s DEAL fail err=%s' % (tag, e))
    return out


def _net_amount(rec):
    """I6: 卖出净额 = px*vol - commission - 印花税 - 过户费；字段不可得则不猜（打 N/A）。"""
    px, vol = rec.get('px'), rec.get('vol')
    if px is None or not vol:
        return 'N/A(px/vol 缺失)'
    gross = px * float(vol)
    comm = rec.get('comm')
    if comm is None:
        return 'N/A(gross=%.4f, comm 缺失)' % gross
    return 'gross=%.4f comm=%.4f 净额(未含税费)=%.4f' % (gross, comm, gross - comm)


def _pick_code(C):
    """I4: 标的池三级兜底；每级失败都打印原因（不再静默空）。"""
    pool = []
    if _in_globals('get_Ashares'):
        try:
            pool = list(get_Ashares() or [])
            _p('POOL L1 get_Ashares n=%d' % len(pool))
        except Exception as e:
            _p('POOL L1 get_Ashares fail err=%s' % e)
    else:
        _p('POOL L1 get_Ashares 不在全局（跳过）')
    if not pool and hasattr(C, 'get_stock_list_in_sector'):
        try:
            pool = list(C.get_stock_list_in_sector('沪深A股') or [])
            _p('POOL L2 sector(沪深A股) n=%d' % len(pool))
        except Exception as e:
            _p('POOL L2 sector fail err=%s' % e)
    if not pool:
        for nm in ('stock_code', 'benchmark', 'bench'):
            v = _get(C, nm)
            if v:
                pool = [v]
                _p('POOL L3 %s=%r' % (nm, v))
                break
    if not pool:
        _p('POOL ALL_EMPTY 三级兜底全空')
        return None, None, None, None, None
    for code in pool[:20]:
        try:
            d = C.get_market_data_ex(['open', 'close', 'high', 'low', 'preClose'],
                                     [code], period='1d', count=2)
            df = d.get(code) if isinstance(d, dict) else None
            if df is None or len(df) < 1:
                continue
            o = _f(df['open'].iloc[-1])
            cz = _f(df['close'].iloc[-1])
            h = _f(df['high'].iloc[-1]) if 'high' in df.columns else None
            lo = _f(df['low'].iloc[-1]) if 'low' in df.columns else None
            pc = _f(df['preClose'].iloc[-1]) if 'preClose' in df.columns else None
            if o and cz and o > 0 and cz > 0:
                _p('picked code=%r open=%r close=%r high=%r low=%r preClose=%r'
                   % (code, o, cz, h, lo, pc))
                return code, o, cz, h, lo
        except Exception as e:
            _p('quote fail code=%r err=%s' % (code, e))
    _p('NO_QUOTE 池内前 20 只均无可用行情')
    return None, None, None, None, None


def init(C):
    _p('init hook 进入 (v2)')
    _detect_mode(C)
    _p('mode=%s SAFETY_GATE=%s' % (GS['mode_note'], 'BACKTEST' if GS['gate'] else 'NOT_BACKTEST'))
    if not GS['gate']:
        _p('!!! 本探针须在「回测」入口运行（start back test mode）；当前非回测 => 不会下单 !!!')
    _diagnose_ctx(C)
    _ensure_account(C)


def handlebar(C):
    GS['bars'] += 1
    if GS['bars'] > 6:
        return
    _p('==== bar #%d ====' % GS['bars'])
    if GS['bars'] == 1:
        _detect_mode(C)
        GS['acc_before'] = _acc_snap(C, 'bar1_before')
        code, o, cz, h, lo = _pick_code(C)
        if code is None:
            _p('NO_CODE 标的池三级兜底全空 => 终止（原因见 POOL 行）')
            return
        GS['code'], GS['bar_open'], GS['bar_close'] = code, o, cz
        GS['bar_high'], GS['bar_low'] = h, lo
        if not GS['gate']:
            _p('SAFETY_GATE_BLOCK 不下单（未确认回测模式）')
            return
        px = round(cz, 2)
        vol = 100
        try:
            passorder(23, 1101, GS['account'], code, 11, px, vol, C)
            GS['ordered'] = True
            GS['order_px'] = px
            GS['order_vol'] = vol
            _p('passorder BUY sent code=%r px=%r vol=%r account=%r prType=11'
               % (code, px, vol, GS['account']))
        except Exception as e:
            _p('passorder fail err=%s' % e)
        return

    # ---- 后续 bar：成交判定 + FEE 快照 ----
    if GS['ordered'] and not GS['fills']:
        dls = _dump_deals(C, 'bar%d' % GS['bars'])
        mine = [d for d in dls if d.get('code') == GS['code'] and d.get('px')]
        GS['fills'] = mine
        if mine:
            fpx = mine[0]['px']
            o, cz = GS['bar_open'], GS['bar_close']
            verdict = 'OTHER'
            if o and abs(fpx - o) < 1e-6:
                verdict = 'OPEN'
            elif cz and abs(fpx - cz) < 1e-6:
                verdict = 'CLOSE'
            elif abs(fpx - GS['order_px']) < 1e-6:
                verdict = 'LIMIT_ORDER_PX'
            elif GS['bar_high'] and GS['bar_low']:
                if abs(fpx - GS['bar_high']) < 1e-6:
                    verdict = 'HIGH'
                elif abs(fpx - GS['bar_low']) < 1e-6:
                    verdict = 'LOW'
            _p('QMCH01 fill_px=%r open=%r close=%r high=%r low=%r order_px=%r => MATCH=%s'
               % (fpx, o, cz, GS['bar_high'], GS['bar_low'], GS['order_px'], verdict))
            GS['acc_after'] = _acc_snap(C, 'bar%d_after_fill' % GS['bars'])
        else:
            _p('QMCH01 fill_px=None（本 bar 无成交，可能未撮合/被拒）')

    # ---- QMCH-03 T+1：成交后同 bar 卖出 ----
    if GS['fills'] and GS['t1'] == 'NOT_RUN':
        try:
            passorder(24, 1101, GS['account'], GS['code'], 11,
                      GS['order_px'], GS['order_vol'], C)
            GS['t1'] = 'SELL_SENT'
            _p('QMCH03 SELL sent（同 bar 卖出刚成交量，观察是否允许 T+0）')
        except Exception as e:
            GS['t1'] = 'SELL_ERR:%s' % e
            _p('QMCH03 SELL fail err=%s' % e)
    if GS['t1'] == 'SELL_SENT':
        dls = _dump_deals(C, 'bar%d_t1' % GS['bars'])
        if len(dls) > len(GS['fills']):
            sell = dls[-1]
            GS['t1'] = 'T0_ALLOWED(卖出成交)'
            _p('QMCH03 net %s' % _net_amount(sell))
        _p('QMCH03 state=%s' % GS['t1'])

    # ---- QMCH-02 涨跌停样本 ----
    if GS['limit'] == 'NOT_RUN' and GS['bars'] >= 3:
        pool = []
        if _in_globals('get_Ashares'):
            try:
                pool = list(get_Ashares() or [])
            except Exception as e:
                _p('QMCH02 pool fail err=%s' % e)
        if not pool:
            GS['limit'] = 'NO_POOL(涨跌停扫描无标的池)'
            _p('QMCH02 state=%s' % GS['limit'])
            return
        for code in pool[:60]:
            try:
                d = C.get_market_data_ex(['close', 'preClose'], [code], period='1d', count=1)
                df = d.get(code) if isinstance(d, dict) else None
                if df is None or len(df) < 1:
                    continue
                c0 = _f(df['close'].iloc[-1])
                pc0 = _f(df['preClose'].iloc[-1]) if 'preClose' in df.columns else None
                if not c0 or not pc0 or pc0 <= 0:
                    continue
                chg = c0 / pc0 - 1.0
                if abs(chg) >= 0.098:
                    _p('QMCH02 candidate code=%r chg=%.4f close=%r' % (code, chg, c0))
                    if GS['gate']:
                        try:
                            passorder(23, 1101, GS['account'], code, 11, round(c0, 2), 100, C)
                            GS['limit'] = 'LIMIT_BUY_SENT:%s(chg=%.4f)' % (code, chg)
                        except Exception as e:
                            GS['limit'] = 'LIMIT_ERR:%s' % e
                    break
            except Exception:
                continue
        if GS['limit'] == 'NOT_RUN':
            GS['limit'] = 'NO_LIMIT_SAMPLE(样本窗无 ±9.8% 标的)'
        _p('QMCH02 state=%s' % GS['limit'])


def stop(C):
    _p('==== end (v2) ====')
    b, a = GS['acc_before'] or {}, GS['acc_after'] or {}
    delta = {}
    for k in ('m_dAvailable', 'm_dAssetBalance', 'm_dCommission'):
        vb, va = _f(b.get(k)), _f(a.get(k))
        if vb is not None and va is not None:
            delta[k] = round(va - vb, 6)
    _p('FEE_DELTA %r' % delta)
    _p('VERDICT mode=%s gate=%s account_src=%s bars=%d ordered=%s fills=%d qmch01=%s qmch02=%s qmch03=%s'
       % (GS['mode_note'], GS['gate'], GS['account_src'], GS['bars'], GS['ordered'],
          len(GS['fills']),
          ('FILLED' if GS['fills'] else 'NO_FILL'), GS['limit'], GS['t1']))
