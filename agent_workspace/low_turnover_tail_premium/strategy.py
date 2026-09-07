"""
低流动性溢价换手尾部极值多头（low_turnover_tail_premium）— QuantStudio 本地专用策略

设计契约: agent_workspace/low_turnover_tail_premium/agent_strategy_design.json (design 2.3)
PTrade 转换: 不在本策略范围（由 PyQt "转 PTrade" tab / qs-compile import 承接）

核心逻辑（客户提示词 2026-09-07，R2.5 审核②⑤⑨确认）:
  换手率非线性有效——仅极低换手尾部极值存在稳定 Alpha；中间/高换手区间明确舍弃。

漏斗（每层经济逻辑见设计契约；参数冻结声明 REQ-1，无回测驱动寻优）:
  L0 全A PIT 池（get_Ashares as-of 快照，含窗口内退市股，无幸存者偏差）
     + 基础数据完整性（listed_date / turnover / pe / pb / float_value / roe 缺失即剔除）
  L1 上市满 60 自然日（listed_date=首根K线日口径，T-1 判定，R2.5③）
  L2 剔除科创板(688/689)与北交所(920/43/83/87)（与 get_Ashares 默认排除重复作双保险）
  L3 剔除 ST/停牌/退市（filter_stock_by_status ['ST','HALT','DELISTING']，T-1 快照）
  L4 流动性门槛：T-1 单日成交额 amount >= 100 万元（10万资金/12只~8300元、成交占比<1%，先于 L5，R2.5④）
  L5 尾部定位（核心）：T-1 换手率 turnover_ratio 全A 升序，仅取前 10% 低换手尾部；
     换手率 NaN/缺失剔除，不参与分位排序（R2.5 补充钉死）
  L6 辅助因子交叉（4 因子交集，R2.5⑤）: PE_TTM>0 ∧ PB<=L5后候选池中位数 ∧ 最新ROE>0
     （get_fundamentals profit_ability，PIT 锚 ann_date<=T-1）∧ 流通市值 float_value 升序
  L7 Amihud 共振（R2.5⑥）: 候选池内 20日均 Amihud（|r|/amount，T-1 截止）
     升序前 50%；排序断 tie = 换手升序->Amihud升序->市值升序->代码升序，取前 12
调仓: 月度首个交易日触发；信号全部 T-1（fq='pre' include=False + ann_date<=T-1 + T-1 快照）；
      T 日收盘成交（close 模式强制）；卖先买后同批，卖出所得立即可用。
执行过滤: T 日 raw bar 预过滤（涨停不买/跌停不卖/停牌无 bar 跳过）+ 引擎撮合拒单兜底；
      未成交买单放弃不递补，接受实际仓位偏离（R2.5 补充钉死）。
空池规则（R2.5⑤补充）: L7 后候选 >=8 只按实际数量 n 等权；<8 只本次调仓持币不动（审计行标记），
      禁止放宽筛选凑数。
仓位: runtime_total_value——买入目标 = min(组合总值/n, 可用现金/待买空位数)，1/n 等权；留任不动。
成本（R2.5⑧）: set_commission(commission_ratio=0.0003, min_commission=5) 双边万3最低5元
      + 引擎默认印花税（卖出单向，日期自动切换千1/2023-08-28起万5）+ 过户费万0.1
      + 比例滑点由驱动层注入（主口径 0.001；灵敏度对照 0.002，同源码 hash——策略内不 set_slippage 防覆盖）。
审计: QS_FUNNEL_AUDIT 逐层计数 / QS_REBALANCE_AUDIT / QS_PORTFOLIO_AUDIT（rule 21）。
分段核心校验（R2.5⑨）: R5 附件研究 [0,10%) 尾部 / [45%,55%) 中间 / [90%,100%] 高换手
      同算法 L0-L5+L6（剔除 L7 防 Amihud 与换手同源污染）+ MC 块自助 p 值；不进入本策略源码。
"""

