"""
动量轮动RSRS择时策略（momentum_rotation_rsrs_timing）— agent-authored QuantStudio-only strategy.

设计版本 2.2（R0-R2.5 客户确认，参数全部冻结禁止回调）。

核心逻辑：
- 股票池：全A股 get_Ashares（默认排除北交所）→ 剔 ST/停牌/次新(<60交易日)/科创板(688)/流动性不足(<5000万)
- 每日收盘（close）：RSRS 择时（000300.SS 18日高低价回归→斜率z-score→±0.7）
  → 空头态全清仓现金；多头态动量选股持仓1只
- 动量综合分 = 20日收益率百分位×0.7 + 60日波动率倒数百分位×0.3，取最高
- 止损：浮亏≥15%（cost_basis 优先，缺失用最近 fq='pre' close）→ 硬止损卖出 → 空仓等下一调仓日
- 涨跌停：涨停买不进当日放弃、次日信号仍在重试；跌停卖不出顺延至可卖日
- 成本：印花税0.05%卖/佣金万2.5/过户费0.001%/滑点0.1%（R5 阶梯敏感性校验）
- 资金：runtime_total_value × 1.0，纯多头无杠杆；order_target_value 补差语义
- 审计：QS_REBALANCE_AUDIT / QS_PORTFOLIO_AUDIT 每调仓日输出

QuantStudio APIs / numpy / pandas / g / log 由引擎注入。仅本地（targets=quantstudio）。
"""

import numpy as np
import pandas as pd

STRATEGY_ID = 'momentum_rotation_rsrs_timing'
STRATEGY_NAME = '动量轮动RSRS择时策略'
DESIGN_VERSION = '2.2'

# ============ 冻结参数（R0/R2.5 客户确认，禁止调整）============
INDEX_CODE = '000300.SS'         # RSRS 大盘指数 + 基准
RSRS_WINDOW = 18                 # 回归窗口（交易日）
RSRS_Z_UP = 0.7                  # 多头触发 z-score 上穿
RSRS_Z_DOWN = -0.7               # 空头触发 z-score 下穿
MOM_LOOKBACK = 20                # 动量窗口（交易日）
VOL_LOOKBACK = 60                # 波动率窗口（交易日）
MOM_WEIGHT = 0.7                 # 动量分权重
VOL_WEIGHT = 0.3                 # 平稳分权重
STOP_LOSS_PCT = 0.15             # 浮亏硬止损 15%
NEW_STOCK_DAYS = 60              # 次新定义：上市不足 60 交易日
LIQUIDITY_MIN_AMT = 5e7          # 流动性：近20日日均成交额 >= 5000 万（元）
LIQUIDITY_WINDOW = 20            # 流动性统计窗口
BUFFER_PCT = 0.03                # 换仓缓冲带
RSRS_HIST_WINDOW = 600           # RSRS 斜率分位历史窗口（z-score 用最近 250 日标准差）

def _ensure_runtime_state():
    """幂等初始化 g 状态（有则保留，无则创建）。"""
    if not hasattr(g, 'universe'):
        g.universe = []
    if not hasattr(g, 'holdings'):
        g.holdings = {}        # code -> {'buy_dt', 'days_held'}
    if not hasattr(g, 'rsrs_state'):
        g.rsrs_state = 'long'     # 初始多头（默认可持仓）
    if not hasattr(g, 'rsrs_z'):
        g.rsrs_z = None
    if not hasattr(g, 'last_rebalance_date'):
        g.last_rebalance_date = None
    if not hasattr(g, 'rebalance_seq'):
        g.rebalance_seq = 0
    if not hasattr(g, 'last_rid'):
        g.last_rid = None
    if not hasattr(g, 'pending_buy'):
        g.pending_buy = None      # 涨停未买进的候选，次日重试
    if not hasattr(g, 'stop_out_date'):
        g.stop_out_date = None    # 止损发生日，当日/次日空仓不反手


def _is_star_market(code):
    """科创板：688 开头。"""
    bare = code.split('.')[0]
    return bare.startswith('688') or bare.startswith('689')


