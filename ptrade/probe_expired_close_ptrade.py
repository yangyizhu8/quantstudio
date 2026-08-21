# probe_expired_close_ptrade.py - 退市强平(is expired)成交明细取证探针
# 用途：在真实 PTrade 平台回测中复现 vol_regime_mom_rev 反转月持仓，捕获
#       "XXX.XSHE is expired, close all positions by system" 强平的成交价/日期，
#       为 D4-S3 引擎对齐规格钉价（当日收盘价 vs 最后成交价 vs 均价）。
# 用法：PTrade 新建策略 → 回测区间 2026-07-01 ~ 2026-07-31，初始资金 100000，基准沪深300。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途，非生产策略；平台 API 差异以实际运行为准。
#
# 说明：
#   - 07-01 直接买入上一轮实测被选中的 5 只反转标的（其中 3 只会在持有期内退市强平）。
#   - 每日 after_trading_end 打印：持仓快照 + 现金 + 当日平台成交（get_trades 尽力而为）。
#   - 检测到持仓消失日 → 打印消失前后现金差，推算强平价 = Δcash / 股数（与成交明细互证）。


def initialize(context):
    set_benchmark('000300.SS')
    g.prev_positions = {}          # code -> {"amount":.., "cost":..}
    g.prev_cash = None
    g.bought = False
    g.probe_codes = ['000004.SZ', '002808.SZ', '002898.SZ', '300029.SZ', '300854.SZ']
    run_daily(context, probe_rebalance, time='9:31')


def probe_rebalance(context):
    """首日按固定股数买入探针池（避开市价单 5 万股上限对取证的干扰）。"""
    if g.bought:
        return
    g.bought = True
    for code in g.probe_codes:
        try:
            order(security=code, amount=10000)   # 每只 1 万股，稳定建仓且低于市价单上限
            log.info("PROBE-BUY %s amount=10000" % (code,))
        except Exception as exc:
            log.info("PROBE-BUY-FAIL %s err=%s" % (code, exc))


def _pos_snapshot(context):
    """持仓快照 {code: {"amount":.., "cost":.., "last":..}}"""
    snap = {}
    try:
        for code, pos in context.portfolio.positions.items():
            try:
                amt = getattr(pos, 'amount', 0) or 0
            except Exception:
                amt = getattr(pos, 'volume', 0) or 0
            if amt and amt > 0:
                snap[str(code)] = {
                    "amount": float(amt),
                    "cost": float(getattr(pos, 'avg_cost', 0) or 0),
                    "last": float(getattr(pos, 'last_sale_price', 0)
                                  or getattr(pos, 'price', 0) or 0),
                }
    except Exception as exc:
        log.warning("pos snapshot failed: %s" % (exc,))
    return snap


def after_trading_end(context, data):
    cur = _pos_snapshot(context)
    cash = float(getattr(context.portfolio, 'cash', 0) or 0)
    day = context.current_dt.date()

    # 1) 检测持仓消失 → 强烈提示被平台强平/退市
    for code, prev in (g.prev_positions or {}).items():
        if code not in cur:
            prev_cash = g.prev_cash if g.prev_cash is not None else cash
            delta = cash - prev_cash
            implied = (delta / prev['amount']) if prev.get('amount', 0) > 0 else 0.0
            log.info("PROBE-EXPIRED %s day=%s prev=%s delta_cash=%.4f implied_px=%.6f"
                     % (code, day, prev, delta, implied))

    # 2) 当日平台成交尽力而为（get_trades 可能因平台版本不可用，异常兜底）
    try:
        trades = get_trades()
        if trades:
            log.info("PROBE-TRADES day=%s n=%d" % (day, len(trades)))
            for t in trades:
                try:
                    log.info("  TRADE %s side=%s price=%s amount=%s time=%s raw=%s" % (
                        getattr(t, 'side', getattr(t, 'type', '?')),
                        getattr(t, 'price', '?'),
                        getattr(t, 'amount', getattr(t, 'volume', '?')),
                        getattr(t, 'time', '?'),
                        t,
                    ))
                except Exception:
                    log.info("  TRADE raw=%s" % (t,))
    except Exception as exc:
        log.info("PROBE get_trades unavailable: %s" % (exc,))

    # 3) 每日快照（现金 + 持仓）
    log.info("PROBE-SNAP day=%s cash=%.2f positions=%s" % (day, cash, cur))

    g.prev_positions = cur
    g.prev_cash = cash


def handle_data(context, data):
    pass