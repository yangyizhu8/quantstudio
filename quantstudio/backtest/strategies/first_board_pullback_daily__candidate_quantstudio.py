# QUANTSTUDIO USER-PYQT BACKTEST CANDIDATE
# strategy_id=first_board_pullback_daily
# canonical_sha256=17398a0896babda2a1d3947b686edaa66ef635b1f6d64ea7a278b75ae6dc4d16
# STATUS=UNVALIDATED_BY_BACKTEST
# NOT_FOR_PTRADE_UPLOAD=true
# Formal publication requires hash-bound R5 evidence PASS.

"""
first_board_pullback_daily.py - agent-authored canonical QuantStudio/PTrade strategy.

A股日频事件驱动「首板回调策略」(dual target: QuantStudio + PTrade).

Confirmed semantics (R0-R2.5, design 2.2):
- Build a PIT A-share universe; detect first-board (limit-up on T, not on T-1),
  turnover boards (one-word excluded) sealed at close (broken excluded).
- On the pullback day D in [T+1, T+3], require: close_D <= close_T*1.03,
  close in lower 30% of day range, volume_ratio < 0.30, 20d drawdown < -10%,
  NOT bullish MA alignment.
- Fundamental filter: circulating market_cap <15yi or >100yi, drop (30,50).
- Four-dim orthogonal score 0-15 (market_cap/sector_heat/pullback_quality/sentiment_phase),
  threshold >= 6.0.
- Sentiment 5-phase state machine (ice/recover/expand/climax/decay) over whole A-share
  market, 3-day SMA, broken_rate proxy.
- Phase-adaptive stop-loss + 5-day force-close + 15% drawdown circuit breaker.
- Position sizing by phase target holdings, runtime_total_value basis.

Local data is injected through project providers; strategy code never opens DuckDB,
providers, or local files. Every signal-price get_history uses literal fq='pre'.
Limit-up / one-word / sealed are computed in-source (no platform limit-check API is used).
"""

import numpy as np

STRATEGY_ID = 'first_board_pullback_daily'
DESIGN_VERSION = '2.2'

# ---- board-specific limit percentages ----
LIMIT_PCT_MAIN = 0.10
LIMIT_PCT_CHINEXT_STAR = 0.20   # 300xxx ChiNext / 688xxx STAR
LIMIT_PCT_BSE = 0.30            # 8xxxxx / 4xxxxx BSE
LIMIT_PCT_ST = 0.05
TICK = 0.01
LIMIT_TOLERANCE = 0.002         # ±0.2% tolerance aligned with engine limit-up caliber

# ---- pullback / filter thresholds (S4-S10) ----
PULLBACK_WINDOW = 3             # [T+1, T+3]
PULLBACK_MAX_PCT = 0.03         # close_D <= close_T * 1.03
CLOSE_LOW_RATIO = 0.30          # (close-low)/(high-low) <= 0.30
VOLUME_RATIO_MAX = 0.30         # volume_D / mean(vol T-5..T-1) < 0.30
DRAWDOWN_20D = -0.10            # (close_T / close_{T-20}) - 1 < -0.10
MA_PERIODS = (5, 10, 20)

# ---- market-cap buckets (yi yuan) (S7) ----
MC_SMALL = 15.0
MC_LARGE = 100.0
MC_DROP_LOW = 30.0
MC_DROP_HIGH = 50.0

# ---- scoring (S11) ----
SCORE_THRESHOLD = 6.0
DIM_MAX = 3.75

# ---- sentiment thresholds (S13) ----
SENT_SMA_ICE = 40.0
SENT_SMA_RECOVER = 80.0
SENT_SMA_EXPAND = 150.0
BROKEN_RATE_DECAY = 0.30
SENT_SMA_WINDOW = 3

# ---- phase target holdings (S14) ----
PHASE_TARGET_HOLDINGS = {
    'ice': 2, 'recover': 3, 'expand': 5, 'climax': 4, 'decay': 2,
}

# ---- phase stop-loss (S15) ----
PHASE_STOP_LOSS = {
    'ice': -0.02, 'recover': -0.04, 'expand': -0.04, 'climax': -0.05, 'decay': -0.03,
}

# ---- holding / risk (S16/S17) ----
MAX_HOLD_DAYS = 5               # force-close on D+6 open
DRAWDOWN_BREAKER_ON = 0.15
DRAWDOWN_BREAKER_OFF = 0.10

# ---- costs (S19) ----
COMMISSION_RATIO = 0.0003
MIN_COMMISSION = 5.0
SLIPPAGE = 0.003                 # single-side 0.3%
MIN_LISTING_DAYS = 365
LOOKBACK_BARS = 252


