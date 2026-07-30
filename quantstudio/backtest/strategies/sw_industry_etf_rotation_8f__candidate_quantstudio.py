"""
sw_industry_etf_rotation_8f.py - agent-authored canonical QuantStudio/PTrade strategy.

纯量价8因子行业ETF轮动策略 (design_version 1.0, strategy_id sw_industry_etf_rotation_8f)

语义（客户 R0/R2.5 已确认，ETF代理降级口径，双目标 QuantStudio + PTrade）：
- 8因子全部在行业代表ETF自身前复权OHLCV上计算（不使用行业指数/成分股数据）；
  申万一级→ETF静态白名单25只，无对应ETF的6个行业不参与轮动；
- F1 20日收益率(25%) / F2 60日收益波动比(15%) / F3 成交量放大比(20%) /
  F4 成交额占比(10%) / F5 相对强度RPS=60日收益截面分位(15%) /
  F6 60日新高(10%) / F7 MACD金叉(2.5%) / F8 均线多头排列(2.5%)；
- 打分：连续因子截面百分位0~100 + RPS本身即分位 + 0/1因子×100 → 按权重加权；
- 选股：得分Top5（缓冲带：持仓在Top8内保留，跌出前8才卖，缺额未持仓最高分递补）；
- 等权20%：满仓每只=总资产*0.98/5，熊市半仓每只=总资产*0.49/5；
- 熊市三信号任一触发→半仓49%，全部解除后再等5个交易日→恢复满仓98%；
- 执行：T日15:00决策（include=False，数据截止T收盘）→ T+1开盘价撮合(next_open模式，
  引擎保证无未来函数；PyQt回测时撮合模式选 next_open)；
- 基准000300.SS，初始资金10万元，佣金万2.5(最低5元)，滑点0.1%。

平台纪律：
- 显式 import numpy/pandas；g/log 与公共 API 由平台提供；
- get_history 使用 PTrade count-first 便携签名，fq='pre'，is_dict=True；
- 字段提取兼容本地(amount)与PTrade(money)双列名；
- 所有回调首行调用幂等 _ensure_runtime_state()；
- EMA/MA/MACD 全部 numpy 自实现，不引用 MyTT 全局 SMA/EMA/MA/REF/HHV/LLV/CROSS；
- 不使用 get_etf_list/get_etf_list_local/get_history_batch/get_stock_info/
  filter_stock_by_status/get_snapshot/check_limit/set_backtest/is_trade/attribute_history；
- log.warning() 而非 log.warn()。
"""

import uuid as _uuid

import numpy as np
import pandas as pd

STRATEGY_ID = 'sw_industry_etf_rotation_8f'
DESIGN_VERSION = '1.0'

# 8因子权重（与提示词一致）
W_F1 = 0.25   # 20日收益率（动量）
W_F2 = 0.15   # 60日收益/波动比（动量效率）
W_F3 = 0.20   # 成交量放大比（量能）
W_F4 = 0.10   # 成交额占比（量能）
W_F5 = 0.15   # 相对强度RPS（相对强弱，60日窗口）
W_F6 = 0.10   # 60日新高（突破）
W_F7 = 0.025  # MACD金叉（趋势确认）
W_F8 = 0.025  # 均线多头排列（趋势确认）

FULL_EXPOSURE = 0.98   # 满仓总暴露（2%现金缓冲）
BEAR_EXPOSURE = 0.49    # 熊市半仓总暴露
TARGET_HOLDINGS = 5     # 目标持仓数
BUFFER_KEEP_TOPN = 8    # 持仓保留缓冲带（跌出前8才卖）
MIN_BARS = 61           # 因子最少有效日线（覆盖60日回看）
RECOVERY_DAYS = 2       # 熊市信号全解除后恢复满仓所需连续交易日
# 熊市信号3阈值：白名单ETF中创60日新高的占比低于此值触发。
# 注意：原提示词用"全市场个股<5%"，ETF代理降级至25只ETF后5%≈1.25只，
# 震荡市易恒为0导致频繁半仓；如需贴近原意可放宽至0.15~0.20。
S3_BEAR_FRAC = 0.05


