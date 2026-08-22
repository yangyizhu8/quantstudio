# probe_pd9_filter_ptrade.py - P-D9 数据前提探针（2026-08-22，方案 v3 验证用，不入库）
# 目标（方案 v3 §8）：
#   ① circ_mv 平台可得性：get_fundamentals 是否含估值类字段（float_value/circ_mv 等）及单位
#   ② 批量取数耗时：get_history 多码批量单次耗时（幸存者兜底可行性判据）
#   ③ close 口径：0.9-1.1 元带抽样（600491/688496 + 仙股）before_trading_start 与盘后 close 对比
#   ④ E 时点：before_trading_start 内 get_history(count=1) 取到的是 T-1 还是 T close（与 later 对照）
#   ⑤ filter_stock_by_status 缺省行为：隐式调用（不传 filter_type）返回集合 vs 显式 ["ST","HALT","DELISTING"]
# 用法：PTrade 新建策略 → 回测 2026-07-01 ~ 2026-07-03，初始资金 100000，基准沪深300。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

PROBE_CODES = ['000004.SZ', '002808.SZ', '002898.SZ', '300029.SZ',   # 仙股（<1 元）
               '600491.SS', '688496.SS',                            # 0.9-1.1 元临界带
               '600000.SS', '300854.SZ']                            # 正常股对照组


def initialize(context):
    set_benchmark('000300.SS')
    g.done_probe = False
    g.bts_close = {}
    run_daily(context, probe_checks, time='15:00')


def before_trading_start(context, data):
    """E 时点确认：此时 get_history(count=1) 取到 T-1 还是 T？"""
    for code in PROBE_CODES:
        try:
            df = get_history(count=1, frequency="1d", field=["close"],
                             security_list=code, fq="pre", include=False)
            if df is not None and len(df) > 0:
                g.bts_close[code] = float(df.iloc[-1].get("close", 0))
        except Exception as exc:
            log.info("PD9-BTS-FAIL %s %s" % (code, exc))
    # ① circ_mv / 估值字段可得性（尽力而为，异常即不可得）
    try:
        fd = get_fundamentals(PROBE_CODES[0], 'valuation',
                              fields=['float_value', 'circ_mv', 'total_mv'],
                              date='20260630', is_dataframe=True)
        log.info("PD9-FUND eval: type=%s" % type(fd).__name__)
        if fd is not None and hasattr(fd, 'to_string'):
            log.info("PD9-FUND val-df head= %s" % str(fd.head(2).to_dict()))
        else:
            log.info("PD9-FUND val=None type=%s" % type(fd).__name__)
    except Exception as exc:
        log.info("PD9-FUND-ERR %s" % (exc,))
    # ② 批量取数耗时（单次 8 码 vs 单码 8 次）
    import time
    t0 = time.time()
    try:
        bh = get_history(count=1, frequency="1d", field=["close"],
                         security_list=PROBE_CODES, fq="pre", include=False)
        t1 = time.time() - t0
        log.info("PD9-BATCH 8codes one-call=%.3fs shape=%s" % (t1, getattr(bh, 'shape', 'N/A')))
    except Exception as exc:
        t1 = time.time() - t0
        log.info("PD9-BATCH-FAIL %.3fs %s" % (t1, exc))
    t0 = time.time()
    for code in PROBE_CODES:
        try:
            get_history(count=1, frequency="1d", field=["close"],
                        security_list=code, fq="pre", include=False)
        except Exception:
            pass
    log.info("PD9-SERIAL 8codes eight-calls=%.3fs" % (time.time() - t0))
    # ⑤ 缺省 filter_type 行为
    try:
        dflt = filter_stock_by_status(PROBE_CODES)
        expl = filter_stock_by_status(PROBE_CODES, ["ST", "HALT", "DELISTING"])
        log.info("PD9-DEFLT filter=%s" % (dflt,))
        log.info("PD9-EXPLT filter=%s" % (expl,))
    except Exception as exc:
        log.info("PD9-FILTER-ERR %s" % (exc,))


def probe_checks(context):
    """盘后（T 收盘后）close 对照 E：与 before_trading_start 的 bts_close 对比。"""
    if g.done_probe:
        return
    g.done_probe = True
    for code in PROBE_CODES:
        try:
            df = get_history(count=1, frequency="1d", field=["close"],
                             security_list=code, fq="pre", include=False)
            if df is not None and len(df) > 0:
                c = float(df.iloc[-1].get("close", 0))
                b = g.bts_close.get(code, None)
                log.info("PD9-CLOSE %s bts=%.4f later=%.4f (E 时点同值? -> %s)"
                         % (code, b if b is not None else -1, c, "EQUAL" if b == c else "DIFF"))
        except Exception as exc:
            log.info("PD9-CLOSE-FAIL %s %s" % (code, exc))


def handle_data(context, data):
    pass