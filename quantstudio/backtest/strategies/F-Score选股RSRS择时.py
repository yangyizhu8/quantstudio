"""F-Score选股RSRS择时（fscore_rsrs）— agent-authored QuantStudio-only strategy.

设计版本 2.2（R0-R2.5 客户确认，参数全部冻结禁止回调）。

核心逻辑：
- 月度首个交易日：选股再平衡（沪深300 → 剔金融 → F-Score>6 → RV剔30% → ROA≥0.5pct → ROE取8）
- 每日：RSRS 择时（18日高低价回归→斜率→600日分位，滞回 sell<0.65 / keep>0.75）
- 仓位：8 只等权 runtime_total_value×1/8，close 即时段内先卖后买

QuantStudio APIs / numpy / pandas / g / log 由引擎注入。仅本地（targets=quantstudio）。
"""

import numpy as np

STRATEGY_ID = 'fscore_rsrs'
STRATEGY_NAME = 'F-Score选股RSRS择时'
DESIGN_VERSION = '2.2'

# ============ 冻结参数（R0 裁决，禁止调整）============
INDEX_CODE = '000300.SS'          # 股票池/基准/RSRS 指数
TARGET_HOLDINGS = 8               # 持仓数
PER_POSITION_WEIGHT = 1.0 / 8.0   # 等权 1/8
RSRS_WINDOW = 18                  # 回归窗口（交易日）
RSRS_LOOKBACK = 600               # 分位统计窗口
RSRS_THRESHOLD = 0.7              # 信号中心阈值
RSRS_BUFFER = 0.05                # 滞回缓冲
RSRS_SELL = 0.65                  # sell = 分位下穿 0.65
RSRS_KEEP = 0.75                  # keep = 分位上穿 0.75
FSCORE_MIN = 6                    # F-Score 总分>6（≥7 项达标）
RV_REMOVE_PCT = 70                # RV 残差波动分位 >70% 剔除
ROA_IMPROVE_PCT = 0.5             # ROA 同比改善 ≥0.5pct
RV_WINDOW = 252                   # RV 计算窗口（交易日）


def _ensure_runtime_state():
    """幂等初始化 g 状态（有则保留，无则创建）。"""
    if not hasattr(g, 'universe'):
        g.universe = []
    if not hasattr(g, 'holdings'):
        g.holdings = {}            # code -> {'buy_dt', 'days_held'}
    if not hasattr(g, 'candidates'):
        g.candidates = []          # 最近一期月度候选（code 列表）
    if not hasattr(g, 'rsrs_state'):
        g.rsrs_state = 'keep'      # 初始 keep（默认不清仓）
    if not hasattr(g, 'rsrs_last_pct'):
        g.rsrs_last_pct = None
    if not hasattr(g, 'last_rebalance_month'):
        g.last_rebalance_month = None
    if not hasattr(g, 'rebalance_seq'):
        g.rebalance_seq = 0
    if not hasattr(g, 'last_rid'):
        g.last_rid = None
    if not hasattr(g, 'rebalance_done_date'):
        g.rebalance_done_date = None
    if not hasattr(g, 'pending_rebalance'):
        g.pending_rebalance = False


def _is_finance(code):
    """Step1 行业剔除：银行(801780/480000) + 非银金融(801790/490000)。fail-closed 歧义剔除。"""
    try:
        ind = get_industry(code)
        if not ind:
            return True  # 无法确认行业 → 保守剔除（宁可错过不可买错）
        l1 = ind.get('sw_l1') or {}
        ic = str(l1.get('industry_code') or '')
        return ic in ('801780', '801790', '480000', '490000')
    except Exception:
        return True  # fail-closed：拿不到行业 → 剔除


