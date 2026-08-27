# probe_fidelity_ashares_ptrade.py - PTrade 保真模式 P-A0 探针（2026-08-24，取证用途，不入库）
# 设计依据：docs/p-d10-gf-contract-design.md（保真模式方案 v2）P-A0 + ZCode 审核必改①
# 目标：采集平台真实 get_Ashares() 全 A 股池，供本地保真模式短窗（起始日>=快照日）对齐验证。
# 输出（FASHARES- 前缀日志行，回贴给 DSH 或保存平台日志文件后本地解析）：
#   FASHARES-DATE   回测起始日（快照采集日，快照 PIT 门禁用）
#   FASHARES-SUMMARY total=<n> sha256=<hex>   # 排序后裸码串的 SHA-256，供本地完整性校验
#   FASHARES-CODE   <code>                    # 逐码一行（平台返回顺序；裸码解析取前 6 位数字）
# 用法：PTrade 新建策略 → 回测起始日设为「当日或近 1-2 个交易日」（如 2026-08-26）→
#       回测 1-2 天即可（before_trading_start 首日执行一次），初始资金 100000，基准沪深300。
#       将平台日志导出为文本文件保存到本地，交给 DSH 执行 scripts/probe_platform_ashares.py 构建快照。
# PTRADE_RUNTIME_UNVERIFIED: 取证用途；平台 API 差异以实际运行为准。

import hashlib


def _run():
    try:
        pool = list(get_Ashares())
    except Exception as exc:
        log.info('FASHARES-ERR get_Ashares %s: %s' % (type(exc).__name__, exc))
        return
    if not pool:
        log.info('FASHARES-EMPTY get_Ashares returned empty')
        return
    # 计算排序后裸码串 SHA-256（完整性校验用）
    bare_sorted = sorted(_bare(c) for c in pool)
    digest = hashlib.sha256('|'.join(bare_sorted).encode('utf-8')).hexdigest()
    log.info('FASHARES-SUMMARY total=%d sha256=%s' % (len(bare_sorted), digest))
    for c in pool:
        log.info('FASHARES-CODE %s' % (c,))
    # 边界统计（供本地快速核对类别构成）
    try:
        from collections import Counter
        tails = Counter(_bare(c)[-2:] for c in pool)  # 后 2 位非交易所；改用后缀统计
    except Exception:
        pass


def _bare(code):
    s = str(code).strip().upper()
    if s == '':
        return ''
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    return s[:max(i, 6)]


def initialize(context):
    set_benchmark('000300.SS')
    g.done_ashares = False


def before_trading_start(context, data):
    """首日执行一次（防重）。FASHARES-DATE = 回测当前日期 = 快照采集日。"""
    if g.done_ashares:
        return
    g.done_ashares = True
    try:
        d = str(context.current_dt.date()) if hasattr(context.current_dt, 'date') else str(context.current_dt)
    except Exception:
        d = 'UNKNOWN'
    log.info('FASHARES-DATE %s' % d)
    _run()


def handle_data(context, data):
    pass