# probe_index_preclose_v2_ptrade.py - D4 补充探针 v2（2026-09-09，取证用途，暂不入库）
# 修订（v2，复审意见二）：v1 在回测首日单次取数（g.done 守卫）导致窗口错位——
#   用户区间 07-01 起时 5 根窗口=06-24~06-30，未覆盖锚点日 07-16/17 → P3 MISSING。
# v2 修订：① 去掉 g.done 守卫，每日取数并累积（include=True 3 根，含当日与前两日）；
#          ② 每日对"已累积且锚点可用"的日期即时判定 P3；③ 末日输出总判定。
# 目标（不变）：P1 preclose 非空且>0；P2 preclose[i]==close[i-1]（逐对）；
#              P3 (close/preclose-1)*100 == 本地 index_daily.pctChg 锚
#              （2026-07-16=-1.8497 / 2026-07-17=-3.0460，逐日对照）。
# 用法：PTrade 新建回测 → 粘贴本文件 → 区间 2026-07-15 ~ 2026-07-17（3 交易日，
#       必须覆盖锚点日 07-16/17）→ 资金 100000 → 运行。回贴 PCL- 开头日志行。
# 判定：末日 PCL-FINAL ALL_PASS=True → pctChg 走 preclose 路径（复用既有 wrapper
#       close/preClose 合成，无损首行）；任一 FAIL → 退 count+1 close 相邻计算 +
#       历史边界 approximation 重新确认（不得宣称完全同构）。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

IDX_CODE = '000001.SS'
LOCAL_ANCHOR = {'2026-07-16': -1.8497, '2026-07-17': -3.0460}


def initialize(context):
    g.collected = {}   # date -> {'close': float, 'preclose': float}


def _fetch_day(context):
    """当日取 include=True 3 根（当日 + 前两日），累积 close/preclose。"""
    df = get_history(3, frequency='1d', field=['close', 'preclose'],
                     security_list=[IDX_CODE], fq='pre', include=True)
    if df is None or len(df) == 0:
        return
    for ix, row in df.iterrows():
        d = str(ix)[:10]
        try:
            c = float(row['close'])
            pc = float(row['preclose'])
        except Exception:
            continue
        g.collected[d] = {'close': c, 'preclose': pc}


def handle_data(context, data):
    _fetch_day(context)
    day = context.current_dt.strftime('%Y-%m-%d')
    log.info('==== PCL2-PROBE ' + day + ' collected=' + str(sorted(g.collected.keys())) + ' ====')

    # ---- P1: preclose 非空且 > 0（全累积集）----
    p1_ok = all(v['preclose'] > 0 for v in g.collected.values()) and len(g.collected) > 0
    log.info('PCL2-P1 preclose_nonnull_pos=' + str(p1_ok)
             + ' -> ' + ('PASS' if p1_ok else 'FAIL'))

    # ---- P2: preclose[i] == close[i-1]（逐对，指数无除权应严格成立）----
    dates = sorted(g.collected.keys())
    p2_checks = []
    for i in range(1, len(dates)):
        d, prev = dates[i], dates[i - 1]
        pc = g.collected[d]['preclose']
        c_prev = g.collected[prev]['close']
        p2_checks.append(abs(pc - c_prev) < 0.01)
    p2_ok = len(p2_checks) > 0 and all(p2_checks)
    log.info('PCL2-P2 preclose_eq_prev_close=' + repr(p2_checks)
             + ' -> ' + ('PASS' if p2_ok else 'FAIL'))

    # ---- P3: 锚点日 (close/preclose-1)*100 == 本地 pctChg ----
    p3_details = []
    p3_done = []
    p3_ok = True
    for d, anchor in sorted(LOCAL_ANCHOR.items()):
        if d not in g.collected:
            p3_details.append(d + '=PENDING(未累积)')
            p3_ok = False
            continue
        v = g.collected[d]
        if v['preclose'] <= 0:
            p3_details.append(d + '=BAD_PRECLOSE')
            p3_ok = False
            continue
        calc = (v['close'] / v['preclose'] - 1.0) * 100.0
        match = abs(calc - anchor) < 0.01
        p3_done.append(match)
        p3_details.append(d + '=calc_' + ('%.4f' % calc) + ' local_' + str(anchor)
                          + ' ' + ('MATCH' if match else 'DIFF'))
    all_done = (len(p3_done) == len(LOCAL_ANCHOR))
    log.info('PCL2-P3 pctchg_vs_local ' + ' | '.join(p3_details)
             + ' -> ' + (('PASS' if p3_ok else 'FAIL') if all_done else 'PENDING'))

    # ---- 汇总（锚点全部累积后输出最终判定；此前输出中间态）----
    if all_done:
        final_ok = p1_ok and p2_ok and p3_ok
        log.info('PCL2-FINAL P1=' + str(p1_ok) + ' P2=' + str(p2_ok)
                 + ' P3=' + str(p3_ok) + ' ALL_PASS=' + str(final_ok)
                 + ' VERDICT=' + ('PRECLOSE_PATH_UNLOCK' if final_ok else 'CLOSE_SHIFT_FALLBACK'))
    else:
        log.info('PCL2-FINAL PENDING（锚点日未全部累积）')


def after_trading_end(context, data):
    pass
