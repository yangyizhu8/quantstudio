# -*- coding: utf-8 -*-
# probe_positions_ptrade.py - P-POS 探针（P-D11/WP-A1 前置取证，2026-08-26，取证用途，不入库）
# 设计依据：docs/dual-end-alignment-master-plan.md WP-A（P-POS 前置探针，映射禁臆测）
# 目标：钉死平台 get_positions()/get_position() 返回契约，为 A1 持仓视图归一 wrapper 提供实证：
#   ① 键原样格式（后缀体系：XSHE/XSHG vs SS/SZ vs 裸码）——CANSLIM STOPDBG basis=-1.0000 根因取证；
#   ② Position 对象字段集（amount/volume/cost_basis/market_value/...）——A1 属性映射依据；
#   ③ 卖出后残影行（volume=0 键是否残留）——CP2 过滤依据（fall_reversal positions=18 虚报取证）；
#   ④ get_position() 后缀敏感性（.SS/.XSHG/.SH/裸码 哪种命中、未持仓代码返回形态）——B1 delta 取持仓入口；
#   ⑤ get_Ashares()[0] 原样格式对照（「同平台双后缀体系并存」假设的直接证据）。
# 用法：PTrade 新建策略 → 回测区间设为 ≥4 个交易日（建议 2026-07-01~07-06）→
#       初始资金 100,000，基准沪深300。回测后导出平台日志，按 PPOS- 前缀行解析回贴。
# 阶段（run_daily 14:55 逐日推进，g.phase 计数）：
#   D1 买入 600000.SS 200 股 + get_Ashares 格式探查 + 买入后即时 dump；
#   D2 get_positions 全量 dump（键/类型/字段）+ get_position 六种后缀/持仓形态对照；
#   D3 清仓（order_target_value(STOCK, 0)）+ 清仓后即时 dump；
#   D4 残影检查：再 dump get_positions + get_position 对照（卖出次日键是否残留/volume=0）。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

STOCK = '600000.SS'
BUY_SHARES = 200

# 探查的 Position 属性候选（本地契约重点字段在前；平台命名未知，全靠 dump 实证）
POS_FIELDS = (
    'amount', 'volume', 'total_amount', 'closeable_amount', 'cost_basis',
    'avg_cost', 'price', 'last_price', 'last_sale_price', 'market_value',
    'value', 'profit', 'sid', 'stock_code', 'code',
)

# get_position 后缀敏感性矩阵：(入参, 标签)——600000 已持仓，000001 全程未持仓
GP_VARIANTS = (
    ('600000.SS', 'held-SS'),
    ('600000.XSHG', 'held-XSHG'),
    ('600000.SH', 'held-SH'),
    ('600000', 'held-bare'),
    ('000001.SZ', 'notheld-SZ'),
    ('000001.XSHE', 'notheld-XSHE'),
)


def _fmt(v):
    try:
        s = str(v)
    except Exception:
        return '<str-fail>'
    return s if len(s) <= 48 else s[:45] + '...'


def _probe_ashares():
    """⑤ get_Ashares 原样格式（与 get_positions 键格式对照 → 双体系并存证据）。"""
    try:
        pool = get_Ashares()
        codes = list(pool)
        log.info('PPOS-ASHARES n=%d first5=%s' % (len(codes), [_fmt(c) for c in codes[:5]]))
    except Exception as exc:
        log.info('PPOS-ASHARES-EXC %s: %s' % (type(exc).__name__, exc))


def _dump_positions(tag):
    """①② get_positions 原样 dump：类型/键列表/逐 Position 字段与公开属性名。"""
    try:
        positions = get_positions()
    except Exception as exc:
        log.info('PPOS-%s GETPOS-EXC %s: %s' % (tag, type(exc).__name__, exc))
        return
    if positions is None:
        log.info('PPOS-%s GETPOS-None' % tag)
        return
    log.info('PPOS-%s TYPE %s' % (tag, type(positions).__name__))
    try:
        keys = list(positions.keys())
    except Exception as exc:
        log.info('PPOS-%s KEYS-EXC %s: %s' % (tag, type(exc).__name__, exc))
        return
    log.info('PPOS-%s KEYS n=%d raw=%s' % (tag, len(keys), [_fmt(k) for k in keys[:10]]))
    for k in keys[:4]:
        try:
            pos = positions[k]
        except Exception as exc:
            log.info('PPOS-%s ITEM %s ITEM-EXC %s: %s' % (tag, _fmt(k), type(exc).__name__, exc))
            continue
        log.info('PPOS-%s ITEM %r postype=%s' % (tag, k, type(pos).__name__))
        fields = {}
        for name in POS_FIELDS:
            try:
                v = getattr(pos, name, None)
            except Exception:
                v = None
            if v is not None:
                fields[name] = _fmt(v)
        log.info('PPOS-%s FIELDS %s' % (tag, fields))
        try:
            pub = [a for a in dir(pos) if not a.startswith('_')]
            log.info('PPOS-%s DIR %s' % (tag, pub[:80]))
        except Exception:
            pass


def _probe_variants(tag):
    """④ get_position 后缀敏感性：六种入参形态逐一调用，记录命中/None/异常与关键字段。"""
    for probe, label in GP_VARIANTS:
        try:
            p = get_position(probe)
        except Exception as exc:
            log.info('PPOS-%s GP %s(%s) -> EXC %s: %s' % (tag, probe, label,
                                                          type(exc).__name__, exc))
            continue
        if p is None:
            log.info('PPOS-%s GP %s(%s) -> None' % (tag, probe, label))
            continue
        vol = _fmt(getattr(p, 'volume', None))
        amt = _fmt(getattr(p, 'amount', None))
        cb = _fmt(getattr(p, 'cost_basis', None))
        mv = _fmt(getattr(p, 'market_value', None))
        log.info('PPOS-%s GP %s(%s) -> type=%s volume=%s amount=%s cost_basis=%s mv=%s'
                 % (tag, probe, label, type(p).__name__, vol, amt, cb, mv))


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
    log.info('PPOS-PHASE %d date=%s' % (g.phase, d))
    if g.phase == 1:
        _probe_ashares()
        try:
            oid = order(STOCK, BUY_SHARES)
            log.info('PPOS-BUY %s x%d ret=%s' % (STOCK, BUY_SHARES, _fmt(oid)))
        except Exception as exc:
            log.info('PPOS-BUY-EXC %s: %s' % (type(exc).__name__, exc))
        _dump_positions('D1_after_buy')
    elif g.phase == 2:
        _dump_positions('D2')
        _probe_variants('D2')
    elif g.phase == 3:
        try:
            oid = order_target_value(STOCK, 0)
            log.info('PPOS-SELLALL %s ret=%s' % (STOCK, _fmt(oid)))
        except Exception as exc:
            log.info('PPOS-SELLALL-EXC %s: %s' % (type(exc).__name__, exc))
        _dump_positions('D3_after_sell')
    elif g.phase == 4:
        _dump_positions('D4_ghost')
        _probe_variants('D4')
    else:
        pass


def handle_data(context, data):
    pass