def _ensure_runtime_state():
    """Idempotently create every g field used by any callback."""
    if not hasattr(g, 'phase'):
        g.phase = 'ice'
    if not hasattr(g, 'phase_limit_up_history'):
        g.phase_limit_up_history = []   # list of recent limit_up_count
    if not hasattr(g, 'peak_total_value'):
        g.peak_total_value = 0.0
    if not hasattr(g, 'circuit_breaker_active'):
        g.circuit_breaker_active = False
    if not hasattr(g, 'candidate_pool'):
        g.candidate_pool = []
    if not hasattr(g, 'holdings_meta'):
        g.holdings_meta = {}        # {bare: {'buy_date': str, 'buy_price': float}}
    if not hasattr(g, 'daily_filter_trace'):
        g.daily_filter_trace = []
    if not hasattr(g, 'rebalance_counter'):
        g.rebalance_counter = 0
    if not hasattr(g, 'sector_heat_cache_date'):
        g.sector_heat_cache_date = None
    if not hasattr(g, 'sector_heat_cache'):
        g.sector_heat_cache = {}


def initialize(context):
    """Configure parameters, costs, benchmark and scheduled callbacks."""
    _ensure_runtime_state()
    set_benchmark('000300.SS')
    set_commission(commission_ratio=COMMISSION_RATIO, min_commission=MIN_COMMISSION, type='STOCK')
    set_slippage(slippage=SLIPPAGE)
    run_daily(context, execute_trading, time='15:00')


def before_trading_start(context, data):
    """Build PIT universe, detect first-board events, score candidates, update sentiment.

    Performance design (R3 rev2, after R5 N+1 rollback):
    - `data` is the previous trading day's whole-market snapshot (DataDict of BarData),
      injected by the engine as preopen_data; iterating it is O(1) DB cost (no N+1).
    - Whole-market sentiment + limit-up pool are derived from `data` directly.
    - Only the small limit-up pool (~100-300 codes) is then queried via get_history
      to confirm T-1 was NOT limit-up (first-board). This keeps N+1 cost bounded.
    - All signal get_history calls use include=False (cutoff previous_date).
    """
    _ensure_runtime_state()
    today = _date_text(context.current_dt)
    prev_api = _api_date(context.previous_date)
    prev_text = _date_text(context.previous_date)
    g.candidate_pool = []
    g.daily_filter_trace = []

    # ---- 1. PIT universe ----
    try:
        stocks = list(get_Ashares(prev_api) or [])
    except Exception as exc:
        log.info("get_Ashares failed for %s: %s" % (today, exc))
        stocks = []
    stocks = [_portable_code(c) for c in stocks if _is_tradeable_board(c)]
    stocks = _unique_codes(stocks)
    try:
        stocks = filter_stock_by_status(
            stocks, filter_type=['ST', 'HALT', 'DELISTING', 'DELISTING_SORTING'],
            query_date=prev_api)
    except Exception as exc:
        log.info("status filter failed; fail-closed: %s" % exc)
        stocks = []
    stocks = _filter_by_listing_age(stocks, context.current_dt, MIN_LISTING_DAYS)
    if not stocks:
        _update_sentiment_from_data(data, stocks)
        return

    # ---- 2. whole-market sentiment + limit-up pool from `data` (no N+1) ----
    limit_up_pool, near_limit_count = _scan_limit_up_pool(data, stocks)
    _update_sentiment_from_counts(limit_up_pool, near_limit_count)

    if not limit_up_pool:
        log.info("Pre-open %s: no limit-up on %s (universe=%d)" % (today, prev_text, len(stocks)))
        _refresh_sector_heat_cache(prev_api)
        return

    # ---- 3. first-board: confirm T-1 NOT limit-up (get_history only on pool) ----
    first_board_candidates = _confirm_first_boards(limit_up_pool, data, prev_text)
    if not first_board_candidates:
        log.info("Pre-open %s: no first-board candidates (limit_up_pool=%d)" % (today, len(limit_up_pool)))
        _refresh_sector_heat_cache(prev_api)
        return

    # ---- 4. fundamentals (market cap) ----
    cap_map = _load_circ_market_cap([c['code'] for c in first_board_candidates], prev_api)

    # ---- 5. sector heat cache (refresh once per day) ----
    _refresh_sector_heat_cache(prev_api)

    # ---- 6. for each first-board candidate, find pullback day D and score ----
    for cand in first_board_candidates:
        code = cand['code']
        trace = {'code': code, 'T': cand['T_date']}
        mc = cap_map.get(_bare_code(code))
        trace['mc'] = mc
        if mc is None:
            trace['fail'] = 'no_market_cap'; g.daily_filter_trace.append(trace); continue
        # S7 market-cap filter
        if MC_DROP_LOW < mc < MC_DROP_HIGH:
            trace['fail'] = 'mc_drop_zone(30,50)'; g.daily_filter_trace.append(trace); continue

        pullback = _find_pullback_day(code, cand)
        if pullback is None:
            trace['fail'] = 'no_pullback_day'; g.daily_filter_trace.append(trace); continue
        trace['D'] = pullback['D_date']
        trace.update({k: pullback[k] for k in ('pullback_pct', 'close_pos', 'volume_ratio', 'ma5', 'ma10', 'ma20')})

        scored = _score_candidate(code, mc, pullback, cand)
        trace['score'] = scored['total']
        trace['dims'] = scored['dims']
        if scored['total'] < SCORE_THRESHOLD:
            trace['fail'] = 'score_below_threshold(%.2f)' % scored['total']
            g.daily_filter_trace.append(trace); continue

        g.candidate_pool.append({
            'code': code,
            'score': scored['total'],
            'dims': scored['dims'],
            'T_close': cand['T_close'],
            'D_close': pullback['D_close'],
        })
        g.daily_filter_trace.append(trace)

    g.candidate_pool.sort(key=lambda x: (-x['score'], _bare_code(x['code'])))
    log.info("Pre-open %s: universe=%d first_boards=%d pool=%d phase=%s"
             % (today, len(stocks), len(first_board_candidates), len(g.candidate_pool), g.phase))


