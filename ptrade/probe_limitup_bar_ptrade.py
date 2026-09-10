# probe_limitup_bar_ptrade.py - D4 涨停判断 bar 字段实证（2026-09-09，取证用途，暂不入库）
# 背景：恐慌抄底策略 PTrade 转换后 07-17 selected=50 tradable=50 但 buy_submitted=0
#   （note=limit_up_skip_all_low_exposure_legal——50 只全部 high_limit>0 且 close>=high_limit）。
#   本地 R5 仅 9 只涨停（41 只可买）→ 平台 data[code].close/high_limit 语义与本地疑似不同。
# 目标：实证平台回测 handle_data 内 data[code] 的字段值——打印 5 只样本股 + 全池统计：
#   P1 close / volume（确认 tradable 判定依据）；
#   P2 high_limit（确认是否全 >0 且 close>=high_limit——验证"全涨停"是数据还是真实现象）；
#   P3 preclose（对照本地 high_limit=preclose*(1+limit_pct) 契约）；
#   P4 统计：全池中 close>=high_limit 的占比。
# 用法：PTrade 新建回测 → 粘贴本文件 → 区间 2026-07-17 ~ 2026-07-17 → 资金 100000 → 运行。
#       回贴 BLP- 开头日志行。PTRADE_RUNTIME_UNVERIFIED: 取证用途。

SAMPLE = ['600000.SS', '000001.SZ', '300750.SZ', '601318.SS', '000858.SZ']


def initialize(context):
    g.done = False


def handle_data(context, data):
    if g.done:
        return
    g.done = True
    day = context.current_dt.strftime('%Y-%m-%d')
    log.info('==== BLP-PROBE ' + day + ' ====')
    try:
        stocks = get_index_stocks('000905.SS', date=context.previous_date.strftime('%Y%m%d'))
    except Exception as exc:
        log.info('BLP-ERR get_index_stocks: ' + type(exc).__name__ + ' ' + str(exc))
        return
    log.info('BLP-P0 constituents=' + str(len(stocks or [])))

    # 样本股字段值
    for code in SAMPLE:
        try:
            bar = data[code]
            log.info('BLP-P1 %s close=%s high_limit=%s low_limit=%s volume=%s preclose=%s'
                     % (code, getattr(bar, 'close', None), getattr(bar, 'high_limit', None),
                        getattr(bar, 'low_limit', None), getattr(bar, 'volume', None),
                        getattr(bar, 'preclose', None)))
        except Exception as exc:
            log.info('BLP-P1 %s ERR %s' % (code, exc))

    # 全池统计：close>0 & volume>0（tradable 判定）；close>=high_limit 占比
    n = 0
    tradable = 0
    limit_up = 0
    hl_zero = 0
    samples_limitup = []
    for code in (stocks or [])[:60]:
        try:
            bar = data[code]
            n += 1
            close = getattr(bar, 'close', None)
            vol = getattr(bar, 'volume', None)
            hl = getattr(bar, 'high_limit', None)
            if close is not None and vol is not None and close > 0 and vol > 0:
                tradable += 1
            if hl is not None and hl > 0:
                if close is not None and close >= hl:
                    limit_up += 1
                    if len(samples_limitup) < 5:
                        samples_limitup.append(code)
            elif hl is not None and hl == 0:
                hl_zero += 1
        except Exception:
            pass
    log.info('BLP-P2 n=%d tradable=%d limit_up=%d hl_zero=%d samples_limitup=%s'
             % (n, tradable, limit_up, hl_zero, samples_limitup))


def after_trading_end(context, data):
    pass
