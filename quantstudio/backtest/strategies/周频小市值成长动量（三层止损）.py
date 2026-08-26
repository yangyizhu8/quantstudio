"""
weekly_smallcap_growth_momentum_10_v2 — 周频小市值增长动量十股策略·三层止损版（QuantStudio 本地专用）

设计契约: output/generated_strategies/weekly_smallcap_growth_momentum_10_v2/agent_strategy_design.json (design 2.2)
v1 语义全部继承（漏斗 L0-L6 / MC 动量排序 / ISO 周触发 / close 模式 / 等权 10 只 /
runtime_total_value 定-size / 涨跌停停牌过滤 / fail-soft / 不变量 0.70-0.30）。
PTrade 转换: 不在本策略范围（PyQt "转 PTrade" tab / qs-compile import 承接）。

v2 新增——三层止损（客户 2026-08-22 指令，参数冻结零寻优 REQ-1 延续）:
  ① 个股硬止损 -20%: 每日盘前 T-1 preclose vs 引擎 avg_cost（A-SL-1: 成交价加权摊薄，
     不含费用）；标记持续有效；标记日=调仓日当天卖出，否则下个调仓日卖出；
     卖出失败（跌停/停牌/无bar）→ 每日重试直至成交（补充条款①）。
  ② 组合档位: DD = 1 - 昨末净值/日终高水位峰值（after_trading_end 维护，仅已完成日）；
     DD>=15% halt（新买全停）/ 12%<=DD<15% half（新买半额）/ DD<12% normal；
     只控新仓规模，绝不强制卖旧仓（A-SL-5）。
  ③ 时间止损 42 自然日: 首成交日（策略账本 g.buy_dates, A-SL-4）起 >=42 天且
     T-1 收盘 < avg_cost → 强制除名（正常卖出路径；卖不出按 v1 顺延，
     每日重试仅适用于①，A-SL-3）。
  标记中的持仓排除出新目标名单（A-SL-6）；退出成交后无冷却期可再入选（Q4=1）。

审计行（补充条款②）:
  QS_STOP_AUDIT rebalance_id=... date=... tier1_marked=<n> tier1_detail=<c:-x.x|...>
      tier2_dd=<0.xxxx> tier2_tier=<normal|half|halt> tier3_flagged=<n> tier3_detail=<c:6w|...>
  QS_STOP_RETRY date=... attempts=<n> filled=<n> pending=<n>

滑点: 源码不设置；A/B 组由引擎层 cost 注入（Q7 延续）。
"""

import numpy as np
import pandas as pd

STRATEGY_ID = 'weekly_smallcap_growth_momentum_10_v2'
DESIGN_VERSION = '2.2'

# ---- 参数冻结声明（REQ-1 延续）：客户规格书 + 2026-08-19 因子答复 + 2026-08-22 止损指令，零寻优 ----
_MIN_LISTED_DAYS = 25
_GROWTH_TOP_PCT = 0.10
_SMALLCAP_POOL = 30
_TARGET_HOLDINGS = 10
_MC112_WINDOW = 112
_MC20_WINDOW = 20
_W_MC112 = 0.4
_W_MC20 = 0.1
_WINSOR_LO = 0.01
_WINSOR_HI = 0.99
_BARS_REQUIRED = _MC112_WINDOW + 1
_EXCLUDED_PREFIXES = ('688', '689', '920', '43', '83', '87')
_BUY_VALUE_FLOOR = 1000.0
# 三层止损冻结参数（客户 2026-08-22 指令）
_STOP_LOSS_PCT = -0.20      # ① 个股硬止损阈值
_TIER_HALF_DD = 0.12        # ② 新买减半档
_TIER_HALT_DD = 0.15        # ② 暂停新买档
_TIME_STOP_DAYS = 42        # ③ 自然日


