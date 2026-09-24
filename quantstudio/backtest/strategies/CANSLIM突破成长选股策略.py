# -*- coding: utf-8 -*-
"""CANSLIM突破成长选股策略.py - agent-authored QuantStudio-only strategy.

Chinese published filename: CANSLIM突破成长选股策略.py
(quantstudio/backtest/strategies/CANSLIM突破成长选股策略.py).

Design contract: agent_workspace/canslim_breakthrough/agent_strategy_design.json
This file is intentionally a lifecycle/API scaffold implementation, not a template.
QuantStudio APIs, registered local extensions, numpy/pandas, g and log are injected locally.
The validated file is published only to the QuantStudio PyQt strategy directory.
"""
import numpy as np
import pandas as pd

STRATEGY_ID = 'canslim_breakthrough'
STRATEGY_NAME = 'CANSLIM突破成长选股策略'
DESIGN_VERSION = '2.2'

# ---------------- 策略常量（语义契约，勿改语义） ----------------
TARGET_HOLDINGS = 5
PER_POSITION_WEIGHT = 0.20
MAX_SINGLE_WEIGHT = 0.25
INITIAL_STOP = 0.075          # 初始止损 -7.5%（前复权成本基准）
TRAILING_STOP = 0.20          # 移动止损：最高收盘回撤 >=20%
LIQUIDITY_FLOOR = 30_000_000.0  # 20日日均成交额 >=3000万
MIN_HISTORY_BARS = 260        # 250日新高/RPS/MA200 需要的历史长度
MIN_LISTING_BARS = 120        # 上市不足120交易日剔除
BREAKOUT_LOOKBACK = 60        # 放量突破平台高点回看
MA_ADHESION_THRESHOLD = 0.03  # 均线粘合 (max-min)/min <= 3%
VOLUME_BREAKOUT_RATIO = 2.0   # 突破日量比 >= 2.0
FACTOR_WEIGHTS = {'C': 0.25, 'A': 0.15, 'N': 0.20, 'S': 0.15, 'L': 0.15, 'I': 0.10}
M_FACTOR_CODE = '510300.SS'   # 沪深300 跟踪 ETF（前复权，2018-01 起全量）
BENCHMARK_CODE = '000300.SS'  # 基准：沪深300 指数 raw


def _extract_history_field(item, field, dtype=float):
    """Rule 17: normalize an extracted get_history item field via np.asarray."""
    if item is None:
        return np.asarray([], dtype=dtype)
    try:
        values = item[field]
    except (KeyError, TypeError, IndexError):
        return np.asarray([], dtype=dtype)
    if hasattr(values, 'values'):
        try:
            values = values.values
        except Exception:
            return np.asarray([], dtype=dtype)
    try:
        return np.asarray(values, dtype=dtype)
    except Exception:
        return np.asarray([], dtype=dtype)


def _ensure_runtime_state():
    """Idempotently create every g field used by any callback."""
    if not hasattr(g, 'universe'):
        g.universe = []
    if not hasattr(g, 'rebalance_id'):
        g.rebalance_id = 0
    if not hasattr(g, 'pending_buys'):
        g.pending_buys = []
    if not hasattr(g, 'buy_pre_prices'):
        g.buy_pre_prices = {}
    if not hasattr(g, 'peak_pre_prices'):
        g.peak_pre_prices = {}
    if not hasattr(g, 'last_audit_date'):
        g.last_audit_date = 0


def initialize(context):
    """Configure parameters, costs, universe and scheduled callbacks."""
    _ensure_runtime_state()
    set_benchmark(BENCHMARK_CODE)
    run_daily(context, daily_logic, time='14:55')
    log.info(f'[{STRATEGY_ID}] initialize done: target={TARGET_HOLDINGS}, stop={INITIAL_STOP:.1%}/{TRAILING_STOP:.1%}')


def before_trading_start(context, data):
    """每日开盘前：状态过滤（无未来数据）。"""
    _ensure_runtime_state()


def after_trading_end(context, data):
    """每日收盘后：更新峰值的兜底（主要峰值在 run_daily 已更新）。"""
    _ensure_runtime_state()


def handle_data(context, data):
    """引擎每 bar 调用：本策略决策集中在 run_daily 14:55 的 daily_logic，此处空操作。"""
    _ensure_runtime_state()