import numpy as np
import pandas as pd

STRATEGY_ID = 'low_turnover_tail_premium'
STRATEGY_NAME = '低流动性溢价换手尾部极值多头'
DESIGN_VERSION = '2.3'

# ---- 参数冻结声明（REQ-1）：全部来自客户提示词或 R2.5 确认，无回测驱动寻优 ----
_TAIL_PCT = 0.10          # 换手率升序前 10% 尾部（R2.5②）
_MIN_AMOUNT = 1e6         # T-1 单日成交额 >= 100 万元（R2.5④）
_LISTED_DAYS = 60         # 次新剔除：上市满 60 自然日（R2.5③）
_TARGET_HOLDINGS = 12     # 目标持仓数（R2.5⑦）
_MIN_HOLD = 8             # 空池下限：候选 <8 只持币（R2.5⑤补充）
_AMIHUD_DAYS = 20         # Amihud 20 日均值
_AMIHUD_KEEP = 0.50       # Amihud 升序前 50%（R2.5⑥）
_BARS_REQUIRED = _AMIHUD_DAYS + 1   # 21 根：20 个收益率 + 对应成交额
_BUFFER = 0.03            # 换仓缓冲带（client 默认 0.03）
_MAX_SINGLE = 0.20        # 单票仓位上限 20%（客户硬约束）
_COMMISSION_RATIO = 0.0003  # 双边万3（R2.5⑧）
_EXCLUDED_PREFIXES = ('688', '689', '920', '43', '83', '87')  # 科创 + 北交
_BUY_VALUE_FLOOR = 1000.0  # 低于此额度的买单跳过（防粉尘订单，非选股参数）


def _ensure_runtime_state():
    """幂等创建全部 g 状态字段（skill 规则：任何回调不得依赖 initialize 成功）。"""
    if not hasattr(g, 'initialized'):
        g.initialized = False
    if not hasattr(g, 'last_rebalance_month'):
        g.last_rebalance_month = None   # (year, month) 上一已消费调仓月份
    if not hasattr(g, 'current_month_key'):
        g.current_month_key = None
    if not hasattr(g, 'rebalance_due'):
        g.rebalance_due = False
    if not hasattr(g, 'target_list'):
        g.target_list = None
    if not hasattr(g, 'target_values'):
        g.target_values = None          # {code: order_target_value}
    if not hasattr(g, 'funnel_counts'):
        g.funnel_counts = None
    if not hasattr(g, 'selection_note'):
        g.selection_note = ''
    if not hasattr(g, 'pending_portfolio_audit_id'):
        g.pending_portfolio_audit_id = None
    if not hasattr(g, 'pool_median_pb'):
        g.pool_median_pb = None


def _month_key(dt):
    """年度-月份元组（月度调仓门控）。"""
    return (int(dt.year), int(dt.month))


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