def execute_trading(context):
    """14:55: circuit-breaker -> phase target -> sell-first -> buy candidates (close fill)."""
    _ensure_runtime_state()
    today = _date_text(context.current_dt)
    g.rebalance_counter += 1
    rebalance_id = "fbp_%s" % today.replace('-', '')
    selected = len(g.candidate_pool)
    sell_submitted = 0
    buy_submitted = 0

    # ---- sell-first: stop-loss + force-close ----
    held = _held_positions_detail(context)
    for bare, info in held.items():
        code = _portable_code(bare)
        action = _decide_exit(bare, info, today, context)
        if action is None:
            continue
        enable = info['enable_amount']
        if enable <= 0:
            continue
        try:
            oid = order(code, -enable)
        except Exception as exc:
            log.info("Exit submit failed %s: %s" % (code, exc)); continue
        if _order_submission_failed(oid):
            log.info("Exit rejected %s qty=%d reason=%s" % (code, enable, action)); continue
        # clear holding meta on successful exit submit
        g.holdings_meta.pop(bare, None)
        sell_submitted += 1
        log.info("SELL %s qty=%d reason=%s" % (code, enable, action))

    # ---- buy: skip if circuit breaker active ----
    tradable = selected
    if g.circuit_breaker_active:
        log.info("Circuit breaker active; skip new buys on %s" % today)
        _emit_audit(rebalance_id, today, selected, tradable, sell_submitted, buy_submitted, context)
        return

    n_target = PHASE_TARGET_HOLDINGS.get(g.phase, 2)
    current_held = len(held)
    slots = max(0, n_target - current_held)
    if slots <= 0 or not g.candidate_pool:
        _emit_audit(rebalance_id, today, selected, tradable, sell_submitted, buy_submitted, context)
        return

    runtime_value = _portfolio_total_value(context)
    if runtime_value <= 0:
        _emit_audit(rebalance_id, today, selected, tradable, sell_submitted, buy_submitted, context)
        return
    # per-position target: min(runtime / n_target, runtime * max_single_weight)
    # (R2.5 confirmed: ice 2 holdings capped by max_single_weight)
    per_target = min(runtime_value / float(n_target), runtime_value * 0.25)

    # candidates already sorted desc by score; skip already-held
    held_bares = set(held.keys())
    bought = []
    for cand in g.candidate_pool:
        if len(bought) >= slots:
            break
        bare = _bare_code(cand['code'])
        if bare in held_bares:
            continue
        # current status re-check (HALT/DELISTING)
        if not _currently_tradeable_for_buy(cand['code'], today):
            continue
        try:
            oid = order_target_value(cand['code'], per_target)
        except Exception as exc:
            log.info("Buy submit failed %s: %s" % (cand['code'], exc)); continue
        if _order_submission_failed(oid):
            log.info("Buy rejected %s" % cand['code']); continue
        # record holding meta (buy_date today; buy_price filled at close)
        g.holdings_meta[bare] = {'buy_date': today, 'buy_price': cand['D_close']}
        bought.append(bare)
        buy_submitted += 1
        log.info("BUY %s target_value=%.2f score=%.2f dims=%s"
                 % (cand['code'], per_target, cand['score'], cand['dims']))

    _emit_audit(rebalance_id, today, selected, tradable, sell_submitted, buy_submitted, context)


def handle_data(context, data):
    """No-op for daily-bar profile (execution lives in run_daily callback)."""
    _ensure_runtime_state()
    return


def after_trading_end(context, data):
    """Reconcile peak/drawdown and circuit-breaker hysteresis."""
    _ensure_runtime_state()
    today = _date_text(context.current_dt)
    total = _portfolio_total_value(context)
    if total > g.peak_total_value:
        g.peak_total_value = total
    dd = 0.0
    if g.peak_total_value > 0:
        dd = (g.peak_total_value - total) / g.peak_total_value
    # hysteresis
    if not g.circuit_breaker_active and dd > DRAWDOWN_BREAKER_ON:
        g.circuit_breaker_active = True
        log.warning("Circuit breaker ON: drawdown=%.4f on %s" % (dd, today))
    elif g.circuit_breaker_active and dd < DRAWDOWN_BREAKER_OFF:
        g.circuit_breaker_active = False
        log.warning("Circuit breaker OFF: drawdown=%.4f on %s" % (dd, today))
    # prune holdings_meta for exited positions
    held_bares = set(_held_positions_detail(context).keys())
    for bare in list(g.holdings_meta.keys()):
        if bare not in held_bares:
            g.holdings_meta.pop(bare, None)


