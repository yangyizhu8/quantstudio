"""
bbi_etf_rotation.py - agent-authored canonical QuantStudio/PTrade strategy.

BBI多空均线ETF轮动策略 (design_version 2.2, strategy_id bbi_etf_rotation)

语义（客户 R0/R2.5 已确认）：
- 核心指标：BBI = (SMA3 + SMA6 + SMA12 + SMA24) / 4，使用收盘价前复权(fq='pre')；
- 大盘择时：五大宽基指数(000001.SH/399001.SZ/399006.SZ/000852.SH/000922.SH)
  日涨幅最高者 > 0 才开仓；
- 标的筛选：6支ETF静态白名单 → 过滤上市<20交易日/停牌/退市 →
  BBI/close比值升序 → 选比值最小且<1的标的；
- 买入：大盘择时通过 + 最优标的BBI/close<1 → 全仓买入；
- 清仓：大盘择时不通过 OR 持仓BBI/close≥1 → 全仓清仓；
- 调仓：最优标的变更 → 先卖后买 (close模式同回调资金即时可用)；
- 无候选时空仓持币。

平台纪律：
- 显式 import numpy/pandas；g/log 与公共 API 由平台提供；
- get_history 使用 PTrade count-first 便携签名，fq='pre'，is_dict=True；
- 字段提取经由 _extract_history_field (np.asarray 归一化)；
- 所有回调首行调用幂等 _ensure_runtime_state()；
- 不使用 MyTT/set_backtest/is_trade/get_snapshot/check_limit；
- log.warning() 而非 log.warn()。
"""

import uuid as _uuid

import numpy as np
import pandas as pd

STRATEGY_ID = 'bbi_etf_rotation'
DESIGN_VERSION = '2.2'


def _ensure_runtime_state():
    """Idempotently create every g field used by any callback."""
    if not hasattr(g, 'etf_pool'):
        g.etf_pool = [
            '510050.SS',   # 华夏上证50ETF
            '510300.SS',   # 华泰柏瑞沪深300ETF
            '510500.SS',   # 南方中证500ETF
            '512100.SS',   # 南方中证1000ETF
            '159915.SZ',   # 易方达创业板ETF
            '588000.SS',   # 华夏科创50ETF
        ]
    if not hasattr(g, 'index_list'):
        g.index_list = [
            '000001.SS',   # 上证指数
            '399001.SZ',   # 深证成指
            '399006.SZ',   # 创业板指
            '000852.SS',   # 中证1000
            '000922.SS',   # 中证红利
        ]
    if not hasattr(g, 'bbi_periods'):
        g.bbi_periods = [3, 6, 12, 24]
    if not hasattr(g, 'min_listing_days'):
        g.min_listing_days = 20
    if not hasattr(g, 'lookback'):
        g.lookback = 24
    if not hasattr(g, 'benchmark'):
        g.benchmark = '000300.SS'
    if not hasattr(g, 'exposure_ratio'):
        g.exposure_ratio = 0.99
    if not hasattr(g, 'active_etfs'):
        g.active_etfs = list(g.etf_pool)


# ---------------------------------------------------------------------------
# 便携工具函数
# ---------------------------------------------------------------------------

def _extract_history_field(history_item, field, dtype=float):
    """从 get_history(is_dict=True) 返回的 item 中提取字段并归一化为 np.ndarray。

    处理 DataFrame / structured array / recarray 等多种返回形态，
    兼容 PTrade 运行时的异构数据类型。
    """
    if history_item is None:
        return np.array([], dtype=dtype)
    try:
        raw = history_item[field]
    except (KeyError, TypeError, IndexError):
        return np.array([], dtype=dtype)
    if raw is None:
        return np.array([], dtype=dtype)
    # 归一化：处理 Series.values 或其他可取值对象
    if hasattr(raw, 'values'):
        raw = raw.values
    arr = np.asarray(raw, dtype=dtype)
    # 剔除 NaN
    arr = arr[np.isfinite(arr)]
    return arr


def _sma(arr, n):
    """简单移动平均：取 arr 最后 n 个元素的均值。长度不足返回 NaN。"""
    if len(arr) < n:
        return np.nan
    return float(np.mean(arr[-n:]))


def _bbi(close_arr, periods):
    """计算 BBI 多空均线。

    BBI = (SMA(close, p1) + SMA(close, p2) + ...) / len(periods)
    任一周期 SMA 为 NaN 则返回 NaN。
    """
    mas = [_sma(close_arr, p) for p in periods]
    if any(np.isnan(m) for m in mas):
        return np.nan
    return float(np.mean(mas))


def _pct_chg(close_arr):
    """从收盘价序列计算最新日涨跌幅。需至少2个有效值。"""
    if len(close_arr) < 2:
        return np.nan
    prev = close_arr[-2]
    curr = close_arr[-1]
    if prev <= 0:
        return np.nan
    return (curr - prev) / prev