def _ensure_runtime_state():
    """幂等创建全部 g 状态字段（skill 规则：任何回调不得依赖 initialize 成功）。"""
    if not hasattr(g, 'initialized'):
        g.initialized = False
    if not hasattr(g, 'last_rebalance_week'):
        g.last_rebalance_week = None
    if not hasattr(g, 'current_week_key'):
        g.current_week_key = None
    if not hasattr(g, 'rebalance_due'):
        g.rebalance_due = False
    if not hasattr(g, 'target_list'):
        g.target_list = None
    if not hasattr(g, 'funnel_counts'):
        g.funnel_counts = None
    if not hasattr(g, 'selection_note'):
        g.selection_note = ''
    # v2 三层止损状态
    if not hasattr(g, 'stop_marks'):
        g.stop_marks = {}        # code -> (dd, mark_date_str)  ①标记持续有效直至卖出
    if not hasattr(g, 'exit_pending'):
        g.exit_pending = {}      # code -> 'time'  ③待除名
    if not hasattr(g, 'buy_dates'):
        g.buy_dates = {}         # code -> 'YYYY-MM-DD' 首成交日账本（A-SL-4）
    if not hasattr(g, 'peak_tv'):
        g.peak_tv = 0.0          # 日终 total_asset 高水位（A-SL-5）
    if not hasattr(g, 'last_eod_tv'):
        g.last_eod_tv = 0.0      # 上一已完成日日终净值
    if not hasattr(g, 'tier'):
        g.tier = 'normal'        # normal | half | halt


def _iso_week_key(dt):
    cal = dt.isocalendar()
    return (int(cal[0]), int(cal[1]))


def _extract_history_field(history_item, field, dtype=float):
    """get_history(is_dict=True) 项字段归一（skill 规则 17；fail-soft 全形状）。"""
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


def _winsorize(values):
    """经典 Winsorize：每侧替换 k=int(n*pct)；n<100 时 k=0 恒等（v1 口径）。"""
    arr = np.asarray(values, dtype=float)
    n = arr.size
    if n == 0:
        return arr
    k = int(n * _WINSOR_LO)
    if k <= 0:
        return arr
    srt = np.sort(arr, kind='mergesort')
    lo_val = srt[min(k, n - 1)]
    hi_val = srt[max(n - 1 - k, 0)]
    return np.clip(arr, lo_val, hi_val)


def _rank_norm(values):
    """rank 标准化映射 [-1,1]（ties=average，确定性；v1 口径）。"""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    return pd.Series(arr).rank(pct=True).values * 2.0 - 1.0


def _mc_from_closes(closes, window):
    """MC_raw（客户 2026-08-19 文档口径）：close[T-1]/close[T-1-window] - 1。"""
    arr = np.asarray(closes, dtype=float)
    return arr[-1] / arr[-(window + 1)] - 1.0


def _latest_by_code(df, value_field):
    """fin 表逐码取 (end_date, publ_date) 最新有值行（v2 口径）。

    P-A3 防线 3：最新公告行 value 为 NULL/NaN 时回退到上一有值报告期，
    与平台「最新有值行」语义对齐（次新股上市前报告期自动回退到上市后首期）。
    """
    if df is None or len(df) == 0:
        return {}
    val = np.asarray(df[value_field], dtype=float)
    mask = ~np.isnan(val)
    if not mask.any():
        return {}
    frame = pd.DataFrame({
        'code': [str(c) for c in df.index],
        'end_date': np.asarray(df['end_date'], dtype=float),
        'publ_date': np.asarray(df['publ_date'], dtype=float),
        'value': val,
    })
    frame = frame[mask]
    frame['end_date'] = frame['end_date'].fillna(-1.0)
    frame['publ_date'] = frame['publ_date'].fillna(-1.0)
    frame = frame.sort_values(['code', 'end_date', 'publ_date'], kind='mergesort')
    frame = frame.drop_duplicates(subset='code', keep='last')
    return dict(zip(frame['code'].tolist(), frame['value'].tolist()))