def daily_logic(context):
    """每日 run_daily 14:55 触发：M 闸门 → 止损检查 → 卖出 → 补位买入 → 审计行。"""
    _ensure_runtime_state()
    g.rebalance_id += 1
    today = context.current_dt
    today_str = today.strftime('%Y%m%d')

    # ---------- 1. M 因子（大盘趋势闸门） ----------
    m_ok = _check_m_factor(today_str)

    # ---------- 2. 持仓与止损 ----------
    positions = get_positions()
    if positions is None:
        positions = {}
    pos_codes = sorted([str(c) for c in positions.keys()])

    # 为持仓加载前复权历史（止损用）
    hist = _load_history(pos_codes + g.universe, count=MIN_HISTORY_BARS, today_str=today_str)

    # 对昨日买入的仓位，用昨日前复权收盘建立成本基准
    for code in list(g.pending_buys):
        if code in pos_codes and code in hist:
            closes = _extract_history_field(hist[code], 'close')
            if len(closes) > 0:
                g.buy_pre_prices[code] = float(closes[-1])
                g.peak_pre_prices[code] = float(closes[-1])
        g.pending_buys.remove(code)

    sells = []
    if pos_codes:
        for code in pos_codes:
            item = hist.get(code)
            if item is None:
                # #4 诊断：持仓股未取到历史（平台侧序列缺失的候选证据）
                log.warning(f'STOPDBG d={today_str[-4:]} {code} hist_missing')
                continue
            closes = _extract_history_field(item, 'close')
            if len(closes) == 0:
                log.warning(f'STOPDBG d={today_str[-4:]} {code} hist_empty')
                continue
            pre_close = float(closes[-1])  # T-1 前复权收盘
            # 更新移动止损峰值
            if code in g.peak_pre_prices:
                g.peak_pre_prices[code] = max(g.peak_pre_prices[code], pre_close)
            else:
                g.peak_pre_prices[code] = pre_close
            # 初始止损
            buy_basis = g.buy_pre_prices.get(code)
            # #4 诊断：打印止损判定输入（closes 末 3 根日期 + 判定值 + 成本基准）
            dates = _extract_history_field(item, 'trade_date', dtype=str)
            last_dates = '[{}]'.format(','.join(str(d) for d in dates[-3:])) if len(dates) > 0 else '[]'
            ratio = (pre_close / buy_basis - 1.0) if (buy_basis is not None and buy_basis > 0) else float('nan')
            log.info(
                f'STOPDBG d={today_str[-4:]} {code} pre={pre_close:.4f} basis={buy_basis if buy_basis is not None else -1:.4f} '
                f'ratio={ratio:.4%} peak={g.peak_pre_prices.get(code, 0):.4f} dates={last_dates}'
            )
            if buy_basis is not None and buy_basis > 0 and pre_close <= buy_basis * (1 - INITIAL_STOP):
                sells.append(code)
                continue
            # 移动止损
            peak = g.peak_pre_prices.get(code)
            if peak is not None and peak > 0 and pre_close <= peak * (1 - TRAILING_STOP):
                sells.append(code)
                continue

    # 执行卖出
    for code in sells:
        try:
            order_target_value(code, 0)
        except Exception as exc:
            log.warning(f'[{STRATEGY_ID}] sell order failed {code}: {exc}')

    # 卖出后次日才补位：今日卖出的空位明天才计入可补位
    # 可补位数 = target - 卖出前持仓数（即卖出前已有的空位）
    fill_count = max(0, TARGET_HOLDINGS - len(pos_codes))

    # ---------- 3. 选股池与因子 ----------
    candidates = []
    passed_count = 0
    if m_ok and fill_count > 0:
        # 动态全 A 池（无参 = 引擎按当前回测日 as-of PIT 快照，含窗口内退市股）
        all_codes = get_Ashares()
        if not isinstance(all_codes, (list, tuple)):
            all_codes = list(all_codes)
        all_codes = sorted([str(c) for c in all_codes])

        # 状态过滤：ST/停牌/退市（wsgm10 同款 filter_stock_by_status 管线）
        filtered = filter_stock_by_status(all_codes, filter_type=['ST', 'HALT', 'DELISTING'],
                                          query_date=None)
        if filtered is None:
            filtered = []
        filtered = sorted([str(c) for c in filtered])

        # 加载历史并做上市/流动性过滤 + 形态突破
        cand_hist = _load_history(filtered, count=MIN_HISTORY_BARS, today_str=today_str)
        passed = []
        liq_pass = 0
        for code in filtered:
            item = cand_hist.get(code)
            if item is None:
                continue
            close = _extract_history_field(item, 'close')
            high = _extract_history_field(item, 'high')
            volume = _extract_history_field(item, 'volume')
            if len(close) < MIN_HISTORY_BARS or len(high) < MIN_HISTORY_BARS or len(volume) < MIN_HISTORY_BARS:
                continue
            # 流动性过滤：20 日日均成交额（close*volume, 元）>= 3000 万
            amt20 = float(np.nanmean(close[-20:] * volume[-20:]))
            if not np.isfinite(amt20) or amt20 < LIQUIDITY_FLOOR:
                continue
            liq_pass += 1
            if not _pass_breakout(close, high, volume):
                continue
            passed.append(code)
        passed_count = len(passed)
        log.info(f'FUN d={today_str[-4:]} m={1 if m_ok else 0} A={len(all_codes)} F={len(filtered)} '
                 f'H={len(cand_hist)} q={liq_pass} b={passed_count}')

        if passed:
            # 价量因子
            price_factors = _compute_price_factors(passed, cand_hist)
            # EPS 因子（'eps' 表返回 ann_date<=T 全部历史行，index=code，含 publ_date/end_date）
            try:
                fin_df = get_fundamentals(passed, 'eps',
                                          fields=['eps', 'publ_date', 'end_date'])
                eps_factors = _compute_eps_factors(fin_df)
            except Exception as exc:
                log.warning(f'[{STRATEGY_ID}] get_fundamentals failed: {exc}')
                eps_factors = pd.DataFrame(index=passed)

            factors = pd.concat([price_factors, eps_factors], axis=1)
            factors['composite'] = _composite_score(factors)
            factors = factors.dropna(subset=['composite'])
            if not factors.empty:
                ordered = factors.sort_values('composite', ascending=False)
                candidates = list(ordered.index)[:fill_count]

    # ---------- 4. 执行买入 ----------
    buys = []
    if candidates:
        total_value = context.portfolio.total_value
        cash = context.portfolio.cash
        target_value = total_value * PER_POSITION_WEIGHT
        # 防止拒单：用可用现金约束，并留极小数缓冲
        n = len(candidates)
        max_per_cash = (cash / max(n, 1)) * 0.99 if cash > 0 else 0.0
        per_value = min(target_value, max_per_cash)
        # 同时不突破单票上限 25%
        max_single_value = total_value * MAX_SINGLE_WEIGHT
        per_value = min(per_value, max_single_value)
        if per_value > 1000:
            for code in candidates:
                try:
                    order_target_value(code, per_value)
                    buys.append(code)
                    g.pending_buys.append(code)
                except Exception as exc:
                    log.warning(f'[{STRATEGY_ID}] buy order failed {code}: {exc}')

    # ---------- 5. 审计行 ----------
    cash_ratio = context.portfolio.cash / max(context.portfolio.total_value, 1e-9)
    gross_exposure = _gross_exposure(positions)
    log.info(
        f'QS_REBALANCE_AUDIT rebalance_id={g.rebalance_id} date={today_str} '
        f'selected={len(candidates)} tradable={passed_count} '
        f'sell_submitted={len(sells)} buy_submitted={len(buys)}'
    )
    log.info(
        f'QS_PORTFOLIO_AUDIT rebalance_id={g.rebalance_id} date={today_str} '
        f'positions={len(positions)} cash_ratio={cash_ratio:.4f} gross_exposure={gross_exposure:.4f}'
    )
    g.last_audit_date = today_str