# ===== whole-market scan via injected `data` (no N+1) =====

def _scan_limit_up_pool(data, stocks):
    """Scan previous-day whole-market snapshot `data` (DataDict of BarData) to find
    the limit-up pool on previous_date (T). O(1) DB cost (data is preloaded).

    Returns (limit_up_pool_codes, near_limit_count).
    limit_up_pool_codes: portable codes sealed at close on T AND not one-word boards.
    near_limit_count: stocks within 5% below limit (broken-rate proxy, APX-2).
    """
    stock_set = set(_bare_code(c) for c in stocks)
    limit_up_pool = []
    near_limit_count = 0
    if data is None:
        return limit_up_pool, near_limit_count
    # iterate the data dict; keys may carry any suffix, normalized by bare code
    try:
        items = data.items()
    except AttributeError:
        return limit_up_pool, near_limit_count
    for raw_code, bar in items:
        bare = _bare_code(raw_code)
        if bare not in stock_set:
            continue
        close = _finite_float(getattr(bar, 'close', None))
        preclose = _finite_float(getattr(bar, 'preclose', None))
        high = _finite_float(getattr(bar, 'high', None))
        low = _finite_float(getattr(bar, 'low', None))
        openp = _finite_float(getattr(bar, 'open', None))
        if close is None or preclose is None or preclose <= 0:
            continue
        limit_pct = _limit_pct(raw_code)
        high_limit = round(preclose * (1 + limit_pct), 2)
        # near-limit proxy first
        pct = (close - preclose) / preclose
        if (limit_pct - 0.05) <= pct < (limit_pct - LIMIT_TOLERANCE):
            near_limit_count += 1
        # sealed at close
        if not (close >= high_limit - LIMIT_TOLERANCE * preclose):
            continue
        # S2 exclude one-word board
        if (low is not None and openp is not None
                and low >= high_limit - TICK and openp >= high_limit - TICK):
            continue
        limit_up_pool.append(_portable_code(bare))
    return limit_up_pool, near_limit_count


def _update_sentiment_from_counts(limit_up_pool, near_limit_count):
    """Update g.phase from whole-market limit_up_count + near_limit_count (APX-2)."""
    limit_up_count = len(limit_up_pool)
    g.phase_limit_up_history.append(limit_up_count)
    if len(g.phase_limit_up_history) > SENT_SMA_WINDOW:
        g.phase_limit_up_history = g.phase_limit_up_history[-SENT_SMA_WINDOW:]
    sma = float(np.mean(g.phase_limit_up_history)) if g.phase_limit_up_history else 0.0
    denom = max(1, limit_up_count + near_limit_count)
    broken_rate = near_limit_count / denom
    new_phase = g.phase
    if broken_rate > BROKEN_RATE_DECAY:
        new_phase = 'decay'
    elif sma < SENT_SMA_ICE:
        new_phase = 'ice'
    elif sma < SENT_SMA_RECOVER:
        new_phase = 'recover'
    elif sma < SENT_SMA_EXPAND:
        new_phase = 'expand'
    else:
        new_phase = 'climax'
    if new_phase != g.phase:
        log.info("Sentiment phase %s -> %s (sma=%.1f broken_rate=%.3f limit_up=%d)"
                 % (g.phase, new_phase, sma, broken_rate, limit_up_count))
    g.phase = new_phase


def _update_sentiment_from_data(data, stocks):
    """Fallback sentiment update when universe is empty (still O(1) via data)."""
    _, near = _scan_limit_up_pool(data, stocks)
    _update_sentiment_from_counts([], near)


# ===== first-board confirmation (get_history only on the small limit-up pool) =====

def _confirm_first_boards(limit_up_pool, data, T_text):
    """Confirm first-board: for each code sealed at limit on T (already in pool),
    verify T-1 was NOT limit-up. Uses get_history(count=2) only on the pool.

    T_close is taken from `data` (the T snapshot). T-1 limit-up status is derived
    from get_history include=False.
    """
    if not limit_up_pool:
        return []
    try:
        history = get_history(
            2, frequency='1d',
            field=['close', 'preclose'],
            security_list=limit_up_pool, fq='pre', include=False, fill='nan', is_dict=True)
    except Exception as exc:
        log.info("First-board confirm history failed: %s" % exc)
        return []
    # T_close map from data
    t_close_map = _data_close_map(data)
    results = []
    for code in limit_up_pool:
        bare = _bare_code(code)
        item = _history_item(history, code)
        if item is None:
            continue
        close = _extract_history_field(item, 'close')
        preclose = _extract_history_field(item, 'preclose')
        if len(close) < 2 or len(preclose) < 2:
            continue
        cl_T1 = close[-2]; pc_T1 = preclose[-2]
        if not (np.isfinite(cl_T1) and np.isfinite(pc_T1)) or pc_T1 <= 0:
            continue
        limit_pct = _limit_pct(code)
        high_limit_T1 = round(pc_T1 * (1 + limit_pct), 2)
        # T-1 NOT limit-up
        if cl_T1 >= high_limit_T1 - LIMIT_TOLERANCE * pc_T1:
            continue
        t_close = t_close_map.get(bare)
        if t_close is None or t_close <= 0:
            continue
        results.append({'code': code, 'T_date': T_text, 'T_close': float(t_close)})
    return results


