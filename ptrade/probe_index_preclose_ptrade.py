# probe_index_preclose_ptrade.py - D4 补充探针（2026-09-09，取证用途，暂不入库）
# 设计依据：docs/index-bar-rewrite-rule-design.md 审计意见三（pctChg 实现前置）+
#           docs/evidence/d4-index-bar-probe-evidence.md（首轮探针：指数代码支持/include 语义已实证）
# 目标：实证 PTrade 平台指数日线 preclose 字段的数值有效性——
#   P1 preclose 非空（000001.SS 近 5 根 preclose 全 > 0）；
#   P2 preclose == 上一日 close（逐行核对：preclose[i] == close[i-1]，指数无除权应严格成立）；
#   P3 由 close/preclose 计算的 pctChg 与本地 index_daily.pctChg 逐日一致
#      （本地实测锚：2026-07-16 = -1.8497 / 2026-07-17 = -3.0460）。
# 判定：P1~P3 全 PASS → pctChg 合成走 preclose 路径（复用既有 _QS_HISTORY_WRAPPER
#       close/preClose 合成，2026-09-01 平台实证能力）；任一 FAIL → 退回 count+1 close
#       相邻计算 + 历史边界 approximation 重新确认。
# 用法：PTrade 新建回测 → 粘贴本文件 → 区间 2026-07-14 ~ 2026-07-17 → 资金 100000 → 运行。
#       完成后回贴日志中以 PCL- 开头的行。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

IDX_CODE = '000001.SS'
# 本地 index_daily 实测锚（docs/evidence/d4-index-bar-probe-evidence.md §二）
LOCAL_ANCHOR = {'2026-07-16': -1.8497, '2026-07-17': -3.0460}


def initialize(context):
    g.done = False


def handle_data(context, data):
    if g.done:
        return
    g.done = True
    day = context.current_dt.strftime('%Y-%m-%d')
    log.info('==== PCL-PROBE ' + day + ' ====')
    try:
        df = get_history(5, frequency='1d', field='close',
                         security_list=[IDX_CODE], fq='pre', include=False)
        dfp = get_history(5, frequency='1d', field='preclose',
                          security_list=[IDX_CODE], fq='pre', include=False)
    except Exception as exc:
        log.info('PCL-ERR fetch ' + type(exc).__name__ + ' ' + str(exc))
        return

    if df is None or len(df) == 0 or dfp is None or len(dfp) == 0:
        log.info('PCL-EMPTY close_rows=' + str(len(df or []))
                 + ' preclose_rows=' + str(len(dfp or [])))
        return

    closes = {}
    for ix, row in df.iterrows():
        closes[str(ix)[:10]] = float(row['close'])
    precis = {}
    for ix, row in dfp.iterrows():
        precis[str(ix)[:10]] = float(row['preclose'])

    # 原始数据留档（D4 证据）
    log.info('PCL-RAW close=' + repr(closes))
    log.info('PCL-RAW preclose=' + repr(precis))

    dates = sorted(closes.keys())
    # ---- P1: preclose 非空且 > 0 ----
    p1_ok = all(precis.get(d, 0) > 0 for d in dates)
    log.info('PCL-P1 preclose_nonnull_pos=' + str(p1_ok) + ' -> '
             + ('PASS' if p1_ok else 'FAIL'))

    # ---- P2: preclose[i] == close[i-1]（指数无除权应严格成立）----
    p2_checks = []
    for i in range(1, len(dates)):
        d, prev = dates[i], dates[i - 1]
        pc, c_prev = precis.get(d), closes.get(prev)
        if pc is None or c_prev is None:
            p2_checks.append(False)
        else:
            p2_checks.append(abs(pc - c_prev) < 0.01)
    p2_ok = all(p2_checks) and len(p2_checks) > 0
    log.info('PCL-P2 preclose_eq_prev_close=' + str(p2_checks)
             + ' -> ' + ('PASS' if p2_ok else 'FAIL'))

    # ---- P3: (close/preclose-1)*100 与本地 pctChg 锚对照 ----
    p3_details = []
    p3_ok = True
    for d, anchor in LOCAL_ANCHOR.items():
        pc, c = precis.get(d), closes.get(d)
        if pc is None or c is None or pc <= 0:
            p3_details.append(d + '=MISSING')
            p3_ok = False
            continue
        calc = (c / pc - 1.0) * 100.0
        match = abs(calc - anchor) < 0.01
        p3_ok = p3_ok and match
        p3_details.append(d + '=calc_' + ('%.4f' % calc) + ' local_' + str(anchor)
                          + ' ' + ('MATCH' if match else 'DIFF'))
    log.info('PCL-P3 pctchg_vs_local ' + ' | '.join(p3_details)
             + ' -> ' + ('PASS' if p3_ok else 'FAIL'))

    # ---- 汇总 ----
    all_ok = p1_ok and p2_ok and p3_ok
    log.info('PCL-SUMMARY P1=' + str(p1_ok) + ' P2=' + str(p2_ok)
             + ' P3=' + str(p3_ok) + ' ALL_PASS=' + str(all_ok)
             + ' VERDICT=' + ('PRECLOSE_PATH_UNLOCK' if all_ok else 'CLOSE_SHIFT_FALLBACK'))


def after_trading_end(context, data):
    pass
