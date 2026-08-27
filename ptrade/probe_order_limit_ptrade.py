# probe_order_limit_ptrade.py - 市价单/委托股数上限边界测定探针（D4-S2 取证）
# 用途：在真实 PTrade 平台测定各板块（深主板/创业板/沪主板/科创板）的单笔委托股数上限
#      与超限处置方式（整单取消 vs 自动拆单 vs 降量），钉死 D4-S2 规格。
# 用法：PTrade 新建策略 → 回测区间 2026-07-01 ~ 2026-07-31，**初始资金建议 ≥ 1 亿**
#      （测 500,000 股×高价股需要充足资金，避免"资金不足降量"混入干扰判定），基准沪深300。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。
#
# 判定表（对照平台日志）：
#   "生成订单 ... 数量：买入N股"           → 该档位通过
#   "WARNING - 市价单下单超过...50000股限制，取消下单" → 该档位超限，整单取消
#   "WARNING - 当前账户资金不足，调整...为N"  → 资金不足降量（应避免出现：资金要充足）
#   "调整...下单数量" 其他形态            → 平台其他自动处置，记录原文
#
# 预期结论：各板块 阈值档位（如 50,000 / 100,000 / 150,000 / 300,000）+ 处置方式。


def initialize(context):
    set_benchmark('000300.SS')
    g.sent = False
    # 档位表：每档位配一只测试标的（保证在回测区间内可交易、非退市、非停牌）
    # 注：若某标的停牌/退市导致无法判定，可换同板块其他代码。
    g.tests = [
        # (板块标签, 代码, 档位股数)
        ("SH-MAIN",   '600000.SS', 50000),   # 沪主板
        ("SH-MAIN",   '600009.SS', 51000),   # 沪主板 略超
        ("SH-MAIN",   '600036.SS', 100000),  # 沪主板
        ("SH-MAIN",   '600519.SS', 150000),  # 沪主板（高价，资金充足才测得动）
        ("SZ-MAIN",   '000001.SZ', 50000),   # 深主板
        ("SZ-MAIN",   '000002.SZ', 51000),   # 深主板 略超
        ("SZ-MAIN",   '000651.SZ', 100000),  # 深主板
        ("SZ-MAIN",   '000858.SZ', 150000),  # 深主板
        ("SZ-MAIN-2", '002415.SZ', 300000),  # 深主板(002)
        ("CHINEXT",   '300750.SZ', 50000),   # 创业板
        ("CHINEXT",   '300059.SZ', 51000),   # 创业板 略超
        ("CHINEXT",   '300015.SZ', 100000),  # 创业板
        ("STAR",      '688981.SS', 50000),   # 科创板
        ("STAR",      '688111.SS', 51000),   # 科创板 略超
    ]
    run_daily(context, send_orders, time='9:31')


def send_orders(context):
    if g.sent:
        return
    g.sent = True
    for tag, code, amount in g.tests:
        try:
            order(security=code, amount=amount)
            log.info("PROBE-ORD %s %s amount=%d" % (tag, code, amount))
        except Exception as exc:
            log.info("PROBE-ORD-FAIL %s %s amount=%d err=%s" % (tag, code, amount, exc))


def after_trading_end(context, data):
    cash = float(getattr(context.portfolio, 'cash', 0) or 0)
    pos = {}
    try:
        for code, p in context.portfolio.positions.items():
            amt = getattr(p, 'amount', 0) or 0
            if amt and amt > 0:
                pos[str(code)] = float(amt)
    except Exception:
        pass
    log.info("PROBE-SNAP day=%s cash=%.2f positions=%s" % (context.current_dt.date(), cash, pos))


def handle_data(context, data):
    pass