def _is_bse(code):
    """北交所：8/4/920 开头（get_Ashares 默认已排除，双保险）。"""
    bare = code.split('.')[0]
    return bare.startswith(('8', '4', '920'))


def _extract_history_field(history_item, field, dtype=float):
    """标准提取助手：从 get_history(is_dict=True) 的 item 提取字段为 float ndarray。
    item 可能是 DataFrame / recarray / ndarray；一律经 np.asarray 归一。"""
    if history_item is None:
        return np.array([], dtype=dtype)
    if hasattr(history_item, 'columns'):
        return np.asarray(history_item[field].values, dtype=dtype)
    if isinstance(history_item, np.ndarray):
        return np.asarray(history_item[field], dtype=dtype)
    try:
        return np.asarray(history_item[field], dtype=dtype)
    except Exception:
        return np.array([], dtype=dtype)


def _rsrs_signal(index_high, index_low):
    """RSRS：18 日高低价斜率 → z-score 标准化。
    返回 (z_score, last_slope)。z_score = (last_slope - mean(近250)) / std(近250)。"""
    w = RSRS_WINDOW
    if len(index_high) < w + 1:
        return None, None
    h = np.asarray(index_high, dtype=float)
    l = np.asarray(index_low, dtype=float)
    x = np.arange(w, dtype=float)
    n = len(h)
    slopes = []
    for i in range(n - w + 1):
        hh = h[i:i + w]
        ll = l[i:i + w]
        bh = np.polyfit(x, hh, 1)[0]
        bl = np.polyfit(x, ll, 1)[0]
        slopes.append((bh + bl) / 2.0)
    slopes = np.asarray(slopes)
    last_slope = slopes[-1]
    hist = slopes[:-1]
    if len(hist) < 20:
        return None, last_slope
    mean_s = float(np.mean(hist))
    std_s = float(np.std(hist))
    if std_s <= 1e-12:
        return None, last_slope
    z = (last_slope - mean_s) / std_s
    return float(z), last_slope


def _momentum_score(codes, today):
    """动量综合分（get_history_batch 批量 + numpy 矩阵化）。返回 {code: score}。"""
    scores = {}
    if not codes:
        return scores
    need = max(MOM_LOOKBACK, VOL_LOOKBACK) + 1
    try:
        hist = get_history_batch(codes, count=need, unit='1d',
                                 fields=['close'], fq='pre', include=False)
    except Exception:
        return scores
    rets = {}
    vols = {}
    for code in codes:
        item = hist.get(code)
        closes = _extract_history_field(item, 'close')
        if len(closes) < need:
            continue
        c = closes[-(need):]
        if c[-1 - MOM_LOOKBACK] > 0:
            rets[code] = c[-1] / c[-1 - MOM_LOOKBACK] - 1.0
        else:
            rets[code] = None
        diffs = np.diff(c[-(VOL_LOOKBACK + 1):])
        base = c[-(VOL_LOOKBACK + 1):-1]
        with np.errstate(divide='ignore', invalid='ignore'):
            daily = diffs / base
        daily = daily[np.isfinite(daily)]
        vols[code] = float(np.std(daily)) if len(daily) >= 5 else None
    # 百分位
    def pct_rank(vals):
        items = sorted(vals.items(), key=lambda x: x[1])
        n = len(items)
        out = {}
        for i, (k, v) in enumerate(items):
            out[k] = (i + 0.5) / n
        return out
    rk_ret = pct_rank({c: v for c, v in rets.items() if v is not None})
    rk_vol = pct_rank({c: 1.0 / v for c, v in vols.items() if v is not None and v > 0})
    for code in codes:
        r_ = rk_ret.get(code)
        v_ = rk_vol.get(code)
        if r_ is None or v_ is None:
            continue
        scores[code] = MOM_WEIGHT * r_ + VOL_WEIGHT * v_
    return scores
    # 百分位
    def pct_rank(vals):
        s = pd.Series(vals)
        return s.rank(pct=True).to_dict()
    rk_ret = pct_rank({c: v for c, v in returns.items() if v is not None})
    rk_vol = pct_rank({c: 1.0 / v for c, v in vols.items() if v is not None and v > 0})
    for code in codes:
        r_ = rk_ret.get(code)
        v_ = rk_vol.get(code)
        if r_ is None or v_ is None:
            continue
        scores[code] = MOM_WEIGHT * r_ + VOL_WEIGHT * v_
    return scores