def _select_weekly_targets(context):
    """L0→L6 漏斗 + MC 动量排名（全部 T-1 数据；v1 逻辑原样）。"""
    counts = {}
    note = ''
    all_codes = get_Ashares()
    counts['L0_all'] = len(all_codes)
    info = get_stock_info(all_codes, field=['listed_date'])
    today = context.current_dt.date()
    stage1 = []
    for code in all_codes:
        listed = (info.get(code) or {}).get('listed_date')
        if not listed:
            continue
        try:
            listed_date = pd.Timestamp(listed).date()
        except Exception:
            continue
        if (today - listed_date).days < _MIN_LISTED_DAYS:
            continue
        stage1.append(code)
    counts['L1_listed'] = len(stage1)
    stage2 = [c for c in stage1 if not str(c).split('.')[0].startswith(_EXCLUDED_PREFIXES)]
    counts['L2_board'] = len(stage2)
    stage3 = filter_stock_by_status(stage2, filter_type=['ST', 'HALT', 'DELISTING'],
                                    query_date=None)
    counts['L3_status'] = len(stage3)
    val_df = get_fundamentals_batch(stage3, 'valuation', fields=['float_value'])
    float_map = {}
    if val_df is not None and len(val_df) > 0:
        float_map = dict(zip([str(c) for c in val_df.index],
                             np.asarray(val_df['float_value'], dtype=float).tolist()))
    growth_df = get_fundamentals(stage3, 'growth_ability',
                                 fields=['or_yoy', 'publ_date', 'end_date'])
    growth_map = _latest_by_code(growth_df, 'or_yoy')
    eps_df = get_fundamentals(stage3, 'eps', fields=['eps', 'publ_date', 'end_date'])
    eps_map = _latest_by_code(eps_df, 'eps')
    stage3c = [c for c in stage3
               if c in float_map and np.isfinite(float_map[c]) and float_map[c] > 0
               and c in growth_map and np.isfinite(growth_map[c])
               and c in eps_map and np.isfinite(eps_map[c])]
    counts['L3v_complete'] = len(stage3c)
    ordered_growth = sorted(stage3c, key=lambda c: (-growth_map[c], float_map[c], c))
    keep_n = max(1, int(len(ordered_growth) * _GROWTH_TOP_PCT))
    stage4 = ordered_growth[:keep_n]
    counts['L4_growth'] = len(stage4)
    ordered_cap = sorted(stage4, key=lambda c: (float_map[c], c))
    stage5 = ordered_cap[:_SMALLCAP_POOL]
    counts['L5_smallcap'] = len(stage5)
    stage6 = [c for c in stage5 if eps_map[c] > 0]
    counts['L6_eps'] = len(stage6)
    if not stage6:
        counts['R_selected'] = 0
        return None, counts, 'empty_after_L6'
    hist = get_history_batch(stage6, _BARS_REQUIRED, '1d', fields=['close'],
                             fq='pre', include=False)
    closes_map = {}
    for code in stage6:
        item = hist.get(code) if hist is not None else None
        if item is None:
            continue
        arr = _extract_history_field(item, 'close')
        if arr.size < _BARS_REQUIRED:
            continue
        if not np.all(np.isfinite(arr)) or np.any(arr <= 0):
            continue
        closes_map[code] = arr
    counts['R_rankable'] = len(closes_map)
    if len(closes_map) < _TARGET_HOLDINGS:
        counts['R_selected'] = 0
        return None, counts, 'fail_soft_rankable_lt_%d' % _TARGET_HOLDINGS
    rank_codes = list(closes_map.keys())
    mc112 = np.array([_mc_from_closes(closes_map[c], _MC112_WINDOW) for c in rank_codes],
                     dtype=float)
    mc20 = np.array([_mc_from_closes(closes_map[c], _MC20_WINDOW) for c in rank_codes],
                    dtype=float)
    norm112 = _rank_norm(_winsorize(mc112))
    norm20 = _rank_norm(_winsorize(mc20))
    scores = _W_MC112 * norm112 + _W_MC20 * norm20
    order = sorted(range(len(rank_codes)),
                   key=lambda i: (-scores[i], float_map[rank_codes[i]], rank_codes[i]))
    target = [rank_codes[i] for i in order[:_TARGET_HOLDINGS]]
    counts['R_selected'] = len(target)
    return target, counts, ''


