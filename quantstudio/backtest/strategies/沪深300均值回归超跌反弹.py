"""
沪深300均值回归超跌反弹（ou_reversal_csi300_10）.py - agent-authored QuantStudio-only strategy.

Chinese published filename: 沪深300均值回归超跌反弹.py (quantstudio/backtest/strategies/<strategy_name>.py).

设计契约: output/generated_strategies/ou_reversal_csi300_10/agent_strategy_design.json (design 2.3, R2.5 已关闭)
E1 原则（项目铁律）: 信号取数一律 get_history(..., fq='pre', include=False) —— 执行日 D 的回调读到信号日 S = D-1；
                     成交价模式显式 match_price_mode='open'（撮合于 D 日开盘价）；不使用 data[code] 任何字段。
QuantStudio APIs, registered local extensions, numpy/pandas, g and log are injected locally.
The validated file is published only to the QuantStudio PyQt strategy directory.
"""

import datetime

import numpy as np

STRATEGY_ID = 'ou_reversal_csi300_10'
STRATEGY_NAME = '沪深300均值回归超跌反弹'
DESIGN_VERSION = '2.3'

# ---------------------------------------------------------------- 参数冻结（客户提示词 + R0/R1/E1 裁定，零寻优）
_BENCHMARK = '000300.SS'
_INDEX_CODE = '000300'
_LISTING_MIN_DAYS = 100          # 上市 > 100 自然日
_DROP3_THRESHOLD_PCT = -8.0      # 近 3 日累计跌幅 < -8% 剔除（pctChg 单位为百分点）
_VOL60_MAX = 0.30                # 60 日年化波动率 < 30%
_PRICE_MIN = 2.0                 # 股价 > 2 元
_AMOUNT60_MIN = 10000000.0       # 60 日均额 > 1000 万元（amount 单位：元）
_ADX_STOCK_MIN = 20.0            # 个股 ADX(14) > 20
_ADX_MARKET_MIN = 25.0           # 市场 ADX(14) > 25
_ADX_PERIOD = 14
_BOLL_PERIOD = 20                # 布林周期（仅用中轨与中轨方向）
_MA_PERIOD = 60                  # MA60
_LOOKBACK_BARS = 61              # 统一取数根数（MA60/波动率/均额/ADX 共用）
_TARGET_HOLDINGS = 10            # 持仓数
_CASH_BUFFER = 0.03              # 3% 缓冲（费用/整手/价格漂移）
_COMMISSION_RATIO = 0.00324      # D3-C 往返等效 0.700%
_MIN_COMMISSION = 5.0
_ANNUALIZE = 250.0 ** 0.5        # √250
_LIMIT_MAIN_PCT = 10.0           # 主板涨跌停幅度（百分点）
_LIMIT_CHINEXT_PCT = 20.0        # 创业板涨跌停幅度（百分点）
_LIMIT_TOLERANCE_PCT = 0.1       # 判定容差（百分点）
_MAX_ORDER_ATTEMPTS = 3 * _TARGET_HOLDINGS   # F1-B 递补尝试上限（30 次）


# ---------------------------------------------------------------- 基础工具
def _ensure_runtime_state():
    """幂等运行状态守卫（每个回调首语句调用；逐属性守卫，重复调用不重置）。"""
    if not hasattr(g, 'rebalance_seq'):
        g.rebalance_seq = 0
    if not hasattr(g, 'day_index'):
        g.day_index = 0
    if not hasattr(g, 'listed_cache'):
        g.listed_cache = {}          # code -> 'YYYY-MM-DD'（上市日静态，会话级缓存）
    if not hasattr(g, 'last_targets'):
        g.last_targets = []
    if not hasattr(g, 'last_gate'):
        g.last_gate = None
    if not hasattr(g, 'signal_days'):
        g.signal_days = 0
    if not hasattr(g, 'order_days'):
        g.order_days = 0


def _date_text(value):
    if value is None:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%Y-%m-%d')
    s = str(value).strip()
    if len(s) >= 10 and s[4] == '-':
        return s[:10]
    if len(s) == 8 and s.isdigit():
        return '%s-%s-%s' % (s[:4], s[4:6], s[6:8])
    return s[:10]


def _api_date(value):
    return _date_text(value).replace('-', '')


def _date_obj(value):
    return datetime.datetime.strptime(_date_text(value), '%Y-%m-%d')


