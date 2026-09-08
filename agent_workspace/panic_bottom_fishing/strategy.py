"""
恐慌抄底事件驱动逆向策略（panic_bottom_fishing）— QuantStudio 本地专用策略

设计契约: agent_workspace/panic_bottom_fishing/agent_strategy_design.json (design 2.3)
PTrade 转换: 不在本策略范围（由 PyQt "转 PTrade" tab / qs-compile import 承接）

核心逻辑（客户提示词 2026-09-08，R0/R2.5 四项裁决 + 五项确认）:
  恐慌抄底事件驱动逆向：上证指数连续两个交易日跌幅均 > 1.5% → 第二个信号日 T
  的 14:55（≈收盘，close 模式近似）满仓买入中证500成分中流通市值最大的 50 只
  （等权）；锁仓 20 个交易日（T 日含起第 20 个交易日收盘全部清仓），锁仓期内
  不做任何止损；无信号期间全程空仓。

信号（A-11 框架修复，docs/get-index-day-bar-design.md）:
  T-1、T 两日上证指数跌幅 = get_index_day_bar('000001.SS', count=2, fields=['pctChg'])
  ——独占 index_daily 路由（无 ETF 代理）、profile-aware 已完成上界（daily 含 T）、
  count 越界 ValueError、空数据 fail-closed（策略 fail-soft 不触发信号 + 审计行）、
  QS_INDEX_BAR 诊断日志。R1 实测 staging 库 000001 覆盖 2018-2026-09-04。

选股（T-1 快照，严格 PIT 无未来函数）:
  get_index_stocks('000905.SS', date=T-1) 中证500 成分 PIT as-of
  → 剔 ST(isST)/停牌(suspendFlag)/成交额<3000万/科创板(688/689)/北交所(920/43/83/87)
  → get_fundamentals('valuation', fields=['float_value'], date=T-1) 流通市值降序前 50
  → 等权满仓（实际可买 n 时 1/n，多余现金留仓）

执行（T 日 close 撮合，14:55≈收盘 R0 S1 确认）:
  涨停（data[code].high_limit>0 且 close>=high_limit）跳过留现金，不递补
  锁仓到期日跌停无法卖出 → 顺延至下一可卖交易日（R0 S9 默认）

审计: QS_REBALANCE_AUDIT / QS_PORTFOLIO_AUDIT（rule 21）。
r5_deployment_invariants fail-soft（终审⑥）: 入场日候选全部涨停时低 gross exposure
  为合法状态（note=limit_up_skip_all_low_exposure_legal）。

参数冻结声明（REQ-1）: SIGNAL_DROP=-1.5 / TOP_N=50 / LOCK_DAYS=20 / MIN_AMOUNT=3000万 /
  BUFFER=0.03 / COMMISSION=0.0003 / SLIPPAGE=0.001，全部来自客户提示词或 R0/R2.5 确认，
  未做回测驱动寻优。
"""

import numpy as np
import pandas as pd

STRATEGY_ID = 'panic_bottom_fishing'
STRATEGY_NAME = '恐慌抄底事件驱动逆向策略'
DESIGN_VERSION = '2.3'

# ---- 参数冻结声明（REQ-1）：全部来自客户提示词或 R0/R2.5 确认 ----
_SIGNAL_DROP = -1.5        # 连续两日上证指数跌幅 > 1.5%（pctChg < -1.5%）
_TOP_N = 50                # 中证500 内流通市值降序前 50 只
_LOCK_DAYS = 20            # 锁仓 20 个交易日（T 日含起第 20 个交易日收盘清仓）
_MIN_AMOUNT = 3.0e7        # T-1 单日成交额 >= 3000 万元（R0 S3 默认）
_BUFFER = 0.03             # 换仓缓冲带（覆盖费用/整手取整）
_COMMISSION_RATIO = 0.0003  # 双边万3（R0 S8）
_EXCLUDED_PREFIXES = ('688', '689', '920', '43', '83', '87')  # 科创 + 北交
_INDEX_SSE = '000001.SS'   # 上证综指（信号源，index_daily 专用路由）
_INDEX_CSI500 = '000905.SS'  # 中证500（选股池）
_BUY_VALUE_FLOOR = 2000.0   # 低于此额度的买单跳过（防粉尘订单）