def _get_close(code, count):
    """获取单个标的的前复权收盘价序列 (np.array, 升序)。

    失败或数据不足返回 None。
    """
    try:
        hist = get_history(
            count, frequency='1d', field=['close'],
            security_list=code, fq='pre', include=False, is_dict=True
        )
    except Exception as e:
        log.warning('get_history failed for %s: %s', code, e)
        return None
    if not isinstance(hist, dict) or code not in hist:
        return None
    arr = _extract_history_field(hist[code], 'close')
    if len(arr) == 0:
        return None
    return arr


def _get_multi_close(codes, count):
    """批量获取多个标的的前复权收盘价序列。

    返回 dict: {code: np.array}，失败的 code 不在结果中。
    注意：PTrade 不提供 get_history_batch，故逐只调用 get_history。
    """
    result = {}
    for code in codes:
        arr = _get_close(code, count)
        if arr is not None:
            result[code] = arr
    return result


# ---------------------------------------------------------------------------
# 生命周期回调
# ---------------------------------------------------------------------------

def initialize(context):
    """配置基准、成本、滑点，注册 run_daily 调度。"""
    _ensure_runtime_state()
    set_benchmark(g.benchmark)
    set_commission(commission_ratio=0.0003, min_commission=5.0, type='stock')
    set_slippage(slippage=0.0001)
    run_daily(context, rebalance, time='15:00')
    log.info('[bbi_etf_rotation] initialize: benchmark=%s, commission=0.03%%, slippage=0.01%%',
             g.benchmark)


def before_trading_start(context, data):
    """PIT 候选池过滤：上市≥20交易日 + 停牌/退市剔除。"""
    _ensure_runtime_state()

    # 获取当前回测日期 (YYYYmmdd 格式，用于 get_trade_days / filter_stock_by_status)
    try:
        today_str = context.current_dt.strftime('%Y%m%d')
    except Exception:
        today_str = None

    active = []
    for etf in g.etf_pool:
        # ---- 上市日期检查 ----
        try:
            info = get_stock_info(etf, field=['listed_date'])
        except Exception:
            log.warning('[bbi_etf_rotation] get_stock_info failed for %s, skipping', etf)
            continue

        listed_date = None
        if isinstance(info, dict) and etf in info:
            listed_date = info[etf].get('listed_date')

        if listed_date is None:
            log.warning('[bbi_etf_rotation] no listed_date for %s, skipping', etf)
            continue

        # 计算上市交易日数
        try:
            # listed_date 为 'YYYY-MM-DD' 格式，需转换为 'YYYYmmdd'
            listed_yyyymmdd = listed_date.replace('-', '')
            trade_days = get_trade_days(start_date=listed_yyyymmdd,
                                        end_date=today_str) if today_str else []
        except Exception:
            log.warning('[bbi_etf_rotation] get_trade_days failed for %s, skipping', etf)
            continue

        if not hasattr(trade_days, '__len__'):
            trade_days = list(trade_days) if trade_days is not None else []

        if len(trade_days) < g.min_listing_days:
            continue

        active.append(etf)

    # ---- 停牌 / 退市过滤 ----
    if active and today_str:
        try:
            active = filter_stock_by_status(active, filter_type=['HALT'],
                                            query_date=today_str)
        except Exception:
            log.warning('[bbi_etf_rotation] filter_stock_by_status(HALT) failed, keeping all')

    if active and today_str:
        try:
            active = filter_stock_by_status(active, filter_type=['DELISTING_SORTING'],
                                            query_date=today_str)
        except Exception:
            log.warning('[bbi_etf_rotation] filter_stock_by_status(DELISTING_SORTING) failed, keeping all')

    g.active_etfs = active
    log.info('[bbi_etf_rotation] before_trading_start: %d ETFs active after filters',
             len(g.active_etfs))


def _log_portfolio_audit(context, rebalance_id, today_str):
    """输出 QS_PORTFOLIO_AUDIT 行，用于所有代码路径。"""
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
# 核心调仓逻辑 (run_daily 15:00)
# ---------------------------------------------------------------------------

