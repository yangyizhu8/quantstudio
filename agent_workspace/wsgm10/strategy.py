"""
weekly_smallcap_growth_momentum_10 — 周频小市值增长动量十股策略（QuantStudio 本地专用）

设计契约: output/generated_strategies/weekly_smallcap_growth_momentum_10/agent_strategy_design.json (design 2.2)
PTrade 转换: 不在本策略范围（由 PyQt "转 PTrade" tab / qs-compile import 承接）

漏斗（每层经济逻辑见设计契约；参数冻结声明 REQ-1）:
  L0 全A PIT 池（get_Ashares as-of 快照，含窗口内退市股，无幸存者偏差）
     + 基础数据完整性（listed_date / float_value / 最新 or_yoy / 最新 eps 缺失即剔除）
  L1 上市满 25 自然日（listed_date=首根K线日口径）
  L2 剔除科创板(688/689)与北交所(920/43/83/87)
  L3 剔除 ST/停牌/退市（filter_stock_by_status ['ST','HALT','DELISTING']，T-1 快照）
  L4 营业收入增长 or_yoy（ann_date<=T-1 逐码最新报告期）降序前 10%
  L5 流通市值 float_value（get_fundamentals valuation PIT）升序前 30
  L6 最新 eps > 0（应用于取 30 之后，候选可少于 30 —— 客户 R2.5-2 确认）
  R  动量排序（客户 2026-08-19 提供口径，仅排序不预测）:
       MC112_raw = close_front[T-1]/close_front[T-113] - 1   （MC1120 判定为 112 日窗口）
       MC20_raw  = close_front[T-1]/close_front[T-21]  - 1   （版本 A）
       标准化（本设计选择，客户 R2.5-1 确认）: 横截面 1%/99% 缩尾 -> rank 映射 [-1,1]
       TotalScore = 0.4*Norm(MC112) + 0.1*Norm(MC20)，降序取前 10
       （n≈30 横截面 1% 分位无样本越界，缩尾为无操作，如实记录）

调仓: ISO 周首个交易日触发（周一休市自动顺延）；信号全部 T-1（fq='pre' include=False +
      ann_date<=T-1 + T-1 状态快照）；T 日收盘成交（close 模式强制，框架 2026-08-13 铁律）；
      卖先买后同批，卖出所得立即可用。
执行过滤: T 日 raw bar 预过滤（涨停不买/跌停不卖/停牌无 bar 跳过）+ 引擎撮合拒单兜底。
仓位: runtime_total_value——买入目标 = min(组合总值/10, 可用现金/待买空位)（R2.5-4 确认）；
      留任持仓不动；不加杠杆。
fail-soft: 可排名候选（含 113 根完整前复权历史）< 10 时本次调仓跳过、持仓保留（R2.5-5 确认）。
审计: QS_FUNNEL_AUDIT（REQ-2 逐层计数）/ QS_REBALANCE_AUDIT / QS_PORTFOLIO_AUDIT（rule 21）。
滑点: 本策略源码不设置滑点；A/B 组由引擎层 cost 注入（Q7 确认）。
"""

import numpy as np
import pandas as pd

STRATEGY_ID = 'weekly_smallcap_growth_momentum_10'
DESIGN_VERSION = '2.2'

# ---- 参数冻结声明（REQ-1）：全部来自客户规格书与 2026-08-19 因子定义答复，未做回测驱动寻优 ----
_MIN_LISTED_DAYS = 25          # 客户规格书 §一.2（自然日，R2.5-3 确认）
_GROWTH_TOP_PCT = 0.10         # 客户规格书 §一.3
_SMALLCAP_POOL = 30            # 客户规格书 §一.4
_TARGET_HOLDINGS = 10          # 客户规格书 §三.1（约 10%/只）
_MC112_WINDOW = 112            # 客户因子定义（MC1120 判定为 112 日窗口）
_MC20_WINDOW = 20              # 客户因子定义（版本 A）
_W_MC112 = 0.4                 # 客户规格书 §二（4:1 合成）
_W_MC20 = 0.1
_WINSOR_LO = 0.01              # 客户因子定义（缩尾上下 1%）
_WINSOR_HI = 0.99
_BARS_REQUIRED = _MC112_WINDOW + 1   # 113 根：close[T-1] 与 close[T-113]
_EXCLUDED_PREFIXES = ('688', '689', '920', '43', '83', '87')  # 科创板 + 北交所（客户 Q3）
_BUY_VALUE_FLOOR = 1000.0      # 低于此额度的买单跳过（防粉尘订单，非选股参数）


def _ensure_runtime_state():
    """幂等创建全部 g 状态字段（skill 规则：任何回调不得依赖 initialize 成功）。"""
    if not hasattr(g, 'initialized'):
        g.initialized = False
    if not hasattr(g, 'last_rebalance_week'):
        g.last_rebalance_week = None      # ISO 周元组，上一已消费调仓周
    if not hasattr(g, 'current_week_key'):
        g.current_week_key = None
    if not hasattr(g, 'rebalance_due'):
        g.rebalance_due = False           # 本周待执行标志（before_trading_start 置位）
    if not hasattr(g, 'target_list'):
        g.target_list = None              # 本周期有序目标名单（≤10，按总分降序）
    if not hasattr(g, 'funnel_counts'):
        g.funnel_counts = None
    if not hasattr(g, 'selection_note'):
        g.selection_note = ''


