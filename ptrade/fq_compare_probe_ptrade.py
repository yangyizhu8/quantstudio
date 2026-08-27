"""
PTrade fq 参数实证：fq='pre' 到底返回前复权还是原始价？
================================================================
只在第 1 天 handle_data 执行一次，对比 4 种 fq 取值的 get_history 结果。

关键标的：
  159995.SZ — factor=0.5（07-07 除权），raw_close ≈ 2.4 / close_front ≈ 1.2
  510300.SS — factor=1.0（无除权），raw = front，作为对照

判定规则（看 159995 的 close 值）：
  fq='pre' close ≈ 1.2  →  fq='pre' 是前复权（与默认不复权不同）
  fq='pre' close ≈ 2.4  →  fq='pre' 也是不复权（或 fq 参数无效）

PTrade 回测设置：
  - 初始资金：100000（不下单，仅取数）
  - 频率：日线
  - 区间：2026-06-01 ~ 2026-06-05
================================================================
"""


def initialize(context):
    g.done = False


def handle_data(context, data):
    if g.done:
        return
    g.done = True

    for sec in ['159995.SZ', '510300.SS']:
        log.info("=" * 55)
        log.info("[FQ_TEST] %s — 最近5日close对比" % sec)

        # 1) 不指定 fq（PTrade 默认行为）
        try:
            h1 = get_history(5, '1d', ['close'], [sec], include=True)
            log.info("[NO_FQ   ] %s" % str(h1))
        except Exception as e:
            log.info("[NO_FQ   ] error: %s" % str(e))

        # 2) fq='pre'（前复权）
        try:
            h2 = get_history(5, '1d', ['close'], [sec], fq='pre', include=True)
            log.info("[FQ=pre  ] %s" % str(h2))
        except Exception as e:
            log.info("[FQ=pre  ] error: %s" % str(e))

        # 3) fq='none'（显式不复权）
        try:
            h3 = get_history(5, '1d', ['close'], [sec], fq='none', include=True)
            log.info("[FQ=none ] %s" % str(h3))
        except Exception as e:
            log.info("[FQ=none ] error: %s" % str(e))

        # 4) fq=None（Python None）
        try:
            h4 = get_history(5, '1d', ['close'], [sec], fq=None, include=True)
            log.info("[FQ=None ] %s" % str(h4))
        except Exception as e:
            log.info("[FQ=None ] error: %s" % str(e))

    log.info("=" * 55)
    log.info("[JUDGE] 159995 raw_close≈2.4  close_front≈1.2")
    log.info("[JUDGE] 若 FQ=pre close≈1.2 → PTrade fq='pre' 是前复权（信号前复权/撮合不复权）")
    log.info("[JUDGE] 若 FQ=pre close≈2.4 → PTrade fq='pre' 也是不复权（全链路不复权）")
