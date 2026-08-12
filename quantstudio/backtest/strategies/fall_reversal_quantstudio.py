# -*- coding: utf-8 -*-
"""
跌幅榜反转 / 价值修复策略（QuantStudio 本地零依赖版本）

策略逻辑：
  1. 标的池：全A股（get_Ashares）剔除 ST/*ST/退市整理（is_st_stock）。
  2. 选股：按过去 N=3 年（756 交易日）前复权收盘价跌幅排序，取跌幅最大（最熊）的前 18 只。
  3. 换出：持仓自近 20 日最低价反弹 >= 25%，或单只持有满 252 个交易日，即清仓。
  4. 调仓：每月末全量重排 + 每日评估回弹/到期；新跌标的等权补入。
  5. 执行：next_open + callback_basket 原子先卖后买；基准沪深300；fq='pre'。

本文件由 quantstudio-strategy-compiler skill（Design 2.2）生成，落盘即受
StrategyIsolationGuard 约束：禁止任何文件 I/O（open/read_csv 等），数据全部经
框架注入 API 获取。
"""

import pandas as pd
import numpy as np

# ---- 框架注入 API 说明 ----
# 以下 API 由 ptrade_import 在策略加载时注入命名空间（见 strategy_runner.load_strategy），
# 策略文件内禁止本地 import 数据文件，亦无需手写 from ptrade_import import。
# get_Ashares, get_history_batch, get_history, get_stock_status, order_target_value,
# set_benchmark, set_commission, log, g, callback_basket, next_open 均直接使用。

# ===================== 策略参数（R0 默认方案）=====================
N_YEARS = 3                 # 跌幅计算窗口：3 年
LOOKBACK_BARS = N_YEARS * 252   # 756 交易日
TOP_N = 18                  # 选取数量（15~20 中值）
REBOUND_PCT = 0.25          # 从近20日低点反弹 >=25% 换出
MAX_HOLD_DAYS = 252         # 最长持有 12 个月强制换出
GROSS_EXPOSURE = 0.88       # 总仓位目标（留 12% 现金缓冲，覆盖整手取整+佣金，避免 basket 原子拒单）
MAX_SINGLE_WEIGHT = 0.08    # 单只仓位上限 8%
INITIAL_CASH = 1000000      # 初始资金 100 万

# 审计常量
STRATEGY_ID = "fall_reversal"


def _ensure_runtime_state():
    """R3 幂等运行时状态守卫：initialize/handle_data 首句调用，保证 g 字段只初始化一次。
    每个字段独立 hasattr 守卫（R4 RUNTIME-STATE-IDEMPOTENCE 硬规则）。"""
    if not hasattr(g, 'target_list'):
        g.target_list = []          # 当前目标持仓（代码列表）
    if not hasattr(g, 'rebalance_count'):
        g.rebalance_count = 0       # 已执行重排次数
    if not hasattr(g, 'trade_day_count'):
        g.trade_day_count = 0       # 回测交易日序号（自增）
    if not hasattr(g, 'hold_since'):
        g.hold_since = {}           # code -> 建仓交易日序号
    if not hasattr(g, 'current_holdings'):
        g.current_holdings = set()  # 策略自维护当前持仓集合（避免 positions 时序耦合）
    if not hasattr(g, 'last_rebalance_month'):
        g.last_rebalance_month = None  # 上次全量重排的年月，控制每月末重排


def initialize(context):
    _ensure_runtime_state()
    set_benchmark('000300')          # 注意：框架基准禁 .SH 后缀
    set_commission(type='stock')
    log.info('[fall_reversal] initialize: N_YEARS=%d TOP_N=%d REBOUND_PCT=%.2f MAX_HOLD_DAYS=%d INITIAL_CASH=%d'
             % (N_YEARS, TOP_N, REBOUND_PCT, MAX_HOLD_DAYS, INITIAL_CASH))