def _portable(code):
    """归一为 .SS/.SZ/.BJ（键精确匹配语义；调用方传入的已是 PTrade 代码时原样返回）。"""
    s = str(code).strip().upper()
    if '.' in s:
        return s
    if s[:3] in ('688', '689') or s[:1] in ('5', '6', '9'):
        return s + '.SS'
    if s[:1] in ('0', '1', '2', '3'):
        return s + '.SZ'
    if s[:1] in ('4', '8'):
        return s + '.BJ'
    return s + '.SS'


def _finite(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if np.isfinite(v) else default


def _is_excluded_board(code):
    """过滤①板块：剔除科创板（688/689）与北交所（920/430/83x/87x 等）。"""
    bare = str(code).split('.')[0]
    if bare[:3] in ('688', '689'):
        return True
    if bare[:3] == '920':
        return True
    if bare[:1] in ('4', '8'):
        return True
    return False


def _limit_pct(code):
    """涨跌停幅度（百分点）：创业板 300/301 为 20，其余（主板）为 10。"""
    bare = str(code).split('.')[0]
    if bare[:3] in ('300', '301'):
        return _LIMIT_CHINEXT_PCT
    return _LIMIT_MAIN_PCT


def _history_field(history, code, field, dtype=float):
    """rule 17：把 get_history(is_dict=True) 的单标的字段归一为 ndarray。"""
    item = history.get(code) if hasattr(history, 'get') else None
    if item is None:
        return np.asarray([], dtype=dtype)
    try:
        series = item[field]
    except Exception:
        return np.asarray([], dtype=dtype)
    try:
        return np.asarray(series, dtype=dtype)
    except (TypeError, ValueError):
        return np.asarray([], dtype=dtype)


def _wilder_adx(high, low, close, period=_ADX_PERIOD):
    """经典 Wilder(14) ADX（内联实现，不使用 MyTT/平台指标名）。返回最新 ADX 或 None。"""
    n = int(len(close))
    if n < 2 * period + 1:
        return None
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0.0) else 0.0
        minus_dm[i] = down if (down > up and down > 0.0) else 0.0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    tr_s = float(np.sum(tr[1:period + 1]))
    pdm_s = float(np.sum(plus_dm[1:period + 1]))
    mdm_s = float(np.sum(minus_dm[1:period + 1]))
    adx = None
    dx_count = 0
    dx_sum = 0.0
    for i in range(period + 1, n):
        tr_s = tr_s - tr_s / period + tr[i]
        pdm_s = pdm_s - pdm_s / period + plus_dm[i]
        mdm_s = mdm_s - mdm_s / period + minus_dm[i]
        if tr_s <= 0.0:
            continue
        pdi = 100.0 * pdm_s / tr_s
        mdi = 100.0 * mdm_s / tr_s
        denom = pdi + mdi
        dx = (100.0 * abs(pdi - mdi) / denom) if denom > 0.0 else 0.0
        if adx is None:
            dx_sum += dx
            dx_count += 1
            if dx_count == period:
                adx = dx_sum / period
        else:
            adx = (adx * (period - 1) + dx) / period
    return adx


