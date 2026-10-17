#coding:gbk
# docs/probe/qmt_lifecycle_probe-20261017.py
# ============================================================================
# QLC 回填探针（QLC-01 handlebar/bar 驱动回调时机 ｜ QLC-04 定时任务/遍历语义）
#
# 目的：QLC-01/04 现为「未验证」，缺 QMT 腿实测。本探针只读观测回调语义：
#   ① init 与 handlebar 的调用时刻/次数（bar 驱动 vs 每日一次）
#   ② ContextInfo 上下文面（barpos/period/stock_code/is_last_bar/get_bar_timetag）
#   ③ 定时/遍历语义面（run_time 等周期调度接口是否存在）
#
# 用法（QMT）：新建回测策略 -> 粘贴全文 -> 区间建议 2024-01-01~2024-03-31（约 58 个交易日）
#   -> 运行 -> 回抄全部 [LC_PROBE] 行
# 判据：
#   - n（handlebar 次数）与 barpos 关系、bar_timetag 可得性、period 取值 => QLC-01
#   - run_time 类接口存在性 + 是否有非 bar 驱动回调 => QLC-04
# ============================================================================


def _qs_lc_now():
    try:
        import time as _t
        return _t.strftime("%H:%M:%S", _t.localtime())
    except Exception:
        return "?"


def init(C):
    C.qs_lc_n = 0
    print("[LC_PROBE] init ts=%s" % _qs_lc_now())
    for _a in ("barpos", "period", "stock_code", "is_last_bar", "get_bar_timetag",
               "run_time", "set_universe", "get_market_data_ex", "get_financial_data",
               "get_stock_list_in_sector", "get_instrument_detail"):
        try:
            _has = hasattr(C, _a)
        except Exception as _e:
            _has = "EXC:%r" % (_e,)
        print("[LC_PROBE] ctx_has %s=%s" % (_a, _has))


def handlebar(C):
    C.qs_lc_n += 1
    _n = C.qs_lc_n
    if _n <= 5 or _n % 20 == 0:
        _bp = _pd = _bt = _last = None
        try:
            _bp = C.barpos
        except Exception as _e:
            _bp = "EXC:%r" % (_e,)
        try:
            _pd = C.period
        except Exception as _e:
            _pd = "EXC:%r" % (_e,)
        try:
            _bt = C.get_bar_timetag(C.barpos)
        except Exception as _e:
            _bt = "EXC:%r" % (_e,)
        try:
            _last = C.is_last_bar()
        except Exception as _e:
            _last = "EXC:%r" % (_e,)
        print("[LC_PROBE] bar n=%d ts=%s barpos=%s period=%s bar_timetag=%s is_last=%s"
              % (_n, _qs_lc_now(), _bp, _pd, _bt, _last))
    try:
        if C.is_last_bar():
            print("[LC_PROBE] END n_total=%d ts=%s" % (_n, _qs_lc_now()))
    except Exception:
        pass