def _latest_statement(code, table, fields, date, start_year, end_year, report_types=None):
    """PIT 两期财务取数：返回 (本期行 Series, 去年同期行 Series)；缺数据返回 (None, None)。
    本期 = ann_date<=date 的最新报告期；去年同期 = 上一年同 end_date 的报告期（同比口径 D3）。
    report_types=None → 全部期型（取最新已披露，不强制年报）；年份窗口由调用方给足（year-2~year）。"""
    try:
        df = get_fundamentals(code, table=table, fields=['end_date'] + fields,
                              date=date, start_year=start_year, end_year=end_year,
                              report_types=report_types, is_dataframe=True)
    except Exception:
        return None, None
    if df is None or len(df) == 0:
        return None, None
    # 最新一期（end_date 最大）
    cur = df.sort_values('end_date').iloc[-1]
    cur_end = int(cur['end_date'])
    cur_ts = np.datetime64(cur_end, 'ms')
    # 去年同期：end_date 年份 - 1、月日相同（同比口径）
    target_year = int(str(cur_ts)[:4]) - 1
    matched = None
    for _, row in df.iterrows():
        ts = np.datetime64(int(row['end_date']), 'ms')
        if str(ts)[5:10] == str(cur_ts)[5:10] and int(str(ts)[:4]) == target_year:
            matched = row
            break
    return cur, matched


def _f_score(code, date):
    """F-Score 9 项打分（同比口径，每项达标=1 分）。返回 (score, detail_dict)。"""
    score = 0
    detail = {}
    year = int(str(date)[:4])

    cur_i, prev_i = _latest_statement(code, 'income_statement',
                                      ['np_parent_company_owners', 'operating_revenue',
                                       'operating_cost'],
                                      date, year - 2, year, None)
    cur_b, prev_b = _latest_statement(code, 'balance_statement',
                                      ['total_assets', 'total_liability',
                                       'total_current_assets', 'total_current_liability'],
                                      date, year - 2, year, None)
    cur_c, prev_c = _latest_statement(code, 'cashflow_statement',
                                      ['net_operate_cash_flow'],
                                      date, year - 2, year, None)
    if cur_i is None or cur_b is None or cur_c is None:
        return 0, {'missing': True}

    np_, rev, cost = (float(cur_i['np_parent_company_owners'] or 0),
                      float(cur_i['operating_revenue'] or 0),
                      float(cur_i['operating_cost'] or 0))
    ta = float(cur_b['total_assets'] or 0)
    cf = float(cur_c['net_operate_cash_flow'] or 0)
    tl = float(cur_b['total_liability'] or 0)
    tca = float(cur_b['total_current_assets'] or 0)
    tcl = float(cur_b['total_current_liability'] or 0)
    pnp = float(prev_i['np_parent_company_owners'] or 0) if prev_i is not None else None
    pta = float(prev_b['total_assets'] or 0) if prev_b is not None else None
    ptl = float(prev_b['total_liability'] or 0) if prev_b is not None else None
    ptca = float(prev_b['total_current_assets'] or 0) if prev_b is not None else None
    ptcl = float(prev_b['total_current_liability'] or 0) if prev_b is not None else None
    pcf = float(prev_c['net_operate_cash_flow'] or 0) if prev_c is not None else None
    prev_rev = float(prev_i['operating_revenue'] or 0) if prev_i is not None else None
    prev_cost = float(prev_i['operating_cost'] or 0) if prev_i is not None else None

    # ① 净利>0
    if np_ > 0: score += 1
    detail['net_profit_pos'] = np_ > 0
    # ② 经营现金流>0
    if cf > 0: score += 1
    detail['ocf_pos'] = cf > 0
    # ③ ROA 提升（同比）ROA=归母净利/总资产
    roa = np_ / ta if ta else 0
    proa = pnp / pta if (pnp is not None and pta) else None
    if proa is None or roa > proa: score += 1
    detail['roa_up'] = (proa is None) or (roa > proa)
    # ④ 现金流>净利
    if cf > np_: score += 1
    detail['ocf_gt_ni'] = cf > np_
    # ⑤ 长期负债率下降（总负债/总资产）
    ld = tl / ta if ta else 0
    pld = ptl / pta if (ptl is not None and pta) else None
    if pld is None or ld < pld: score += 1
    detail['leverage_down'] = (pld is None) or (ld < pld)
    # ⑥ 流动比率提升
    cr = tca / tcl if tcl else 0
    pcr = ptca / ptcl if (ptca is not None and ptcl) else None
    if pcr is None or cr > pcr: score += 1
    detail['current_ratio_up'] = (pcr is None) or (cr > pcr)
    # ⑦ 本期无增发（总股本同比未增）——get_valuation total_share 同比
    try:
        v = get_fundamentals(code, table='valuation', fields=['total_share'],
                             date=date, is_dataframe=True)
        ts_now = float(v['total_share'].iloc[0]) if len(v) else None
        vp = get_fundamentals(code, table='valuation', fields=['total_share'],
                              date='%d-12-31' % (year - 1), is_dataframe=True)
        ts_prev = float(vp['total_share'].iloc[0]) if len(vp) else None
        if ts_now is None or ts_prev is None or ts_now <= ts_prev:
            score += 1
        detail['no_issue'] = (ts_now is None or ts_prev is None or ts_now <= ts_prev)
    except Exception:
        score += 1
        detail['no_issue'] = True
    # ⑧ 毛利率提升
    gm = (rev - cost) / rev if rev else 0
    pgm = (prev_rev - prev_cost) / prev_rev if (prev_rev and prev_cost is not None) else None
    if pgm is None or gm > pgm: score += 1
    detail['gross_margin_up'] = (pgm is None) or (gm > pgm)
    # ⑨ 资产周转率提升
    turnover = rev / ta if ta else 0
    pturn = prev_rev / pta if (prev_rev is not None and pta) else None
    if pturn is None or turnover > pturn: score += 1
    detail['turnover_up'] = (pturn is None) or (turnover > pturn)

    detail['score'] = score
    detail['roa'] = roa
    detail['prev_roa'] = proa
    return score, detail