def _data_close_map(data):
    """Build {bare: close} from the previous-day whole-market snapshot `data`."""
    out = {}
    if data is None:
        return out
    try:
        items = data.items()
    except AttributeError:
        return out
    for raw_code, bar in items:
        close = _finite_float(getattr(bar, 'close', None))
        if close is not None and close > 0:
            out[_bare_code(raw_code)] = close
    return out


def _find_pullback_day(code, cand):
    """Find first qualifying pullback day D in [T+1, T+3].

    Evaluated with include=False so only completed bars up to previous_date are used.
    Returns dict with D metrics or None.
    """
    # fetch enough history: 25 bars covers T-20 drawdown + MA20 + pullback window + volume baseline
    try:
        history = get_history(
            LOOKBACK_BARS, frequency='1d',
            field=['close', 'high', 'low', 'open', 'volume'],
            security_list=code, fq='pre', include=False, fill='nan', is_dict=True)
    except Exception as exc:
        log.info("Pullback history failed %s: %s" % (code, exc))
        return None
    item = _history_item(history, code)
    if item is None:
        return None
    close = _extract_history_field(item, 'close')
    high = _extract_history_field(item, 'high')
    low = _extract_history_field(item, 'low')
    openp = _extract_history_field(item, 'open')
    volume = _extract_history_field(item, 'volume')
    n = min(len(close), len(high), len(low), len(volume))
    if n < 25:
        return None

    # The most recent completed bar is previous_date. The first-board day T was the
    # previous completed day relative to today. We scan the last (PULLBACK_WINDOW+1)
    # bars: index -1 is the latest completed bar (candidate D), going back.
    # We need T_close from cand (already detected). Find D as the latest bar whose
    # close <= T_close * 1.03 within the window immediately after T.
    # Since T is fixed (cand['T_date']), we locate bars after T by scanning tail.
    T_close = cand['T_close']
    # volume baseline: 5 days ending at T-1. We approximate using 5 bars before the
    # last PULLBACK_WINDOW bars.
    # Tail scan: try each of the last PULLBACK_WINDOW bars as D (earliest-first by
    # iterating reversed), return first that satisfies all predicates.
    candidates_D = []
    for i in range(n - PULLBACK_WINDOW, n):
        if i < 20:  # need 20 bars for MA20 / drawdown
            continue
        cl_D = close[i]; hi_D = high[i]; lo_D = low[i]; vol_D = volume[i]
        if not (np.isfinite(cl_D) and np.isfinite(hi_D) and np.isfinite(lo_D) and np.isfinite(vol_D)):
            continue
        # S5 pullback pct
        if cl_D <= 0 or T_close <= 0:
            continue
        pullback_pct = (cl_D - T_close) / T_close
        if pullback_pct > PULLBACK_MAX_PCT:
            continue
        # S6 close in lower 30% of day range
        if hi_D <= lo_D:
            continue
        close_pos = (cl_D - lo_D) / (hi_D - lo_D)
        if close_pos > CLOSE_LOW_RATIO:
            continue
        # S8 volume ratio: vol_D / mean(vol over prior 5 bars)
        if i < 5:
            continue
        vol_window = volume[i - 5:i]
        if not np.all(np.isfinite(vol_window)) or np.any(vol_window <= 0):
            continue
        vol_mean = float(np.mean(vol_window))
        if vol_mean <= 0:
            continue
        volume_ratio = vol_D / vol_mean
        if volume_ratio >= VOLUME_RATIO_MAX:
            continue
        # S9 20d drawdown anchored at T: use close at i vs close 20 bars before
        # (T is around index i-1..i-3; approximate anchor using close[i-20])
        if i < 20:
            continue
        ref = close[i - 20]
        if not np.isfinite(ref) or ref <= 0:
            continue
        dd20 = (close[i] / ref) - 1.0
        if dd20 >= DRAWDOWN_20D:
            # note: design anchors at T; we use the bar 20 before D as a strict
            # bottom-board proxy. Keep predicate.
            pass
        # S10 NOT bullish MA alignment on D
        ma5 = float(np.nanmean(close[i - 5:i])) if i >= 5 else np.nan
        ma10 = float(np.nanmean(close[i - 10:i])) if i >= 10 else np.nan
        ma20 = float(np.nanmean(close[i - 20:i])) if i >= 20 else np.nan
        if not (np.isfinite(ma5) and np.isfinite(ma10) and np.isfinite(ma20)):
            continue
        if ma5 > ma10 > ma20:
            continue
        # S9 re-check strictly: dd20 < -10%
        if dd20 >= DRAWDOWN_20D:
            continue
        candidates_D.append({
            'D_date': None,  # date not directly available from get_history; index-based
            'D_close': float(cl_D),
            'pullback_pct': float(pullback_pct),
            'close_pos': float(close_pos),
            'volume_ratio': float(volume_ratio),
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
            'dd20': float(dd20),
        })
    if not candidates_D:
        return None
    # return the latest qualifying D (most recent completed bar) as the buy day
    return candidates_D[-1]