def _iso_week_key(dt):
    """ISO 年-周元组（确定性，周一休市时本周首个交易日触发）。"""
    cal = dt.isocalendar()
    return (int(cal[0]), int(cal[1]))


def _extract_history_field(history_item, field, dtype=float):
    """get_history(is_dict=True) 项字段归一（skill 规则 17 硬门禁）。

    项可能为 DataFrame / 结构化数组 / recarray / Series / None / 缺字段形状；
    提取字段可能为 Series 或 ndarray。统一经 np.asarray 归一后再数值化；
    任何不可提取形状 fail-soft 返回空 ndarray（长度检查处自然剔除该标的），
    禁止裸 .values/.iloc/.index。
    """
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
    """经典 Winsorize（客户因子定义口径，scipy mstats 语义）。

    每侧替换 k = int(n * pct) 个极端值为次序统计量；k=0（n < 100 时 1% 分位）
    为恒等无操作——与设计契约"n≈30 缩尾为无操作"的记录一致。
    """
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
    """rank 标准化映射 [-1, 1]（ties=average，确定性；本设计选择，R2.5-1 确认）。"""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    return pd.Series(arr).rank(pct=True).values * 2.0 - 1.0


def _mc_from_closes(closes, window):
    """MC_raw（客户 2026-08-19 文档口径）：close[T-1] / close[T-1-window] - 1。

    closes 为止于 T-1 的前复权收盘序列（长度 >= window+1）；
    window=112 -> MC112，window=20 -> MC20（版本 A）。
    """
    arr = np.asarray(closes, dtype=float)
    return arr[-1] / arr[-(window + 1)] - 1.0