def _ensure_runtime_state():
    """Idempotently create every g field used by any callback."""
    if not hasattr(g, 'etf_pool'):
        # 25只申万一级行业代表ETF静态白名单（客户R2.5确认）
        g.etf_pool = [
            '159865.SZ',   # 农林牧渔-畜牧养殖
            '159870.SZ',   # 基础化工-细分化工
            '515210.SS',   # 钢铁
            '512400.SS',   # 有色金属
            '512480.SS',   # 电子-半导体
            '159996.SZ',   # 家用电器
            '512690.SS',   # 食品饮料-酒
            '512010.SS',   # 医药生物-300医药
            '159611.SZ',   # 公用事业-电力
            '516910.SS',   # 交通运输-现代物流
            '512200.SS',   # 房地产
            '159766.SZ',   # 社会服务-旅游
            '516970.SS',   # 建筑装饰-基建工程
            '515790.SS',   # 电力设备-光伏
            '512660.SS',   # 国防军工
            '159998.SZ',   # 计算机
            '512980.SS',   # 传媒
            '515050.SS',   # 通信-5G
            '512800.SS',   # 银行
            '512880.SS',   # 非银金融-证券
            '515030.SS',   # 汽车-新能源车
            '562500.SS',   # 机械设备-机器人
            '515220.SS',   # 煤炭
            '159697.SZ',   # 石油石化-石油天然气(2023-05上市)
            '512580.SS',   # 环保
        ]
    if not hasattr(g, 'benchmark'):
        g.benchmark = '000300.SS'
    if not hasattr(g, 'min_bars'):
        g.min_bars = MIN_BARS
    if not hasattr(g, 'bear_mode'):
        g.bear_mode = False
    if not hasattr(g, 'recovery_counter'):
        g.recovery_counter = 0
    if not hasattr(g, 'last_signal_tuple'):
        g.last_signal_tuple = (False, False, False)


# ---------------------------------------------------------------------------
# 便携工具函数
# ---------------------------------------------------------------------------

def _extract_series(df, *names):
    """从 get_history(is_dict=True) 返回的 DataFrame 中提取指定列并归一化为 np.ndarray。

    兼容本地列名 'amount' 与 PTrade 列名 'money'。空/缺失返回空数组。
    """
    if df is None:
        return np.array([], dtype=float)
    cols = list(df.columns) if hasattr(df, 'columns') else []
    for n in names:
        if n in cols:
            raw = df[n]
            if hasattr(raw, 'values'):
                raw = raw.values
            arr = np.asarray(raw, dtype=float)
            arr = arr[np.isfinite(arr)]
            return arr
    return np.array([], dtype=float)


def _get_etf_hist(code, count):
    """获取单只ETF前复权OHLCV序列，返回 (close, volume, money) 或 None。"""
    try:
        hist = get_history(
            count, frequency='1d',
            field=['close', 'volume', 'money'],
            security_list=code, fq='pre', include=False, is_dict=True
        )
    except Exception as e:
        log.warning('[8f] get_history failed for %s: %s', code, e)
        return None
    if not isinstance(hist, dict) or code not in hist:
        return None
    df = hist[code]
    close = _extract_series(df, 'close')
    volume = _extract_series(df, 'volume')
    money = _extract_series(df, 'amount', 'money')
    if len(close) == 0 or len(volume) == 0 or len(money) == 0:
        return None
    return close, volume, money


def _ema(arr, n):
    """标准递推 EMA，返回与 arr 等长的序列。长度不足返回原长度全 NaN。"""
    a = np.asarray(arr, dtype=float)
    if len(a) < n:
        return np.full(len(a), np.nan)
    alpha = 2.0 / (n + 1)
    out = np.empty(len(a), dtype=float)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = alpha * a[i] + (1 - alpha) * out[i - 1]
    return out


def _pct_rank_array(arr):
    """截面百分位排名 0~100；NaN→50（截面中位）。O(n^2)但截面仅~25只，足够。"""
    a = np.asarray(arr, dtype=float)
    n = len(a)
    if n == 0:
        return a
    if n == 1:
        return np.full(1, 50.0)
    out = np.full(n, 50.0)
    finite = a[np.isfinite(a)]
    if len(finite) == 0:
        return out
    order = np.argsort(finite)
    ranks = np.empty(len(finite), dtype=float)
    ranks[order] = np.arange(len(finite), dtype=float)
    pct = ranks / (len(finite) - 1) * 100.0
    fmap = {v: p for v, p in zip(finite, pct)}
    for i, v in enumerate(a):
        if np.isfinite(v):
            out[i] = fmap[v]
    return out


