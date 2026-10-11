#coding:gbk
"""QS M5 探针 —— QMT 真实环境一次性取证（只读，不下单）。

用途（M5 批2 用户域操作件）：
  在 QMT 客户端（迅投内置 Python）以「模型交易/回测」方式运行**一次**，导出输出日志；
  日志内 `QS_M5_PROBE:` 前缀行即结构化证据，覆盖 M5 清单 11 项（P0×4 + P1×6 + P2×1）。

纪律（写死）：
  * **只读**：本探针不调用 passorder，不下任何单；
  * **严禁实盘账号**：账号串用占位 'testS'（08-交易函数.md 惯例）；
  * 逐项独立 try/except：任一项失败不影响其余项，失败项打印 `ok=false` + 异常原文（不掩盖）。

依据：docs/qmt/inner-api/（转写册）行级引用见各探测点注释。
输出格式：每行 `QS_M5_PROBE: <tag> <json>`，便于回传后由本机脚本（P-4）解析归因。
"""

import json as _json

ACCOUNT = 'testS'          # 占位（回测/模型交易界面运行时由策略配置账号覆盖）
PROBE_CODE = '000001.SZ'   # 探测标的（平安银行；请确认本地已补充该标的日线数据）
SECTOR = '沪深A股'          # M5-9：等价性探测


def _p(tag, **kw):
    """结构化打印（唯一输出通道）。"""
    try:
        body = _json.dumps(kw, ensure_ascii=False, default=repr)
    except Exception as exc:
        body = '{"_json_error": %r}' % (exc,)
    print('QS_M5_PROBE: %s %s' % (tag, body))


def _safe(tag, fn):
    """逐项独立探测：异常不掩盖、不中断其余项。"""
    try:
        return fn()
    except Exception as exc:
        _p(tag, ok=False, err=repr(exc)[:300])
        return None


def _shape_of(obj):
    """返回 (类型名, 顶层键样本/长度, 内层键样本)——供形态判定（M5-1 核心）。"""
    tname = type(obj).__name__
    outer = None
    inner = None
    if isinstance(obj, dict):
        keys = list(obj.keys())
        outer = [str(k) for k in keys[:5]]
        first = obj.get(keys[0]) if keys else None
        if isinstance(first, dict):
            inner = [str(k) for k in list(first.keys())[:5]]
    elif hasattr(obj, 'index') and hasattr(obj, 'columns'):
        outer = [str(k) for k in list(obj.index)[:5]]
        inner = [str(k) for k in list(obj.columns)[:5]]
    return tname, outer, inner


# ---------------------------------------------------------------------------
# init：M5-3（init 内不可用性）+ M5-10（跨 bar 存活标记种子）
# ---------------------------------------------------------------------------

def init(C):
    _p('init_enter', account=getattr(C, 'accountID', None),
       barpos=getattr(C, 'barpos', None))

    # M5-3：06-系统函数.md:12「init 函数执行完成前部分接口无法使用，如交易日获取函数
    # get_trading_dates」——本项**必须**在 init 内调用以验证该约束是否如实。
    def _m53_init():
        r = C.get_trading_dates(PROBE_CODE, '', '', 5, '1d')
        _p('M5-3_trading_dates_in_init', ok=True, type=type(r).__name__,
           len=(len(r) if hasattr(r, '__len__') else None), sample=repr(r)[:300])
    _safe('M5-3_trading_dates_in_init', _m53_init)

    # M5-10：ContextInfo 自定义属性跨 bar 存活检测（06-系统函数.md:8 称随 bar 重置）
    try:
        C._qs_probe_marker = 'set_in_init'
        _p('M5-10_marker_seeded', marker='set_in_init')
    except Exception as exc:
        _p('M5-10_marker_seeded', ok=False, err=repr(exc)[:200])


# ---------------------------------------------------------------------------
# handlebar：M5-1/2/4/5/6/8/9/10/13
# ---------------------------------------------------------------------------

