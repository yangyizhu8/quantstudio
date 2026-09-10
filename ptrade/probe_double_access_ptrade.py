# probe_double_access_ptrade.py - D4 双次访问实证（2026-09-09，取证用途）
# 背景：BL-B3 单次访问 ok=30；真实策略对同一 code 访问两次（tradable 统计 + 买入）。
#   若平台 BarDict 单次消费（第二次返回 0/空）→ 买入循环全 skip。
# 目标：对 3 只样本 code 连续访问 data[code] 两次，对比 close/volume/high_limit。
# 用法：PTrade 回测 2026-07-17 单日，回贴 DA- 开头行。

SAMPLE = ['600486.SS', '600563.SS', '603786.SS']


def initialize(context):
    g.done = False


def handle_data(context, data):
    if g.done:
        return
    g.done = True
    for code in SAMPLE:
        try:
            b1 = data[code]
            c1 = getattr(b1, 'close', None)
            v1 = getattr(b1, 'volume', None)
            h1 = getattr(b1, 'high_limit', None)
        except Exception as exc:
            log.info('DA-%s first ERR %s' % (code, exc))
            continue
        try:
            b2 = data[code]
            c2 = getattr(b2, 'close', None)
            v2 = getattr(b2, 'volume', None)
            h2 = getattr(b2, 'high_limit', None)
        except Exception as exc:
            log.info('DA-%s second ERR %s' % (code, exc))
            continue
        same_c = (c1 == c2)
        same_v = (v1 == v2)
        same_h = (h1 == h2)
        log.info('DA-%s c1=%s c2=%s same_c=%s v1=%s v2=%s same_v=%s h1=%s h2=%s same_h=%s'
                 % (code, c1, c2, same_c, v1, v2, same_v, h1, h2, same_h))


def after_trading_end(context, data):
    pass