# ---------------- 内部 helpers ----------------

def _load_history(codes, count, today_str):
    """分批加载前复权日线（500/批，vol_regime 同款：大批量瞬态内存竞态会静默返回空）。"""
    if not codes:
        return {}
    codes = sorted([str(c) for c in codes])
    result = {}
    _CHUNK = 500
    for i in range(0, len(codes), _CHUNK):
        grp = codes[i:i + _CHUNK]
        try:
            _h = get_history(count=count, frequency='1d',
                             field=['close', 'high', 'volume'],
                             security_list=grp, fq='pre', include=False,
                             is_dict=True) or {}
        except Exception as exc:
            log.warning(f'[{STRATEGY_ID}] history chunk failed: {exc}')
            _h = {}
        result.update(_h or {})
    return result


def _check_m_factor(today_str):
    """沪深300 趋势：close > MA200 且 MA60_today > MA60_20trade_ago（510300 前复权）。"""
    try:
        hist = get_history(count=MIN_HISTORY_BARS, frequency='1d', field=['close'],
                           security_list=[M_FACTOR_CODE], fq='pre', include=False)
    except Exception as exc:
        log.warning(f'[{STRATEGY_ID}] M factor history failed: {exc}')
        return False
    if hist is None or len(hist) < 220:
        return False
    close = _extract_history_field(hist, 'close')
    if len(close) < 220:
        return False
    ma200 = float(np.nanmean(close[-200:]))
    ma60_today = float(np.nanmean(close[-60:]))
    ma60_20ago = float(np.nanmean(close[-80:-60]))
    if np.isnan(ma200) or np.isnan(ma60_today) or np.isnan(ma60_20ago):
        return False
    return close[-1] > ma200 and ma60_today > ma60_20ago


