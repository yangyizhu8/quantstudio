# probe_gf_contract_v2_ptrade.py - P-D10 探针二（2026-08-22，取证用途，不入库）
# 设计依据：docs/p-d10-gf-contract-design.md §3.2（or_yoy 处置方向 A：探针二是事实补全）
# 目标（用户/ZCode 复核指令）：
#   ① 全表 schema 枚举：growth_ability/eps/valuation + 候选表（income_statement/profit_ability/
#      cashflow_statement/operating_ability/debt_paying_ability）逐表 fields=None 全列返回，
#      钉死平台各表真实可用 schema（v1 只看到 3 列，是"请求返回"还是"全表 schema"未决）
#   ② 等价字段语义比对：在 growth_ability 及候选表上逐候选增长字段请求（np_yoy/equity_yoy/
#      oper_rev_yoy/revenue_yoy/netprofit_yoy/yoy + income_statement.operating_revenue 原始值），
#      记录每个字段 OK/KeyError 与样例值+end_date（供与本地 fin_indicator 同码同期数值语义比对）
#   ③ 100 码大 list 计时（外推 5000 码单次 list 周成本）+ 必查项②：返回唯一 code 数 vs 请求数
#      （截断/批量上限检测 → 决定 wrapper 是否需要分片；超上限则 wrapper 改 chunk 分片循环）
# 用法：PTrade 新建策略 → 回测 2026-07-01 ~ 2026-07-03，初始资金 100000，基准沪深300。
#       运行完成后，把日志中以 GF2- 开头的行全部回贴给 DSH 汇总。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

PROBE_CODE = '000001.SZ'     # 单码 schema/字段探针用（深主板）
PROBE_DATE = '20260630'      # T-1（回测起始日 07-01 前一日，避免 D3-X4 平台缺数日）
SCHEMA_TABLES = [
    'growth_ability', 'eps', 'valuation',
    'income_statement', 'profit_ability',
    'cashflow_statement', 'operating_ability', 'debt_paying_ability',
]
GROWTH_FIELDS = ['or_yoy', 'np_yoy', 'equity_yoy', 'oper_rev_yoy',
                 'revenue_yoy', 'netprofit_yoy', 'yoy']
INC_FIELDS = ['operating_revenue', 'operating_cost']   # 原始值 → 手动算营收同比的证据
PROFIT_FIELDS = ['roe', 'roa']
LIST_SIZES = [100, 500]      # 大 list 计时+截断检测（100 基础 → 500 探上限）


def _probe(label, call):
    """执行一次探针并打印 GF2- 描述行；返回结果对象。"""
    try:
        obj = call()
    except Exception as exc:
        log.info('GF2-%s ERR %s: %s' % (label, type(exc).__name__, exc))
        return None
    line = 'GF2-%s OK type=%s' % (label, type(obj).__name__)
    if obj is None:
        line += ' val=None'
    elif hasattr(obj, 'shape'):
        line += ' shape=%s' % (tuple(obj.shape),)
    elif obj is not None and hasattr(obj, '__len__'):
        line += ' len=%d' % len(obj)
    if hasattr(obj, 'index') and hasattr(obj, 'columns'):
        line += (' | idx=%s first5=%s | cols=%s' % (
            type(obj.index).__name__,
            [str(x) for x in list(obj.index)[:5]],
            list(obj.columns)))
        if len(obj) > 0:
            line += ' | end_date=%r np_yoy=%r' % (
                obj['end_date'].iloc[0] if 'end_date' in obj.columns else 'ABSENT',
                obj['np_yoy'].iloc[0] if 'np_yoy' in obj.columns else 'ABSENT')
    log.info(line)
    return obj


def _schema_block():
    """① 全表 schema 枚举：fields=None 返回全列。"""
    for table in SCHEMA_TABLES:
        _probe('SCHEMA-%s' % table,
               lambda t=table: get_fundamentals(PROBE_CODE, t, fields=None,
                                                date=PROBE_DATE, is_dataframe=True))


def _field_block(table, fields, tag):
    """② 字段可得性：逐候选字段请求（KeyError/空/OK 分类 + 样例值）。"""
    for fld in fields:
        _probe('FIELD-%s-%s' % (tag, fld),
               lambda f=fld, t=table: get_fundamentals(PROBE_CODE, t, fields=[f],
                                                       date=PROBE_DATE,
                                                       is_dataframe=True))


def _list_timing():
    """③ 大 list 单调用计时 + 截断检测（必查项②）。"""
    import time
    try:
        pool = [str(c) for c in get_Ashares()]
    except Exception as exc:
        log.info('GF2-LIST-POOL-ERR %s: %s' % (type(exc).__name__, exc))
        return
    log.info('GF2-LIST-POOL size=%d' % len(pool))
    for n in LIST_SIZES:
        if len(pool) < n:
            log.info('GF2-LIST-SIZE-%d SKIP pool=%d' % (n, len(pool)))
            continue
        codes = pool[:n]
        t1 = time.time()
        obj = _probe('LIST-TIMING-%d' % n,
                     lambda c=codes: get_fundamentals(c, 'valuation',
                                                      fields=['float_value'],
                                                      date=PROBE_DATE,
                                                      is_dataframe=True))
        elapsed = time.time() - t1
        # 必查项②：返回唯一 code 数 == 请求数 ？（平台截断/批量上限检测）
        rows = 0
        uniq = -1
        if obj is not None and hasattr(obj, 'index'):
            try:
                rows = len(obj)
                uniq = len(set(str(x) for x in obj.index))
            except Exception as exc:
                uniq = -2
        log.info('GF2-LIST-TIMING-%d elapsed=%.3fs rows=%d req=%d uniq_codes=%d verdict=%s'
                 % (n, elapsed, rows, len(codes), uniq,
                    'FULL' if rows == len(codes) and uniq == len(codes)
                    else 'TRUNCATED-OR-LOSS'))


def initialize(context):
    set_benchmark('000300.SS')
    g.done_probe = False


def before_trading_start(context, data):
    """首日（2026-07-01）执行全部探针，g.done_probe 防重。"""
    if g.done_probe:
        return
    g.done_probe = True
    _schema_block()
    _field_block('growth_ability', GROWTH_FIELDS, 'GF')
    _field_block('income_statement', INC_FIELDS, 'INC')
    _field_block('profit_ability', PROFIT_FIELDS, 'PROFIT')
    _list_timing()


def handle_data(context, data):
    pass