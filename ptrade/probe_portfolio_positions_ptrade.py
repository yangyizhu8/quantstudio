# -*- coding: utf-8 -*-
# probe_portfolio_positions_ptrade.py - P-POS-2 探针（P-D11b 前置取证，2026-08-26，取证用途，不入库）
# 设计依据：docs/evidence/pd11-implementation-acceptance-20260826.md §6.1（范围外发现①）
# 目标：钉死平台 context.portfolio.positions 容器契约（4/6 策略消费路径）：
#   ① 键原样格式（后缀体系：XSHE/XSHG vs SS/SZ vs 裸码）——与 get_positions() 的 F1 对照；
#   ② Position 对象字段集（amount/cost_basis/market_value/...）——与 P-POS F3 对照；
#   ③ 卖出后残影行（volume/amount=0 键是否残留、何时清理）——与 F4 对照；
#   ④ membership 语义（'code.SS' in positions 是否命中 'code.XSHG' 键——alias-aware 与否）；
#   ⑤ 与 get_positions() 键集 diff（同一时刻两个 API 返回的持仓是否一致）。
# 用法：PTrade 新建策略 → 回测区间 ≥4 个交易日（建议 2026-07-01~07-06）→
#       初始资金 100,000，基准沪深300。回测后导出平台日志，按 PPOS2- 前缀行解析回贴。
# 阶段（run_daily 14:55 逐日推进，g.phase 计数）：
#   D1 买入 600000.SS 200 股 + 510300.SS（沪 ETF）100 股——同时覆盖股票与 ETF 键格式；
#   D2 容器全量 dump（键/类型/字段）+ membership 交叉测试 + 与 get_positions() diff；
#   D3 清仓（order_target_value 0，两标的）；
#   D4 残影检查：容器再 dump + membership 复测。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

STOCK = '600000.SS'
ETF = '510300.SS'

# 探查的 Position 属性候选（与 P-POS F3 同集，补 portfolio 容器特有可能字段）
POS_FIELDS = (
    'amount', 'volume', 'total_amount', 'closeable_amount', 'cost_basis',
    'avg_cost', 'price', 'last_price', 'last_sale_price', 'market_value',
    'value', 'profit', 'sid', 'stock_code', 'code', 'enable_amount',
)


def _fmt(v):
    try:
        s = str(v)
    except Exception:
        return '<str-fail>'
    return s if len(s) <= 48 else s[:45] + '...'


def _dump_container(context, tag):
    """①② 容器原样 dump：类型/键列表/逐 Position 字段。"""
    try:
        positions = context.portfolio.positions
    except Exception as exc:
        log.info('PPOS2-%s CONT-EXC %s: %s' % (tag, type(exc).__name__, exc))
        return None
    if positions is None:
        log.info('PPOS2-%s CONT-None' % tag)
        return None
    log.info('PPOS2-%s TYPE %s' % (tag, type(positions).__name__))
    try:
        keys = list(positions.keys())
    except Exception as exc:
        log.info('PPOS2-%s KEYS-EXC %s: %s' % (tag, type(exc).__name__, exc))
        return positions
    log.info('PPOS2-%s KEYS n=%d raw=%s' % (tag, len(keys),
                                            [_fmt(k) for k in keys[:10]]))
    for k in keys[:4]:
        try:
            pos = positions[k]
        except Exception as exc:
            log.info('PPOS2-%s ITEM %s ITEM-EXC %s: %s'
                     % (tag, _fmt(k), type(exc).__name__, exc))
            continue
        log.info('PPOS2-%s ITEM %r postype=%s' % (tag, k, type(pos).__name__))
        fields = {}
        for name in POS_FIELDS:
            try:
                v = getattr(pos, name, None)
            except Exception:
                v = None
            if v is not None:
                fields[name] = _fmt(v)
        log.info('PPOS2-%s FIELDS %s' % (tag, fields))
    return positions


