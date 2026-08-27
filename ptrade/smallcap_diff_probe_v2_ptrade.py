"""
PTrade 小市值选股差异排查探针 v2（最终确认版）。

目标：一次性确认三个关键问题：
  ① float_value 的 dtype（字符串 vs 数值）→ 判定排序行为
  ② 002719/002872/002193 在 sort_values 后的精确排名位置
  ③ df_sorted 前 100 的完整索引列表（含 002193 是否在其中）

回测设置：日线，区间 2026-07-01 ~ 2026-07-02（只看第 1 天）
"""


def initialize(context):
    set_benchmark("000300.XSHG")
    g.index = "399101.XBHS"
    g.probe_codes = ['002719.SZ', '002872.SZ', '002193.SZ',
                     '003003.SZ', '002200.SZ', '002188.SZ']


def before_trading_start(context, data):
    log.info("=" * 60)
    stock_list = get_index_stocks(g.index)
    df_all = get_fundamentals(stock_list, "valuation",
                              fields=["float_value", "a_floats", "total_value"],
                              date=context.previous_date)
    log.info("[PROBE] 全成分估值返回 %d 行" % len(df_all))

    # ① dtype 确认
    log.info("[PROBE] float_value dtype: %s" % df_all['float_value'].dtype)
    log.info("[PROBE] a_floats dtype: %s" % df_all['a_floats'].dtype)
    log.info("[PROBE] total_value dtype: %s" % df_all['total_value'].dtype)

    # ② 排序（与策略完全一致的写法）
    df_sorted = df_all.sort_values(by="float_value").head(100)

    # ③ 6 只关键股票在排序中的位置
    sorted_index = list(df_sorted.index)
    for code in g.probe_codes:
        if code in sorted_index:
            rank = sorted_index.index(code) + 1
            fv = df_sorted.loc[code, 'float_value']
            log.info("[PROBE]   %s 排名 #%d float_value=%s" % (code, rank, fv))
        elif code in df_all.index:
            log.info("[PROBE]   %s 不在前100（float_value=%s）"
                     % (code, df_all.loc[code, 'float_value']))
        else:
            log.info("[PROBE]   %s 不在 df_all 中！" % code)

    # ④ 前 100 中 float_value 最小的 10 个值（看排序是否合理）
    log.info("[PROBE] df_sorted 前 10 的 float_value 值:")
    for i, (code, fv) in enumerate(
            df_sorted['float_value'].head(10).items(), 1):
        log.info("[PROBE]   #%d %s float_value=%s (type=%s)"
                 % (i, code, fv, type(fv).__name__))

    # ⑤ 如果 dtype 是 object，试转数值后重排看差异
    if str(df_all['float_value'].dtype) == 'object':
        df_numeric = df_all.copy()
        df_numeric['float_value'] = df_numeric['float_value'].astype(float)
        df_num_sorted = df_numeric.sort_values(by="float_value").head(100)
        num_index = list(df_num_sorted.index)
        log.info("[PROBE] --- 转数值后重排的前 5 ---")
        for i, code in enumerate(num_index[:5], 1):
            log.info("[PROBE]   #%d %s float_value=%s"
                     % (i, code, df_num_sorted.loc[code, 'float_value']))
        # 002719 在数值排序中的位置
        for code in ['002719.SZ', '002872.SZ', '002193.SZ']:
            if code in num_index:
                log.info("[PROBE] 数值排序中 %s 排名 #%d"
                         % (code, num_index.index(code) + 1))

    # ⑥ filter_stock_by_status 的过滤结果（检查 6 只是否被过滤）
    filtered = filter_stock_by_status(
        df_sorted.index.tolist(),
        filter_type=["ST", "HALT", "DELISTING"], query_date=None)
    for code in g.probe_codes:
        passed = code in filtered if code in df_sorted.index else "不在前100"
        log.info("[PROBE]   %s filter通过: %s" % (code, passed))

    log.info("=" * 60)


def handle_data(context, data):
    log.info("[PROBE] handle_data 触发，探针完成")
    return