def _ensure_runtime_state():
    """幂等创建全部 g 状态字段（skill 规则：任何回调不得依赖 initialize 成功）。"""
    if not hasattr(g, 'initialized'):
        g.initialized = False
    if not hasattr(g, 'lock_active'):
        g.lock_active = False        # 是否处于锁仓期
    if not hasattr(g, 'lock_start_date'):
        g.lock_start_date = None     # 锁仓起始交易日（str YYYY-MM-DD）
    if not hasattr(g, 'lock_end_date'):
        g.lock_end_date = None       # 第 20 个交易日（清仓日）
    if not hasattr(g, 'target_list'):
        g.target_list = None         # 入场目标名单
    if not hasattr(g, 'pending_portfolio_audit_id'):
        g.pending_portfolio_audit_id = None
    if not hasattr(g, 'last_signal_note'):
        g.last_signal_note = ''


def _extract_history_field(history_item, field, dtype=float):
    """get_history(is_dict=True) 项字段归一（skill 规则 17 硬门禁）。"""
    if history_item is None:
        return np.asarray([], dtype=dtype)
    try:
        values = history_item[field]
    except (KeyError, IndexError, TypeError):
        return np.asarray([], dtype=dtype)
    if values is None:
        return np.asarray([], dtype=dtype)
    if hasattr(values, 'values'):
        values = values.values
    return np.asarray(values, dtype=dtype)


def _signal_fired(context):
    """恐慌信号：上证指数连续两个交易日 pctChg 均 < -1.5%（跌幅均 > 1.5%）。

    T 日跌幅 = get_index_day_bar 当日已完成读数（daily-bar-v1 close 模式，
    14:55≈收盘 R0 S1 确认）；T-1 跌幅 = count=2 的前一行。
    fail-closed：指数数据缺失 → 空 DataFrame → 不触发信号（审计行记录）。
    """
    try:
        idx = get_index_day_bar(_INDEX_SSE, count=2, fields=['pctChg'])
    except Exception as exc:
        log.warning('QS_INDEX_BAR_FAIL code=%s err=%s', _INDEX_SSE, exc)
        g.last_signal_note = 'index_bar_fail'
        return False
    if idx is None or len(idx) < 2:
        g.last_signal_note = 'index_bar_insufficient'
        log.info('QS_INDEX_BAR_EMPTY code=%s date=%s note=%s',
                 _INDEX_SSE, context.current_dt.strftime('%Y-%m-%d'),
                 g.last_signal_note)
        return False
    pct = np.asarray(idx['pctChg'], dtype=float)
    t1, t0 = float(pct[-2]), float(pct[-1])
    fired = (np.isfinite(t1) and np.isfinite(t0)
             and t1 < _SIGNAL_DROP and t0 < _SIGNAL_DROP)
    g.last_signal_note = 'fired' if fired else 'not_fired'
    log.info('QS_SIGNAL code=%s date=%s d1=%.3f d0=%.3f fired=%s',
             _INDEX_SSE, context.current_dt.strftime('%Y-%m-%d'),
             t1, t0, fired)
    return fired


