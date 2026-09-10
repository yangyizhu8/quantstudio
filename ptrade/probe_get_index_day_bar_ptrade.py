# probe_get_index_day_bar_ptrade.py - D4 平台探针（2026-09-08，取证用途，暂不入库）
# 设计依据：docs/get-index-day-bar-design.md §3.3（转换门禁 D4 序列）+
#           docs/conversion-local-only-gate-design.md（重写解锁前置）
# 目标：实证 PTrade 平台 get_history 对"指数代码"的支持与 include 语义，
#       为 get_index_day_bar 的转换重写映射（重写为平台 get_history include=True）
#       提供平台实证。四个探针：
#   IDX1 指数代码支持：get_history('000001.SS', 5, '1d', 'close') 返回非空、
#        值为指数点位（3000~5000 量级，非个股价格量级）；
#   IDX2 include=True 含当日：日线回测 handle_data 时点，include=True 返回的
#        最后一根 bar 日期 == 当日（context.current_dt 日期）；
#   IDX3 include 对比：include=True 比 include=False（同期数）恰好多一根当日 bar；
#   IDX4 pctChg 字段可用：fields=['pctChg']（field 单字段）返回非空数值，
#        且与 close/preclose 口径合理性抽查（|pctChg| < 11，指数无 20cm）。
# 判定：四个探针全 PASS → 重写映射可解锁（转换器将 get_index_day_bar 重写为
#       平台 get_history(..., include=True, fq='pre')）；任一 FAIL → 维持转换 BLOCK。
# 用法：PTrade 新建回测 → 粘贴本文件 → 区间 2026-07-13 ~ 2026-07-17（覆盖已知
#       大跌日 07-16/-1.85% 07-17/-3.05%，便于 pctChg 对照本地副本库实测值）→
#       资金 100000 → 运行。完成后回贴日志中以 IDX 开头的行。
# 对照基准（本地副本库 index_daily 实测，docs/get-index-day-bar-design.md §3.1）：
#       2026-07-15 close=3999.90 前后 / 2026-07-16 pctChg≈-1.85 / 2026-07-17 pctChg≈-3.05
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

IDX_CODE = '000001.SS'
FORK_CLOSE_LOW = 3000.0   # 指数点位下界（防串成个股价格）
FORK_CLOSE_HIGH = 5000.0  # 指数点位上界


def _df_close_info(df):
    """返回 (行数, 最后一行 close, 最后一行日期字符串或 index 字符串)。"""
    if df is None or len(df) == 0:
        return (0, None, None)
    try:
        last_close = float(df['close'].iloc[-1])
    except Exception:
        last_close = None
    try:
        if 'time' in df.columns:
            last_dt = str(df['time'].iloc[-1])
        else:
            last_dt = str(df.index[-1])
    except Exception:
        last_dt = None
    return (len(df), last_close, last_dt)


def initialize(context):
    g.day_no = 0
    g.results = {}   # day -> {probe: 'PASS'/'FAIL'/…}，末日汇总