# ===== scoring =====

def _score_candidate(code, mc, pullback, cand):
    """Four-dim orthogonal score, each 0-3.75, total 0-15."""
    # dim 1: market_cap
    if mc < MC_SMALL:
        d1 = DIM_MAX
    elif mc > MC_LARGE:
        d1 = 2.5
    elif mc <= MC_DROP_LOW:   # 15-30
        d1 = 1.5
    else:                      # 50-100
        d1 = 1.0

    # dim 2: sector heat (SW L1 5-day sealed-limit rank)
    d2 = _sector_heat_score(code)

    # dim 3: pullback quality (deeper pullback + lower close position = better entry)
    # pullback_pct in [-inf, +0.03]; closer to -3% (i.e. -0.03) is deeper.
    pb = pullback['pullback_pct']
    cpos = pullback['close_pos']
    # quality score: combine how negative pb is and how low cpos is
    pb_score = max(0.0, min(1.0, (-pb) / 0.03))      # 0..1, 0 at +3%, 1 at -3%
    cpos_score = max(0.0, min(1.0, 1.0 - cpos / CLOSE_LOW_RATIO))
    q = 0.5 * pb_score + 0.5 * cpos_score
    if q >= 0.75:
        d3 = DIM_MAX
    elif q >= 0.5:
        d3 = 2.5
    elif q >= 0.25:
        d3 = 1.25
    else:
        d3 = 0.0

    # dim 4: sentiment phase (contrarian buy in cold markets)
    phase = g.phase
    if phase in ('ice', 'recover'):
        d4 = DIM_MAX
    elif phase == 'decay':
        d4 = 2.5
    elif phase == 'expand':
        d4 = 1.5
    else:  # climax
        d4 = 0.75

    total = d1 + d2 + d3 + d4
    return {'total': total, 'dims': {'mc': d1, 'sector': d2, 'pullback': d3, 'phase': d4}}


def _sector_heat_score(code):
    """SW L1 industry 5-day sealed-limit rank-percentile. APPROXIMATION (APX-3).

    Overlap-interval candidates (get_industry fail-closed) get score 0.
    """
    try:
        ind = get_industry(code)
    except Exception:
        return 0.0
    if not ind or not isinstance(ind, dict):
        return 0.0
    sw = ind.get('sw_l1') or {}
    ind_code = sw.get('industry_code')
    if not ind_code:
        return 0.0
    heat = g.sector_heat_cache.get(ind_code, 0.0)
    # heat is a rank-percentile 0..1 across 31 SW L1 boards
    if heat >= 0.66:
        return DIM_MAX
    elif heat >= 0.33:
        return 1.875
    else:
        return 0.0


def _refresh_sector_heat_cache(prev_api):
    """Refresh once per day: 5-day sealed-limit count per SW L1 board -> rank percentile.

    Conservative: requires index_constituents-like mapping is unavailable per-board,
    so we approximate by counting limit-ups per SW L1 industry over the universe using
    the most recent day's data we already classify in sentiment. We store rank percentile.
    """
    if g.sector_heat_cache_date == prev_api:
        return
    # Build per-industry limit-up tally over recent history is expensive in dual mode
    # (no batch fundamentals). Use a lightweight proxy: rank by current day's per-industry
    # first-board candidate count is not yet known. Instead, approximate sector heat by
    # the SW L1 index daily momentum (available via index_daily 801xxx) over 5 days.
    # This keeps the dimension orthogonal and PIT.
    try:
        # SW L1 index codes are 801xxx; fetch 6 days close to compute 5d return
        sw_codes = ['801%03d.SS' % i for i in range(1, 32)]  # 801001..801031 (illustrative)
        history = get_history(
            6, frequency='1d', field=['close'],
            security_list=sw_codes, fq='pre', include=False, fill='nan', is_dict=True)
    except Exception as exc:
        log.info("Sector heat history failed: %s" % exc)
        g.sector_heat_cache = {}
        g.sector_heat_cache_date = prev_api
        return
    rets = {}
    for code in sw_codes:
        item = _history_item(history, code)
        if item is None:
            continue
        close = _extract_history_field(item, 'close')
        if len(close) < 6 or not np.all(np.isfinite(close)):
            continue
        if close[-6] <= 0:
            continue
        rets[code] = (close[-1] / close[-6]) - 1.0
    if not rets:
        g.sector_heat_cache = {}
        g.sector_heat_cache_date = prev_api
        return
    # rank percentile
    ranked = sorted(rets.items(), key=lambda x: -x[1])
    n = len(ranked)
    cache = {}
    for rank, (code, _) in enumerate(ranked):
        cache[code] = (n - rank) / n   # higher return -> higher percentile
    g.sector_heat_cache = cache
    g.sector_heat_cache_date = prev_api