def _portfolio_total_value(context):
    try:
        value = float(getattr(context.portfolio, 'portfolio_value', 0.0) or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if not np.isfinite(value) or value <= 0.0:
        try:
            value = float(getattr(context.portfolio, 'total_value', 0.0) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
    return value if (np.isfinite(value) and value > 0.0) else 0.0


def _positions_view(context):
    try:
        positions = get_positions() or {}
    except Exception:
        positions = {}
    if not positions:
        positions = getattr(context.portfolio, 'positions', None) or {}
    return positions


def _held_codes(context):
    """持仓（正股数）的 PTrade 代码列表，按 code 索引对齐（禁止按位置取值）。"""
    held = []
    for code, position in list(_positions_view(context).items()):
        amount = _finite(getattr(position, 'amount', 0.0), 0.0)
        if amount > 0.0:
            held.append(_portable(code))
    return sorted(dict.fromkeys(held))


# ---------------------------------------------------------------- 信号层
def _market_gate(day_text):
    """大盘择时门。指数行按 E1-4 消费至信号日 S = D-1（丢弃 trade_date >= D 的行）。"""
    try:
        idx = get_index_day_bar(_INDEX_CODE, count=_LOOKBACK_BARS + 1,
                                fields=['high', 'low', 'close'])
    except Exception as exc:
        log.warning('QS_INDEX_BAR_FAIL code=%s err=%s' % (_INDEX_CODE, exc))
        return False, 'index_bar_fail', ''
    if idx is None or len(idx) < _BOLL_PERIOD + 2:
        return False, 'index_bar_insufficient', ''
    try:
        trade_dates = [str(v)[:10] for v in list(idx.index)]
    except Exception:
        return False, 'index_bar_no_date', ''
    keep = [i for i, text in enumerate(trade_dates) if text < day_text]
    if len(keep) < _LOOKBACK_BARS:
        return False, 'index_rows_before_D_insufficient:%d' % len(keep), ''
    sub = idx.iloc[keep[-_LOOKBACK_BARS:]]
    highs = np.asarray(sub['high'], dtype=float)
    lows = np.asarray(sub['low'], dtype=float)
    closes = np.asarray(sub['close'], dtype=float)
    if not (np.all(np.isfinite(highs)) and np.all(np.isfinite(lows)) and np.all(np.isfinite(closes))):
        return False, 'index_bar_nan', ''
    signal_date = trade_dates[keep[-1]]
    adx = _wilder_adx(highs, lows, closes, _ADX_PERIOD)
    if adx is None:
        return False, 'index_adx_insufficient', signal_date
    mid_now = float(np.mean(closes[-_BOLL_PERIOD:]))
    mid_prev = float(np.mean(closes[-_BOLL_PERIOD - 1:-1]))
    last_close = float(closes[-1])
    opened = bool(adx > _ADX_MARKET_MIN) and bool(last_close > mid_now) and bool(mid_now > mid_prev)
    log.info('QS_MARKET_GATE date=%s signal_date=%s adx14=%.2f close=%.4f boll20=%.4f boll20_prev=%.4f open=%s'
             % (day_text, signal_date, adx, last_close, mid_now, mid_prev, opened))
    return opened, 'ok', signal_date


def _listing_age_ok(code, signal_date):
    """过滤①：上市 > 100 自然日（上市日静态，会话级缓存）。"""
    listed = g.listed_cache.get(code)
    if listed is None:
        try:
            info = get_stock_info([code], field=['listed_date'])
        except Exception:
            info = None
        record = info.get(code) if isinstance(info, dict) else None
        listed = (record or {}).get('listed_date') if isinstance(record, dict) else None
        g.listed_cache[code] = listed if isinstance(listed, str) else ''
    if not listed:
        return False
    try:
        age = (_date_obj(signal_date) - _date_obj(listed)).days
    except (TypeError, ValueError):
        return False
    return age > _LISTING_MIN_DAYS


def _select_targets(signal_date):
    """五层过滤 + OU 因子降序 Top10。全部取数经 include=False（E1-1）。"""
    try:
        universe = get_index_stocks(_INDEX_CODE, date=_api_date(signal_date))
    except Exception as exc:
        log.warning('QS_UNIVERSE_FAIL date=%s err=%s' % (signal_date, exc))
        return 0, []
    if not universe:
        return 0, []
    codes = [c for c in sorted(dict.fromkeys([_portable(c) for c in universe]))
             if not _is_excluded_board(c)]
    if not codes:
        return 0, []
    # 过滤① ST（is_st_reliable）/ 停牌（suspendFlag/volume）；无 query_date → 引擎 S 日快照
    try:
        st_map = get_stock_status(codes, query_type='ST') or {}
    except Exception:
        st_map = {}
    try:
        halt_map = get_stock_status(codes, query_type='HALT') or {}
    except Exception:
        halt_map = {}
    codes = [c for c in codes if not st_map.get(c) and not halt_map.get(c)]
    if not codes:
        return 0, []
    codes = [c for c in codes if _listing_age_ok(c, signal_date)]
    if not codes:
        return 0, []
    try:
        history = get_history(_LOOKBACK_BARS, frequency='1d',
                              field=['close', 'high', 'low', 'pctChg', 'money'],
                              security_list=codes, fq='pre', include=False, is_dict=True)
    except Exception as exc:
        log.warning('QS_HISTORY_FAIL date=%s err=%s' % (signal_date, exc))
        return 0, []
    scored = []
    for code in codes:
        closes = _history_field(history, code, 'close')
        if closes.size < _LOOKBACK_BARS:
            continue
        highs = _history_field(history, code, 'high')
        lows = _history_field(history, code, 'low')
        pct = _history_field(history, code, 'pctChg')
        amounts = _history_field(history, code, 'money')
        if highs.size < _LOOKBACK_BARS or lows.size < _LOOKBACK_BARS:
            continue
        if pct.size < 3 or amounts.size < _MA_PERIOD:
            continue
        window = closes[-_LOOKBACK_BARS:]
        if not (np.all(np.isfinite(window)) and np.all(np.isfinite(highs[-_LOOKBACK_BARS:]))
                and np.all(np.isfinite(lows[-_LOOKBACK_BARS:]))):
            continue
        # 过滤①涨跌停（信号日 S）
        if abs(float(pct[-1])) >= _limit_pct(code) - _LIMIT_TOLERANCE_PCT:
            continue
        # 过滤①近 3 日累计跌幅
        if float(np.sum(pct[-3:])) < _DROP3_THRESHOLD_PCT:
            continue
        price = float(window[-1])
        # 过滤③股价
        if not price > _PRICE_MIN:
            continue
        # 过滤④60 日均额
        if not float(np.mean(amounts[-_MA_PERIOD:])) > _AMOUNT60_MIN:
            continue
        # 过滤②60 日年化波动率（std ddof=1 × √250）
        base = window[-(_MA_PERIOD + 1):]
        if base.size < _MA_PERIOD + 1:
            continue
        rets = base[1:] / base[:-1] - 1.0
        if not np.all(np.isfinite(rets)):
            continue
        if not float(np.std(rets, ddof=1)) * _ANNUALIZE < _VOL60_MAX:
            continue
        # 过滤⑤个股 ADX(14)
        adx = _wilder_adx(highs[-_LOOKBACK_BARS:], lows[-_LOOKBACK_BARS:], window, _ADX_PERIOD)
        if adx is None or not adx > _ADX_STOCK_MIN:
            continue
        # OU 相对偏离度
        ma60 = float(np.mean(window[-_MA_PERIOD:]))
        scored.append(((ma60 - price) / price, code))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return len(scored), [code for _, code in scored]


# ---------------------------------------------------------------- 执行层
def _execute(context, day_text, gate_open, ranked, candidates, gate_note):
    """先卖后买 + F1-B 候选递补（A-14，路径①同批真值循环）。

    ranked = 过滤后按 OU 降序的**完整候选名单**（不预先截断为 10 只）。
    按名单顺序逐一下单，以引擎返回真值判定可买性：单手买不起 → 顺延下一位，
    直到凑满 _TARGET_HOLDINGS 只或触达守卫上限/名单耗尽。
    """
    held = _held_codes(context)
    held_set = list(dict.fromkeys(held))
    sell_submitted = 0
    skip_notes = []
    total_value = _portfolio_total_value(context)
    if total_value <= 0.0:
        return
    per_target = total_value * (1.0 - _CASH_BUFFER) / _TARGET_HOLDINGS
    if per_target <= 0.0:
        return
    # ---- 阶段一：先卖（排名口径的前 N 之外一律清出）——保证递补建仓有资金可用
    intended = list(dict.fromkeys(ranked[:_TARGET_HOLDINGS])) if gate_open else []
    sold = []
    for code in sorted(held_set):
        if code not in intended:
            order_target_value(code, 0)
            sell_submitted += 1
            sold.append(code)
    # ---- 阶段二：按 OU 降序名单确定最终目标（已持有直接计入；新仓以引擎真值判定，买不起则顺延）
    selected = []
    attempts = 0
    backfilled = 0
    if intended:
        for code in ranked:
            if len(selected) >= _TARGET_HOLDINGS:
                break
            if code in held_set:
                selected.append(code)
                _adj = order_target_value(code, per_target)   # 已持有：等权微调
                if getattr(_adj, 'status', '') == 'filled':
                    if getattr(_adj, 'direction', '') == 'sell':
                        sell_submitted += 1
                    else:
                        attempts += 1
                continue
            if attempts >= _MAX_ORDER_ATTEMPTS:
                skip_notes.append('attempt_cap:%d' % attempts)
                break
            attempts += 1
            order = order_target_value(code, per_target)
            status = getattr(order, 'status', '')
            reason = getattr(order, 'reason', '')
            if status == 'filled' and getattr(order, 'direction', '') == 'sell':
                sell_submitted += 1
            if status == 'filled' or reason == 'below_rebalance_threshold':
                selected.append(code)
            elif reason == 'delta_below_one_lot':
                backfilled += 1
                continue
            else:
                skip_notes.append('%s:%s' % (code, reason or 'unknown'))
    # ---- 阶段三：安全网——清出「已持有但未入选」且尚未卖出者（幂等，避免重复卖单）
    for code in sorted(held_set):
        if code not in selected and code not in sold:
            order_target_value(code, 0)
            sell_submitted += 1
    g.last_targets = list(selected)
    buy_submitted = attempts
    if sell_submitted + buy_submitted > 0 and not gate_open:
        # 择时门关闭 → 清仓事件：属风险门事件，不构成 rebalance；
        # 仅输出轻量 QS_LIQUIDATION 行，不输出 QS_REBALANCE_AUDIT/QS_PORTFOLIO_AUDIT
        # （避免与 r5_deployment_invariants 的建仓日口径错配——设计 §8 已声明）。
        log.info('QS_LIQUIDATION date=%s signal_date=%s gate=%s sold=%d holdings_after=%d'
                 % (day_text, day_text, gate_note, sell_submitted, len(_held_codes(context))))
        return
    if sell_submitted + buy_submitted > 0:
        g.rebalance_seq += 1
        g.order_days += 1
        rid = '%s-%03d' % (day_text.replace('-', ''), g.rebalance_seq)
        notes = []
        if not gate_open:
            notes.append('market_gate_closed:%s' % gate_note)
        if candidates < _TARGET_HOLDINGS:
            notes.append('candidates_below_10_legal(A-7):%d' % candidates)
        if len(selected) < _TARGET_HOLDINGS:
            notes.append('target_below_10_legal(A-7):%d' % len(selected))
        if backfilled > 0:
            notes.append('backfill(A-14):%d' % backfilled)
        if skip_notes:
            notes.append('not_filled:%d[%s]' % (len(skip_notes), ';'.join(skip_notes[:5])))
        tradable = min(candidates, _TARGET_HOLDINGS)
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
                 'sell_submitted=%d buy_submitted=%d note=%s'
                 % (rid, day_text, len(selected), tradable, sell_submitted, buy_submitted,
                    ','.join(notes) if notes else 'ok'))
        positions = _positions_view(context)
        total_after = _portfolio_total_value(context)
        cash = _finite(getattr(context.portfolio, 'cash', 0.0), 0.0)
        market_value = 0.0
        for _, position in list(positions.items()):
            market_value += _finite(getattr(position, 'market_value', 0.0), 0.0)
        cash_ratio = (cash / total_after) if total_after > 0.0 else 0.0
        gross = (market_value / total_after) if total_after > 0.0 else 0.0
        log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d cash_ratio=%.4f '
                 'gross_exposure=%.4f' % (rid, day_text, len(_held_codes(context)), cash_ratio, gross))


