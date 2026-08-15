"""
div_tax_probe_etf_510500.py — PTrade 平台分红口径实证（ETF：510500 中证500ETF）

验证目标：确定 PTrade 平台对 **ETF 现金分红** 的入账系数（本地阶段 2 假设为 1.0 = 公募基金
分红免税全额入账，与股票 0.8 区分）。
本策略买入 510500 并持有跨越 2026-07-15 除息日（每份 0.149 元），逐日打印分析数据。

【回测设置】初始资金 100,000；区间 2026-07-01 ~ 2026-07-31（必须覆盖 07-15）；日线频率。

【除息日事实】510500 2026-07-15 除息：每份 0.149 元（tushare fund_div / 本地 etf_dividend 实测，
与 preClose 缺口逐分吻合：07-14 close 8.413 → 07-15 preClose 8.264 = 8.413 − 0.149）。
除息日持仓市值当天跌 vol×0.149。

【判断方法（用 Log.txt 的 total_value 序列）】
  除息日净值缺口 = 1 − nav(07-15)/nav(07-14)（剔除当日市场涨跌后）
  三种口径的理论净值缺口（持仓市值占比 ≈ 缺口/(vol×0.149)）：
    A. 不入账（系数 0）：缺口 = vol×0.149           → 占比 100%（净值 ≈ 跌 1.8%，0.149/8.26）
    B. 0.8 入账（若平台套用股票税）：缺口 = vol×0.149×0.20 → 占比 20%
    C. 1.0 入账（本地阶段 2 假设，公募免税）：缺口 ≈ 0  → 占比 0%
  剔除市场涨跌：当日 pctChg = (close−preClose)/preClose；净值变化率 − pctChg ≈ 分红缺口占比。

【结果回传】回测完成后在平台导出：交易详情 CSV + 持仓明细 CSV + Log.txt（与既有样本同样方式），
本地用 Log.txt 的 total_value 序列做上述计算，与本地引擎 1.0（ETF 免税）口径对比。
"""


def initialize(context):
    g.target = '510500.SS'   # 中证500ETF（沪市，07-15 除息 0.149 元/份）
    g.bought = False
    set_universe([g.target])
    # 佣金对齐 ETF 惯例（万0.5 / 最低 0.5 元，与 ETF动量策略一致）；除息日分析不受佣金影响
    set_commission(commission_ratio=0.00005, min_commission=0.5, type="ETF")


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