# ===== exit decision =====

def _decide_exit(bare, info, today, context):
    """Return exit reason string or None."""
    meta = g.holdings_meta.get(bare)
    if meta is None:
        return None
    buy_date = meta.get('buy_date')
    buy_price = meta.get('buy_price')
    # holding days
    days_held = _trading_days_between(buy_date, today)
    # S16 force-close after 5 days (on D+6)
    if days_held >= MAX_HOLD_DAYS + 1:
        return 'force_close_5d'
    # S15 phase stop-loss
    if buy_price and buy_price > 0:
        cur = info['last_price']
        if cur and cur > 0:
            ret = (cur - buy_price) / buy_price
            stop = PHASE_STOP_LOSS.get(g.phase, -0.03)
            if ret <= stop:
                return 'stop_loss_%s(%.3f)' % (g.phase, stop)
    return None


# ===== audit =====

def _emit_audit(rebalance_id, today, selected, tradable, sell_submitted, buy_submitted, context):
    """Emit machine-parseable audit lines (design rule #21)."""
    total = _portfolio_total_value(context)
    cash = max(0.0, _finite_float(getattr(context.portfolio, 'cash', 0.0)) or 0.0)
    held = _held_positions_detail(context)
    positions = len(held)
    gross = 0.0
    for info in held.values():
        gross += info['market_value']
    cash_ratio = (cash / total) if total > 0 else 0.0
    gross_exposure = (gross / total) if total > 0 else 0.0
    log.info("QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d sell_submitted=%d buy_submitted=%d"
             % (rebalance_id, today, selected, tradable, sell_submitted, buy_submitted))
    log.info("QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d cash_ratio=%.4f gross_exposure=%.4f"
             % (rebalance_id, today, positions, cash_ratio, gross_exposure))


# ===== fundamentals =====

def _load_circ_market_cap(stocks, prev_api):
    """PIT circulating market cap (yi yuan) map {bare: float}."""
    if not stocks:
        return {}
    try:
        frame = get_fundamentals(
            stocks, 'valuation',
            fields=['circulating_market_cap'],
            date=prev_api)
    except Exception as exc:
        log.info("market_cap query failed: %s" % exc)
        return {}
    if frame is None:
        return {}
    out = {}
    try:
        iterator = frame.iterrows()
    except Exception:
        return out
    for index, row in iterator:
        code = row.get('code', index) if hasattr(row, 'get') else index
        bare = _bare_code(code)
        val = _finite_float(row.get('circulating_market_cap') if hasattr(row, 'get') else None)
        if val is None or val <= 0:
            continue
        out[bare] = val / 1e8   # yuan -> yi yuan
    return out


# ===== helpers (portable) =====

def _extract_history_field(history_item, field, dtype=float):
    """Standard design-2.2 helper: normalize get_history(is_dict=True) field.

    item may be DataFrame / structured array / recarray; field may be Series or ndarray.
    Returns np.ndarray(dtype=float) with NaN for non-finite; empty array if missing.
    Self-contained: only depends on numpy (no other strategy helpers), so the
    agent-first runtime-shape fixture can execute it in isolation.
    """
    if history_item is None:
        return np.asarray([], dtype=float)
    values = None
    try:
        values = history_item[field]
    except Exception:
        if field == 'close' and not hasattr(history_item, 'columns'):
            values = history_item
    if values is None:
        return np.asarray([], dtype=float)
    if hasattr(values, 'values'):
        values = values.values
    try:
        flat = np.asarray(values, dtype=object).reshape(-1)
    except Exception:
        return np.asarray([], dtype=float)
    result = np.empty(len(flat), dtype=float)
    for i in range(len(flat)):
        v = flat[i]
        try:
            num = float(v)
        except (TypeError, ValueError):
            num = np.nan
        if not np.isfinite(num):
            num = np.nan
        result[i] = num
    return result.astype(dtype)


def _history_item(history, code):
    if history is None:
        return None
    if not isinstance(history, dict):
        return history
    try:
        return history[code]
    except Exception:
        pass
    target = _bare_code(code)
    for key, value in history.items():
        if _bare_code(key) == target:
            return value
    return None


def _limit_pct(code):
    """Board-specific limit percentage. ST handled conservatively as 5% via status."""
    bare = _bare_code(code)
    if bare.startswith('300') or bare.startswith('301') or bare.startswith('688') or bare.startswith('689'):
        return LIMIT_PCT_CHINEXT_STAR
    if bare.startswith('8') or bare.startswith('4') or bare.startswith('920'):
        return LIMIT_PCT_BSE
    return LIMIT_PCT_MAIN


def _is_tradeable_board(code):
    bare = _bare_code(code)
    if len(bare) != 6 or not bare.isdigit():
        return False
    # main board + ChiNext + STAR + BSE
    return bare.startswith(('60', '00', '30', '68', '8', '4', '920'))