def _latest_by_code(df, value_field):
    """profit_ability/eps 表逐码取最新有值行：按 (end_date, publ_date) 最大者。

    get_fundamentals(profit_ability) 返回 ann_date<=T 的全部历史行；
    同一 end_date 多次公告（重述）取 publ_date 最新；最新行 value NULL/NaN
    时回退到上一有值报告期（平台「最新有值行」语义对齐）。
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


def _amihud20(closes, amounts):
    """Amihud 非流动性 = mean(|r_t| / amount_t)，20 交易日。

    closes/amounts 为止于 T-1 的长度 >= 21 的序列（时间升序）。
    r_t = close_t / close_{t-1} - 1；对应成交额取 amount_t（T-21..T-1 这 20 个
    |r|/amount 的均值）。与 R5.5 G6 同方法 MC 块自助用于分段研究（策略外）。
    """
    c = np.asarray(closes, dtype=float)
    a = np.asarray(amounts, dtype=float)
    n = c.size
    if n < _AMIHUD_DAYS + 1:
        return np.nan
    c = c[-(_AMIHUD_DAYS + 1):]
    a = a[-_AMIHUD_DAYS:]
    with np.errstate(divide='ignore', invalid='ignore'):
        r = np.abs(c[1:] / c[:-1] - 1.0)
    denom = np.where(a > 0, a, np.nan)
    ratio = r / denom
    valid = np.isfinite(ratio)
    if valid.sum() == 0:
        return np.nan
    return float(np.nanmean(ratio))


def _select_monthly_targets(context):
    """L0->L7 漏斗 + Amihud 共振（全部 T-1 数据）。返回 (target_list or None, counts, note)。"""
    counts = {}
    note = ''

    # ---- L0: 全A PIT 快照 -----
    all_codes = get_Ashares()
    counts['L0_all'] = len(all_codes)

    # ---- L1: 上市日期已知 + 上市满 60 自然日 ----
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
        if (today - listed_date).days < _LISTED_DAYS:
            continue
        stage1.append(code)
    counts['L1_listed'] = len(stage1)

    # ---- L2: 科创板(688/689) + 北交所(920/43/83/87) 剔除 ----
    stage2 = [c for c in stage1 if not str(c).split('.')[0].startswith(_EXCLUDED_PREFIXES)]
    counts['L2_board'] = len(stage2)

    # ---- L3: ST / 停牌 / 退市（T-1 快照）----
    stage3 = filter_stock_by_status(stage2, filter_type=['ST', 'HALT', 'DELISTING'],
                                    query_date=None)
    counts['L3_status'] = len(stage3)

    if not stage3:
        counts['L4_amount'] = 0
        counts['L5_tail'] = 0
        counts['L6_cross'] = 0
        counts['L7_amihud'] = 0
        counts['R_selected'] = 0
        return None, counts, 'empty_after_L3'

    # ---- L4: T-1 单日成交额 >= 100 万 + 换手率/估值（valuation PIT，T-1）----
    val_df = get_fundamentals_batch(stage3, 'valuation',
                                    fields=['turnover_ratio', 'pe_ttm', 'pb_ratio', 'float_value'])
    val = {}
    if val_df is not None and len(val_df) > 0:
        for code_raw, row in val_df.iterrows():
            code = str(code_raw)
            try:
                val[code] = {
                    'turnover': float(row['turnover_ratio']) if row['turnover_ratio'] == row['turnover_ratio'] else np.nan,
                    'pe_ttm': float(row['pe_ttm']) if row['pe_ttm'] == row['pe_ttm'] else np.nan,
                    'pb': float(row['pb_ratio']) if row['pb_ratio'] == row['pb_ratio'] else np.nan,
                    'float_value': float(row['float_value']) if row['float_value'] == row['float_value'] else np.nan,
                }
            except (KeyError, TypeError, ValueError):
                pass
    amount_map = {}
    try:
        amt_hist = get_history_batch(stage3, 1, '1d', fields=['amount'], fq='pre', include=False)
        for code in stage3:
            item = amt_hist.get(code) if amt_hist is not None else None
            if item is None:
                continue
            arr = _extract_history_field(item, 'amount')
            if arr.size >= 1 and np.isfinite(arr[-1]) and arr[-1] > 0:
                amount_map[code] = float(arr[-1])
    except Exception:
        amount_map = {}
    stage4 = []
    for code in stage3:
        v = val.get(code)
        if v is None:
            continue
        if not np.isfinite(v['turnover']) or not np.isfinite(v['float_value']):
            continue
        amt = amount_map.get(code, np.nan)
        if not np.isfinite(amt) or amt < _MIN_AMOUNT:
            continue
        stage4.append(code)
    counts['L4_amount'] = len(stage4)

    if not stage4:
        counts['L5_tail'] = 0
        counts['L6_cross'] = 0
        counts['L7_amihud'] = 0
        counts['R_selected'] = 0
        return None, counts, 'empty_after_L4'

    # ---- L5: 换手率升序前 10% 尾部（核心定位；NaN 已在 L4 剔除）----
    ordered_turn = sorted(stage4, key=lambda c: (val[c]['turnover'], c))
    keep_n = max(1, int(len(ordered_turn) * _TAIL_PCT))
    stage5 = ordered_turn[:keep_n]
    counts['L5_tail'] = len(stage5)

    if not stage5:
        counts['L6_cross'] = 0
        counts['L7_amihud'] = 0
        counts['R_selected'] = 0
        return None, counts, 'empty_after_L5'

    # ---- L6: 辅助因子交叉（4 因子交集；PB 中位数分母 = L5 后候选池，T-1 当期）----
    pb_vals = np.array([val[c]['pb'] for c in stage5], dtype=float)
    pb_vals = pb_vals[np.isfinite(pb_vals) & (pb_vals > 0)]
    pb_median = float(np.median(pb_vals)) if pb_vals.size else np.nan
    g.pool_median_pb = pb_median

    roe_df = get_fundamentals(stage5, 'profit_ability', fields=['roe', 'end_date', 'publ_date'])
    roe_map = _latest_by_code(roe_df, 'roe')

    stage6 = []
    for code in stage5:
        v = val[code]
        pe_ok = np.isfinite(v['pe_ttm']) and v['pe_ttm'] > 0
        pb_ok = (np.isfinite(v['pb']) and v['pb'] > 0 and
                 np.isfinite(pb_median) and v['pb'] <= pb_median)
        roe_val = roe_map.get(code, np.nan)
        roe_ok = np.isfinite(roe_val) and roe_val > 0
        if pe_ok and pb_ok and roe_ok:
            stage6.append(code)
    counts['L6_cross'] = len(stage6)

    if not stage6:
        counts['L7_amihud'] = 0
        counts['R_selected'] = 0
        return None, counts, 'empty_after_L6'

    # ---- L7: Amihud 共振（20日均 |r|/amount 升序前 50%）----
    hist = get_history_batch(stage6, _BARS_REQUIRED, '1d', fields=['close', 'amount'],
                             fq='pre', include=False)
    amihud_map = {}
    for code in stage6:
        item = hist.get(code) if hist is not None else None
        if item is None:
            continue
        closes = _extract_history_field(item, 'close')
        amounts = _extract_history_field(item, 'amount')
        if closes.size < _BARS_REQUIRED or amounts.size < _BARS_REQUIRED:
            continue
        if not np.all(np.isfinite(closes)) or np.any(closes <= 0):
            continue
        a = _amihud20(closes, amounts)
        if np.isfinite(a) and a > 0:
            amihud_map[code] = a
    counts['L7_amihud'] = len(amihud_map)

    if not amihud_map:
        counts['R_selected'] = 0
        return None, counts, 'empty_after_L7'

    ordered_amihud = sorted(amihud_map, key=lambda c: (amihud_map[c], c))
    keep_a = max(1, int(len(ordered_amihud) * _AMIHUD_KEEP))
    stage7 = ordered_amihud[:keep_a]
    counts['L7a_keep'] = len(stage7)

    # ---- R: 排序断 tie（换手升序->Amihud升序->市值升序->代码升序）取前 12 ----
    order_key = lambda c: (val[c]['turnover'], amihud_map[c], val[c]['float_value'], c)
    ranked = sorted(stage7, key=order_key)
    target = ranked[:_TARGET_HOLDINGS]
    counts['R_selected'] = len(target)
    return target, counts, ''


def initialize(context):
    """参数、基准、成本（冻结参数见模块头 REQ-1 声明；成本口径 R2.5⑧）。"""
    _ensure_runtime_state()
    set_benchmark('000300.SS')
    set_commission(commission_ratio=_COMMISSION_RATIO, min_commission=5.0, type='STOCK')
    # 滑点（主口径 0.1%，R2.5⑧）由驱动层注入（A=0.001/B=0.002 灵敏度对照，同源码 hash）
    g.initialized = True


def before_trading_start(context, data):
    """月度门控 + 漏斗选股（全部 T-1 数据，无当日未来数据）。"""
    _ensure_runtime_state()
    month = _month_key(context.current_dt)
    g.current_month_key = month
    if month == g.last_rebalance_month:
        g.rebalance_due = False
        return
    g.rebalance_due = True
    target, counts, note = _select_monthly_targets(context)
    g.target_list = target
    g.funnel_counts = counts
    g.selection_note = note
    rebalance_id = context.current_dt.strftime('%Y%m%d')
    parts = ''.join(' %s=%s' % (k, counts[k]) for k in sorted(counts.keys()))
    log.info('QS_FUNNEL_AUDIT rebalance_id=%s date=%s%s note=%s'
             % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'), parts,
                note or 'ok'))


def handle_data(context, data):
    """执行层：可交易性预过滤 -> 卖先买后 -> 审计行（未成交不递补）。"""
    _ensure_runtime_state()
    if not g.rebalance_due:
        return
    rebalance_id = context.current_dt.strftime('%Y%m%d')
    month = g.current_month_key
    g.rebalance_due = False
    g.last_rebalance_month = month  # 消费本月调仓（fail-soft 亦消费）

    if g.target_list is None:
        log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=0 tradable=0 '
                 'sell_submitted=0 buy_submitted=0 reason=%s'
                 % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'),
                    g.selection_note or 'no_target'))
        g.pending_portfolio_audit_id = rebalance_id
        return

    target = list(g.target_list)
    positions_before = list(context.portfolio.positions.keys())

    # tradable = 目标名单中当日有有效 bar 的标的数
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
            continue
        if bar.low_limit > 0 and bar.close <= bar.low_limit:
            continue
        order_target_value(code, 0)
        sell_submitted += 1

    # ---- 买入：新入选按实际 n 等权（涨停跳过不递补；R2.5 补充钉死）----
    buys = [c for c in target if c not in positions_before]
    n_buys = len(buys)
    n_total = len(target) if len(target) >= _MIN_HOLD else 0
    buy_submitted = 0
    for i, code in enumerate(buys):
        if n_total == 0:
            break
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            continue
        if bar.high_limit > 0 and bar.close >= bar.high_limit:
            continue  # 涨停买不进：放弃不递补
        remaining = n_buys - i
        tv = context.portfolio.total_value
        cash = context.portfolio.cash
        target_val = min(tv / n_total * (1 - _BUFFER), cash / remaining)
        if target_val < _BUY_VALUE_FLOOR:
            continue
        order_target_value(code, target_val)
        buy_submitted += 1

    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
             'sell_submitted=%d buy_submitted=%d'
             % (rebalance_id, context.current_dt.strftime('%Y-%m-%d'), len(target),
                tradable, sell_submitted, buy_submitted))
    g.pending_portfolio_audit_id = rebalance_id


def after_trading_end(context, data):
    """日终对账：输出 QS_PORTFOLIO_AUDIT（撮合后真实持仓——引擎权威口径）。

    rule 21 要求组合审计与 rebalance 一一对应：本函数在调仓日（g.last_rebalance_audit
    非 None）输出当日 PORTFOLIO_AUDIT；非调仓日仅轻量 debug。持仓以 engine 撮合后
    context.portfolio 为准（handle_data 内为撮合前快照，不可用于部署不变量）。
    """
    _ensure_runtime_state()
    rid = getattr(g, 'pending_portfolio_audit_id', None)
    if rid is not None:
        g.pending_portfolio_audit_id = None  # 消费
        tv = context.portfolio.total_value
        cash = context.portfolio.cash
        mv = context.portfolio.market_value
        log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
                 'cash_ratio=%.4f gross_exposure=%.4f'
                 % (rid, context.current_dt.strftime('%Y-%m-%d'),
                    len(context.portfolio.positions),
                    (cash / tv if tv > 0 else 0.0),
                    (mv / tv if tv > 0 else 0.0)))