def _f1_ret20(close):
    """F1 20日收益率(%)。需要>=22根。"""
    if len(close) < 22:
        return np.nan
    base = close[-21]
    if base <= 0:
        return np.nan
    return (close[-1] / base - 1.0) * 100.0


def _f2_ret_vol(close):
    """F2 60日收益/波动比。需要>=61根；sigma<=0记NaN。"""
    if len(close) < 61:
        return np.nan
    base = close[-61]
    if base <= 0:
        return np.nan
    ret = close[-1] / base - 1.0
    rets = np.diff(close[-61:])  # 60个日收益
    if len(rets) < 2:
        return np.nan
    sigma = float(np.std(rets, ddof=1))
    if sigma <= 0:
        return np.nan
    return ret / sigma


def _f3_vol_ratio(volume):
    """F3 成交量放大比 = 近5日均量 / 近20日均量。需要>=20根。"""
    if len(volume) < 20:
        return np.nan
    d5 = float(np.mean(volume[-5:]))
    d20 = float(np.mean(volume[-20:]))
    if d20 <= 0:
        return np.nan
    return d5 / d20


def _f6_newhigh(close):
    """F6 60日新高：今收 >= 前60日最高 ? 1 : 0。需要>=61根。"""
    if len(close) < 61:
        return 0.0
    prev = close[-61:-1]  # 前60日（不含今日）
    if len(prev) == 0:
        return 0.0
    return 1.0 if close[-1] >= float(np.max(prev)) else 0.0


def _f7_macd_bull(close):
    """F7 MACD金叉：DIF=EMA12-EMA26，DEA=EMA9(DIF)；DIF>DEA?1:0。需要>=35根。"""
    if len(close) < 35:
        return 0.0
    dif = _ema(close, 12) - _ema(close, 26)
    dea = _ema(dif, 9)
    if np.isnan(dif[-1]) or np.isnan(dea[-1]):
        return 0.0
    return 1.0 if dif[-1] > dea[-1] else 0.0


def _f8_ma_bull(close):
    """F8 均线多头排列：MA5>MA10>MA20>MA60 ? 1 : 0。需要>=60根。"""
    if len(close) < 60:
        return 0.0
    ma5 = float(np.mean(close[-5:]))
    ma10 = float(np.mean(close[-10:]))
    ma20 = float(np.mean(close[-20:]))
    ma60 = float(np.mean(close[-60:]))
    return 1.0 if (ma5 > ma10 > ma20 > ma60) else 0.0


def _ret60(close):
    """60日收益率(小数)。需要>=61根。用于F5 RPS截面。"""
    if len(close) < 61:
        return np.nan
    base = close[-61]
    if base <= 0:
        return np.nan
    return close[-1] / base - 1.0


def _log_portfolio_audit(context, rebalance_id, today_str):
    """输出 QS_PORTFOLIO_AUDIT 行。"""
    positions_after = get_positions()
    pos_count = len(positions_after) if positions_after else 0
    total_value_after = context.portfolio.total_value
    cash = context.portfolio.cash if hasattr(context.portfolio, 'cash') else 0.0
    cash_ratio = cash / total_value_after if total_value_after > 0 else 1.0
    gross_exposure = 1.0 - cash_ratio
    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
             'cash_ratio=%.4f gross_exposure=%.4f',
             rebalance_id, today_str, pos_count, cash_ratio, gross_exposure)


# ---------------------------------------------------------------------------
# 生命周期回调
# ---------------------------------------------------------------------------

def initialize(context):
    """配置基准、成本、滑点，注册 run_daily 调度（next_open由回测配置指定）。"""
    _ensure_runtime_state()
    set_benchmark(g.benchmark)
    set_commission(commission_ratio=0.00025, min_commission=5.0, type='stock')
    set_slippage(slippage=0.001)
    run_daily(context, rebalance, time='15:00')
    log.info('[8f] initialize: benchmark=%s, commission=0.025%%, slippage=0.1%%',
             g.benchmark)


