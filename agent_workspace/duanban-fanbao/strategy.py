# 断板反包策略_ptrade.py - 由 QuantStudio source_import 转换生成
# 来源: 断板反包策略.py
# profile: ptrade-default (ptrade_profile_version 1.1.0-source-import)
# 已知差异:
# - get_history: get_history 签名 A→B（count-first，PTrade 契约；本地双签名兼容）
# - get_history: get_history 签名 A→B（count-first，PTrade 契约；本地双签名兼容）
# PTRADE_RUNTIME_UNVERIFIED: 真实券商平台行为未验证，部署前须人工冒烟。


# -*- coding: utf-8 -*-
"""断板反包策略（broken_board_reversal）— QuantStudio 本地专用回测策略。

形态（时序严格 T-2 → T-1 → T）：
  T-2 涨停封板（首板或连板均可）
  T-1 断板：不再涨停，收放量阴线（烂板回落/高开低走）
  T   反包：收阳线，收盘价 ≥ T-1 阴线开盘价，量能在 T-1 量 0.8~1.2 倍带内

硬性过滤：无量一字板炸板剔除 / T-1 涨跌幅 [-8%,-3%] / 量能带 / T-2~T 三日站上 MA20 /
流动性（T-1 成交额 ≥ 5000 万元）/ T 日收盘涨停弃买 / 除权窗口弃号（宁缺勿假）。

交易：T 日收盘确认信号并以收盘价买入（close 模式），T+2 日收盘卖出；
最多同时持有 2 只、单只目标仓位 50%（runtime_total_value）；对标基准 000852（中证1000）。

参数冻结（R0 客户裁决，禁止按回测结果回调）：
  VOL_RATIO 0.8/1.2 · DROP_BAND [-8,-3] · NO_VOL_ONEWORD 0.5 · LIQ_AMT 5e7 · HOLD_DAYS 2

targets: quantstudio 本地专用（不声明 PTrade 可移植性；PTrade 转换由 PyQt tab/CLI 承接）。
"""
import numpy as np

def initialize(context):
    _ensure_runtime_state()
    set_benchmark(INDEX_CODE)
    log.info('断板反包策略 initialized: benchmark=%s hold_days=%d max_holdings=%d '
             'per_position=%.2f' % (INDEX_CODE, HOLD_DAYS, MAX_HOLDINGS,
                                    PER_POSITION_WEIGHT))


def before_trading_start(context, data):
    _ensure_runtime_state()
    members = get_index_stocks(INDEX_CODE)          # 回测注入当日，PIT 月度 complete 快照
    members = sorted(members) if members else []
    # 状态硬过滤（执行层边界，非形态规则）：默认 ST/HALT/DELISTING
    filtered = filter_stock_by_status(members)
    g.universe = sorted(filtered) if filtered else []
    log.debug('universe %d -> %d after status filter' % (len(members), len(g.universe)))