def handlebar(C):
    n = int(getattr(C, '_qs_probe_barcount', 0)) + 1
    try:
        C._qs_probe_barcount = n
    except Exception:
        pass

    # M5-4：handlebar 节拍（日线周期应=每根日 bar 一次；计数与 barpos 逐根打印）
    _p('M5-4_handlebar_tick', n=n, barpos=getattr(C, 'barpos', None))

    # M5-10：跨 bar 存活（首根之后每根检查一次）
    if n >= 2:
        _p('M5-10_marker_survived', n=n,
           marker=getattr(C, '_qs_probe_marker', None),
           barcount=getattr(C, '_qs_probe_barcount', None))

    if n != 1:
        return  # 重证据仅首根 bar 采集（一次取证；节拍项已逐根打印）

    # ---- M5-13：bar 时间语义（毫秒？） --------------------------------------
    def _m513():
        bp = getattr(C, 'barpos', None)
        ts = C.get_bar_timetag(bp)
        _p('M5-13_bar_timetag', barpos=bp, raw=repr(ts), type=type(ts).__name__,
           ms_scale_guess=('seconds' if isinstance(ts, (int, float)) and ts < 1e11
                           else 'milliseconds' if isinstance(ts, (int, float)) else 'unknown'))
    _safe('M5-13_bar_timetag', _m513)

    # ---- M5-2：末根 bar 是否含「当前未完成 bar」（E1 [-2] 假定） -------------
    def _m52():
        r = C.get_market_data_ex(['close'], [PROBE_CODE], period='1d', count=3,
                                 dividend_type='front')   # 07:96 / 07:122
        fr = None
        try:
            fr = r[PROBE_CODE] if isinstance(r, dict) else None
        except Exception:
            fr = None
        idx = [str(x) for x in list(fr.index)[-3:]] if fr is not None and hasattr(fr, 'index') else None
        _p('M5-2_market_data_tail', type=type(r).__name__, index_tail=idx,
           closes=(list(map(lambda v: float(v), list(fr['close'])[-3:]))
                   if fr is not None and 'close' in getattr(fr, 'columns', []) else None),
           bar_timetag=repr(C.get_bar_timetag(getattr(C, 'barpos', 0))))
    _safe('M5-2_market_data_tail', _m52)

    # ---- M5-1 + M5-5：财务返回形态 + PERSHAREINDEX 字段枚举 -----------------
    def _m51():
        fields = ['PERSHAREINDEX.s_fa_eps_basic',      # 07:2388
                  'PERSHAREINDEX.inc_revenue_rate',    # 07:2395
                  'PERSHAREINDEX.m_anntime',           # M5-5：字段表未显式列（07:2382-2412）
                  'PERSHAREINDEX.m_timetag',
                  'CAPITALSTRUCTURE.circulating_capital']  # 07:2376
        raw = C.get_financial_data(fields, [PROBE_CODE], '20200101', '',
                                   report_type='announce_time')   # 07:1852/1867/1877
        tname, outer, inner = _shape_of(raw)
        _p('M5-1_financial_shape', type=tname, outer_keys=outer, inner_keys=inner,
           repr_head=repr(raw)[:400])
    _safe('M5-1_financial_shape', _m51)

    # ---- M5-8：instrument_detail 字段枚举 -----------------------------------
    def _m58():
        d = C.get_instrument_detail(PROBE_CODE, True)      # 07:2459
        keys = sorted(d.keys()) if isinstance(d, dict) else None
        _p('M5-8_instrument_detail', type=type(d).__name__, keys=keys,
           InstrumentName=(d.get('InstrumentName') if isinstance(d, dict) else None),
           InstrumentStatus=(d.get('InstrumentStatus') if isinstance(d, dict) else None),
           IsTrading=(d.get('IsTrading') if isinstance(d, dict) else None),
           ExpireDate=(d.get('ExpireDate') if isinstance(d, dict) else None),
           FloatVolume=(d.get('FloatVolume') if isinstance(d, dict) else None),
           UpStopPrice=(d.get('UpStopPrice') if isinstance(d, dict) else None),
           PreClose=(d.get('PreClose') if isinstance(d, dict) else None))
    _safe('M5-8_instrument_detail', _m58)

    # ---- M5-9：板块成分股（沪深A股 等价性） ---------------------------------
    def _m59():
        lst = C.get_stock_list_in_sector(SECTOR)           # 07:3297
        n = len(lst) if hasattr(lst, '__len__') else None
        _p('M5-9_sector_list', sector=SECTOR, type=type(lst).__name__, len=n,
           sample=[str(x) for x in list(lst)[:5]] if hasattr(lst, '__iter__') else None)
    _safe('M5-9_sector_list', _m59)

    # ---- M5-3（handlebar 内对照）：交易日历可用性 ---------------------------
    def _m53_hb():
        r = C.get_trading_dates(PROBE_CODE, '', '', 5, '1d')
        _p('M5-3_trading_dates_in_handlebar', ok=True, type=type(r).__name__,
           len=(len(r) if hasattr(r, '__len__') else None), sample=repr(r)[:300])
    _safe('M5-3_trading_dates_in_handlebar', _m53_hb)

    # ---- M5-6：账号与资金字段 ----------------------------------------------
    def _m56():
        acct_id = getattr(C, 'accountID', None)
        rows = C.get_trade_detail_data(acct_id or ACCOUNT, 'STOCK', 'ACCOUNT')  # 08:583
        first = rows[0] if isinstance(rows, (list, tuple)) and rows else None
        attrs = [a for a in dir(first) if a.startswith('m_')] if first is not None else None
        _p('M5-6_account', accountID=repr(acct_id), rows_type=type(rows).__name__,
           rows_len=(len(rows) if hasattr(rows, '__len__') else None),
           attrs=attrs,
           m_dCash=(getattr(first, 'm_dCash', None) if first is not None else None))
    _safe('M5-6_account', _m56)

    # ---- M5-11：持仓面（只读；无持仓时如实打印空） --------------------------
    def _m511():
        rows = C.get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')            # 08:583/648
        _p('M5-11_positions', rows_len=(len(rows) if hasattr(rows, '__len__') else None),
           sample_attrs=([a for a in dir(rows[0]) if a.startswith('m_')]
                         if isinstance(rows, (list, tuple)) and rows else None))
    _safe('M5-11_positions', _m511)

    _p('probe_done', note='首根 bar 证据采集完毕；后续 bar 仅打印节拍与存活项')
