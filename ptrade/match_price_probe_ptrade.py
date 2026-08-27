"""
PTrade 撮合机制实证测试策略
================================================================
目的：确认 PTrade 平台 order() 的成交价到底用哪个价格

  A) 当日收盘价 close → 与 QuantStudio 本地默认 close 模式一致
  B) 当日开盘价 open
  C) 次日开盘价 next_open（T+1 成交）

原理：
  - 每天 handle_data 买入 1 只【不同】ETF 的 100 股
  - 每只 ETF 只买一次 → cost_basis = 该日唯一成交价，不会被稀释
  - after_trading_end 打印 cost_basis（成交价）+ 尝试打印 OHLC
  - 对比成交价 vs OHLC 即可判定撮合机制

PTrade 回测设置建议：
  - 初始资金：100000
  - 回测区间：选一段波动期（如 2026-06-01 ~ 2026-07-31）
  - 频率：日线（Day 级别）

日志分析（跑完后看 [FILL] 行的 cost_basis）：
  cost_basis ≈ T 日 close  → PTrade 用 close 撮合（与本地一致）
  cost_basis ≈ T 日 open   → PTrade 用 open 撮合
  cost_basis ≈ T+1 日 open → PTrade 用 next_open 撮合（T+1 成交）
================================================================
"""


def initialize(context):
    # 5 只不同 ETF，每天买一只
    # 每只 cost_basis 是该日唯一买入价（只买 100 股，后续不再追加）
    g.test_plan = [
        '510300.SS',   # Day1 沪深300ETF
        '510500.SS',   # Day2 中证500ETF
        '159915.SZ',   # Day3 创业板ETF
        '518880.SS',   # Day4 黄金ETF
        '159995.SZ',   # Day5 芯片ETF
    ]
    g.day_count = 0


def before_trading_start(context, data):
    g.day_count += 1
    log.info("=" * 60)
    log.info("[BTS] === 第 %d 个交易日 ===" % g.day_count)


def handle_data(context, data):
    # 前 5 天每天买入一只不同 ETF
    if g.day_count <= len(g.test_plan):
        sec = g.test_plan[g.day_count - 1]
        try:
            order(sec, 100)
            log.info("[ORDER] 第%d天 买入 %s 100股" % (g.day_count, sec))
        except Exception as e:
            log.info("[ORDER ERROR] %s : %s" % (sec, str(e)))


def after_trading_end(context, data):
    log.info("[ATE] === 第 %d 天 持仓明细 ===" % g.day_count)

    # 遍历所有持仓，打印 cost_basis（= 成交价）
    # 每天新增的那只 ETF 的 cost_basis 即为当天成交价
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
        log.info("[FILL ERROR] 遍历持仓失败: %s" % str(e))
        log.info("[FILL DEBUG] positions=%s" % str(context.portfolio.positions))

    # 尝试获取当日 OHLC（辅助对比；get_history 可能有 API 差异，失败不影响核心）
    try:
        hist = get_history(1, '1d', ['open', 'high', 'low', 'close'],
                           g.test_plan, include=True)
        log.info("[OHLC-T] 当日OHLC(include=True): %s" % str(hist))
    except Exception as e:
        log.info("[OHLC-T] get_history 异常（不影响成交价判断）: %s" % str(e))

    # 尝试获取 T-1 OHLC
    try:
        hist_prev = get_history(1, '1d', ['open', 'close'],
                                g.test_plan, include=False)
        log.info("[OHLC-T1] 前日OHLC(include=False): %s" % str(hist_prev))
    except Exception as e:
        log.info("[OHLC-T1] get_history 异常: %s" % str(e))

    log.info("=" * 60)

    # 最后一天：打印最近 10 天完整 OHLC 供对比
    if g.day_count >= len(g.test_plan):
        log.info("*" * 60)
        log.info("[SUMMARY] === 撮合实证总结 ===")
        log.info("[SUMMARY] 以下是 5 只 ETF 最近 10 天完整 OHLC：")
        try:
            hist_all = get_history(10, '1d', ['open', 'high', 'low', 'close'],
                                   g.test_plan, include=True)
            log.info("[SUMMARY OHLC] %s" % str(hist_all))
        except Exception as e:
            log.info("[SUMMARY OHLC] get_history 异常: %s" % str(e))

        log.info("[SUMMARY] ============ 分析方法 ============")
        log.info("[SUMMARY] 对比每天 [FILL] 行的 cost_basis 与 OHLC：")
        log.info("[SUMMARY]   成交价 ≈ 买入日 close  → PTrade 用 close 撮合")
        log.info("[SUMMARY]   成交价 ≈ 买入日 open   → PTrade 用 open 撮合")
        log.info("[SUMMARY]   成交价 ≈ 买入次日 open → PTrade 用 next_open 撮合")
        log.info("[SUMMARY] 同时请查看 PTrade 平台【成交明细】CSV 的精确成交价")
        log.info("*" * 60)
