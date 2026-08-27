"""
PTrade 分钟撮合机制实证测试策略
================================================================
目的：确认 PTrade 分钟策略 order() 的成交价到底用哪个价格

  A) 当前 bar 的 close  → 与 QuantStudio 本地 minute-bar-v1 profile 一致
  B) 当前 bar 的 open
  C) 下一 bar 的 open（T+1 bar 成交）
  D) 当日 VWAP 或其他

同时确认：
  - 分钟 bar 的时间标记方式（bar 开始 vs bar 结束）
  - 分钟 get_history(include=True/False) 的切片语义

原理：
  - 每天 3 个时间点买入 3 只【不同】ETF（每只 100 股）
  - 每只 ETF 只买一次 → cost_basis = 该 bar 唯一成交价 + 佣金
  - 下单时同时打印最近 3 根 bar 的 OHLC（供对比）
  - 前 5 个 bar 打印 context.current_dt（确认时间标记方式）

PTrade 回测设置（重要）：
  - 初始资金：100000
  - 频率：【1 分钟】（必须选1分钟，否则时间点可能不被命中）
  - 回测区间：选一段波动期（如 2026-06-01 ~ 2026-06-05）

日志分析（对比 [FILL] 的 cost_basis 与 [BAR_OHLC] 的 OHLC）：
  日线实证已确认 cost_basis = close + 5元佣金/100股 = close + 0.05
  分钟版需确认：cost_basis - 0.05 ≈ 下单时刻哪个 bar 的 open/close
    ≈ 当前 bar close → PTrade 分钟用 close 撮合（bar 结束后调用）
    ≈ 当前 bar open  → PTrade 分钟用 open 撮合
    ≈ 下一 bar open  → PTrade 分钟用 next-bar-open
================================================================
"""


def initialize(context):
    # Day1-2 每天不同 ETF，每个时间点不同标的
    # 确保 cost_basis 不被多次买入稀释（每只只买 100 股一次）
    g.plan = {
        1: [(9, 35, '510300.SS'),   # 开盘后第5个bar
            (10, 30, '159915.SZ'),  # 上午盘中
            (14, 30, '518880.SS')], # 下午盘中
        2: [(9, 35, '510500.SS'),
            (10, 30, '159995.SZ'),
            (14, 30, '513100.SS')],  # 纳指ETF
    }
    g.day_count = 0
    g.bought_today = set()
    g.bar_log_count = 0


def before_trading_start(context, data):
    g.day_count += 1
    g.bought_today = set()
    g.bar_log_count = 0
    log.info("=" * 60)
    log.info("[BTS] === Day %d ===" % g.day_count)


def handle_data(context, data):
    t = context.current_dt

    # 前 5 个 bar 打印时间，确认 PTrade 分钟 bar 的时间标记方式
    if g.day_count <= 2 and g.bar_log_count < 5:
        g.bar_log_count += 1
        log.info("[BAR_TIME] Day%d bar#%d current_dt=%s" % (g.day_count, g.bar_log_count, str(t)))

    # 按计划下单
    if g.day_count not in g.plan:
        return
    for h, m, sec in g.plan[g.day_count]:
        key = '%02d%02d' % (h, m)
        if t.hour == h and t.minute == m and key not in g.bought_today:
            g.bought_today.add(key)
            log.info("[ORDER] Day%d %02d:%02d 买入 %s 100股 (dt=%s)" %
                     (g.day_count, h, m, sec, str(t)))
            try:
                order(sec, 100)
            except Exception as e:
                log.info("[ORDER ERROR] %s : %s" % (sec, str(e)))
                continue
            # 下单后立即取最近 3 根 bar 的 OHLC（含当前 bar）
            try:
                hist = get_history(3, '1m', ['open', 'high', 'low', 'close'],
                                   [sec], include=True)
                log.info("[BAR_OHLC] %s 最近3根bar(include=True): %s" % (sec, str(hist)))
            except Exception as e:
                log.info("[BAR_OHLC] get_history异常: %s" % str(e))
            # 同时取 include=False 对比
            try:
                hist2 = get_history(3, '1m', ['open', 'close'],
                                    [sec], include=False)
                log.info("[BAR_OHLC_PREV] %s 前3根bar(include=False): %s" % (sec, str(hist2)))
            except Exception as e:
                log.info("[BAR_OHLC_PREV] 异常: %s" % str(e))


def after_trading_end(context, data):
    log.info("[ATE] === Day %d 持仓明细 ===" % g.day_count)
    try:
        for sec_key in context.portfolio.positions:
            pos = context.portfolio.positions[sec_key]
            cb = getattr(pos, 'cost_basis', None)
            if cb is None:
                cb = getattr(pos, 'avg_cost', None)
            amt = getattr(pos, 'amount', None)
            if amt is None:
                amt = getattr(pos, 'total_amount', None)
            last = getattr(pos, 'last_sale_price', None)
            log.info("[FILL] %s | cost_basis=%s | amount=%s | last_price=%s" %
                     (sec_key, cb, amt, last))
    except Exception as e:
        log.info("[FILL ERROR] %s" % str(e))
        log.info("[FILL DEBUG] positions=%s" % str(context.portfolio.positions))
    log.info("=" * 60)

    # 最后一天总结
    if g.day_count >= 2:
        log.info("*" * 60)
        log.info("[SUMMARY] === 分钟撮合实证总结 ===")
        log.info("[SUMMARY] 对比 [FILL] 的 cost_basis 与下单时 [BAR_OHLC] 的 OHLC")
        log.info("[SUMMARY] 日线实证已确认: cost_basis = close + 0.05(佣金)")
        log.info("[SUMMARY] 分钟版: cost_basis - 0.05 ≈ 哪个 bar 的 open/close？")
        log.info("[SUMMARY]   ≈ 下单 bar 的 close → PTrade 分钟用 close 撮合")
        log.info("[SUMMARY]   ≈ 下单 bar 的 open  → PTrade 分钟用 open 撮合")
        log.info("[SUMMARY]   ≈ 下一 bar 的 open  → PTrade 分钟用 next-bar-open")
        log.info("[SUMMARY] 同时请查看 PTrade 平台【成交明细】CSV 的精确成交价和成交时间")
        log.info("*" * 60)