def _select_targets(context):
    """T-1 中证500 成分 PIT → 过滤 → 流通市值降序前 50。返回 (target_list, counts)。"""
    counts = {}
    prev = context.previous_date

    # ---- 1. 中证500 成分 PIT（T-1 as-of，严格无未来）----
    try:
        constituents = get_index_stocks(_INDEX_CSI500, date=prev)
    except Exception as exc:
        log.warning('get_index_stocks fail: %s', exc)
        constituents = []
    counts['L1_constituents'] = len(constituents)
    if not constituents:
        return None, counts

    # ---- 2. 板块剔除（科创 688/689、北交 920/43/83/87）----
    stage2 = [c for c in constituents
              if not str(c).split('.')[0].startswith(_EXCLUDED_PREFIXES)]
    counts['L2_board'] = len(stage2)

    # ---- 3. 估值（T-1 PIT：流通市值 float_value）----
    val_df = get_fundamentals(stage2, 'valuation', fields=['float_value'], date=prev)
    val = {}
    if val_df is not None and len(val_df) > 0:
        for code_raw, row in val_df.iterrows():
            code = str(code_raw)
            try:
                fv = float(row['float_value']) if row['float_value'] == row['float_value'] else np.nan
            except (KeyError, TypeError, ValueError):
                fv = np.nan
            if np.isfinite(fv) and fv > 0:
                val[code] = fv
    counts['L3_has_float'] = len(val)

    # ---- 4. T-1 成交额 + 状态过滤（isST / 停牌）----
    amount_map = {}
    try:
        # get_history_batch 返回 CodeDict{ptrade_code: DataFrame}，T-1 成交额（include=False 锚定 prev）
        amt = get_history_batch(stage2, 1, '1d', fields=['amount'], fq='pre', include=False)
        if amt is not None and len(amt) > 0:
            for code, item in amt.items():
                arr = _extract_history_field(item, 'amount')
                if arr.size >= 1 and np.isfinite(arr[-1]) and arr[-1] > 0:
                    amount_map[code] = float(arr[-1])
    except Exception:
        amount_map = {}

    # ST / 停牌剔除（T-1 状态快照，经公共状态 API get_stock_status）
    st_codes = []
    halt_codes = []
    try:
        st_res = get_stock_status(stage2, query_type='ST', query_date=prev)
        if isinstance(st_res, dict):
            st_codes = sorted([k for k, v in st_res.items() if v])
    except Exception:
        st_codes = []
    try:
        halt_res = get_stock_status(stage2, query_type='HALT', query_date=prev)
        if isinstance(halt_res, dict):
            halt_codes = sorted([k for k, v in halt_res.items() if v])
    except Exception:
        halt_codes = []

    stage4 = []
    for code in stage2:
        if code not in val:
            continue
        if code in st_codes:
            continue  # ST 剔除
        if code in halt_codes:
            continue  # 停牌剔除
        amt = amount_map.get(code, np.nan)
        if not np.isfinite(amt) or amt < _MIN_AMOUNT:
            continue
        stage4.append(code)
    counts['L4_amount_ge_3kw'] = len(stage4)

    if not stage4:
        return None, counts

    # ---- 5. 流通市值降序前 50（tie: 代码升序保证确定性）----
    ordered = sorted(stage4, key=lambda c: (-val[c], c))
    target = ordered[:_TOP_N]
    counts['R_top50'] = len(target)
    return target, counts


def initialize(context):
    """参数、基准、成本（冻结参数见模块头 REQ-1 声明）。"""
    _ensure_runtime_state()
    set_benchmark(_INDEX_CSI500)
    set_commission(commission_ratio=_COMMISSION_RATIO, min_commission=5.0, type='STOCK')
    set_slippage(slippage=0.001)
    g.initialized = True
    log.info('STRATEGY_INIT strategy=%s capital_mode=runtime_total_value',
             STRATEGY_ID)


def handle_data(context, data):
    """每日收盘（close 模式，14:55≈收盘）：
    1) 锁仓期内 → 不动；到期日 → 全部清仓；
    2) 非锁仓 → 信号判定 → 触发则满仓入场；否则空仓不动。"""
    _ensure_runtime_state()
    day = context.current_dt.strftime('%Y-%m-%d')
    rebalance_id = context.current_dt.strftime('%Y%m%d')

    # ---- 锁仓期状态机 ----
    if g.lock_active:
        if day >= g.lock_end_date:
            # 到期：全部清仓（跌停无法卖出 → 顺延至下一可卖交易日）
            _liquidate_all(context, data, rebalance_id, note='lock_expired')
            g.lock_active = False
            g.lock_start_date = None
            g.lock_end_date = None
            g.target_list = None
        else:
            # 锁仓期内：不进行任何止损操作（客户硬约束）
            log.info('QS_LOCK_HOLD date=%s lock_start=%s lock_end=%s',
                     day, g.lock_start_date, g.lock_end_date)
            g.pending_portfolio_audit_id = rebalance_id
        return

    # ---- 非锁仓：先重试顺延清仓（R0 S9：跌停/停牌未卖顺延至下一可卖交易日）----
    # 注：positions dict 含已清仓的 volume=0 残留 key，须以真实持仓（amount>0）判定
    def _pos_amt(p):
        return (getattr(p, 'amount', 0) or getattr(p, 'volume', 0) or 0)
    real_holdings = [c for c, p in context.portfolio.positions.items()
                     if _pos_amt(p) > 0]
    if real_holdings:
        _liquidate_all(context, data, rebalance_id, note='deferred_sell_retry')
        # 顺延卖单重试后本日不再开新仓（锁仓到期清仓语义：先出清再谈入场）
        g.pending_portfolio_audit_id = rebalance_id
        return

    # ---- 非锁仓（持仓已清空）：信号判定 ----
    fired = _signal_fired(context)
    if not fired:
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=0 tradable=0 '
                 'sell_submitted=0 buy_submitted=0 note=%s'
                 % (rebalance_id, day, g.last_signal_note or 'no_signal'))
        g.pending_portfolio_audit_id = rebalance_id
        return

    # ---- 信号触发：满仓入场 ----
    target, counts = _select_targets(context)
    note = ''
    if not target:
        note = 'empty_after_filters'
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=0 tradable=0 '
                 'sell_submitted=0 buy_submitted=0 note=%s counts=%s'
                 % (rebalance_id, day, note, counts))
        g.pending_portfolio_audit_id = rebalance_id
        return

    # 入场：等权满仓（实际可买 n 时 1/n；涨停跳过留现金，不递补）
    positions_before = list(context.portfolio.positions.keys())
    n_target = len(target)
    tradable = 0
    for code in target:
        bar = data[code]
        if bar.close > 0 and bar.volume > 0:
            tradable += 1

    buy_submitted = 0
    skipped = []
    buys = [c for c in target if c not in positions_before]
    for i, code in enumerate(buys):
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            skipped.append(code)
            continue
        if bar.high_limit > 0 and bar.close >= bar.high_limit:
            skipped.append(code)  # 涨停买不进：跳过留现金，不递补
            continue
        remaining = len(buys) - i
        tv = context.portfolio.total_value
        cash = context.portfolio.cash
        target_val = min(tv / n_target * (1 - _BUFFER), cash / remaining)
        if target_val < _BUY_VALUE_FLOOR:
            skipped.append(code)
            continue
        order_target_value(code, target_val)
        buy_submitted += 1

    # 进入锁仓期（T 日含起第 20 个交易日 = 清仓日）
    g.lock_active = True
    g.lock_start_date = day
    trade_days = get_trade_days(start_date=day, end_date=None)
    if trade_days is not None and len(trade_days) >= _LOCK_DAYS:
        g.lock_end_date = str(trade_days[_LOCK_DAYS - 1])[:10]
    else:
        g.lock_end_date = day  # fail-safe：当日即到期（极端情况不持仓穿越）
    g.target_list = target

    note_skip = 'limit_up_skip_all_low_exposure_legal' if (
        skipped and len(skipped) == len(buys)) else ('skip_%d' % len(skipped))
    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
             'sell_submitted=%d buy_submitted=%d note=%s'
             % (rebalance_id, day, len(target), tradable, 0, buy_submitted,
                note_skip))
    g.pending_portfolio_audit_id = rebalance_id