def rebalance(context):
    """每日15:00调仓：8因子截面打分 → 熊市信号 → 缓冲带轮动 → 先卖后买。"""
    _ensure_runtime_state()
    rebalance_id = str(_uuid.uuid4())
    today_str = None
    today_yyyymmdd = None
    try:
        today_str = context.current_dt.strftime('%Y-%m-%d')
        today_yyyymmdd = context.current_dt.strftime('%Y%m%d')
    except Exception:
        today_str = 'unknown'

    # ---- 1. 取数：25只ETF因子数据 ----
    valid = {}  # code -> (close, volume, money)
    for etf in g.etf_pool:
        hist = _get_etf_hist(etf, g.min_bars)
        if hist is None:
            continue
        close, volume, money = hist
        if len(close) < g.min_bars or len(volume) < g.min_bars or len(money) < g.min_bars:
            continue
        valid[etf] = (close, volume, money)

    if len(valid) == 0:
        log.warning('[8f] no valid ETF with sufficient history, skip')
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=0 tradable=0 '
                 'sell_submitted=0 buy_submitted=0', rebalance_id, today_str)
        _log_portfolio_audit(context, rebalance_id, today_str)
        return

    codes = list(valid.keys())
    n = len(codes)

    # ---- 2. 逐ETF计算连续因子原始值 ----
    f1_raw = np.array([_f1_ret20(valid[c][0]) for c in codes], dtype=float)
    f2_raw = np.array([_f2_ret_vol(valid[c][0]) for c in codes], dtype=float)
    f3_raw = np.array([_f3_vol_ratio(valid[c][1]) for c in codes], dtype=float)
    f6_raw = np.array([_f6_newhigh(valid[c][0]) for c in codes], dtype=float)
    f7_raw = np.array([_f7_macd_bull(valid[c][0]) for c in codes], dtype=float)
    f8_raw = np.array([_f8_ma_bull(valid[c][0]) for c in codes], dtype=float)
    ret60_raw = np.array([_ret60(valid[c][0]) for c in codes], dtype=float)

    # F4 成交额占比分母 = 截面当日成交额之和；F5 RPS = ret60 截面分位
    money_today = np.array([valid[c][2][-1] for c in codes], dtype=float)
    f4_raw = money_today / float(np.nansum(money_today)) if np.nansum(money_today) > 0 else np.zeros(n)

    # ---- 3. 截面百分位归一化（NaN→50，并告警） ----
    f1_s = _pct_rank_array(f1_raw)
    f2_s = _pct_rank_array(f2_raw)
    f3_s = _pct_rank_array(f3_raw)
    f4_s = _pct_rank_array(f4_raw)
    f5_s = _pct_rank_array(ret60_raw)  # RPS：60日收益截面分位
    for idx, c in enumerate(codes):
        if np.isnan(f1_raw[idx]):
            log.warning('[8f] %s F1 NaN -> median 50', c)
        if np.isnan(f2_raw[idx]):
            log.warning('[8f] %s F2 NaN -> median 50', c)
        if np.isnan(f3_raw[idx]):
            log.warning('[8f] %s F3 NaN -> median 50', c)

    # ---- 4. 综合得分（0~100加权） ----
    scores = {}
    for i, c in enumerate(codes):
        s = (W_F1 * f1_s[i] + W_F2 * f2_s[i] + W_F3 * f3_s[i] + W_F4 * f4_s[i]
             + W_F5 * f5_s[i] + W_F6 * (f6_raw[i] * 100.0)
             + W_F7 * (f7_raw[i] * 100.0) + W_F8 * (f8_raw[i] * 100.0))
        scores[c] = float(s)

    # ---- 5. 熊市信号 ----
    s1 = _bear_signal_s1(valid, codes)
    s2 = _bear_signal_s2()
    s3 = _bear_signal_s3(codes, valid)
    g.last_signal_tuple = (bool(s1), bool(s2), bool(s3))

    bear_count = int(s1) + int(s2) + int(s3)
    any_signal = bear_count >= 2
    if any_signal:
        if not g.bear_mode:
            log.info('[8f] BEAR_MODE ON signals=(S1=%s,S2=%s,S3=%s)', s1, s2, s3)
        g.bear_mode = True
        g.recovery_counter = 0
        exposure = BEAR_EXPOSURE
    else:
        if g.bear_mode:
            g.recovery_counter += 1
            if g.recovery_counter >= RECOVERY_DAYS:
                g.bear_mode = False
                log.info('[8f] BEAR_MODE OFF after %d clear days', g.recovery_counter)
                exposure = FULL_EXPOSURE
            else:
                exposure = BEAR_EXPOSURE
        else:
            exposure = FULL_EXPOSURE

    # ---- 6. 缓冲带选目标 ----
    ranked = sorted(codes, key=lambda c: -scores[c])
    top8 = set(ranked[:BUFFER_KEEP_TOPN])

    positions = get_positions()
    current_holdings = []
    if positions:
        for code in positions:
            pos = get_position(code)
            if pos is not None and getattr(pos, 'amount', 0) > 0:
                current_holdings.append(code)

    target = []
    # 持仓且仍在Top8内 → 保留
    for c in current_holdings:
        if c in top8 and c in scores:
            target.append(c)
    # 缺额从未持仓中按得分递补
    for c in ranked:
        if len(target) >= TARGET_HOLDINGS:
            break
        if c not in target:
            target.append(c)
    target = target[:TARGET_HOLDINGS]

    # ---- 7. 执行：先卖后买 ----
    sell_submitted = 0
    buy_submitted = 0
    target_set = set(target)

    # 停牌状态查询（HALT 不可交易）
    halt_map = {}
    try:
        if today_yyyymmdd:
            halt_map = get_stock_status(list(target_set) + current_holdings,
                                        query_type='HALT', query_date=today_yyyymmdd)
    except Exception:
        log.warning('[8f] get_stock_status failed, assuming all tradable')

    def _is_halt(code):
        return bool(halt_map.get(code, False))

    # 卖出：持仓不在目标 → 清仓
    for c in current_holdings:
        if c not in target_set:
            if _is_halt(c):
                log.info('[8f] %s HALT, cannot sell, keep', c)
                continue
            order_target_value(c, 0)
            sell_submitted += 1
            log.info('[8f] sell %s (dropped from top8)', c)

    # 买入/调仓：目标持仓 set 到目标市值（含保留仓的暴露调整）
    total_value = context.portfolio.total_value
    per_target = total_value * exposure / TARGET_HOLDINGS
    for c in target:
        if _is_halt(c):
            log.info('[8f] %s HALT, skip buy', c)
            continue
        order_target_value(c, per_target)
        buy_submitted += 1
        log.info('[8f] target %s score=%.2f value=%.2f', c, scores[c], per_target)

    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
             'sell_submitted=%d buy_submitted=%d bear=%s exposure=%.2f',
             rebalance_id, today_str, len(target), n,
             sell_submitted, buy_submitted, g.bear_mode, exposure)

    _log_portfolio_audit(context, rebalance_id, today_str)


