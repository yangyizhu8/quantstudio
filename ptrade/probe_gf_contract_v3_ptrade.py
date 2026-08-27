# probe_gf_contract_v3_ptrade.py - P-D10 探针三（2026-08-23，取证用途，不入库）
# 设计依据：docs/p-d10-gf-contract-design.md §3.3（探针二结论 → or_yoy 等价字段候选
#   operating_revenue_grow_rate / net_profit_grow_rate，需数值对照钉死口径/单位）
# 目标：对 4 只探针码 × growth_ability(operating_revenue_grow_rate/net_profit_grow_rate) + eps，
#       打印 end_date + 数值 + publ_date —— 与本地 fin_indicator 同码同期数值逐项对照：
#       本地 000001.SZ 2026-03-31: or_yoy=4.6516 np_yoy=3.0292 eps=0.67 publ=2026-04-25
#       本地 600000.SS 2026-03-31: or_yoy=1.4176 np_yoy=1.4945
#       本地 300255.SZ 2025-12-31: or_yoy=-15.1025 (2025-09-30: -13.1119)
#       本地 688496.SS 2025-12-31: or_yoy=-11.1582 (2025-09-30: -13.6446)
# 判定：operating_revenue_grow_rate ≈ or_yoy（同百分点单位 → 字段映射 or_yoy→operating_revenue_grow_rate）
#       net_profit_grow_rate ≈ np_yoy；eps ≈ eps。数值差>0.5pct 或符号分歧 → 口径分歧，映射存疑。
# 用法：PTrade 新建策略 → 回测 2026-07-01 ~ 2026-07-03，资金 100000，基准沪深300。
#       运行完成后回贴日志中以 GF3- 开头的行。appx 4 码 x 2 调用，秒级完成。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

PROBE_CODES = ['000001.SZ', '600000.SS', '300255.SZ', '688496.SS']
PROBE_DATE = '20260630'


def _probe(code, table, fields):
    try:
        df = get_fundamentals(code, table, fields=fields, date=PROBE_DATE,
                              is_dataframe=True)
    except Exception as exc:
        log.info('GF3-%s-%s ERR %s: %s' % (code, table, type(exc).__name__, exc))
        return
    parts = ['GF3-%s-%s OK' % (code, table)]
    if df is None or len(df) == 0:
        parts.append('EMPTY')
    else:
        for f in fields:
            if f in df.columns:
                vals = list(df[f].dropna().iloc[:2])
                parts.append('%s=%r' % (f, vals))
            else:
                parts.append('%s=ABSENT' % f)
    log.info(' '.join(parts))


def initialize(context):
    set_benchmark('000300.SS')
    g.done_probe = False


def before_trading_start(context, data):
    if g.done_probe:
        return
    g.done_probe = True
    for code in PROBE_CODES:
        _probe(code, 'growth_ability',
               ['operating_revenue_grow_rate', 'net_profit_grow_rate',
                'publ_date', 'end_date'])
        _probe(code, 'eps', ['eps', 'publ_date', 'end_date'])


def handle_data(context, data):
    pass