# ===================== v2 三层止损组件 =====================

def _position_ratio(code, data):
    """①监控比值（A-SL-1/2）：T-1 preclose / avg_cost。无持仓/无效价返回 None。

    触发判定用比值（ratio <= 1 + STOP_LOSS_PCT）而非差值（dd <= STOP_LOSS_PCT），
    避免 80/100-1 = -0.1999…96 的浮点伪边界：0.8 <= 0.8 恰好精确成立。
    """
    pos = get_position(code)
    if pos is None or not getattr(pos, 'amount', 0):
        return None
    avg_cost = float(getattr(pos, 'avg_cost', 0.0) or 0.0)
    if avg_cost <= 0:
        return None
    pre = float(getattr(data[code], 'preclose', 0.0) or 0.0)
    if pre <= 0:
        return None
    return pre / avg_cost


def _daily_stop_monitor(context, data):
    """每日盘前①硬止损标记（before_trading_start 内调用，仅用 T-1 数据）。

    标记持续有效期间每日刷新跌幅（审计行报告当前真实跌幅，R5 归因需要），
    mark_date 保持首次触发日不变。
    """
    today_str = context.current_dt.strftime('%Y-%m-%d')
    for code in list(get_positions().keys()):
        ratio = _position_ratio(code, data)
        if ratio is None:
            continue
        if ratio <= 1.0 + _STOP_LOSS_PCT:
            if code in g.stop_marks:
                g.stop_marks[code] = (ratio - 1.0, g.stop_marks[code][1])
            else:
                g.stop_marks[code] = (ratio - 1.0, today_str)


def _evaluate_portfolio_tier():
    """②组合档位（A-SL-5）：DD = 1 - 昨末净值/峰值；双阈值滞回。"""
    if g.peak_tv <= 0 or g.last_eod_tv <= 0:
        g.tier = 'normal'
        return 0.0
    dd = 1.0 - g.last_eod_tv / g.peak_tv
    if dd >= _TIER_HALT_DD:
        g.tier = 'halt'
    elif dd >= _TIER_HALF_DD:
        g.tier = 'half'
    else:
        g.tier = 'normal'
    return dd


def _flag_time_stops(context, data):
    """③时间止损标记：>=42 自然日且 T-1 收盘 < avg_cost（A-SL-3/4）。"""
    today = context.current_dt.date()
    flagged = []
    for code in list(get_positions().keys()):
        if code in g.exit_pending:
            continue
        buy_date = g.buy_dates.get(code)
        if buy_date is None:
            g.buy_dates[code] = context.current_dt.strftime('%Y-%m-%d')  # 防御补账
            continue
        try:
            held_days = (today - pd.Timestamp(buy_date).date()).days
        except Exception:
            continue
        if held_days < _TIME_STOP_DAYS:
            continue
        ratio = _position_ratio(code, data)
        if ratio is not None and ratio < 1.0:
            g.exit_pending[code] = 'time'
            flagged.append(code)
    return flagged


def _reconcile_buy_ledger(context):
    """C18 买入账本对账：有持仓无账本→补当日（保守）；无持仓→移除。"""
    today_str = context.current_dt.strftime('%Y-%m-%d')
    held = dict.fromkeys(get_positions().keys())   # 确定性容器（集合类型禁用）
    for code in held:
        if code not in g.buy_dates:
            g.buy_dates[code] = today_str
    for code in list(g.buy_dates.keys()):
        if code not in held:
            del g.buy_dates[code]