def _liquidate_all(context, data, rebalance_id, note='lock_expired'):
    """到期全清：跌停/停牌无法卖出 → 顺延至下一可卖交易日（R0 S9 默认）。

    仅处理真实持仓（amount>0）；positions dict 含已清仓 volume=0 残留 key，
    必须过滤，否则每日重试分支永不退出。
    """
    def _pos_amt(p):
        return (getattr(p, 'amount', 0) or getattr(p, 'volume', 0) or 0)
    positions = [c for c, p in context.portfolio.positions.items()
                 if _pos_amt(p) > 0]
    sell_submitted = 0
    for code in positions:
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            log.info('QS_LIQUIDATE_DEFER code=%s date=%s reason=no_bar',
                     code, context.current_dt.strftime('%Y-%m-%d'))
            continue
        if bar.low_limit > 0 and bar.close <= bar.low_limit:
            log.info('QS_LIQUIDATE_DEFER code=%s date=%s reason=limit_down',
                     code, context.current_dt.strftime('%Y-%m-%d'))
            continue
        order_target_value(code, 0)
        sell_submitted += 1
    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
             'sell_submitted=%d buy_submitted=0 note=%s'
             % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'),
                len(positions), len(positions), sell_submitted, note))
    g.pending_portfolio_audit_id = rebalance_id


def after_trading_end(context, data):
    """日终对账：输出 QS_PORTFOLIO_AUDIT（撮合后真实持仓——引擎权威口径）。"""
    _ensure_runtime_state()
    rid = getattr(g, 'pending_portfolio_audit_id', None)
    if rid is not None:
        g.pending_portfolio_audit_id = None
        tv = context.portfolio.total_value
        cash = context.portfolio.cash
        mv = context.portfolio.market_value
        def _pos_amt(p):
            return (getattr(p, 'amount', 0) or getattr(p, 'volume', 0) or 0)
        real_pos = [c for c, p in context.portfolio.positions.items()
                    if _pos_amt(p) > 0]
        log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
                 'cash_ratio=%.4f gross_exposure=%.4f'
                 % (rid, context.current_dt.strftime('%Y-%m-%d'),
                    len(real_pos),
                    (cash / tv if tv > 0 else 0.0),
                    (mv / tv if tv > 0 else 0.0)))