def _membership_test(context, tag):
    """④ membership 语义：不同后缀形态的 in 测试（alias-aware 与否）。"""
    try:
        positions = context.portfolio.positions
    except Exception:
        return
    probes = ('600000.SS', '600000.XSHG', '600000.SH', '600000',
              '510300.SS', '510300.XSHG', '510300.SH', '510300',
              '000001.SZ')
    results = {}
    for p in probes:
        try:
            results[p] = p in positions
        except Exception as exc:
            results[p] = 'EXC:%s' % type(exc).__name__
    log.info('PPOS2-%s MEMBERSHIP %s' % (tag, results))
    # 取值交叉：用 SS 后缀取 XSHG 键的值
    try:
        v = positions.get('600000.SS') if hasattr(positions, 'get') else None
        log.info('PPOS2-%s GET-SS %s' % (tag, _fmt(type(v).__name__) if v else 'None'))
    except Exception as exc:
        log.info('PPOS2-%s GET-EXC %s' % (tag, exc))


def _diff_with_get_positions(context, tag):
    """⑤ 与 get_positions() 键集 diff（P-D11 包装 vs 原生容器）。"""
    try:
        wrapped = set(get_positions().keys())     # P-D11 包装（键 .SS/.SZ）——本探针
        # 不含 P-D11 注入时为平台原生；无论哪种，diff 仍有意义
    except Exception as exc:
        log.info('PPOS2-%s GP-EXC %s: %s' % (tag, type(exc).__name__, exc))
        return
    try:
        container_keys = set(str(k) for k in context.portfolio.positions.keys())
    except Exception:
        container_keys = set()
    only_wrapped = sorted(wrapped - container_keys)
    only_container = sorted(container_keys - wrapped)
    log.info('PPOS2-%s DIFF wrapped_n=%d container_n=%d only_wrapped=%s'
             ' only_container=%s' % (tag, len(wrapped), len(container_keys),
                                     [_fmt(k) for k in only_wrapped[:5]],
                                     [_fmt(k) for k in only_container[:5]]))


def initialize(context):
    set_benchmark('000300.SS')
    g.phase = 0
    run_daily(context, _daily, time='14:55')


def _daily(context):
    g.phase += 1
    try:
        d = str(context.current_dt.date()) if hasattr(context.current_dt, 'date') \
            else str(context.current_dt)
    except Exception:
        d = 'UNKNOWN'
    log.info('PPOS2-PHASE %d date=%s' % (g.phase, d))
    if g.phase == 1:
        try:
            oid1 = order(STOCK, 200)
            log.info('PPOS2-BUY %s x200 ret=%s' % (STOCK, _fmt(oid1)))
        except Exception as exc:
            log.info('PPOS2-BUY-EXC %s: %s' % (type(exc).__name__, exc))
        try:
            oid2 = order(ETF, 100)
            log.info('PPOS2-BUY %s x100 ret=%s' % (ETF, _fmt(oid2)))
        except Exception as exc:
            log.info('PPOS2-BUY2-EXC %s: %s' % (type(exc).__name__, exc))
        _dump_container(context, 'D1_after_buy')
    elif g.phase == 2:
        _dump_container(context, 'D2')
        _membership_test(context, 'D2')
        _diff_with_get_positions(context, 'D2')
    elif g.phase == 3:
        for code in (STOCK, ETF):
            try:
                oid = order_target_value(code, 0)
                log.info('PPOS2-SELLALL %s ret=%s' % (code, _fmt(oid)))
            except Exception as exc:
                log.info('PPOS2-SELLALL-EXC %s %s: %s'
                         % (code, type(exc).__name__, exc))
        _dump_container(context, 'D3_after_sell')
    elif g.phase == 4:
        _dump_container(context, 'D4_ghost')
        _membership_test(context, 'D4')
        _diff_with_get_positions(context, 'D4')
    else:
        pass


def handle_data(context, data):
    pass