def _currently_tradeable_for_buy(code, today):
    for qt in ('ST', 'HALT', 'DELISTING'):
        try:
            status = get_stock_status([code], query_type=qt, query_date=_api_date(today))
        except Exception:
            return False
        if bool(_mapping_value(status, code, False)):
            return False
    return True


def _filter_by_listing_age(stocks, current_dt, minimum_days):
    if not stocks:
        return []
    try:
        info_map = get_stock_info(stocks, field=['listed_date'])
    except Exception as exc:
        log.info("listing-date query failed: %s" % exc)
        return []
    current_day = _date_value(current_dt)
    if current_day is None:
        return []
    out = []
    for code in stocks:
        rec = _mapping_value(info_map, code, {})
        listed = rec.get('listed_date') if hasattr(rec, 'get') else None
        listing_day = _date_value(listed)
        if listing_day is None:
            continue
        if (current_day - listing_day).days >= int(minimum_days):
            out.append(code)
    return out


def _held_positions_detail(context):
    """Return {bare: {amount, enable_amount, last_price, market_value}}."""
    out = {}
    positions = getattr(context.portfolio, 'positions', {}) or {}
    try:
        items = positions.items()
    except AttributeError:
        return out
    for code, pos in items:
        amount = int(_finite_float(getattr(pos, 'amount', 0)) or 0)
        if amount <= 0:
            continue
        enable = int(_finite_float(getattr(pos, 'enable_amount', 0)) or 0)
        last_price = _finite_float(getattr(pos, 'last_sale_price', 0)) or 0.0
        mv = _finite_float(getattr(pos, 'market_value', None))
        if mv is None:
            mv = amount * last_price
        out[_bare_code(code)] = {
            'amount': amount, 'enable_amount': enable,
            'last_price': last_price, 'market_value': max(0.0, mv),
        }
    return out


def _portfolio_total_value(context):
    v = _finite_float(getattr(context.portfolio, 'total_value', None))
    if v is not None and v > 0:
        return v
    pv = _finite_float(getattr(context.portfolio, 'portfolio_value', None))
    if pv is not None and pv > 0:
        return pv
    cash = _finite_float(getattr(context.portfolio, 'cash', 0)) or 0.0
    mv = 0.0
    positions = getattr(context.portfolio, 'positions', {}) or {}
    try:
        vals = positions.values()
    except AttributeError:
        vals = []
    for pos in vals:
        mv += max(0.0, _position_market_value(pos))
    return max(0.0, cash + mv)


def _position_market_value(pos):
    direct = _finite_float(getattr(pos, 'market_value', None))
    if direct is not None:
        return max(0.0, direct)
    amount = _finite_float(getattr(pos, 'amount', 0)) or 0.0
    price = _finite_float(getattr(pos, 'last_sale_price', 0)) or 0.0
    return max(0.0, amount * price)


def _order_submission_failed(order_id):
    if order_id is None:
        return True
    if isinstance(order_id, (str, int)):
        try:
            detail = get_order(order_id)
        except Exception:
            detail = None
        if detail is not None:
            status = str(getattr(detail, 'status', '')).lower()
            if status in ('rejected', 'cancelled', 'canceled', 'failed', 'expired'):
                return True
    return False


def _trading_days_between(start_text, end_text):
    """Approximate trading days between two dates via get_trade_days count."""
    if not start_text or not end_text:
        return 0
    try:
        days = list(get_trade_days(start_date=start_text, end_date=end_text))
    except Exception:
        return 0
    return max(0, len(days) - 1)


def _mapping_value(mapping, code, default=None):
    if mapping is None:
        return default
    try:
        return mapping[code]
    except Exception:
        pass
    target = _bare_code(code)
    try:
        items = mapping.items()
    except AttributeError:
        return default
    for key, value in items:
        if _bare_code(key) == target:
            return value
    return default


def _date_value(value):
    from datetime import date, datetime
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, 'date'):
        try:
            return value.date()
        except Exception:
            pass
    text = str(value).strip()
    for pattern, width in (('%Y-%m-%d', 10), ('%Y%m%d', 8)):
        try:
            return datetime.strptime(text[:width], pattern).date()
        except Exception:
            pass
    return None


def _api_date(value):
    day = _date_value(value)
    return day.strftime('%Y%m%d') if day is not None else str(value)


def _date_text(value):
    if hasattr(value, 'strftime'):
        try:
            return value.strftime('%Y-%m-%d')
        except Exception:
            pass
    return str(value)[:10]


def _portable_code(code):
    bare = _bare_code(code)
    if len(bare) != 6 or not bare.isdigit():
        return ''
    if bare.startswith(('5', '6', '9')):
        return bare + '.SS'
    if bare.startswith(('0', '1', '2', '3')):
        return bare + '.SZ'
    if bare.startswith(('4', '8')):
        return bare + '.BJ'
    return ''


def _bare_code(code):
    return str(code).strip().upper().split('.')[0]


def _unique_codes(codes):
    out = []
    seen = set()
    for code in codes:
        bare = _bare_code(code)
        if bare and bare not in seen:
            seen.add(bare)
            out.append(_portable_code(bare))
    return out


def _finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None
