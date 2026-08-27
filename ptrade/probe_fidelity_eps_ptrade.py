# probe_fidelity_eps_ptrade.py - PTrade 保真模式 P-A2 探针（2026-08-24，取证用途，不入库）
# 设计依据：docs/p-d10-gf-contract-design.md（保真模式方案 v2）P-A2 + ZCode 审核小项①：
#   "实施第一步必须是平台端枚举 eps 类可用字段（全表 schema dump），映射表按实测建"
# 目标：钉死平台 eps 表真实 schema，逐候选基础字段确认可得性 + 双码数值样例，
#       供本地 fidelity_eps_basis 双端映射表按实测构建（任一端缺失 → 显性报错/降级 passthrough）。
# 输出（FEPS- 前缀日志行，回贴给 DSH 汇总）：
#   FEPS-SCHEMA       eps 表 fields=None 全列枚举
#   FEPS-FIELD <f>    OK val=<v> end_date=<e> | ERR <exc>
# 用法：PTrade 新建策略 → 回测 2026-07-01 ~ 2026-07-03，初始资金 100000，基准沪深300。
#       运行完成后，把日志中以 FEPS- 开头的行全部回贴给 DSH 汇总。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

PROBE_CODE = '000001.SZ'   # 深主板
PROBE_CODE2 = '600000.SS'  # 沪主板
PROBE_DATE = '20260630'    # T-1（回测起始日 07-01 前一日）

# 候选基础字段：本地 eps 表列（base.py/ptrade_api.py _FUND_TABLES.eps）∩ 猜测平台名 + 常见变体
EPS_CANDIDATES = ['eps', 'bps', 'diluted_eps', 'deducted_eps', 'operating_eps',
                  'basic_eps', 'eps_ttm', 'total_asset_share']


def _probe(label, call):
    try:
        obj = call()
    except Exception as exc:
        log.info('FEPS-%s ERR %s: %s' % (label, type(exc).__name__, exc))
        return None
    line = 'FEPS-%s OK type=%s' % (label, type(obj).__name__)
    if obj is None:
        line += ' val=None'
    elif hasattr(obj, 'shape'):
        line += ' shape=%s' % (tuple(obj.shape),)
    elif hasattr(obj, '__len__'):
        line += ' len=%d' % len(obj)
    log.info(line)
    return obj


def _schema_block():
    obj = _probe('SCHEMA',
                 lambda: get_fundamentals(PROBE_CODE, 'eps', fields=None,
                                          date=PROBE_DATE, is_dataframe=True))
    if obj is not None and hasattr(obj, 'columns'):
        log.info('FEPS-SCHEMA-COLS %s' % (list(obj.columns),))
        if len(obj) > 0:
            first = obj.iloc[0]
            for col in ('end_date', 'publ_date', 'eps'):
                if col in obj.columns:
                    log.info('FEPS-SCHEMA-%s %r' % (col, first[col]))


def _field_block():
    for fld in EPS_CANDIDATES:
        for code, tag in ((PROBE_CODE, 'A'), (PROBE_CODE2, 'B')):
            obj = _probe('FIELD-%s-%s' % (tag, fld),
                         lambda f=fld, c=code: get_fundamentals(c, 'eps', fields=[f],
                                                                date=PROBE_DATE,
                                                                is_dataframe=True))
            if obj is not None and hasattr(obj, 'columns') and len(obj) > 0:
                try:
                    val = obj.iloc[0].get(fld, 'ABSENT')
                    ed = obj.iloc[0].get('end_date', 'ABSENT') if 'end_date' in obj.columns else 'N/A'
                except Exception:
                    val, ed = '?', '?'
                log.info('FEPS-FIELD-%s-%s VAL %r end_date=%r' % (tag, fld, val, ed))


def initialize(context):
    set_benchmark('000300.SS')
    g.done_eps = False


def before_trading_start(context, data):
    if g.done_eps:
        return
    g.done_eps = True
    _schema_block()
    _field_block()


def handle_data(context, data):
    pass