def _pass_breakout(close, high, volume):
    """放量突破 + 均线粘合必要条件。"""
    if len(close) < BREAKOUT_LOOKBACK + 1 or len(high) < BREAKOUT_LOOKBACK + 1 or len(volume) < 21:
        return False
    # 突破前 60 日高点（不含当日）
    prior_high = float(np.nanmax(high[-(BREAKOUT_LOOKBACK + 1):-1]))
    if np.isnan(prior_high) or prior_high <= 0:
        return False
    if not (close[-1] > prior_high):
        return False
    # 放量
    vol_today = float(volume[-1])
    vol_20_mean = float(np.nanmean(volume[-20:]))
    if vol_20_mean <= 0 or vol_today < vol_20_mean * VOLUME_BREAKOUT_RATIO:
        return False
    # 均线粘合
    ma5 = float(np.nanmean(close[-5:]))
    ma10 = float(np.nanmean(close[-10:]))
    ma20 = float(np.nanmean(close[-20:]))
    ma_min = min(ma5, ma10, ma20)
    ma_max = max(ma5, ma10, ma20)
    if ma_min <= 0:
        return False
    if (ma_max - ma_min) / ma_min > MA_ADHESION_THRESHOLD:
        return False
    return True


def _compute_price_factors(codes, hist):
    """计算 N/S/L/I 四个价量因子（截面百分位）。"""
    rows = []
    for code in codes:
        item = hist.get(code)
        if item is None:
            continue
        close = _extract_history_field(item, 'close')
        high = _extract_history_field(item, 'high')
        volume = _extract_history_field(item, 'volume')
        if len(close) < MIN_HISTORY_BARS or len(volume) < 60:
            continue
        # N: 250 日新高接近度（不含当日）
        nh = float(np.nanmax(high[-(MIN_HISTORY_BARS - 1):-1])) if len(high) >= MIN_HISTORY_BARS else np.nan
        n = close[-1] / nh if nh > 0 else np.nan
        # S: 5/20 量比
        s = (float(np.nanmean(volume[-5:])) / float(np.nanmean(volume[-20:]))) if np.nanmean(volume[-20:]) > 0 else np.nan
        # L: 250 日收益率
        l = (close[-1] / close[-251] - 1.0) if len(close) >= 252 and close[-251] > 0 else np.nan
        # I: 60 日上涨日成交量占比
        ret = np.diff(close[-61:]) if len(close) >= 61 else np.asarray([])
        vol60 = volume[-60:]
        if len(ret) == 60 and len(vol60) == 60:
            up_mask = ret > 0
            up_vol = float(np.nansum(vol60[up_mask]))
            total_vol = float(np.nansum(vol60))
            i = up_vol / total_vol if total_vol > 0 else np.nan
        else:
            i = np.nan
        rows.append((code, n, s, l, i))
    if not rows:
        return pd.DataFrame(columns=['N', 'S', 'L', 'I'])
    df = pd.DataFrame(rows, columns=['code', 'N', 'S', 'L', 'I']).set_index('code')
    # 截面百分位排名
    for col in ['N', 'S', 'L', 'I']:
        df[col] = df[col].rank(pct=True, na_option='keep')
    return df