def _try_sell_marked(context, data):
    """①标记卖出尝试（调仓日与每日重试共用）：跌停/停牌/无bar跳过。

    返回 (attempts, filled)。成功清仓的 code 从 stop_marks 移除。
    """
    attempts = 0
    filled = 0
    for code in list(g.stop_marks.keys()):
        if code not in get_positions():
            del g.stop_marks[code]   # 已经由其他路径清仓（如正常调仓卖出）
            continue
        attempts += 1
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            continue  # 停牌/无bar：标记保留，次日重试
        if bar.low_limit > 0 and bar.close <= bar.low_limit:
            continue  # 跌停卖不出：标记保留，每日重试（补充条款①）
        order_target_value(code, 0)
        if get_position(code).amount == 0:
            del g.stop_marks[code]
            g.exit_pending.pop(code, None)
            filled += 1
    return attempts, filled


def initialize(context):
    _ensure_runtime_state()
    set_benchmark('000300.SS')
    g.initialized = True


def before_trading_start(context, data):
    """每日：①硬止损标记；调仓日：漏斗选股 + ②档位 + ③时间止损 + 审计行。"""
    _ensure_runtime_state()
    _daily_stop_monitor(context, data)          # 每日执行（Q1）
    week = _iso_week_key(context.current_dt)
    g.current_week_key = week
    if week == g.last_rebalance_week:
        g.rebalance_due = False
        return
    g.rebalance_due = True
    # ---- ② 组合档位（调仓日评估，仅控新买）----
    dd = _evaluate_portfolio_tier()
    # ---- ③ 时间止损标记（调仓日盘前）----
    flagged = _flag_time_stops(context, data)
    # ---- 漏斗 + 动量排名（v1 原样）----
    target, counts, note = _select_weekly_targets(context)
    # ---- A-SL-6：①③标记中的持仓排除出新目标名单（dict 成员检查，确定性）----
    if target is not None:
        target = [c for c in target
                  if c not in g.stop_marks and c not in g.exit_pending]
        counts['R_selected'] = len(target)
    g.target_list = target
    g.funnel_counts = counts
    g.selection_note = note
    # ---- QS_FUNNEL_AUDIT（v1 原样）----
    rebalance_id = context.current_dt.strftime('%Y%m%d')
    parts = ''.join(' %s=%s' % (k, counts[k]) for k in sorted(counts.keys()))
    log.info('QS_FUNNEL_AUDIT rebalance_id=%s date=%s%s note=%s'
             % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'), parts,
                note or 'ok'))
    # ---- QS_STOP_AUDIT（补充条款②）----
    t1_detail = '|'.join('%s:%.3f' % (c, g.stop_marks[c][0])
                         for c in sorted(g.stop_marks.keys()))
    t3_detail = '|'.join('%s:%dw' % (c, max(1, (context.current_dt.date() -
                pd.Timestamp(g.buy_dates.get(c, context.current_dt.strftime('%Y-%m-%d'))).date()).days // 7))
                for c in sorted(g.exit_pending.keys()))
    log.info('QS_STOP_AUDIT rebalance_id=%s date=%s tier1_marked=%d tier1_detail=%s '
             'tier2_dd=%.4f tier2_tier=%s tier3_flagged=%d tier3_detail=%s'
             % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'),
                len(g.stop_marks), t1_detail, dd, g.tier,
                len(g.exit_pending), t3_detail))


def handle_data(context, data):
    """调仓日执行（含①③退出与②调制）；非调仓日仅①每日重试。"""
    _ensure_runtime_state()
    date_str = context.current_dt.strftime('%Y-%m-%d')
    if not g.rebalance_due:
        # ---- 非调仓日：①硬止损每日重试（补充条款①，仅 stop_marks 非空时）----
        if g.stop_marks:
            attempts, filled = _try_sell_marked(context, data)
            log.info('QS_STOP_RETRY date=%s attempts=%d filled=%d pending=%d'
                     % (date_str, attempts, filled, len(g.stop_marks)))
        return
    rebalance_id = context.current_dt.strftime('%Y%m%d')
    week = g.current_week_key
    g.rebalance_due = False
    g.last_rebalance_week = week
    # ---- ①硬止损标记卖出（标记日=调仓日当天即卖；此前标记的也在今日卖）----
    stop_attempts, stop_filled = _try_sell_marked(context, data)
    if g.target_list is None:
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=0 tradable=0 '
                 'sell_submitted=%d buy_submitted=0 reason=%s stop_retry=%d/%d'
                 % (rebalance_id, date_str, stop_filled,
                    g.selection_note or 'no_target', stop_filled, stop_attempts))
        log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
                 'cash_ratio=%.4f gross_exposure=%.4f'
                 % (rebalance_id, date_str, len(context.portfolio.positions),
                    (context.portfolio.cash / context.portfolio.total_value
                     if context.portfolio.total_value > 0 else 0.0),
                    (context.portfolio.market_value / context.portfolio.total_value
                     if context.portfolio.total_value > 0 else 0.0)))
        _reconcile_buy_ledger(context)
        return
    target = list(g.target_list)
    positions_before = list(context.portfolio.positions.keys())
    tradable = 0
    for code in target:
        bar = data[code]
        if bar.close > 0 and bar.volume > 0:
            tradable += 1
    # ---- 常规卖出：不在新名单（含③exit_pending，经排除已不在名单内）----
    sell_submitted = 0
    for code in positions_before:
        if code in target:
            continue
        if code not in get_positions():
            continue  # 已在①止损卖出中清仓（防重复下单）
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            continue
        if bar.low_limit > 0 and bar.close <= bar.low_limit:
            continue
        order_target_value(code, 0)
        sell_submitted += 1
    # 已卖出的③标记清除
    for code in list(g.exit_pending.keys()):
        if code not in get_positions():
            del g.exit_pending[code]
    # ---- 买入（含②档位调制）----
    buys = [c for c in target if c not in positions_before
            and c not in get_positions()]
    n_buys = len(buys)
    buy_submitted = 0
    if g.tier != 'halt':   # halt：新买全停（只控新买，不卖旧仓）
        tier_mult = 0.5 if g.tier == 'half' else 1.0
        for i, code in enumerate(buys):
            bar = data[code]
            if bar.close <= 0 or bar.volume <= 0:
                continue
            if bar.high_limit > 0 and bar.close >= bar.high_limit:
                continue
            remaining = n_buys - i
            tv = context.portfolio.total_value
            cash = context.portfolio.cash
            target_val = min(tv / _TARGET_HOLDINGS, cash / remaining) * tier_mult
            if target_val < _BUY_VALUE_FLOOR:
                continue
            order_target_value(code, target_val)
            buy_submitted += 1
    tv_after = context.portfolio.total_value
    cash_after = context.portfolio.cash
    mv_after = context.portfolio.market_value
    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
             'sell_submitted=%d buy_submitted=%d tier=%s stop_retry=%d/%d'
             % (rebalance_id, date_str, len(target), tradable,
                sell_submitted, buy_submitted, g.tier, stop_filled, stop_attempts))
    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
             'cash_ratio=%.4f gross_exposure=%.4f'
             % (rebalance_id, date_str, len(context.portfolio.positions),
                (cash_after / tv_after if tv_after > 0 else 0.0),
                (mv_after / tv_after if tv_after > 0 else 0.0)))
    _reconcile_buy_ledger(context)


def after_trading_end(context, data):
    """日终：②峰值跟踪（仅已完成日）+ 轻量对账。"""
    _ensure_runtime_state()
    tv = context.portfolio.total_value
    if tv > 0:
        g.last_eod_tv = tv
        if tv > g.peak_tv:
            g.peak_tv = tv
    positions = context.portfolio.positions
    if positions:
        log.debug('%s day_end positions=%d cash=%.2f total_value=%.2f tier=%s'
                  % (STRATEGY_ID, len(positions), context.portfolio.cash, tv, g.tier))
