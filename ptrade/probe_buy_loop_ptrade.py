# probe_buy_loop_ptrade.py - D4 买入循环逐分支实证（2026-09-09，取证用途）
# 背景：恐慌抄底 PTrade 07-17 selected=50 tradable=50 但 buy_submitted=0
#   note=limit_up_skip_all_low_exposure_legal。BLP 探针显示 data[code] close/high_limit 正常且
#   limit_up=0 → 策略判全涨停矛盾 → 需模拟策略买入循环逐分支计数。
# 目标：
#   B1 持仓视图：context.portfolio.positions.keys()（首次 handle_data 是否有残留键→buys 缩水）；
#   B2 total_value/cash/market_value 实际值；
#   B3 逐分支：close<=0 / high_limit(+close>=hl) / target_val<floor / 正常下单；
#   B4 每分支首个触发样本。
# 用法：PTrade 回测 2026-07-17 单日，回贴 BL- 开头行。

def initialize(context):
    g.done = False

def handle_data(context, data):
    if g.done:
        return
    g.done = True
    day = context.current_dt.strftime('%Y-%m-%d')
    log.info('==== BL-PROBE ' + day + ' ====')

    # B1 持仓视图
    try:
        pos_keys = list(context.portfolio.positions.keys())
        log.info('BL-B1 positions_keys=' + str(len(pos_keys)) + ' sample=' + str(pos_keys[:3]))
    except Exception as exc:
        log.info('BL-B1 ERR ' + str(exc))

    # B2 资金
    try:
        tv = context.portfolio.total_value
        cash = context.portfolio.cash
        log.info('BL-B2 total_value=%s cash=%s' % (tv, cash))
    except Exception as exc:
        log.info('BL-B2 ERR ' + str(exc))

    # B3/B4 买入循环模拟（前 30 只）
    try:
        stocks = get_index_stocks('000905.SS', date=context.previous_date.strftime('%Y%m%d'))
    except Exception as exc:
        log.info('BL-ERR stocks ' + str(exc))
        return
    target = (stocks or [])[:30]
    n_target = len(target)
    BUFFER = 0.03
    FLOOR = 2000.0
    c_bad = 0; c_hl = 0; c_floor = 0; c_ok = 0
    s_bad = []; s_hl = []; s_floor = []; s_ok = []
    for i, code in enumerate(target):
        try:
            bar = data[code]
        except Exception as exc:
            log.info('BL-ERR data[' + code + '] ' + str(exc))
            continue
        close = getattr(bar, 'close', None)
        vol = getattr(bar, 'volume', None)
        hl = getattr(bar, 'high_limit', None)
        if close is None or vol is None or close <= 0 or vol <= 0:
            c_bad += 1
            if len(s_bad) < 3: s_bad.append(code)
            continue
        if hl is not None and hl > 0 and close >= hl:
            c_hl += 1
            if len(s_hl) < 3: s_hl.append(code)
            continue
        try:
            remaining = len(target) - i
            target_val = min(tv / n_target * (1 - BUFFER), cash / remaining)
        except Exception as exc:
            log.info('BL-ERR target_val ' + str(exc) + ' tv=' + repr(tv) + ' cash=' + repr(cash))
            continue
        if target_val < FLOOR:
            c_floor += 1
            if len(s_floor) < 3: s_floor.append(code)
            continue
        c_ok += 1
        if len(s_ok) < 3: s_ok.append(code)
    log.info('BL-B3 bad=%d hl=%d floor=%d ok=%d' % (c_bad, c_hl, c_floor, c_ok))
    log.info('BL-B4 s_bad=%s s_hl=%s s_floor=%s s_ok=%s' % (s_bad, s_hl, s_floor, s_ok))

def after_trading_end(context, data):
    pass