def handle_data(context, data):
    _ensure_runtime_state()
    today = context.current_dt.strftime('%Y-%m-%d')
    funnel = {'scanned': 0, 'e0': 0, 'e1': 0, 'e2': 0, 'e3': 0, 'e4': 0, 'e5': 0,
              'e6': 0, 'e7': 0, 'e8': 0, 'e9': 0}

    # ---- 市场基准日（000852 指数前两交易日，停牌缺口防御锚）----
    mkt = get_history(2, frequency='1d', field=['trade_date'], security_list=INDEX_CODE, fq='pre', include=False, is_dict=True)
    mkt_values = list(mkt.values()) if mkt else []
    if not mkt_values:
        return
    mkt_td = _extract_history_field(mkt_values[0], 'trade_date', dtype=str)
    if mkt_td.shape[0] < 2:
        return
    t1_date, t2_date = str(mkt_td[-1])[:10], str(mkt_td[-2])[:10]

    # ---- 批量取史（前复权信号基准；include=False 截至 T-1）----
    hist = get_history(HIST_COUNT, frequency='1d', field=FIELDS, security_list=g.universe, fq='pre', include=False, is_dict=True)
    if not hist:
        return

    candidates = []
    for code in g.universe:
        if code in g.holdings:                       # E10 已持仓忽略（不加仓）
            continue
        item = hist.get(code)
        if item is None:
            continue
        funnel['scanned'] += 1
        try:
            arr_open = _extract_history_field(item, 'open')      # front（前复权）
            arr_high = _extract_history_field(item, 'high')      # front
            arr_close = _extract_history_field(item, 'close')    # front
            arr_vol = _extract_history_field(item, 'volume')     # raw
            arr_amt = _extract_history_field(item, 'amount')     # raw（元）
            arr_pct = _extract_history_field(item, 'pctChg')     # 百分比
            arr_prec = _extract_history_field(item, 'preClose')  # raw
            arr_td = _extract_history_field(item, 'trade_date', dtype=str)
        except Exception:
            continue
        # ---- E0 数据完整性：≥22 根、日期对齐市场基准日、T 日 bar 有效 ----
        if arr_close.shape[0] < 22:
            continue
        bar = data[code]
        if (str(arr_td[-1])[:10] != t1_date or str(arr_td[-2])[:10] != t2_date
                or bar.volume <= 0 or bar.close <= 0 or bar.open <= 0):
            continue
        if np.isnan(arr_close[-21:]).any() or np.isnan(arr_pct[-2:]).any():
            continue
        funnel['e0'] += 1

        pct2, pct1 = float(arr_pct[-2]), float(arr_pct[-1])
        vol2, vol1 = float(arr_vol[-2]), float(arr_vol[-1])
        amt1 = float(arr_amt[-1])
        prec1 = float(arr_prec[-1])
        limit = _limit_pct(code)

        # ---- E1 T-2 涨停封板（pctChg ≥ 板块幅度 − 0.1pct，收盘封板近似）----
        if not (pct2 >= limit * 100.0 - 0.1):
            continue
        funnel['e1'] += 1

        # ---- E2 T-1 断板放量阴线：跌幅带 + 收阴 + 较涨停日放量 ----
        if not (DROP_MIN_PCT <= pct1 <= DROP_MAX_PCT
                and arr_close[-1] < arr_open[-1]
                and vol1 > vol2):
            continue
        funnel['e2'] += 1

        # ---- E3 无量一字板炸板剔除 ----
        f1 = float(arr_close[-1]) / (prec1 * (1.0 + pct1 / 100.0))   # T-1 复权因子
        close_raw1 = prec1 * (1.0 + pct1 / 100.0)                    # T-1 原始收盘
        open_raw1 = float(arr_open[-1]) / f1                         # T-1 原始开盘
        high_limit1 = round(prec1 * (1.0 + limit), 2)                # T-1 涨停价
        if (open_raw1 >= high_limit1 - 0.01
                and close_raw1 < high_limit1
                and vol1 < NO_VOL_ONEWORD_RATIO * vol2):
            continue
        funnel['e3'] += 1

        # ---- E4 T 反包阳线：收盘 ≥ 开盘 且 收盘 ≥ T-1 开盘（原始价）----
        if not (bar.close >= bar.open and bar.close >= open_raw1):
            continue
        funnel['e4'] += 1

        # ---- E5 T 量能带 [0.8, 1.2] × volume(T-1) ----
        if not (VOL_RATIO_MIN * vol1 <= bar.volume <= VOL_RATIO_MAX * vol1):
            continue
        funnel['e5'] += 1

        # ---- E6 MA20 三日过滤（前复权收盘、含当日滚动）----
        closes = arr_close[-21:]                                     # T-21..T-1
        ma_t2 = float(np.mean(closes[:20]))                          # T-21..T-2
        ma_t1 = float(np.mean(closes[1:]))                           # T-20..T-1
        # T 为除权日时 E9 已弃号；无除权则 front 连续：close_front(T)=close_front(T-1)×close_T/preClose_T
        close_front_t = float(closes[-1]) * bar.close / bar.preclose
        ma_t0 = (float(np.sum(closes[2:])) + close_front_t) / 20.0   # T-19..T
        if not (float(closes[-2]) > ma_t2 and float(closes[-1]) > ma_t1
                and close_front_t > ma_t0):
            continue
        funnel['e6'] += 1

        # ---- E7 流动性：T-1 成交额 ≥ 5000 万元 ----
        if not (amt1 >= LIQ_AMT_MIN):
            continue
        funnel['e7'] += 1

        # ---- E8 T 日收盘涨停 → 放弃买入（买不进按现实模拟）----
        if bar.close >= bar.high_limit - 0.001:
            continue
        funnel['e8'] += 1

        # ---- E9 除权防御（宁缺勿假）：量比/反包比较跨除权即弃 ----
        f2 = float(arr_close[-2]) / (float(arr_prec[-2]) * (1.0 + pct2 / 100.0))
        if (abs(f2 - f1) > F_TOL
                or abs(bar.preclose - close_raw1) > 0.001 * close_raw1):
            continue
        funnel['e9'] += 1

        # ---- 完全反包标记（排序优先），进入候选 ----
        fully_covered = bar.close >= float(arr_high[-2]) / f1        # T 收 ≥ T-1 最高
        candidates.append((0 if fully_covered else 1, -amt1, code))

    # ---- 排序：完全反包优先 → T-1 成交额降序 → code 升序（确定性）----
    candidates.sort()

    # ---- 先卖（T+2 到期，close 即时撮合，卖出款同周期可用）----
    g.rebalance_seq += 1
    rid = 'bbrev-%s-%04d' % (today.replace('-', ''), g.rebalance_seq)
    sell_submitted = 0
    for code in sorted(g.holdings.keys()):
        if g.holdings[code]['days_held'] >= HOLD_DAYS:
            order_target_value(code, 0)
            sell_submitted += 1
            pos = get_position(code)
            if getattr(pos, 'amount', 0) <= 0:
                g.holdings.pop(code, None)             # X3 对账：已清仓移除账本

    # ---- 后买（最多补满 2 只，runtime_total_value × 0.5；P1 设计契约）----
    # D4-S6 框架修复已落定（ptrade_api 接线层换算价=②层当日撮合价，2026-08-27），
    # order_target_value 现按当日收盘精确核算，回归标准实现（不再需要显式股数自保）。
    buy_submitted = 0
    slots = MAX_HOLDINGS - len(g.holdings)
    for _rank, _neg_amt, code in candidates:
        if buy_submitted >= slots:
            break
        target_value = context.portfolio.total_value * PER_POSITION_WEIGHT
        order_target_value(code, target_value)
        buy_submitted += 1
        g.holdings[code] = {'buy_dt': today, 'days_held': 0}
        pos = get_position(code)
        if getattr(pos, 'amount', 0) <= 0:              # 受理未成交（如边界拒单）回滚账本
            g.holdings.pop(code, None)
            buy_submitted -= 1

    # ---- 审计行（R5 部署不变量 + 信号漏斗，rebalance_id 1:1）----
    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
             'sell_submitted=%d buy_submitted=%d'
             % (rid, today, len(candidates), funnel['e9'],
                sell_submitted, buy_submitted))
    tv = context.portfolio.total_value
    cash_ratio = context.portfolio.cash / tv if tv > 0 else 0.0
    gross = context.portfolio.market_value / tv if tv > 0 else 0.0
    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
             'cash_ratio=%.4f gross_exposure=%.4f'
             % (rid, today, len(g.holdings), cash_ratio, gross))
    log.info('QS_SIGNAL_AUDIT date=%s scanned=%d e0=%d e1=%d e2=%d e3=%d e4=%d '
             'e5=%d e6=%d e7=%d e8=%d e9=%d signals=%d buys=%d sells=%d'
             % (today, funnel['scanned'], funnel['e0'], funnel['e1'], funnel['e2'],
                funnel['e3'], funnel['e4'], funnel['e5'], funnel['e6'], funnel['e7'],
                funnel['e8'], funnel['e9'], len(candidates), buy_submitted,
                sell_submitted))


def after_trading_end(context, data):
    _ensure_runtime_state()
    for code in sorted(g.holdings.keys()):
        g.holdings[code]['days_held'] += 1
        pos = get_position(code)
        if getattr(pos, 'amount', 0) <= 0:              # 外部强平/退市对账清理
            g.holdings.pop(code, None)
