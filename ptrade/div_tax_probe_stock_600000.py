"""
div_tax_probe_stock_600000.py — PTrade 平台分红税口径实证（股票：600000 浦发银行）

验证目标：确定 PTrade 平台对**股票现金分红**的入账系数（本地引擎假设为 0.8 = 税前 × 20% 短持红利税）。
本策略买入 600000 并持有跨越 2026-07-16 除息日（税前 0.42 元/股），逐日打印分析数据。

【回测设置】初始资金 100,000；区间 2026-07-01 ~ 2026-07-31（必须覆盖 07-16）；日线频率。

【除息日事实】600000 2026-07-16 除息：每股税前 0.42 元（tushare stock_dividend 实测）。
除息日 preClose = 前收 − 0.42；持仓市值当天跌 vol×0.42。

【判断方法（用 Log.txt 的 total_value 序列）】
  除息日净值缺口 = 1 − nav(07-16)/nav(07-15)（剔除当日市场涨跌后，见下）
  三种口径的理论净值缺口（持仓市值占比 ≈ 缺口/(vol×0.42)）：
    A. 不入账（系数 0）：缺口 = vol×0.42           → 占比 100%
    B. 0.8 入账（本地假设）：缺口 = vol×0.42×0.20   → 占比 20%（净值 ≈ 跌 0.17%，0.42/49.6×20%）
    C. 1.0 入账（免税/全额）：缺口 ≈ 0              → 占比 0%
  剔除市场涨跌：当日 pctChg = (close−preClose)/preClose；净值变化率 − pctChg ≈ 分红缺口占比。
  也可顺带观察持仓明细"持仓成本价"在 07-16 是否下降 0.42（本地引擎会 avg_cost −= div）。

【结果回传】回测完成后在平台导出：交易详情 CSV + 持仓明细 CSV + Log.txt（与既有样本同样方式），
本地用 Log.txt 的 total_value 序列做上述计算，与本地引擎 0.8 口径对比。
"""


def initialize(context):
    g.target = '600000.SS'   # 浦发银行（沪市，07-16 除息 0.42 元/股）
    g.bought = False
    set_universe([g.target])
    # 佣金对齐本地 DEFAULT_TRADE_COST（万3.5 / 最低 5 元）；除息日分析不受佣金影响
    set_commission(commission_ratio=0.00035, min_commission=5.0, type="STOCK")


def handle_data(context, data):
    # 第一天满仓买入，之后持有不动（跨越除息日）
    if not g.bought:
        order_target_value(g.target, context.portfolio.total_value)
        g.bought = True
        # PTrade log.info 不支持 printf 风格参数（会原样打印 %s），必须调用前拼接
        log.info("DIVTAX 买入 " + g.target + " target_value=" + str(round(context.portfolio.total_value, 2)))

    # 逐日打印分析数据（Log.txt 全量保留）
    pos = get_position(g.target)
    if pos is None:
        log.info("DIVTAX " + str(context.current_dt)[:10]
                 + " total=" + str(round(context.portfolio.total_value, 2))
                 + " cash=" + str(round(context.portfolio.cash, 2))
                 + " pos=None")
    else:
        log.info("DIVTAX " + str(context.current_dt)[:10]
                 + " total=" + str(round(context.portfolio.total_value, 2))
                 + " cash=" + str(round(context.portfolio.cash, 2))
                 + " amount=" + str(getattr(pos, 'amount', '?'))
                 + " avg_cost=" + str(getattr(pos, 'avg_cost', '?'))
                 + " price=" + str(getattr(pos, 'price', '?')))
