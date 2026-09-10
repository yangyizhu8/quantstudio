# probe_buy_replica_ptrade.py - D4 买入循环 1:1 复刻（2026-09-10，取证用途）
# 完全复刻恐慌抄底策略买入循环（含 positions_before / tradable 统计 / buys / 逐分支），
# target = get_index_stocks 前 50 只（近似真实 50 只池）。对比真实策略"全 skip"。
# 输出 BR- 开头行。用法：回测 2026-07-17 单日。

BUFFER = 0.03
FLOOR = 2000.0
N_TARGET = 50


def initialize(context):
    g.done = False


def handle_data(context, data):
    if g.done:
        return
    g.done = True
    day = context.current_dt.strftime('%Y-%m-%d')
    try:
        stocks = get_index_stocks('000905.SS', date=context.previous_date.strftime('%Y%m%d'))
    except Exception as exc:
        log.info('BR-ERR stocks ' + str(exc))
        return
    target = (stocks or [])[:N_TARGET]
    log.info('BR-B0 target_n=%d sample=%s' % (len(target), str(target[:3])))

    positions_before = list(context.portfolio.positions.keys())
    n_target = len(target)
    tradable = 0
    for code in target:
        bar = data[code]
        if bar.close > 0 and bar.volume > 0:
            tradable += 1
    log.info('BR-B1 positions_before=%d n_target=%d tradable=%d'
             % (len(positions_before), n_target, tradable))

    buy_submitted = 0
    skipped = []
    buys = [c for c in target if c not in positions_before]
    log.info('BR-B2 buys=%d' % len(buys))
    skip_bad = []
    skip_hl = []
    skip_floor = []
    for i, code in enumerate(buys):
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            skipped.append(code)
            skip_bad.append(code)
            continue
        if bar.high_limit > 0 and bar.close >= bar.high_limit:
            skipped.append(code)
            skip_hl.append(code)
            continue
        remaining = len(buys) - i
        tv = context.portfolio.total_value
        cash = context.portfolio.cash
        target_val = min(tv / n_target * (1 - BUFFER), cash / remaining)
        if target_val < FLOOR:
            skipped.append(code)
            skip_floor.append(code)
            continue
        # 模拟下单（不实际下单，计数）
        buy_submitted += 1
    log.info('BR-B3 buy_submitted=%d skipped=%d' % (buy_submitted, len(skipped)))
    log.info('BR-B4 bad=%d hl=%d floor=%d samples: bad=%s hl=%s floor=%s'
             % (len(skip_bad), len(skip_hl), len(skip_floor),
                str(skip_bad[:3]), str(skip_hl[:3]), str(skip_floor[:3])))


def after_trading_end(context, data):
    pass