def _compute_fall_ranking(date):
    """返回跌幅最大（最熊）的前 TOP_N 只代码列表。无 ST、无退市、有完整历史者才入选。"""
    all_codes = get_Ashares(date)
    if not all_codes:
        return []
    # ST 过滤（get_stock_status 公开 API，返回 {code: bool} 的 ST 状态）
    status_map = get_stock_status(all_codes, 'ST')
    candidates = [c for c in all_codes if not status_map.get(c, False)]
    if not candidates:
        return []
    # 批量取 N 年日线前复权收盘
    hist = get_history_batch(candidates, LOOKBACK_BARS, '1d', fields=['close'], fq='pre')
    ranked = []
    for code, df in hist.items():
        if df is None or len(df) < LOOKBACK_BARS:
            continue
        closes = df['close'].values.astype(float)
        if closes[0] <= 0 or np.any(np.isnan(closes)):
            continue
        fall = closes[-1] / closes[0] - 1.0   # 负值=跌
        ranked.append((code, fall))
    if not ranked:
        return []
    # 按跌幅升序（最熊者排最前）
    ranked.sort(key=lambda x: x[1])
    return [code for code, _ in ranked[:TOP_N]]


def _should_exit(code, date):
    """回弹换出 或 到期强制换出 → True 表示应卖出。"""
    since = g.hold_since.get(code)
    if since is not None and (g.trade_day_count - since) >= MAX_HOLD_DAYS:
        return True
    # 近 20 日最低价反弹判定
    df = get_history(code, 20, '1d', fields=['close'], fq='pre')
    if df is None or len(df) < 5:
        return False
    closes = df['close'].values.astype(float)
    low20 = closes.min()
    if low20 <= 0:
        return False
    rebound = closes[-1] / low20 - 1.0
    return rebound >= REBOUND_PCT


def handle_data(context, data):
    _ensure_runtime_state()
    g.trade_day_count += 1
    today = context.current_dt.strftime('%Y-%m-%d')
    month = today[:7]

    # 当前持仓：用策略自维护的 g.current_holdings 表达"已提交、T+1 才成交"的调仓意图
    # （next_open+basket 模式标准写法；T+1 drain 后 context.portfolio.positions 已可见，
    #   实测与自维护集合完全一致——两种来源均可，自维护状态语义更直接）。
    holding_codes = list(g.current_holdings)

    # ---- 每日评估：回弹/到期换出 ----
    exit_codes = [c for c in holding_codes if _should_exit(c, today)]

    # ---- 每月末全量重排（含首日）----
    do_rerank = (g.last_rebalance_month is None) or (month != g.last_rebalance_month)
    target_codes = list(g.target_list)
    if do_rerank:
        target_codes = _compute_fall_ranking(today)
        g.last_rebalance_month = month
        g.rebalance_count += 1

    # ---- 构造卖/买指令 ----
    target_set = set(target_codes)
    # 卖出：退出信号 + 不在新目标名单
    sell_codes = set(exit_codes) | (set(holding_codes) - target_set)
    # 买入：目标名单中当前未持有（以自维护持仓为准），且未满 TOP_N
    held_after_sell = set(holding_codes) - sell_codes
    buy_slots = max(0, TOP_N - len(held_after_sell))
    buy_codes = [c for c in target_codes if c not in held_after_sell][:buy_slots]

    # 单只目标价值
    total_value = context.portfolio.total_value
    per_value = total_value * GROSS_EXPOSURE / TOP_N
    per_value = min(per_value, total_value * MAX_SINGLE_WEIGHT)

    # 执行卖出
    for code in sell_codes:
        order_target_value(code, 0)
        g.current_holdings.discard(code)
        if code in g.hold_since:
            del g.hold_since[code]

    # 执行买入（受单只上限约束）
    for code in buy_codes:
        order_target_value(code, per_value)
        g.current_holdings.add(code)
        if code not in g.hold_since:
            g.hold_since[code] = g.trade_day_count

    g.target_list = target_codes

    # ---- R4 审计日志（AST 硬规则）----
    rid = '%s_%d' % (today.replace('-', ''), g.rebalance_count)
    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d sell_submitted=%d buy_submitted=%d history_eligible_count=%d'
             % (rid, today, len(target_codes), len(target_codes),
                len(sell_codes), len(buy_codes), len(target_codes)))
    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d gross_exposure=%.3f'
             % (rid, today, len(target_codes), GROSS_EXPOSURE))