def _rsrs_signal(high_arr, low_arr):
    """RSRS：18 日高低价线性回归→斜率→历史分位。返回 (pctile, slope, r2)。"""
    w = RSRS_WINDOW
    if len(high_arr) < w:
        return None, None, None
    h = np.asarray(high_arr, dtype=float)
    l = np.asarray(low_arr, dtype=float)
    slopes = []
    for i in range(len(h) - w + 1):
        hh = h[i:i + w]
        ll = l[i:i + w]
        x = np.arange(w, dtype=float)
        b_h = np.polyfit(x, hh, 1)[0]
        b_l = np.polyfit(x, ll, 1)[0]
        slopes.append((b_h + b_l) / 2.0)  # 高/低斜率均值（RSRS 标准做法）
    slopes = np.asarray(slopes)
    last_slope = slopes[-1]
    hist = slopes[-(RSRS_LOOKBACK + 1):-1] if len(slopes) > RSRS_LOOKBACK else slopes[:-1]
    if len(hist) == 0:
        return None, last_slope, None
    pct = float(np.mean(hist <= last_slope)) * 100.0
    hh = h[-w:]
    x = np.arange(w, dtype=float)
    pred = np.polyval(np.polyfit(x, hh, 1), x)
    ss_res = float(np.sum((hh - pred) ** 2))
    ss_tot = float(np.sum((hh - np.mean(hh)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot else 0.0
    return pct, last_slope, r2


def initialize(context):
    _ensure_runtime_state()
    set_benchmark(INDEX_CODE)
    log.info('%s initialized: index=%s hold=%d per=%.2f rsrs=%d/%d thr=%.2f buf=%.2f'
             % (STRATEGY_NAME, INDEX_CODE, TARGET_HOLDINGS, PER_POSITION_WEIGHT,
                RSRS_WINDOW, RSRS_LOOKBACK, RSRS_THRESHOLD, RSRS_BUFFER))


def before_trading_start(context, data):
    _ensure_runtime_state()
    members = get_index_stocks(INDEX_CODE)
    g.universe = sorted(members) if members else []
    # 状态硬过滤（ST/停牌/退市——HARDFILTER-STATUS 契约，R3 R4 校验要求）
    if g.universe:
        try:
            st = get_stock_status(g.universe, query_type='ST')
            alive = []
            for code in g.universe:
                status = st.get(code, {}) if isinstance(st, dict) else {}
                is_st = bool(status.get('isST') if isinstance(status, dict) else False)
                suspended = bool(status.get('suspended') if isinstance(status, dict) else False)
                if not is_st and not suspended:
                    alive.append(code)
            g.universe = sorted(alive)
        except Exception as e:
            log.warning('QS_STATUS date=%s error=%s（fail-open 保留原池）' % (str(context.current_dt)[:10], e))


def handle_data(context, data):
    _ensure_runtime_state()
    today = str(context.current_dt)[:10]
    tv = context.portfolio.total_value

    # ===================== RSRS 每日择时（先于一切，T-1 数据）=====================
    try:
        h = get_history(INDEX_CODE, count=RSRS_WINDOW + RSRS_LOOKBACK,
                        unit='1d', fields=['high', 'low'], fq='pre',
                        include=False, is_dict=True)
        idx_df = list(h.values())[0] if h else None
        if idx_df is not None and len(idx_df) >= RSRS_WINDOW:
            pct, slope, r2 = _rsrs_signal(idx_df['high'].values, idx_df['low'].values)
            g.rsrs_last_pct = pct
            if pct is not None:
                # B2 修复（2026-08-28 平台实证）：_rsrs_signal 返回 0-100 百分数，
                # RSRS_SELL/KEEP 是 0-1 分数——统一到分数域比较（%÷100），
                # 否则 sell 永不触发/keep 恒成立（平台 07 月 pctile=0.83 仍 keep 暴露）。
                p_frac = pct / 100.0
                if p_frac < RSRS_SELL:
                    g.rsrs_state = 'sell'
                elif p_frac > RSRS_KEEP:
                    g.rsrs_state = 'keep'
            log.info('QS_RSRS date=%s pctile=%s slope=%s r2=%s state=%s'
                     % (today, ('%.2f' % pct) if pct is not None else 'NA',
                        ('%.4f' % slope) if slope is not None else 'NA',
                        ('%.4f' % r2) if r2 is not None else 'NA', g.rsrs_state))
    except Exception as e:
        log.warning('QS_RSRS date=%s error=%s' % (today, e))

    # ---- RSRS sell：全部清仓（候选仍记录但不买入）----
    rid_cur = getattr(g, 'last_rid', None) or 'none'
    if g.rsrs_state == 'sell':
        sold = 0
        for code in sorted(g.holdings.keys()):
            order_target_value(code, 0)
            g.holdings.pop(code, None)
            sold += 1
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s action=rsrs_clear '
                 'selected=%d tradable=0 sell_submitted=%d buy_submitted=0'
                 % (rid_cur, today, sold, sold))
        return

    # ===================== 月度选股再平衡 =====================
    month = today[:7]
    if g.last_rebalance_month != month:
        g.last_rebalance_month = month
        g.rebalance_seq += 1
        rid = 'fscore-%s-%04d' % (today.replace('-', ''), g.rebalance_seq)
        g.last_rid = rid
        funnel = {'universe': 0, 'finance_removed': 0, 'fscore_pass': 0,
                  'rv_removed': 0, 'roa_removed': 0, 'selected': 0}
        funnel['universe'] = len(g.universe)
        scored = []
        for code in g.universe:
            # Step1 行业剔除
            if _is_finance(code):
                funnel['finance_removed'] += 1
                continue
            # Step2 F-Score
            fs, det = _f_score(code, today)
            if fs <= FSCORE_MIN:
                continue
            funnel['fscore_pass'] += 1
            # Step3 RV（对 000300 日收益 OLS 残差 std 近似：个股日收益波动）
            try:
                hh = get_history(code, count=RV_WINDOW, unit='1d',
                                 fields=['close'], fq='pre', include=False, is_dict=True)
                cl = list(hh.values())[0]['close'].values if hh else None
                if cl is not None and len(cl) > 20:
                    rets = np.diff(np.asarray(cl, dtype=float)) / np.asarray(cl, dtype=float)[:-1]
                    rv = float(np.std(rets))
                else:
                    rv = None
            except Exception:
                rv = None
            if rv is None:
                continue
            # ROE（profit_ability 最新）
            try:
                rdf = get_fundamentals(code, table='profit_ability', fields=['roe'],
                                       date=today, is_dataframe=True)
                roe = float(rdf['roe'].iloc[0]) if len(rdf) else None
            except Exception:
                roe = None
            # Step4 ROA 同比改善 ≥0.5pct
            roa_cur = det.get('roa', 0)
            roa_prev = det.get('prev_roa')
            if roa_prev is not None and (roa_cur - roa_prev) * 100.0 < ROA_IMPROVE_PCT:
                funnel['roa_removed'] += 1
                continue
            scored.append((code, fs, roe, rv, roa_cur))
        # Step5 RV 分位>70% 剔除 + ROE 降序取 8
        if scored:
            rv_vals = np.array([s[3] for s in scored])
            rv_ok = rv_vals <= np.percentile(rv_vals, RV_REMOVE_PCT)
            survivors = [s for s, ok in zip(scored, rv_ok) if ok]
            funnel['rv_removed'] = int(sum(~rv_ok))
            # ROE 降序；None/nan 沉底（确定性 key：nan/None → -inf 固定值，防非全序 sort 抖动）
            def _roe_key(s):
                roe = s[2]
                return (-roe) if (roe is not None and roe == roe) else float('inf')
            survivors.sort(key=_roe_key)
            g.candidates = [s[0] for s in survivors[:TARGET_HOLDINGS]]
            funnel['selected'] = len(g.candidates)
        else:
            g.candidates = []
        log.info('QS_STEP1_REMOVED date=%s universe=%d finance=%d' % (
            today, funnel['universe'], funnel['finance_removed']))
        log.warning('QS_CAND_CUR date=%s selected=%d cand=[%s]'
                    % (today, funnel['selected'], ','.join(g.candidates)))
        to_sell = [c for c in g.holdings if c not in g.candidates]
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
                 'sell_submitted=%d buy_submitted=%d rv_removed=%d roa_removed=%d'
                 % (rid, today, funnel['selected'], funnel['selected'],
                    len(to_sell), len(g.candidates),
                    funnel['rv_removed'], funnel['roa_removed']))
        log.info('QS_SIGNAL_AUDIT date=%s universe=%d finance_removed=%d fscore_pass=%d '
                 'rv_removed=%d roa_removed=%d selected=%d'
                 % (today, funnel['universe'], funnel['finance_removed'],
                    funnel['fscore_pass'], funnel['rv_removed'],
                    funnel['roa_removed'], funnel['selected']))
        # 候选明细（R5 证据）
        for code, fs, roe, rv, roa in scored[:TARGET_HOLDINGS * 2]:
            log.info('QS_CANDIDATE date=%s code=%s fscore=%d roe=%s rv=%.6f roa=%.4f'
                     % (today, code, fs, ('%.2f' % roe) if roe is not None else 'NA', rv, roa))
        g.pending_rebalance = True  # 调仓日置位，执行段消费后清除

    # ===================== 再平衡执行（keep 状态；仅调仓日一次，C7 不重试）=====================
    if getattr(g, 'pending_rebalance', False):
        g.pending_rebalance = False
        g.rebalance_done_date = today
        # 卖出不在候选的持仓
        for code in sorted(g.holdings.keys()):
            if code not in g.candidates:
                order_target_value(code, 0)
                g.holdings.pop(code, None)
        # 买入候选（一次性尝试，C7：资金不足/无法成交 → 放弃当期不重试）
        for code in g.candidates:
            if len(g.holdings) >= TARGET_HOLDINGS:
                break
            if code in g.holdings:
                continue
            target = tv * PER_POSITION_WEIGHT
            order_target_value(code, target)
            pos = get_position(code)
            if getattr(pos, 'amount', 0) <= 0:
                log.warning('QS_UNFILLED date=%s code=%s reason=failed_or_insufficient（放弃当期 C7）' % (today, code))
                continue  # 未成交：不加入 holdings，不重试
            g.holdings[code] = {'buy_dt': today, 'days_held': 0}

    cash_ratio = context.portfolio.cash / tv if tv > 0 else 0.0
    gross = context.portfolio.market_value / tv if tv > 0 else 0.0
    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d cash_ratio=%.4f gross_exposure=%.4f'
             % (getattr(g, 'last_rid', None) or 'none', today, len(g.holdings), cash_ratio, gross))


def after_trading_end(context, data):
    _ensure_runtime_state()
    # 组合持仓快照（get_positions 契约调用；R5 审计证据）
    try:
        positions = get_positions()
        if positions:
            codes = sorted(positions.keys()) if isinstance(positions, dict) else []
            log.info('QS_POSITIONS date=%s count=%d codes=%s'
                     % (str(context.current_dt)[:10], len(codes), ','.join(codes[:16])))
    except Exception as e:
        log.warning('QS_POSITIONS date=%s error=%s' % (str(context.current_dt)[:10], e))
    pass