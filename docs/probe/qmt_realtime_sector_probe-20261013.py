#coding:gbk
# ============================================================================
# QS_REALTIME_PROBE（D-31，2026-10-13 立项，用户令）
# 目的：定谳 ContextInfo.get_stock_list_in_sector(sectorname, realtime) 的
#       realtime 参数是否支持「历史时点板块成份回放」（PIT 名单可行性 B4）。
# 文档依据：docs/qmt/inner-api/07-行情函数.md:3288-3313（realtime=实时数据的
#       毫秒级时间戳——语义未承诺历史回放，须实测）。
# 用法：粘贴进迅投知识库「新建策略」，回测模式任意区间运行一次，
#       把带 QS_REALTIME_PROBE 前缀的输出行整段回贴给 DSH 会话。
# 判读（三分支）：
#   ① diff_old>0 且 old 缺 2020 年后上市代码（30xxxx/68xxxx 样本）
#      → realtime 支持历史回放 → B4=支持，名单 PIT 可行性大增；
#   ② 三组 diff 全 0 → realtime 不影响返回（被忽略/仅实时语义）
#      → B4=不支持 → ST/板块历史成份登记为 QMT 能力边界；
#   ③ 介于中间/异常 → 原样回贴输出，呈报裁定。
# 安全性：纯只读探针（一次板块查询 ×4 + 集合比对），无下单、无写文件。
# ============================================================================
import time


def init(C):
    pass


def handlebar(C):
    if getattr(C, '_qs_rt_probe_done', False):
        return
    try:
        C._qs_rt_probe_done = True
    except Exception:
        pass
    sector = '沪深A股'
    base = set(C.get_stock_list_in_sector(sector))            # 不传 realtime（文档示例形态）
    now_ms = int(time.time() * 1000)                          # 当前毫秒时间戳
    hist_ms = int(time.mktime(time.strptime('20260105', '%Y%m%d')) * 1000)  # 回测区间日
    old_ms = int(time.mktime(time.strptime('20200101', '%Y%m%d')) * 1000)   # 远古时点
    try:
        p_now = set(C.get_stock_list_in_sector(sector, now_ms))
    except Exception as e:
        p_now = None
        print('QS_REALTIME_PROBE now_call_err=%s' % repr(e)[:120])
    try:
        p_hist = set(C.get_stock_list_in_sector(sector, hist_ms))
    except Exception as e:
        p_hist = None
        print('QS_REALTIME_PROBE hist_call_err=%s' % repr(e)[:120])
    try:
        p_old = set(C.get_stock_list_in_sector(sector, old_ms))
    except Exception as e:
        p_old = None
        print('QS_REALTIME_PROBE old_call_err=%s' % repr(e)[:120])
    print('QS_REALTIME_PROBE base=%d now=%s hist=%s old=%s' % (
        len(base),
        ('%d' % len(p_now)) if p_now is not None else 'ERR',
        ('%d' % len(p_hist)) if p_hist is not None else 'ERR',
        ('%d' % len(p_old)) if p_old is not None else 'ERR'))
    if p_now is not None:
        print('QS_REALTIME_PROBE diff_base_vs_now=%d' % len(base ^ p_now))
    if p_hist is not None:
        print('QS_REALTIME_PROBE diff_base_vs_hist=%d' % len(base ^ p_hist))
    if p_old is not None:
        _d = base ^ p_old
        print('QS_REALTIME_PROBE diff_base_vs_old=%d' % len(_d))
        if _d:
            _only_old = sorted(x for x in _d if x not in base)   # 老时点在场、当前不在场
            _only_now = sorted(x for x in _d if x in base)       # 当前在场、老时点不在场（2020 后新股）
            print('QS_REALTIME_PROBE old_extra=%d now_extra=%d' % (len(_only_old), len(_only_now)))
            print('QS_REALTIME_PROBE now_extra_sample=%s' % _only_now[:10])
            print('QS_REALTIME_PROBE old_extra_sample=%s' % _only_old[:10])
    print('QS_REALTIME_PROBE done ts=%s' % time.strftime('%Y-%m-%d %H:%M:%S'))