def rebalance(context):
    """每日调仓：大盘择时 → BBI筛选 → 买卖执行。"""
    _ensure_runtime_state()
    rebalance_id = str(_uuid.uuid4())
    today_str = None
    try:
        today_str = context.current_dt.strftime('%Y-%m-%d')
    except Exception:
        today_str = 'unknown'

    # ---- 1. 大盘择时 ----
    index_closes = _get_multi_close(g.index_list, count=g.lookback + 1)
    max_pct = -999.0
    for idx in g.index_list:
        if idx not in index_closes:
            continue
        close_arr = index_closes[idx]
        pct = _pct_chg(close_arr)
        if not np.isnan(pct) and pct > max_pct:
            max_pct = pct

    market_ok = max_pct > 0.0
    log.info('[bbi_etf_rotation] market_timing: max_pct_chg=%.4f, market_ok=%s',
             max_pct, market_ok)

    # ---- 2. 获取当前持仓 ----
    positions = get_positions()
    current_holding = None
    if positions:
        for code in positions:
            pos = get_position(code)
            if pos is not None and getattr(pos, 'amount', 0) > 0:
                current_holding = code
                break

    # ---- 3. 大盘不通过 → 清仓 ----
    if not market_ok:
        if current_holding is not None:
            # 检查持仓是否停牌（停牌则无法卖出，保持持仓）
            try:
                halt_status = get_stock_status([current_holding], query_type='HALT',
                                               query_date=today_str.replace('-', ''))
                if halt_status and halt_status.get(current_holding):
                    log.info('[bbi_etf_rotation] %s is HALT, cannot liquidate', current_holding)
                else:
                    order_target_value(current_holding, 0)
                    log.info('[bbi_etf_rotation] market CLOSED: liquidated %s', current_holding)
            except Exception:
                order_target_value(current_holding, 0)
                log.info('[bbi_etf_rotation] market CLOSED: liquidated %s', current_holding)
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=0 tradable=%d '
                 'sell_submitted=%d buy_submitted=%d',
                 rebalance_id, today_str, 0,
                 1 if current_holding is not None else 0, 0)
        _log_portfolio_audit(context, rebalance_id, today_str)
        return

    # ---- 4. BBI 标的筛选 ----
    active = g.active_etfs if hasattr(g, 'active_etfs') and g.active_etfs else list(g.etf_pool)
    tradable_count = len(active)

    if not active:
        # 无有效候选 → 清仓
        if current_holding is not None:
            order_target_value(current_holding, 0)
            log.info('[bbi_etf_rotation] no active ETFs: liquidated %s', current_holding)
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=0 tradable=%d '
                 'sell_submitted=%d buy_submitted=%d',
                 rebalance_id, today_str, 0,
                 1 if current_holding is not None else 0, 0)
        _log_portfolio_audit(context, rebalance_id, today_str)
        return

    # 获取各 ETF 收盘价并计算 BBI/close 比值
    etf_closes = _get_multi_close(active, count=g.lookback + 1)
    candidates = []  # (ratio, code, close_price)
    for etf in active:
        if etf not in etf_closes:
            continue
        close_arr = etf_closes[etf]
        if len(close_arr) < g.lookback:
            continue
        bbi_val = _bbi(close_arr, g.bbi_periods)
        if np.isnan(bbi_val) or bbi_val <= 0:
            continue
        curr_close = close_arr[-1]
        if curr_close <= 0:
            continue
        ratio = bbi_val / curr_close
        candidates.append((ratio, etf, curr_close))

    # 按 ratio 升序排列
    candidates.sort(key=lambda x: x[0])

    # 过滤 ratio < 1 并选最优
    best = None
    for c in candidates:
        if c[0] < 1.0:
            best = c
            break

    # ---- 5. 无合格标的 → 清仓 ----
    if best is None:
        if current_holding is not None:
            order_target_value(current_holding, 0)
            log.info('[bbi_etf_rotation] no BBI candidate (all ratio>=1): liquidated %s',
                     current_holding)
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=0 tradable=%d '
                 'sell_submitted=%d buy_submitted=%d',
                 rebalance_id, today_str, tradable_count,
                 1 if current_holding is not None else 0, 0)
        _log_portfolio_audit(context, rebalance_id, today_str)
        return

    best_ratio, best_code, best_close = best
    log.info('[bbi_etf_rotation] best candidate: %s, BBI/close=%.4f',
             best_code, best_ratio)

    # ---- 6. 调仓执行 ----
    sell_submitted = 0
    buy_submitted = 0

    if current_holding == best_code:
        # 无需调仓，继续持有
        log.info('[bbi_etf_rotation] holding unchanged: %s', best_code)
    else:
        # 需要调仓：先卖后买
        if current_holding is not None:
            order_target_value(current_holding, 0)
            sell_submitted = 1
            log.info('[bbi_etf_rotation] sold %s', current_holding)

        # 全仓买入最优标的
        total_value = context.portfolio.total_value
        target_value = total_value * g.exposure_ratio
        order_target_value(best_code, target_value)
        buy_submitted = 1
        log.info('[bbi_etf_rotation] bought %s, target_value=%.2f', best_code, target_value)

    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=1 tradable=%d '
             'sell_submitted=%d buy_submitted=%d',
             rebalance_id, today_str, tradable_count,
             sell_submitted, buy_submitted)

    # ---- 7. 组合审计 ----
    _log_portfolio_audit(context, rebalance_id, today_str)