def handle_data(context, data):
    g.day_no += 1
    day = context.current_dt.strftime('%Y-%m-%d')
    log.info('==== IDX-PROBE day ' + str(g.day_no) + ' ' + day + ' ====')
    day_res = {}

    # ---- IDX1: 指数代码支持（include=False 历史查询）----
    try:
        df1 = get_history(5, frequency='1d', field='close',
                          security_list=[IDX_CODE], fq='pre', include=False)
        n, last_close, last_dt = _df_close_info(df1)
        ok = (n == 5 and last_close is not None
              and FORK_CLOSE_LOW <= last_close <= FORK_CLOSE_HIGH)
        day_res['IDX1'] = 'PASS' if ok else 'FAIL'
        log.info('IDX1 support include=False rows=' + str(n)
                 + ' last_close=' + repr(last_close)
                 + ' last_dt=' + repr(last_dt)
                 + ' -> ' + day_res['IDX1'])
        if not ok:
            log.info('IDX1 RAW ' + repr(df1))
    except Exception as exc:
        day_res['IDX1'] = 'ERR:' + type(exc).__name__
        log.info('IDX1-ERR ' + type(exc).__name__ + ' ' + str(exc))

    # ---- IDX2: include=True 是否含当日 ----
    try:
        df2 = get_history(3, frequency='1d', field='close',
                          security_list=[IDX_CODE], fq='pre', include=True)
        n2, c2, dt2 = _df_close_info(df2)
        ok2 = (n2 >= 1 and dt2 is not None and day in str(dt2))
        day_res['IDX2'] = 'PASS' if ok2 else 'FAIL'
        log.info('IDX2 include=True rows=' + str(n2)
                 + ' last_close=' + repr(c2)
                 + ' last_dt=' + repr(dt2)
                 + ' expect_day=' + day
                 + ' -> ' + day_res['IDX2'])
        log.info('IDX2 RAW ' + repr(df2))
    except Exception as exc:
        day_res['IDX2'] = 'ERR:' + type(exc).__name__
        log.info('IDX2-ERR ' + type(exc).__name__ + ' ' + str(exc))

    # ---- IDX3: include=True vs include=False 同期对比（恰多一根当日 bar）----
    try:
        dfa = get_history(3, frequency='1d', field='close',
                          security_list=[IDX_CODE], fq='pre', include=True)
        dfb = get_history(3, frequency='1d', field='close',
                          security_list=[IDX_CODE], fq='pre', include=False)
        # include=True 的前 2 根应与 include=False 的后 2 根完全一致，
        # 且 include=True 末根 == 当日。
        ok3 = (len(dfa) == 3 and len(dfb) == 2)
        if ok3:
            a_vals = list(dfa['close'].iloc[:2])
            b_vals = list(dfb['close'].iloc[:2])
            ok3 = all(abs(float(x) - float(y)) < 1e-6
                      for x, y in zip(a_vals, b_vals))
        day_res['IDX3'] = 'PASS' if ok3 else 'FAIL'
        log.info('IDX3 include=True rows=' + str(len(dfa))
                 + ' include=False rows=' + str(len(dfb))
                 + ' prefix_match=' + str(ok3)
                 + ' -> ' + day_res['IDX3'])
    except Exception as exc:
        day_res['IDX3'] = 'ERR:' + type(exc).__name__
        log.info('IDX3-ERR ' + type(exc).__name__ + ' ' + str(exc))

    # ---- IDX4: pctChg 字段可用 + 指数量级合理性 ----
    try:
        df4 = get_history(2, frequency='1d', field='pctChg',
                          security_list=[IDX_CODE], fq='pre', include=True)
        if df4 is not None and len(df4) > 0 and 'pctChg' in df4.columns:
            vals = [float(v) for v in df4['pctChg'].dropna()]
            ok4 = (len(vals) > 0 and all(abs(v) < 11.0 for v in vals))
        else:
            vals = []
            ok4 = False
        day_res['IDX4'] = 'PASS' if ok4 else 'FAIL'
        log.info('IDX4 pctChg vals=' + repr(vals) + ' -> ' + day_res['IDX4'])
    except Exception as exc:
        day_res['IDX4'] = 'ERR:' + type(exc).__name__
        log.info('IDX4-ERR ' + type(exc).__name__ + ' ' + str(exc))

    g.results[day] = day_res

    # ---- 末日/任意日均可输出汇总（每日都汇总，覆盖全区间）----
    all_ok = all(v == 'PASS' for v in day_res.values())
    log.info('IDX-SUMMARY ' + day + ' '
             + ' '.join(k + '=' + v for k, v in sorted(day_res.items()))
             + ' ALL_PASS=' + str(all_ok))


def after_trading_end(context, data):
    # 末日输出整体判定（每天重复输出最终状态，便于日志末尾直接回贴）
    all_days = g.results
    all_pass = all(all(v == 'PASS' for v in d.values()) for d in all_days.values())
    log.info('IDX-FINAL days=' + str(len(all_days))
             + ' ALL_PASS=' + str(all_pass)
             + ' VERDICT=' + ('REWRITE_UNLOCK_CANDIDATE' if all_pass else 'KEEP_BLOCKED'))