def _compute_eps_factors(fin_df):
    """计算 C（当季 EPS 加速）与 A（3 年 EPS CAGR）。"""
    if fin_df is None or fin_df.empty:
        return pd.DataFrame(columns=['C', 'A'])
    # 统一列名：get_fundamentals('eps') 返回 index=code、无 'code' 列（wsgm10 同款契约）。
    # 显式命名 index 后 reset_index 产生 'code' 列，避免列名 'index' 与 KeyError。
    df = fin_df.copy()
    if 'code' not in df.columns:
        df.index.name = 'code'
        df = df.reset_index()
    # end_date/publ_date 为 epoch 毫秒（北京时间 00:00）——先转 YYYYMMDD 整数再参与季度拆分
    df['end_date'] = pd.to_numeric(df['end_date'], errors='coerce')
    df['end_date'] = (pd.to_datetime(df['end_date'], unit='ms', utc=True)
                      .dt.tz_convert('Asia/Shanghai').dt.strftime('%Y%m%d')).astype('Int64')
    df['eps'] = pd.to_numeric(df['eps'], errors='coerce')
    df = df.dropna(subset=['code', 'end_date', 'eps'])
    if df.empty:
        return pd.DataFrame(columns=['C', 'A'])

    C = {}
    A = {}
    # publ_date（重述同报告期取最新公告版；缺失按 0）
    if 'publ_date' in df.columns:
        pub = pd.to_numeric(df['publ_date'], errors='coerce').fillna(0)
    else:
        pub = pd.Series([0.0] * len(df))
    df = df.assign(_pub=pub.values)
    for code, group in df.groupby('code'):
        # 同 end_date 多次公告（重述）取 publ_date 最新一行
        best = {}
        for ed, p, eps in zip(group['end_date'].astype(int).tolist(),
                              group['_pub'].astype(float).tolist(),
                              group['eps'].tolist()):
            if ed not in best or p > best[ed][0]:
                best[ed] = (p, eps)
        rows = sorted((ed, eps) for ed, (_p, eps) in best.items())
        if not rows:
            continue
        # 单季 eps
        by_year = {}
        single = {}
        for ed, eps in rows:
            y = ed // 10000
            md = ed % 10000
            by_year.setdefault(y, {})[md] = eps
            if md == 331:
                q_single = eps
            elif md == 630:
                prev = by_year.get(y, {}).get(331, np.nan)
                q_single = eps - prev if not np.isnan(prev) else np.nan
            elif md == 930:
                prev = by_year.get(y, {}).get(630, np.nan)
                q_single = eps - prev if not np.isnan(prev) else np.nan
            elif md == 1231:
                prev = by_year.get(y, {}).get(930, np.nan)
                q_single = eps - prev if not np.isnan(prev) else np.nan
            else:
                q_single = np.nan
            single[ed] = q_single

        latest_ed = rows[-1][0]
        latest_single = single.get(latest_ed, np.nan)
        md = latest_ed % 10000
        y = latest_ed // 10000
        ly_ed = (y - 1) * 10000 + md
        ly_single = single.get(ly_ed, np.nan)
        c_yoy = np.nan
        if latest_single > 0 and ly_single > 0:
            c_yoy = latest_single / ly_single - 1.0
        # 上一季度 yoy
        prev_rows = [r for r in rows if r[0] < latest_ed]
        c_prev_yoy = np.nan
        if prev_rows:
            prev_ed = prev_rows[-1][0]
            prev_single = single.get(prev_ed, np.nan)
            p_md = prev_ed % 10000
            p_y = prev_ed // 10000
            p_ly_ed = (p_y - 1) * 10000 + p_md
            p_ly_single = single.get(p_ly_ed, np.nan)
            if prev_single > 0 and p_ly_single > 0:
                c_prev_yoy = prev_single / p_ly_single - 1.0
        if not np.isnan(c_yoy) and not np.isnan(c_prev_yoy):
            C[code] = c_yoy - c_prev_yoy
        elif not np.isnan(c_yoy):
            C[code] = c_yoy
        else:
            C[code] = np.nan

        # A: 年报 CAGR
        annuals = sorted([(ed, eps) for ed, eps in rows if ed % 10000 == 1231])
        if len(annuals) >= 2 and annuals[-1][1] > 0 and annuals[0][1] > 0:
            n = len(annuals) - 1
            A[code] = (annuals[-1][1] / annuals[0][1]) ** (1.0 / n) - 1.0
        else:
            A[code] = np.nan

    return pd.DataFrame({'C': C, 'A': A})


def _composite_score(factors):
    """六因子加权合成；NaN 按剩余权重归一化。"""
    cols = list(FACTOR_WEIGHTS.keys())
    for col in cols:
        if col not in factors.columns:
            factors[col] = np.nan
        else:
            factors[col] = factors[col].rank(pct=True, na_option='keep')
    total_weight = sum(FACTOR_WEIGHTS.values())
    score = np.zeros(len(factors))
    wsum = np.zeros(len(factors))
    for col, w in FACTOR_WEIGHTS.items():
        valid = factors[col].notna()
        score[valid] += factors[col].values[valid] * w
        wsum[valid] += w
    # 按实际有值权重归一化
    norm = np.where(wsum > 0, score / wsum, np.nan)
    return pd.Series(norm, index=factors.index)


def _gross_exposure(positions):
    """估算总敞口 = 持仓市值 / 总资产（近似）。"""
    total = 0.0
    for pos in positions.values():
        try:
            total += float(pos.value)
        except Exception:
            pass
    return total
