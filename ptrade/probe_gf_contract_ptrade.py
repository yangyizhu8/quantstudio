# probe_gf_contract_ptrade.py - P-D10 get_fundamentals 契约探针（2026-08-22，取证用途，不入库）
# 设计依据：docs/p-d10-gf-contract-design.md §3（P-D10 终审修订版，探针先行）
# 目标（三未知点 + 计时）：
#   U1 调用形态：单码 vs list —— list 是否被平台原生接受，各返回 type/shape
#   U2 index 行为：单码与 list 两种形态分别记录——返回 index 是股票代码还是 RangeIndex、是否含 code 列；
#      growth_ability/eps 多报告行形态（index 重复码 vs RangeIndex）；end_date/publ_date 的 dtype 与样例值
#   U3 字段可得性与签名：growth/eps 字段逐字段+组合是否 KeyError；date 格式 ('20260630' vs '2026-06-30' vs None)；
#      is_dataframe kwarg（True/False/缺省）有效性，TypeError/KeyError 分类记录
#   P  计时：50 码逐码 get_fundamentals('valuation', fields=['float_value']) 单次耗时均值/最小/最大
#      → 外推单周三段（valuation≈5000 + growth≈5000 + eps≈5000 ≈ 15000 码）总耗时 vs 阈值 90s
# 用法：PTrade 新建策略 → 回测 2026-07-01 ~ 2026-07-03，初始资金 100000，基准沪深300。
#       运行完成后，把日志中以 GF- 开头的行全部回贴给 DSH 汇总（无需其他输出）。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

PROBE_CODES = ['000001.SZ', '600000.SS', '300255.SZ', '688496.SS']  # 深主板/沪主板/创业板/科创板
PROBE_DATE = '20260630'      # 回测起始日（07-01）前一日，T-1 估值数据（avoid 07-01 平台数据缺失日，D3-X4）
PROBE_DATE2 = '2026-06-30'   # 格式对照
PERF_THRESHOLD_S = 90.0      # 单周 15000 码外推阈值（设计 §6）
PERF_CALL_N = 50             # 计时样本数（审计必改项 P）
PERF_WEEKLY_N = 15000        # 单周三段调用总量（valuation 5000 + growth 5000 + eps 5000）


def _dtypes(df, cols):
    """取指定列的 dtype + 首行样例值（供 end_date/publ_date 类型核验）。"""
    if df is None or not hasattr(df, 'dtypes') or len(df) == 0:
        return 'empty'
    out = []
    for c in cols:
        if c in df.columns:
            try:
                out.append('%s=%s:%r' % (c, df[c].dtype, df[c].iloc[0]))
            except Exception as exc:
                out.append('%s=ERR(%s)' % (c, exc))
        else:
            out.append('%s=ABSENT' % c)
    return ' '.join(out)


def _probe(label, call, extra_cols=()):
    """执行一次探针调用并打印 GF- 描述行；返回结果对象（供后续复用）。"""
    try:
        obj = call()
    except Exception as exc:
        log.info('GF-%s ERR %s: %s' % (label, type(exc).__name__, exc))
        return None
    line = 'GF-%s OK type=%s' % (label, type(obj).__name__)
    if hasattr(obj, 'shape'):
        line += ' shape=%s' % (tuple(obj.shape),)
    elif isinstance(obj, dict):
        line += ' keys=%d first=%s' % (len(obj), [str(k) for k in list(obj)[:3]])
    elif obj is not None and hasattr(obj, '__len__'):
        line += ' len=%d' % len(obj)
    if hasattr(obj, 'index') and hasattr(obj, 'columns'):
        line += (' | idx=%s dtype=%s first5=%s | cols=%s' % (
            type(obj.index).__name__,
            str(getattr(obj.index, 'dtype', '?')),
            [str(x) for x in list(obj.index)[:5]],
            list(obj.columns)))
        if extra_cols:
            line += ' | ' + _dtypes(obj, extra_cols)
    log.info(line)
    return obj