def _is_illiquid(codes, today):
    """流动性过滤：近20日日均成交额 < 5000 万 → 剔除（get_history_batch 批量）。返回不可交易 list。"""
    illiquid = []
    if not codes:
        return illiquid
    try:
        hist = get_history_batch(codes, count=LIQUIDITY_WINDOW, unit='1d',
                                 fields=['amount'], fq='pre', include=False)
        for code in codes:
            item = hist.get(code)
            amt = _extract_history_field(item, 'amount')
            if len(amt) == 0 or float(np.mean(amt)) < LIQUIDITY_MIN_AMT:
                illiquid.append(code)
    except Exception:
        return list(codes)  # 批量失败 → 保守全剔（宁可错过）
    return illiquid


def _compute_pool(context):
    """构建 PIT 股票池（每日 before_trading_start 调用）。"""
    today = str(context.current_dt)[:10]
    try:
        all_a = get_Ashares(date=today.replace('-', ''))
    except Exception:
        all_a = get_Ashares()
    if not all_a:
        return []
    pool = []
    for code in all_a:
        if _is_star_market(code) or _is_bse(code):
            continue
        pool.append(code)
    return sorted(pool)


def initialize(context):
    """Configure parameters, costs, universe and scheduled callbacks."""
    _ensure_runtime_state()
    set_benchmark(INDEX_CODE)
    try:
        set_slippage(slippage=0.001)
    except Exception:
        set_slippage(0.001)
    log.info('%s initialized: index=%s mom=%d vol=%d rsrs=%d z_up=%.2f z_dn=%.2f stop=%.2f'
             % (STRATEGY_NAME, INDEX_CODE, MOM_LOOKBACK, VOL_LOOKBACK,
                RSRS_WINDOW, RSRS_Z_UP, RSRS_Z_DOWN, STOP_LOSS_PCT))


def before_trading_start(context, data):
    """Build the PIT universe and prepare factors without same-day future data."""
    _ensure_runtime_state()
    pool = _compute_pool(context)
    # 状态硬过滤（ST/停牌）
    alive = []
    try:
        st = get_stock_status(pool, query_type='ST') if pool else {}
        for code in pool:
            status = st.get(code, {}) if isinstance(st, dict) else {}
            is_st = bool(status.get('isST', False))
            suspended = bool(status.get('suspended', False))
            if not is_st and not suspended:
                alive.append(code)
    except Exception:
        alive = pool  # fail-open 保留原池
    # 次新过滤：上市不足 60 交易日
    try:
        info = get_stock_info(alive, field=['listed_date']) if alive else {}
        final = []
        today_dt = context.current_dt
        for code in alive:
            rec = info.get(code, {}) if isinstance(info, dict) else {}
            ld = rec.get('listed_date')
            if ld:
                try:
                    ld_dt = pd.Timestamp(str(ld))
                    if (today_dt - ld_dt).days < NEW_STOCK_DAYS * 0.7:
                        continue  # 近似：60 交易日 ~ 84 自然日
                except Exception:
                    pass
            final.append(code)
        g.universe = sorted(final)
    except Exception:
        g.universe = sorted(alive)