# ---------------------------------------------------------------- 生命周期
def initialize(context):
    """参数、成本、基准与状态初始化。"""
    _ensure_runtime_state()
    set_benchmark(_BENCHMARK)
    set_commission(commission_ratio=_COMMISSION_RATIO, min_commission=_MIN_COMMISSION)
    log.info('沪深300均值回归超跌反弹: init | 参数冻结 上市>%d日 跌幅<%d%% 波动率<%d%% 价>%.0f元 '
             '均额>%.0f万 个股ADX>%.0f 市场ADX>%.0f BOLL%d MA%d 持仓%d 成本%.5f'
             % (_LISTING_MIN_DAYS, int(_DROP3_THRESHOLD_PCT), int(_VOL60_MAX * 100), _PRICE_MIN,
                _AMOUNT60_MIN / 10000.0, _ADX_STOCK_MIN, _ADX_MARKET_MIN, _BOLL_PERIOD,
                _MA_PERIOD, _TARGET_HOLDINGS, _COMMISSION_RATIO))


def handle_data(context, data):
    """唯一决策回调：读信号日 S=D-1 数据 → 择时门 → 五层过滤 → OU Top10 → 按目标市值下单（撮合于 D 开盘价）。"""
    _ensure_runtime_state()
    g.day_index += 1
    day_text = _date_text(context.blotter.current_dt)
    gate_open, gate_note, signal_date = _market_gate(day_text)
    candidates = 0
    ranked = []
    if gate_open:
        signal_date = signal_date or _date_text(context.previous_date)
        candidates, ranked = _select_targets(signal_date)
    held = _held_codes(context)
    planned = ranked[:_TARGET_HOLDINGS]
    changed = 1 if sorted(dict.fromkeys(planned)) != sorted(dict.fromkeys(held)) else 0
    g.signal_days += 1
    log.info('QS_SIGNAL date=%s signal_date=%s gate=%s candidates=%d planned=%d changed=%d'
             % (day_text, signal_date, gate_note, candidates, len(planned), changed))
    _execute(context, day_text, gate_open, ranked, candidates, gate_note)
    g.last_gate = bool(gate_open)


def after_trading_end(context, data):
    """收盘后状态收尾与诊断。"""
    _ensure_runtime_state()
    log.info('Post-close %s: held=%d targets=%d gate=%s signal_days=%d order_days=%d'
             % (_date_text(context.blotter.current_dt), len(_held_codes(context)),
                len(g.last_targets), g.last_gate, g.signal_days, g.order_days))