def _run_ufund_blocks():
    """U1/U2/U3：调用形态、index 行为、字段可得性。"""
    # ---- U1 调用形态：单码 vs list ----
    _probe('U1-SINGLE',
           lambda: get_fundamentals(PROBE_CODES[0], 'valuation',
                                    fields=['float_value'], date=PROBE_DATE,
                                    is_dataframe=True), ('float_value',))
    _probe('U1-LIST4',
           lambda: get_fundamentals(PROBE_CODES, 'valuation',
                                    fields=['float_value'], date=PROBE_DATE,
                                    is_dataframe=True), ('float_value',))
    _probe('U1-LIST2',
           lambda: get_fundamentals(PROBE_CODES[:2], 'valuation',
                                    fields=['float_value'], date=PROBE_DATE,
                                    is_dataframe=True), ('float_value',))

    # ---- U2 index 行为：growth/eps 多报告行（单码 + list）----
    _probe('U2-GROWTH-S',
           lambda: get_fundamentals(PROBE_CODES[0], 'growth_ability',
                                    fields=['or_yoy', 'publ_date', 'end_date'],
                                    date=PROBE_DATE, is_dataframe=True),
           ('or_yoy', 'publ_date', 'end_date'))
    _probe('U2-EPS-S',
           lambda: get_fundamentals(PROBE_CODES[0], 'eps',
                                    fields=['eps', 'publ_date', 'end_date'],
                                    date=PROBE_DATE, is_dataframe=True),
           ('eps', 'publ_date', 'end_date'))
    _probe('U2-GROWTH-L',
           lambda: get_fundamentals(PROBE_CODES, 'growth_ability',
                                    fields=['or_yoy', 'publ_date', 'end_date'],
                                    date=PROBE_DATE, is_dataframe=True),
           ('or_yoy', 'publ_date', 'end_date'))
    _probe('U2-EPS-L',
           lambda: get_fundamentals(PROBE_CODES, 'eps',
                                    fields=['eps', 'publ_date', 'end_date'],
                                    date=PROBE_DATE, is_dataframe=True),
           ('eps', 'publ_date', 'end_date'))

    # ---- U3 字段可得性：逐字段 + 组合（KeyError 分类）----
    for fld in ('or_yoy', 'publ_date', 'end_date'):
        _probe('U3-GF-SINGLE-%s' % fld,
               lambda f=fld: get_fundamentals(PROBE_CODES[0], 'growth_ability',
                                              fields=[f], date=PROBE_DATE,
                                              is_dataframe=True), (fld,))
    for fld in ('eps', 'publ_date', 'end_date'):
        _probe('U3-EPS-SINGLE-%s' % fld,
               lambda f=fld: get_fundamentals(PROBE_CODES[0], 'eps',
                                              fields=[f], date=PROBE_DATE,
                                              is_dataframe=True), (fld,))
    _probe('U3-GROWTH-COMBO',
           lambda: get_fundamentals(PROBE_CODES[0], 'growth_ability',
                                    fields=['or_yoy', 'publ_date', 'end_date'],
                                    date=PROBE_DATE, is_dataframe=True),
           ('or_yoy', 'publ_date', 'end_date'))

    # ---- U3 date 格式：'20260630' vs '2026-06-30' vs None ----
    _probe('U3-DATE-YYYYMMDD',
           lambda: get_fundamentals(PROBE_CODES[0], 'valuation',
                                    fields=['float_value'], date=PROBE_DATE,
                                    is_dataframe=True), ('float_value',))
    _probe('U3-DATE-DASH',
           lambda: get_fundamentals(PROBE_CODES[0], 'valuation',
                                    fields=['float_value'], date=PROBE_DATE2,
                                    is_dataframe=True), ('float_value',))
    _probe('U3-DATE-NONE',
           lambda: get_fundamentals(PROBE_CODES[0], 'valuation',
                                    fields=['float_value'], date=None,
                                    is_dataframe=True), ('float_value',))

    # ---- U3 is_dataframe kwarg：True / False / 缺省（TypeError/KeyError 分类）----
    _probe('U3-ISDF-TRUE',
           lambda: get_fundamentals(PROBE_CODES[0], 'valuation',
                                    fields=['float_value'], date=PROBE_DATE,
                                    is_dataframe=True), ('float_value',))
    _probe('U3-ISDF-FALSE',
           lambda: get_fundamentals(PROBE_CODES[0], 'valuation',
                                    fields=['float_value'], date=PROBE_DATE,
                                    is_dataframe=False), ('float_value',))
    _probe('U3-ISDF-OMIT',
           lambda: get_fundamentals(PROBE_CODES[0], 'valuation',
                                    fields=['float_value'], date=PROBE_DATE),
           ('float_value',))


def _run_perf_timing():
    """P 计时：50 码逐码 get_fundamentals 单次耗时 → 外推 15000 码单周。"""
    import time
    codes = list(PROBE_CODES)
    try:
        pool = list(get_Ashares())
        if len(pool) >= PERF_CALL_N:
            codes = pool[:PERF_CALL_N]
    except Exception:
        pass
    # 补足 50 码（池不可得时循环复用，仅统计耗时）
    while len(codes) < PERF_CALL_N:
        codes.append(codes[len(codes) % len(PROBE_CODES)])
    codes = codes[:PERF_CALL_N]

    times = []
    ok = 0
    t0 = time.time()
    for code in codes:
        t1 = time.time()
        try:
            get_fundamentals(code, 'valuation', fields=['float_value'],
                             date=PROBE_DATE, is_dataframe=True)
            ok += 1
        except Exception as exc:
            log.info('GF-P-SKIP %s %s' % (code, type(exc).__name__))
        times.append(time.time() - t1)
    total = time.time() - t0
    times.sort()
    mean = total / max(len(times), 1)
    est_weekly = mean * PERF_WEEKLY_N
    log.info('GF-P-SUMMARY calls=%d ok=%d total=%.4fs mean=%.4fs min=%.4fs med=%.4fs max=%.4fs'
             % (len(times), ok, total, mean, times[0], times[len(times) // 2], times[-1]))
    log.info('GF-P-EXTRAP weekly_15000=%.1fs threshold=%.1fs verdict=%s'
             % (est_weekly, PERF_THRESHOLD_S,
                'PASS' if est_weekly <= PERF_THRESHOLD_S else 'OVER-BUDGET'))


def initialize(context):
    set_benchmark('000300.SS')
    g.done_probe = False


def before_trading_start(context, data):
    """首日（2026-07-01）执行全部探针，g.done_probe 防重。"""
    if g.done_probe:
        return
    g.done_probe = True
    _run_ufund_blocks()
    _run_perf_timing()


def handle_data(context, data):
    pass