def _latest_by_code(df, value_field):
    """growth/eps 表逐码取最新有值行：按 (end_date, publ_date) 最大者。

    get_fundamentals(fin_indicator) 返回 ann_date<=T 的全部历史行；
    同一 end_date 多次公告（重述）取 publ_date 最新。
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
    """L0→L6 漏斗 + MC 动量排名（全部 T-1 数据）。返回 (target_list or None, counts, note)。"""
    counts = {}
    note = ''

    # ---- L0: 全A PIT 快照（as-of 当前回测日，含窗口内已退市标的）----
    all_codes = get_Ashares()
    counts['L0_all'] = len(all_codes)

    # ---- L1: 上市日期已知 + 上市满 25 自然日 ----
    info = get_stock_info(all_codes, field=['listed_date'])
    today = context.current_dt.date()
    stage1 = []
    for code in all_codes:
        listed = (info.get(code) or {}).get('listed_date')
        if not listed:
            continue  # 基础数据不完整（L0 精神：无法参与可复现排序）
        try:
            listed_date = pd.Timestamp(listed).date()
        except Exception:
            continue
        if (today - listed_date).days < _MIN_LISTED_DAYS:
            continue
        stage1.append(code)
    counts['L1_listed'] = len(stage1)

    # ---- L2: 科创板(688/689) + 北交所(920/43/83/87) 剔除 ----
    stage2 = [c for c in stage1 if not str(c).split('.')[0].startswith(_EXCLUDED_PREFIXES)]
    counts['L2_board'] = len(stage2)

    # ---- L3: ST / 停牌 / 退市（T-1 快照；本地 'ST' 分支含退市风险兜底 close<1 或 circ_mv<5亿）----
    stage3 = filter_stock_by_status(stage2, filter_type=['ST', 'HALT', 'DELISTING'],
                                    query_date=None)
    counts['L3_status'] = len(stage3)

    # ---- L0 完整性补全：float_value / or_yoy / eps 已知（缺失即剔除）----
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

    # ---- L4: 营业收入增长 or_yoy 降序前 10%（max(1, floor(n*10%))）----
    ordered_growth = sorted(stage3c, key=lambda c: (-growth_map[c], float_map[c], c))
    keep_n = max(1, int(len(ordered_growth) * _GROWTH_TOP_PCT))
    stage4 = ordered_growth[:keep_n]
    counts['L4_growth'] = len(stage4)

    # ---- L5: 流通市值升序前 30 ----
    ordered_cap = sorted(stage4, key=lambda c: (float_map[c], c))
    stage5 = ordered_cap[:_SMALLCAP_POOL]
    counts['L5_smallcap'] = len(stage5)

    # ---- L6: 最新 eps > 0（取 30 之后应用 —— R2.5-2 确认，候选可少于 30）----
    stage6 = [c for c in stage5 if eps_map[c] > 0]
    counts['L6_eps'] = len(stage6)

    if not stage6:
        counts['R_selected'] = 0
        return None, counts, 'empty_after_L6'

    # ---- R: MC 动量（fq='pre' include=False 止于 T-1；113 根完整历史方可排名）----
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

    # 确定性排序：总分降序 -> 市值升序 -> 代码升序
    order = sorted(range(len(rank_codes)),
                   key=lambda i: (-scores[i], float_map[rank_codes[i]], rank_codes[i]))
    target = [rank_codes[i] for i in order[:_TARGET_HOLDINGS]]
    counts['R_selected'] = len(target)
    return target, counts, ''

def initialize(context):
    """参数与基准（冻结参数见模块头 REQ-1 声明；滑点由引擎层注入，本源码不设置）。"""
    _ensure_runtime_state()
    set_benchmark('000300.SS')
    g.initialized = True


def before_trading_start(context, data):
    """周门控 + 漏斗选股 + 动量排名（全部 T-1 数据，无当日未来数据）。"""
    _ensure_runtime_state()
    week = _iso_week_key(context.current_dt)
    g.current_week_key = week
    if week == g.last_rebalance_week:
        g.rebalance_due = False
        return
    g.rebalance_due = True
    target, counts, note = _select_weekly_targets(context)
    g.target_list = target
    g.funnel_counts = counts
    g.selection_note = note
    rebalance_id = context.current_dt.strftime('%Y%m%d')
    parts = ''.join(' %s=%s' % (k, counts[k]) for k in sorted(counts.keys()))
    log.info('QS_FUNNEL_AUDIT rebalance_id=%s date=%s%s note=%s'
             % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'), parts,
                note or 'ok'))


def handle_data(context, data):
    """执行层：可交易性预过滤 -> 卖先买后 -> 审计行。"""
    _ensure_runtime_state()
    if not g.rebalance_due:
        return
    rebalance_id = context.current_dt.strftime('%Y%m%d')
    week = g.current_week_key
    g.rebalance_due = False
    g.last_rebalance_week = week  # 消费本周调仓（fail-soft 亦消费，见下）

    if g.target_list is None:
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=0 tradable=0 '
                 'sell_submitted=0 buy_submitted=0 reason=%s'
                 % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'),
                    g.selection_note or 'no_target'))
        log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
                 'cash_ratio=%.4f gross_exposure=%.4f'
                 % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'),
                    len(context.portfolio.positions),
                    (context.portfolio.cash / context.portfolio.total_value
                     if context.portfolio.total_value > 0 else 0.0),
                    (context.portfolio.market_value / context.portfolio.total_value
                     if context.portfolio.total_value > 0 else 0.0)))
        return

    target = list(g.target_list)
    positions_before = list(context.portfolio.positions.keys())

    # tradable = 目标名单中当日有有效 bar（未停牌/有行情）的标的数
    tradable = 0
    for code in target:
        bar = data[code]
        if bar.close > 0 and bar.volume > 0:
            tradable += 1

    # ---- 卖出：不在新名单（T 日 raw bar 预过滤：无bar/停牌跳过、跌停跳过）----
    sell_submitted = 0
    for code in positions_before:
        if code in target:
            continue
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            continue  # 停牌/无当日bar：无法卖出，保留至下一调仓日
        if bar.low_limit > 0 and bar.close <= bar.low_limit:
            continue  # 跌停卖不出（现实条件模拟），保留至下一调仓日
        order_target_value(code, 0)
        sell_submitted += 1

    # ---- 买入：新入选（涨停跳过、资金顺延给下一名候选）----
    buys = [c for c in target if c not in positions_before]
    n_buys = len(buys)
    buy_submitted = 0
    for i, code in enumerate(buys):
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            continue  # 停牌/无bar：不下单
        if bar.high_limit > 0 and bar.close >= bar.high_limit:
            continue  # 涨停买不进：跳过，资金顺延
        remaining = n_buys - i  # 含当前候选的剩余待买数
        tv = context.portfolio.total_value
        cash = context.portfolio.cash
        target_val = min(tv / _TARGET_HOLDINGS, cash / remaining)
        if target_val < _BUY_VALUE_FLOOR:
            continue
        order_target_value(code, target_val)
        buy_submitted += 1

    tv_after = context.portfolio.total_value
    cash_after = context.portfolio.cash
    mv_after = context.portfolio.market_value
    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
             'sell_submitted=%d buy_submitted=%d'
             % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'), len(target),
                tradable, sell_submitted, buy_submitted))
    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
             'cash_ratio=%.4f gross_exposure=%.4f'
             % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'),
                len(context.portfolio.positions),
                (cash_after / tv_after if tv_after > 0 else 0.0),
                (mv_after / tv_after if tv_after > 0 else 0.0)))


def after_trading_end(context, data):
    """日终轻量对账（不承担交易）。"""
    _ensure_runtime_state()
    positions = context.portfolio.positions
    if positions:
        log.debug('%s day_end positions=%d cash=%.2f total_value=%.2f'
                  % (STRATEGY_ID, len(positions), context.portfolio.cash,
                     context.portfolio.total_value))
