"""
div_tax_probe_stock_split_300492.py — PTrade 平台股票送股/转增实证（300492 华图山鼎）

验证目标：确定 PTrade 平台对**股票送股/转增 + 现金分红同日复合除权**的处理：
  ① 送股：除息日持仓股数是否 ×(1+stk_div)（300492: 10送4派1 → stk_div=0.4）
  ② 现金：每股 0.1 元派息是否按 ×0.8 入账（股票分红口径，600000 实证已确认 0.8）
  ③ 净值：送股不创造 PnL（市值 ×1.4 且价格 ÷1.4），净值仅受现金入账系数影响
本策略买入 300492 并持有跨越 2026-07-07 除息日，逐日打印分析数据。

【回测设置】初始资金 100,000；区间 2026-07-01 ~ 2026-07-31（必须覆盖 07-07）；日线频率。

【除息日事实】300492 2026-07-07 除息：10送4派1（tushare stock_dividend 实测：
stk_div=0.4 送股、cash_div_before_tax=0.1 现金；stk_bo_rate=0.4 明确为送股非转增）。
复合除权参考价 = (前收 − 0.1) / 1.4：07-06 close 36.6 → 07-07 preClose 26.08 ✅（本地实测吻合）。

【判断方法（用 Log.txt 的 amount/total 序列）】
  ① 送股：07-07 起 amount = 除权前 × 1.4（如 2700 → 3780）→ PTrade 送股处理 ✅
  ② 现金入账：07-07 cash 增量 = vol×0.1×0.8（股票口径 0.8，与 600000 实证一致）
  ③ 净值缺口（剔除当日涨跌后）：
      0.8 入账：缺口 = 0.2×0.1×vol ≈ 0.055%（×2700 股，总资产 10 万）
      1.0 入账：缺口 ≈ 0
      不入账：  缺口 = 0.1×vol ≈ 0.27%
  ④ 顺带（持仓明细 CSV）：“持仓成本价”在 07-07 是否摊薄为 前收成本/1.4（本地逻辑
     avg_cost = (avg_cost−0.1)×old/new；平台若一致则除权处理完整）

【结果回传】回测完成后在平台导出：交易详情 CSV + 持仓明细 CSV + Log.txt，本地核对。
"""


def initialize(context):
    g.target = '300492.SZ'   # 华图山鼎（创业板，07-07 除息：10送4派1）
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
        log.info("SPLITTAX 买入 " + g.target + " target_value=" + str(round(context.portfolio.total_value, 2)))

    # 逐日打印分析数据（Log.txt 全量保留；amount 变化 = 送股直接证据）
    pos = get_position(g.target)
    if pos is None:
        log.info("SPLITTAX " + str(context.current_dt)[:10]
                 + " total=" + str(round(context.portfolio.total_value, 2))
                 + " cash=" + str(round(context.portfolio.cash, 2))
                 + " pos=None")
    else:
        log.info("SPLITTAX " + str(context.current_dt)[:10]
                 + " total=" + str(round(context.portfolio.total_value, 2))
                 + " cash=" + str(round(context.portfolio.cash, 2))
                 + " amount=" + str(getattr(pos, 'amount', '?'))
                 + " avg_cost=" + str(getattr(pos, 'avg_cost', '?'))
                 + " price=" + str(getattr(pos, 'price', '?')))
