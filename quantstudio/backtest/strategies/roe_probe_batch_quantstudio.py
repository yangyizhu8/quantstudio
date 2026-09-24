"""
roe_probe_batch_quantstudio.py - ROE 覆盖探针载体（一次性采数工具，2026-09-05 任务三）。
非产品策略：为触发 source_import QS_ROE_PROBE v2 探针（多码批量 profit_ability+roe
+ roe 缺列/NULL）构造的最小批量请求载体。经转换管线生成 ptrade 产物（探针随转换注入），
上传平台跑 2026-01-01~2026-04-30 → 平台日志 grep QS_ROE_PROBE → 判型。采数完成即弃。
"""
import numpy as np
import pandas as pd


def initialize(context):
    set_benchmark('000300')
    # 缺失码样例（双端差异分析 P3<P2 集中 1-4 月，~12 码——代表性子集）
    g.missing_candidates = ['000656.SZ', '001366.SZ', '002326.SZ', '002535.SZ',
                            '002712.SZ', '002427.SZ', '002654.SZ', '002696.SZ',
                            '002713.SZ', '002805.SZ']
    # 正常对照码（roe 有值）
    g.normal_codes = ['000001.SZ', '600000.SS']
    g.done = False


def handle_data(context, data):
    if g.done:
        return
    today = context.current_dt.strftime('%Y-%m-%d')
    if today >= '2026-01-05':
        codes = g.missing_candidates + g.normal_codes
        log.info('ROE-PROBE-BATCH codes=%d date=%s' % (len(codes), today))
        df = get_fundamentals(codes, table='profit_ability',
                              fields=['roe', 'end_date'], date='20260131',
                              is_dataframe=True)
        n = len(df) if df is not None else 0
        log.info('ROE-PROBE-BATCH result rows=%d' % n)
        g.done = True
