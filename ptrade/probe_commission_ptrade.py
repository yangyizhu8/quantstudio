# probe_commission_ptrade.py - PTrade 平台佣金模型实证探针（A1 前置门）
# 用途：钉死 PTrade 回测的佣金计费（是否含"最低 X 元/笔"），为订单拆单 A1 提供费用决策输入。
# 判定逻辑（利用 platform 持仓成本价=含手续费买入均价的语义，interface-contract B.3）：
#   fee_est = (avg_cost - last) × amount —— 首日一次性买入后推导单笔手续费。
#   - 若小额单 fee_est < 5 元（如 0.08 元）→ 平台无最低佣金（按费率实收）；
#   - 若小额单 fee_est ≈ 5.00 元 → 平台有最低佣金 5 元/笔（与本地 min_commission=5.0 一致）；
#   - 若 fee_est 介于两者且随金额非线性 → 平台有其他费用结构，需按实测值归因。
# 用法：PTrade 新建策略 → 回测区间 2026-07-01 ~ 2026-07-31，初始资金 100000，基准沪深300。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

TARGETS = [
    # (标签, 代码, 股数) —— 各档位 100 股，价格自然形成 300~4000 元档
    ("LOW-1",   '601398.SS', 100),   # 低价档（~5 元级）
    ("LOW-2",   '600000.SS', 100),   # 中低价档（~9 元级）
    ("MID-1",   '600036.SS', 100),   # 中价档（~35 元级）
    ("MID-2",   '600519.SS', 100),   # 高价档（~1400 元级）
    ("MID-3",   '000858.SZ', 100),   # 中高价档（~130 元级）
]


def initialize(context):
    set_benchmark('000300.SS')
    g.sent = False
    run_daily(context, send_orders, time='9:31')


def send_orders(context):
    if g.sent:
        return
    g.sent = True
    for tag, code, amount in TARGETS:
        try:
            order(security=code, amount=amount)
            log.info("PROBE-ORD %s %s amount=%d" % (tag, code, amount))
        except Exception as exc:
            log.info("PROBE-ORD-FAIL %s %s err=%s" % (tag, code, exc))


def after_trading_end(context, data):
    day = context.current_dt.date()
    cash = float(getattr(context.portfolio, 'cash', 0) or 0)
    # 推导每只持仓的手续费：fee_est = (avg_cost - last) × amount
    for code, pos in context.portfolio.positions.items():
        amt = float(getattr(pos, 'amount', 0) or 0)
        if amt <= 0:
            continue
        avg_cost = float(getattr(pos, 'avg_cost', 0) or 0)
        last = float(getattr(pos, 'last_sale_price', 0)
                     or getattr(pos, 'price', 0) or 0)
        fee_est = (avg_cost - last) * amt if last > 0 else float('nan')
        log.info("PROBE-FEE day=%s code=%s amount=%s avg_cost=%.6f last=%.6f fee_est=%.4f"
                 % (day, code, amt, avg_cost, last, fee_est))
    log.info("PROBE-SNAP day=%s cash=%.2f positions_n=%d"
             % (day, cash, len(context.portfolio.positions)))


def handle_data(context, data):
    pass