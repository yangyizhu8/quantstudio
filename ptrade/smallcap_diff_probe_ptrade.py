"""
PTrade 小市值选股差异排查探针（临时诊断用，不用于实盘/回测收益）。

目的：定位本地 vs PTrade 选股分叉根因——对比三个环节：
  ① 399101（中小板综）成分股数量与 8 只关键股票是否在成分中
  ② 8 只关键股票的 float_value / a_floats / total_value（PTrade 聚源 vs 本地 tushare/MCP）
  ③ 按市值排序的前 10 名（与本地排名对比）

回测设置：
  - 初始资金：100000（不下单，仅打印）
  - 频率：日线
  - 区间：任意 2~3 天（如 2026-07-01 ~ 2026-07-02，只看第 1 天日志）

本地对照数据（2026-06-30，stock_daily_valuation）：
  002719 circ_mv=92,429万（本地排名第1）
  002872 circ_mv=96,498万（本地排名第2）
  002193 circ_mv=108,088万（本地排名第3）
  003003 circ_mv=117,207万（两端都选）
  002200 circ_mv=117,436万（两端都选）
  002188 circ_mv=126,108万（PTrade选）
  002809 circ_mv=126,458万（PTrade选）
  002494 circ_mv=127,326万（PTrade选）
  本地 399101 成分股：1011 只
"""


def initialize(context):
    set_benchmark("000300.XSHG")
    g.index = "399101.XBHS"  # 中小板综（与小市值策略一致）
    # 8 只关键股票（本地独有 3 + PTrade 独有 3 + 两端共选 2）
    g.probe_codes = [
        '003003.SZ',  # 两端选
        '002200.SZ',  # 两端选
        '002188.SZ',  # PTrade 选（本地排第6）
        '002809.SZ',  # PTrade 选（本地排第7）
        '002494.SZ',  # PTrade 选（本地排第8）
        '002719.SZ',  # 本地选（本地排第1）
        '002872.SZ',  # 本地选（本地排第2）
        '002193.SZ',  # 本地选（本地排第3）
    ]


def before_trading_start(context, data):
    log.info("=" * 60)
    log.info("[PROBE] === 环节① 399101 成分股 ===")

    # ① 获取成分股列表
    stock_list = get_index_stocks(g.index)
    log.info("[PROBE] 399101 成分股数量: %d（本地对照: 1011）" % len(stock_list))

    # 检查 8 只关键股票是否在成分中
    for code in g.probe_codes:
        in_list = code in stock_list
        # 也检查裸码格式（防止成分列表用不同后缀）
        bare = code.split('.')[0]
        in_list_bare = any(bare in str(s) for s in stock_list)
        log.info("[PROBE]   %s 在成分中: %s（裸码匹配: %s）" % (code, in_list, in_list_bare))

    log.info("=" * 60)
    log.info("[PROBE] === 环节② 关键股票市值（PTrade 聚源）===")

    # ② 查 8 只的估值数据
    try:
        df = get_fundamentals(g.probe_codes, "valuation",
                              fields=["float_value", "a_floats", "total_value"],
                              date=context.previous_date)
        log.info("[PROBE] get_fundamentals 返回 %d 行" % len(df))
        for code in g.probe_codes:
            try:
                if code in df.index:
                    fv = df.loc[code, 'float_value']
                    af = df.loc[code, 'a_floats']
                    tv = df.loc[code, 'total_value']
                    log.info("[PROBE]   %s: float_value=%s a_floats=%s total_value=%s"
                             % (code, fv, af, tv))
                else:
                    log.info("[PROBE]   %s: 不在返回结果中！（数据缺失或被过滤）" % code)
            except Exception as e:
                log.info("[PROBE]   %s: 读取异常 %s" % (code, e))
    except Exception as e:
        log.info("[PROBE] get_fundamentals 异常: %s" % e)

    log.info("=" * 60)
    log.info("[PROBE] === 环节③ 按市值排序前 10（PTrade 排名）===")

    # ③ 模拟小市值策略的完整排序（前 100 → 过滤 → 前 10）
    try:
        df_all = get_fundamentals(stock_list, "valuation",
                                  fields=["float_value", "a_floats", "total_value"],
                                  date=context.previous_date)
        log.info("[PROBE] 全成分估值返回 %d 行" % len(df_all))
        if len(df_all) > 0:
            df_sorted = df_all.sort_values(by="float_value").head(100)
            # filter_stock_by_status 过滤（与小市值策略一致）
            filtered = filter_stock_by_status(
                df_sorted.index.tolist(),
                filter_type=["ST", "HALT", "DELISTING"],
                query_date=None)
            df_final = df_sorted[df_sorted.index.isin(filtered)]
            log.info("[PROBE] 过滤后剩余: %d 只（过滤前 %d）"
                     % (len(df_final), len(df_sorted)))
            top10 = df_final.head(10)
            log.info("[PROBE] PTrade 市值排名前 10:")
            for i, (code, row) in enumerate(top10.iterrows(), 1):
                log.info("[PROBE]   #%d %s float_value=%s a_floats=%s"
                         % (i, code, row['float_value'], row['a_floats']))
    except Exception as e:
        log.info("[PROBE] 排序异常: %s" % e)

    log.info("=" * 60)
    log.info("[PROBE] 本地对照排名: 002719(92429) 002872(96498) 002193(108088) "
             "003003(117207) 002200(117436) 002188(126108) 002809(126458) 002494(127326)")
    log.info("[PROBE] 判定: 002719/002872/002193 在 PTrade 的 float_value 是多少？")


def handle_data(context, data):
    # 不下单，仅触发一次
    log.info("[PROBE] handle_data 触发（日期=%s，探针完成）" % context.current_dt)
    return