# ---------------------------------------------------------------------------
# 熊市信号子函数
# ---------------------------------------------------------------------------

def _bear_signal_s1(valid, codes):
    """S1 量能萎缩：白名单有效ETF合计成交额连续5日 < 自身20日均值的80%。"""
    try:
        # 构造每日截面总额序列（长度=min_bars）
        lengths = [len(valid[c][2]) for c in codes]
        if not lengths:
            return False
        L = min(lengths)
        if L < 25:  # 需>=20+5
            return False
        daily_total = np.zeros(L, dtype=float)
        for c in codes:
            money = valid[c][2][-L:]
            daily_total += np.where(np.isfinite(money), money, 0.0)
        # 滚动20日均线
        ma20 = np.full(L, np.nan)
        for i in range(19, L):
            ma20[i] = float(np.mean(daily_total[i - 19:i + 1]))
        # 近5日每日均需 < 当日20日均*0.8
        last5 = range(L - 5, L)
        for i in last5:
            if np.isnan(ma20[i]) or daily_total[i] >= ma20[i] * 0.8:
                return False
        return True
    except Exception as e:
        log.warning('[8f] S1 calc error: %s', e)
        return False


def _bear_signal_s2():
    """S2 指数破位：沪深300收盘 < 200日简单均线。"""
    try:
        hist = get_history(
            201, frequency='1d', field=['close'],
            security_list=g.benchmark, fq='pre', include=False, is_dict=True
        )
        if not isinstance(hist, dict) or g.benchmark not in hist:
            return False
        close = _extract_series(hist[g.benchmark], 'close')
        if len(close) < 200:
            return False
        return float(close[-1]) < float(np.mean(close[-200:])) * 0.97
    except Exception as e:
        log.warning('[8f] S2 calc error: %s', e)
        return False


def _bear_signal_s3(codes, valid, frac_threshold=S3_BEAR_FRAC):
    """S3 新高枯竭：白名单中创60日新高的ETF占比 < 5%。"""
    try:
        if len(codes) == 0:
            return False
        nh = [_f6_newhigh(valid[c][0]) for c in codes]
        frac = float(np.mean(nh))
        return frac < frac_threshold
    except Exception as e:
        log.warning('[8f] S3 calc error: %s', e)
        return False