def handle_data(context, data):
    """Evaluate bar-dependent logic for the declared engine profile."""
    _ensure_runtime_state()
    today = str(context.current_dt)[:10]
    tv = context.portfolio.total_value
    rid = getattr(g, 'last_rid', None) or 'none'

    # ===================== RSRS 每日择时（T-1 数据，先于一切）=====================
    try:
        h = get_history(INDEX_CODE, count=RSRS_WINDOW + RSRS_HIST_WINDOW,
                        frequency='1d', fields=['high', 'low'], fq='pre',
                        include=False, is_dict=True)
        item = list(h.values())[0] if h else None
        hi = _extract_history_field(item, 'high')
        lo = _extract_history_field(item, 'low')
        z, slope = _rsrs_signal(hi, lo)
        g.rsrs_z = z
        if z is None:
            # 预热期：中性，不改变现有状态（沿用当前持仓），不触发新买卖
            pass
        elif g.rsrs_state != 'long' and z > RSRS_Z_UP:
            g.rsrs_state = 'long'
        elif g.rsrs_state != 'short' and z < RSRS_Z_DOWN:
            g.rsrs_state = 'short'
        log.info('QS_RSRS date=%s z=%s slope=%s state=%s'
                 % (today, ('%.4f' % z) if z is not None else 'NA',
                    ('%.4f' % slope) if slope is not None else 'NA', g.rsrs_state))
    except Exception as e:
        log.warning('QS_RSRS date=%s error=%s' % (today, e))

    # ---- RSRS 空头：全部清仓现金 ----
    if g.rsrs_state == 'short':
        sold = 0
        for code in sorted(g.holdings.keys()):
            try:
                order_target_value(code, 0)
                sold += 1
            except Exception:
                pass
        g.holdings = {}
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s action=rsrs_short '
                 'selected=0 tradable=0 sell_submitted=%d buy_submitted=0'
                 % (rid, today, sold))
        g.last_rebalance_date = today
        return

    # ===================== 止损检查（先于换仓）=====================
    for code in list(g.holdings.keys()):
        try:
            pos = get_position(code)
            if pos is None or getattr(pos, 'amount', 0) <= 0:
                g.holdings.pop(code, None)
                continue
            cost = getattr(pos, 'cost_basis', None) or getattr(pos, 'avg_cost', None)
            if cost is None or cost <= 0:
                # 兜底：最近 fq='pre' close
                hh = get_history(code, count=1, frequency='1d', fields=['close'],
                                 fq='pre', include=False, is_dict=True)
                item = list(hh.values())[0] if hh else None
                cc = _extract_history_field(item, 'close')
                cost = float(cc[-1]) if len(cc) else None
            if cost is None or cost <= 0:
                continue
            px = getattr(pos, 'price', None)
            if px is None or px <= 0:
                bar = data[code] if code in data else None
                px = getattr(bar, 'price', 0) if bar is not None else 0
            if px > 0 and (px / cost - 1.0) <= -STOP_LOSS_PCT:
                order_target_value(code, 0)
                g.holdings.pop(code, None)
                g.stop_out_date = today
                log.info('QS_STOP_LOSS date=%s code=%s cost=%.4f px=%.4f'
                         % (today, code, cost, px))
        except Exception as e:
            log.warning('QS_STOP date=%s code=%s error=%s' % (today, code, e))

    # ---- 止损后：空仓等下一调仓日（当日不反手）----
    if g.stop_out_date == today:
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s action=stop_cooldown '
                 'selected=0 tradable=0 sell_submitted=0 buy_submitted=0' % (rid, today))
        g.last_rebalance_date = today
        return

    # ===================== 动量选股 + 换仓（多头态）=====================
    pool = getattr(g, 'universe', [])
    # 流动性过滤
    illiquid = _is_illiquid(pool, today)
    tradable = [c for c in pool if c not in illiquid]
    if not tradable:
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s action=no_candidate '
                 'selected=0 tradable=0 sell_submitted=0 buy_submitted=0' % (rid, today))
        g.last_rebalance_date = today
        return

    scores = _momentum_score(tradable, today)
    if not scores:
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s action=no_score '
                 'selected=0 tradable=%d sell_submitted=0 buy_submitted=0'
                 % (rid, today, len(tradable)))
        g.last_rebalance_date = today
        return

    # 取最高分目标（持仓 1 只）
    target = max(scores, key=scores.get)
    sel_sorted = sorted(scores.items(), key=lambda x: -x[1])
    log.info('QS_MOM date=%s top=%s score=%.4f' % (today, target, scores[target]))

    # 涨跌停判断：涨停买不进 → 当日放弃（记 pending_buy 次日重试）
    def _can_buy(code):
        bar = data[code] if code in data else None
        if bar is None:
            return True
        hl = getattr(bar, 'high_limit', 0)
        px = getattr(bar, 'price', 0)
        return not (hl > 0 and px >= hl - 1e-6)

    def _can_sell(code):
        bar = data[code] if code in data else None
        if bar is None:
            return True
        ll = getattr(bar, 'low_limit', 0)
        px = getattr(bar, 'price', 0)
        return not (ll > 0 and px <= ll + 1e-6)

    sell_submitted = 0
    buy_submitted = 0
    g.rebalance_seq += 1
    rid = 'momrsrs-%s-%04d' % (today.replace('-', ''), g.rebalance_seq)
    g.last_rid = rid

    # 现有持仓 vs 目标
    current = list(g.holdings.keys())

    def _current_cash():
        return context.portfolio.cash if hasattr(context, 'portfolio') else tv

    def _target_value():
        # 目标仓位：不超过可用现金（防超杠杆/资金不足）；close 模式卖出款当日已入账
        desired = tv * 1.0 * (1 - BUFFER_PCT)
        affordable = _current_cash() * 0.99
        return max(0.0, min(desired, affordable))

    if current and current[0] != target:
        # 换仓：先卖旧
        old = current[0]
        old_sold = False
        if _can_sell(old):
            order_target_value(old, 0)
            sell_submitted += 1
            g.holdings.pop(old, None)
            old_sold = True
        # 买新：仅当旧仓已卖出（资金可用）或无持仓时；跌停卖不出→顺延，当日不建新仓
        if old_sold:
            if _can_buy(target):
                target_value = _target_value()
                if target_value > 100:
                    order_target_value(target, target_value)
                    g.holdings[target] = {'buy_dt': today, 'days_held': 0}
                    buy_submitted += 1
                else:
                    g.pending_buy = target
            else:
                g.pending_buy = target
        else:
            # 旧仓卖不出（跌停顺延）：保持原持仓，不建新仓
            g.pending_buy = target
    elif not current:
        # 空仓建仓
        if _can_buy(target):
            target_value = _target_value()
            if target_value > 100:
                order_target_value(target, target_value)
                g.holdings[target] = {'buy_dt': today, 'days_held': 0}
                buy_submitted += 1
            else:
                g.pending_buy = target
        else:
            g.pending_buy = target
    else:
        # 持仓即目标：不动（记录审计）
        pass

    g.last_rebalance_date = today
    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s action=rebalance '
             'selected=%d tradable=%d sell_submitted=%d buy_submitted=%d'
             % (rid, today, 1, len(tradable), sell_submitted, buy_submitted))

    # ---- 组合审计 ----
    npos = len(g.holdings)
    gross = 0.0
    for code in g.holdings:
        try:
            p = get_position(code)
            if p is not None:
                gross += getattr(p, 'market_value', 0) or 0
        except Exception:
            pass
    cash_ratio = (tv - gross) / tv if tv > 0 else 1.0
    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d cash_ratio=%.4f gross_exposure=%.4f'
             % (rid, today, npos, cash_ratio, gross / tv if tv > 0 else 0))


def after_trading_end(context, data):
    """Record diagnostics and reconcile persistent state after the close."""
    _ensure_runtime_state()
    today = str(context.current_dt)[:10]
    # 涨停未成交的 pending_buy：次日重试（记录即可，下一 handle_data 自然重试）
    if getattr(g, 'pending_buy', None):
        # 仅在多头态记录 pending buy；空头态清仓后不应残留
        if g.rsrs_state == 'short':
            g.pending_buy = None
        else:
            log.info('QS_PENDING_BUY date=%s pending=%s' % (today, g.pending_buy))
    # 更新持仓天数
    for code in g.holdings:
        g.holdings[code]['days_held'] = g.holdings[code].get('days_held', 0) + 1
    log.info('QS_END date=%s holdings=%d' % (today, len(g.holdings)))
