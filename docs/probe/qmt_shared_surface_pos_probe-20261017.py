#coding:gbk
# ==== QS-QMT auto-generated header（source_import_qmt.py · M2b 架构 D · 块1骨架）====
# 产物落盘物理编码必须为 GBK（与首行 #coding:gbk 声明一致），由写出方负责。
import pandas as pd  # _QS_QMT_FUNDAMENTALS_EXT 消费
import os            # D-30 快照层（instrument snapshot）：路径存在性/文件名
import json          # D-30 快照层：快照 JSON 解析（UTF-8）
import hashlib       # D-30 快照层：快照指纹 sha256 前 16 位

# ---- D-30 PIT 冻结合约快照层（instrument snapshot，2026-10-13）------------------
# 设计：docs/qmt-instrument-snapshot-design.md v0.3（§三=合同）。
# 动机：四消费点（_qs_get_stock_info/_qs_status_flags/_qs_get_ashares/
# _QmtDataBar）读活的本地合约信息表，供应商推送更新该表 -> 跨推送窗口两次
# 回测漂移（含执行层涨跌停判断链）-> 逐位门禁不可判；本层以冻结快照替换数据
# 来源，根治可复现性（非历史 PIT 真相，PIT 三层另案见设计 §七）。
# 约定优于配置（v1 不参数化）：启用=文件存在且 enabled:true；禁用=删文件/改
# false；刷新=重跑导出脚本（docs/probe/qmt_instrument_snapshot_export-20261013.py）
# + 重跑回测（三态缓存下运行中变更文件不生效）。
# 【实盘风险】wrapper 无语境判别能力——文件存在即任何语境消费（含同机 QMT
# 实盘运行）：实盘运行机不部署快照文件（删除即禁用）。

_QS_QMT_INSTR_SNAP_PATH = r'D:\miniQMT策略实盘\QuantStudio\output\qmt_snapshot\instrument_snapshot.json'

# 三态缓存载体：None=未加载；否则 dict(state/records/sha16/n)——四态
# （loaded/disabled/broken/absent）均缓存，运行中新建/变更文件不生效（须重跑
# 回测）。模块级理由同 _QS_QMT_ONCE（局部变量跨调用丢失；每产物文件独立模块
# 命名空间，多策略同进程加载不串味）。
_QS_QMT_SNAP_STATE = None


def _qs_snapshot_load():
    """D-30 快照惰性单次加载 + 四态缓存（loaded/disabled/broken/absent）。

    判序：文件不存在 -> absent（零输出=默认零漂移）；读失败 -> broken；
    JSON 解析失败 / records 非 dict -> broken（n=-1）；enabled 非 true ->
    disabled；否则 loaded（records dict 可用）。加载期任何异常一律坍缩为
    broken/absent，不抛出（消费点回落 live）。非 absent 态首印审计行恰一次
    （_QS_QMT_ONCE['snap_print']，定义于 _QS_QMT_FUNDAMENTALS_EXT，调用期
    解析可用）：
      QS_QMT_SNAPSHOT mode=on|off|off(broken) file=<文件名> sha=<sha256 前16位> n=<len(records) 或 -1>
    """
    global _QS_QMT_SNAP_STATE
    if _QS_QMT_SNAP_STATE is not None:
        return _QS_QMT_SNAP_STATE
    st = {'state': 'absent', 'records': None, 'sha16': None, 'n': -1}
    try:
        exists = os.path.exists(_QS_QMT_INSTR_SNAP_PATH)
    except Exception:
        exists = False
    if exists:
        raw = None
        try:
            with open(_QS_QMT_INSTR_SNAP_PATH, 'rb') as _f:
                raw = _f.read()
        except Exception:
            raw = None
        if raw is None:
            # 读失败（如权限/占锁）：文件存在但字节不可得 -> broken（sha 无从
            # 计算，占位 '-' 披露；n=-1）。
            st['state'] = 'broken'
            st['sha16'] = '-'
        else:
            st['sha16'] = hashlib.sha256(raw).hexdigest()[:16]
            try:
                doc = json.loads(raw.decode('utf-8'))
            except Exception:
                doc = None
            if not isinstance(doc, dict) or not isinstance(doc.get('records'), dict):
                # schema 不符（顶层非 dict / records 非 dict）-> broken（n=-1）。
                st['state'] = 'broken'
            elif doc.get('enabled') is not True:
                # enabled 非 true（文件在=已部署但显式关闭）-> disabled。
                st['state'] = 'disabled'
                st['n'] = len(doc.get('records'))
            else:
                st['state'] = 'loaded'
                st['records'] = doc.get('records')
                st['n'] = len(st['records'])
    _QS_QMT_SNAP_STATE = st
    if st['state'] != 'absent' and 'snap_print' not in _QS_QMT_ONCE:
        _QS_QMT_ONCE['snap_print'] = True
        print('QS_QMT_SNAPSHOT mode=%s file=%s sha=%s n=%d' % (
            'on' if st['state'] == 'loaded'
            else ('off(broken)' if st['state'] == 'broken' else 'off'),
            os.path.basename(_QS_QMT_INSTR_SNAP_PATH),
            st['sha16'], st['n']))
    return st


def _qs_instr_detail(code, C=None):
    """快照命中替换访问器（只负责命中替换：不吞任何异常、不调 live）。

    loaded 且码在 records -> 返回 raw dict（冻结值，调用方跳过 live）；
    loaded 但缺码 -> 打印 QS_QMT_SNAPSHOT first_miss=<code>（_QS_QMT_ONCE
    ['snap_first_miss'] 恰一次）后返回 None（调用方回落原 live 路径）；
    其余态（absent/disabled/broken）-> 恒 None。C 仅保持消费点统一传参
    形态（本访问器不消费 C）。
    """
    st = _qs_snapshot_load()
    if st is None or st.get('state') != 'loaded':
        return None
    d = (st.get('records') or {}).get(code)
    if d is None:
        if 'snap_first_miss' not in _QS_QMT_ONCE:
            _QS_QMT_ONCE['snap_first_miss'] = True
            print('QS_QMT_SNAPSHOT first_miss=%s' % (code,))
        return None
    return d


def _qs_instr_list():
    """快照名单访问器（_qs_get_ashares 用；loaded -> sorted 稳定序冻结名单）。

    严禁向 get_stock_list_in_sector 传 realtime（D-31 定谳：回测模式静默
    返回空列表）；本访问器不触 live。其余态（absent/disabled/broken）
    恒 None（调用方回落原 live 路径）。
    """
    st = _qs_snapshot_load()
    if st is None or st.get('state') != 'loaded':
        return None
    return sorted((st.get('records') or {}).keys())
# ---- QS-QMT log view shim（机制①别名注入式视图）----------------------------
# PTrade 侧 log.info/warn/error → QMT 内置 Python 无注入 log 对象，统一 print 前缀化。
# （print 为 Python 内建，无平台文档依赖）


class _QmtLogView(object):
    """PTrade log 语义子集：debug / info / warning(warn) / error / critical。"""

    def info(self, msg):
        print('[QS-QMT log.info] %s' % (msg,))

    def warn(self, msg):
        print('[QS-QMT log.warn] %s' % (msg,))

    def warning(self, msg):
        print('[QS-QMT log.warning] %s' % (msg,))

    def error(self, msg):
        print('[QS-QMT log.error] %s' % (msg,))

    def critical(self, msg):
        print('[QS-QMT log.critical] %s' % (msg,))

    def debug(self, msg):
        print('[QS-QMT log.debug] %s' % (msg,))


def _qs_log_view():
    return _QmtLogView()


# ---- D-24 进度审计行（漏斗取数层可观测性）------------------------------------
# 设计依据：docs/qmt-progress-audit-design.md v1.2 §4.2（六步流水线②审计两轮闭环）。
# 判型＝新增检测型：只 print，不改任何既有返回值/计数/调用顺序/分支语义。
# 动机：首个 bar 的漏斗取数（≈14000 次逐只 API 调用）全程静默，用户无法区分
#       「在计算」与「已死锁」（2026-10-12 16:34 实测两行日志后长时间无输出）。
_QS_QMT_PROGRESS_EVERY = 500


def _qs_progress_start():
    """批次起算点（D-24）。局部 import：产物头部仅 import pandas
    （_QS_QMT_MODULE_HEADER），不得依赖模块级 time——与本模块 re/datetime
    既有局部 import 纪律同款。失败返回 None（elapsed 记 0.0，不抛）。"""
    try:
        import time as _qs_t
        return _qs_t.time()
    except Exception:
        return None


def _qs_progress(func, done, total, t0=None, phase='tick', extra=None):
    """批次进度审计行（D-24）：仅 total >= 阈值时按固定步长打印，防刷屏。

    行形态（七字段契约）：
      QS_QMT_PROGRESS func=<name> done=<n>/<total> pct=<p> elapsed=<s> phase=<tick|begin|end>[ extra=...]
    节流（用户裁定）：固定步长 500；批 <500 全静默（小批刷屏风险 > 可见性价值）；
    done==total 收尾恒打印（总量非 500 整数倍亦可观测批次结束）。
    零行为影响：只 print，无返回值。
    """
    if not total or total < _QS_QMT_PROGRESS_EVERY:
        return
    if phase == 'tick' and done % _QS_QMT_PROGRESS_EVERY and done != total:
        return
    elapsed = 0.0
    try:
        import time as _qs_t
        # t0 须为有效正时间戳（_qs_progress_start() 的返回值）；0/负数视为无效 →
        # 记 0.0，避免把 epoch 零点误算成天文数字 elapsed（如 1791806094.2s）误导判读。
        if t0 is not None and t0 > 0:
            elapsed = _qs_t.time() - t0
    except Exception:
        elapsed = 0.0
    line = ('QS_QMT_PROGRESS func=%s done=%d/%d pct=%.1f elapsed=%.1fs phase=%s'
            % (func, done, total, 100.0 * done / float(total), elapsed, phase))
    if extra:
        line += ' extra=%s' % (extra,)
    print(line)
# ---- QS-QMT context view shim（机制①别名注入式视图）------------------------
# PTrade context → QMT ContextInfo 视图：portfolio 经持仓明细实现（行级依据见
# _QS_QMT_POSITION_EXT）；未显式映射属性经 __getattr__ 透传 C（不发明语义）。
# 行级依据（docs/qmt/inner-api/07-行情函数.md）：
#   C.get_market_data_ex(['close'], [code], period='1d', count=1, dividend_type='none')
#     :81/:96；dividend_type 枚举 :122（'front' 前复权 / 'none' 不复权——市值用不复权价）。


def _qs_pick_ctx(C):
    """机制②的 C 捕获源：显式传参优先，否则取模块级别名 g（init/handlebar 内刷新）。"""
    if C is not None:
        return C
    if g is None:
        raise RuntimeError('QS-QMT: ContextInfo 尚未注入（init/handlebar 执行前调用了 wrapper）')
    return g


def _qs_snap_kwargs(C):
    """快照类取数锚定（D-1 配套，2026-10-10）：count=1=全局最新一根，回测语境
    构成未来函数。改窗口（end=当前 bar 当日、start=回退 10 自然日，消费端
    tail(1)）——实盘语境 end=今日、回测语境 end=回测当日，两语境统一正确。
    timetag 不可得（异常语境）→ 回退 count=1（原实盘语义，fail-safe）。"""
    try:
        import datetime as _qs_dtg
        ts = C.get_bar_timetag(C.barpos)
        end_dt = _qs_dtg.datetime.fromtimestamp(ts / 1000.0)
        start_dt = end_dt - _qs_dtg.timedelta(days=10)
        return {'start_time': start_dt.strftime('%Y%m%d'),
                'end_time': end_dt.strftime('%Y%m%d')}
    except Exception:
        return {'count': 1}


class _QmtPortfolioView(object):
    """组合视图（PTrade portfolio 语义子集）。

    cash / positions / positions_value / total_value；持仓侧字段行级依据见
    _QS_QMT_POSITION_EXT（08-交易函数.md:583/:648/:649）。
    资金侧 ACCOUNT 明细字段未入本次行级索引 →【M5 实测项】。
    """

    def __init__(self, C):
        object.__setattr__(self, '_qs_C', C)

    def _last_close(self, code):
        try:
            mkt = self._qs_C.get_market_data_ex(['close'], [code], period='1d', **_qs_snap_kwargs(self._qs_C),
                                                dividend_type='none')
            df = mkt.get(code) if isinstance(mkt, dict) else None
            if df is not None and len(df) > 0:
                return float(df.iloc[-1]['close'])
        except Exception:
            pass
        return None

    @property
    def cash(self):
        # D-18 修复（2026-10-11）：实盘链字段名实测修正——ACCOUNT 明细可用
        # 金额字段为 m_dAvailable（08-交易函数.md:655 示例；v1 假设的 m_dCash
        # 不存在于 04-数据结构 ACCOUNT 字段表）。
        try:
            acc = get_trade_detail_data(getattr(self._qs_C, 'accountID', ''), 'STOCK', 'ACCOUNT')
            for a in acc or []:
                v = getattr(a, 'm_dAvailable', None)
                if v is not None:
                    return float(v)
        except Exception:
            pass
        # D-18 修复：回测语境无柜台查询通道（C.accountID 探针 v1 实证不存在；
        # 模块级 account 变量探针 v2 实证 NOGLOBAL；get_trade_detail_data 存在
        # 但无账号可查）——C.capital 回测初始资金（03-变量约定.md:168-186，
        # 探针 v1 实证存在，默认 1000000）推导近似：cash = capital - 持仓市值。
        cap = getattr(self._qs_C, 'capital', None)
        if cap is not None:
            try:
                return max(float(cap) - self.positions_value, 0.0)
            except Exception:
                pass
        return 0.0

    @property
    def positions(self):
        return _qs_get_positions(self._qs_C)

    @property
    def positions_value(self):
        total = 0.0
        for code, pos in list(self.positions.items()):
            close = self._last_close(code)
            if close is not None:
                total += pos.amount * close
        return total

    @property
    def total_value(self):
        return self.cash + self.positions_value

    @property
    def market_value(self):
        return self.positions_value  # PTrade portfolio.market_value 别名（持仓市值）

    def __getattr__(self, name):
        # getattr 回退链：未映射属性显式 None（getattr(portfolio,'x',默认) 不抛错，
        # 不发明语义）
        return None


class _QmtContextView(object):
    """PTrade context → ContextInfo 视图（机制①）。

    显式映射：portfolio；current_dt / previous_date 由 get_bar_timetag 推导
    （M5 批2 实测落锤：bar_date / pre_bar_date 属性不存在，D-10；timetag 为
    北京午夜锚定毫秒 int，M5-13）。其余属性 __getattr__ 透传 C。
    """

    def __init__(self, C):
        object.__setattr__(self, '_qs_C', C)

    @property
    def portfolio(self):
        return _QmtPortfolioView(self._qs_C)

    @property
    def current_dt(self):
        # M5 批2 实测落锤：C.bar_date 不存在（getattr→None→daily_logic strftime 崩溃，
        # 证据 docs/evidence/qmt-m5-batch2-probe-20261010.md D-10）。
        # 改由 get_bar_timetag(barpos)（M5-13 实测：北京午夜锚定毫秒 int）推导 bar 时点
        # datetime；消费面为日期级（strftime/.date()/isocalendar/日期算术），
        # 本地时区=北京时间（QMT 国内交易端前提，方案 §2.1 登记）。
        import datetime as _qs_dtc
        try:
            ts = self._qs_C.get_bar_timetag(self._qs_C.barpos)
            return _qs_dtc.datetime.fromtimestamp(ts / 1000.0)
        except Exception as _qs_exc:
            print('QS_QMT_CONTEXT_DT_UNAVAILABLE: %r' % (_qs_exc,))
            return None

    @property
    def previous_date(self):
        # M5 实测：pre_bar_date 不存在；用 barpos-1 的 timetag 推导前一交易 bar 时点。
        # 首 bar（barpos<=0）→ None：QMT 侧边界语义差（PTrade 侧首日形态未核实，
        # 不声称一致）；六产物策略主体现无消费（潜伏机制修复）。
        import datetime as _qs_dtp
        try:
            bp = self._qs_C.barpos
            if bp is None or bp <= 0:
                return None
            ts = self._qs_C.get_bar_timetag(bp - 1)
            return _qs_dtp.datetime.fromtimestamp(ts / 1000.0)
        except Exception as _qs_exc:
            print('QS_QMT_CONTEXT_DT_UNAVAILABLE: %r' % (_qs_exc,))
            return None

    @property
    def portfolio_value(self):
        return self.portfolio.total_value  # PTrade context.portfolio_value 别名

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_qs_C'), name)


def _qs_context_view(C):
    return _QmtContextView(C)
# ---- QS-QMT data view shim（机制①别名注入式视图）---------------------------
# PTrade data[code] 当日快照 → QMT 最新一根日 bar（count=1，不复权）。
# 行级依据（docs/qmt/inner-api/07-行情函数.md）：
#   C.get_market_data_ex(...) :81/:96；dividend_type 枚举 :122（'front' 前复权 / 'none' 不复权）。
# 【E1 铁律（AGENTS.md 2026-09-22）】data[code] 当日 raw 快照仅限执行层判断（涨跌停/停牌），
# 不得作为信号输入——信号价格一律来自 get_history(..., fq='pre', include=False) 的 D-1
# 口径（块 2 _qs_get_history wrapper 落地）。


class _QmtDataBar(object):
    """单标的数据视图（PTrade UnitData 语义子集）。

    行情字段 last_price/open/high/low/close/volume ← get_market_data_ex
    （07:81/:96；dividend_type 枚举 :122，快照用不复权）；
    昨收与涨跌停 ← get_instrument_detail 快照：PreClose(:2490)、UpStopPrice(:2492)、
    DownStopPrice(:2493)——QMT 无 bar 内涨跌停字段，快照口径（盘中静态值）→【M5 实测项】。
    """

    _FIELDS = ('open', 'high', 'low', 'close', 'volume')

    def __init__(self, C, code):
        vals = {}
        try:
            mkt = C.get_market_data_ex(list(self._FIELDS), [code], period='1d', **_qs_snap_kwargs(C),
                                       dividend_type='none')
            df = mkt.get(code) if isinstance(mkt, dict) else None
            if df is not None and len(df) > 0:
                last = df.iloc[-1]
                for k in self._FIELDS:
                    # D-16a 修复（2026-10-11）：QMT volume=手（04-数据结构.md:24），
                    # 本地/PTrade 语义=股——快照口径层同步 ×100（与 _qs_fetch_bars 一致）
                    vals[k] = float(last[k]) * (100.0 if k == 'volume' else 1.0)
        except Exception:
            vals = {}
        det = _qs_instr_detail(code, C)  # D-30 快照层：非 None 即冻结值（跳过 live）
        if det is None:
            try:
                det = C.get_instrument_detail(code) or {}  # M5-8 实测签名：单位置参数（iscomplete 非位置参，3 参 TypeError）
            except Exception:
                det = {}
        vals['preclose'] = det.get('PreClose')        # :2490
        vals['high_limit'] = det.get('UpStopPrice')   # :2492
        vals['low_limit'] = det.get('DownStopPrice')  # :2493
        object.__setattr__(self, '_code', code)
        object.__setattr__(self, '_vals', vals)

    @property
    def code(self):
        return self._code

    @property
    def open(self):
        return self._vals.get('open')

    @property
    def high(self):
        return self._vals.get('high')

    @property
    def low(self):
        return self._vals.get('low')

    @property
    def close(self):
        return self._vals.get('close')

    @property
    def volume(self):
        return self._vals.get('volume')

    @property
    def last_price(self):
        return self._vals.get('close')

    @property
    def preclose(self):
        return self._vals.get('preclose')  # PreClose :2490

    @property
    def high_limit(self):
        return self._vals.get('high_limit')  # UpStopPrice :2492

    @property
    def low_limit(self):
        return self._vals.get('low_limit')  # DownStopPrice :2493

    def __getattr__(self, name):
        return None  # 未映射字段显式 None（不抛错，不发明语义）


class _QmtDataView(object):
    """PTrade data 参数视图：data[code] → _QmtDataBar。

    _qs_C 经 __init__ 持有——data 作形参传给 helper 函数后仍可继续索引
    （视图对象自包含，不依赖调用点局部名）。
    """

    def __init__(self, C):
        object.__setattr__(self, '_qs_C', C)

    def __getitem__(self, code):
        return _QmtDataBar(self._qs_C, code)


def _qs_data_view(C):
    return _QmtDataView(C)
# ---- QS-QMT history 取数（E1 铁律口径）---------------------------------------
# 行级依据（docs/qmt/inner-api/07-行情函数.md）：
#   C.get_market_data_ex(field_list, stock_list, period='1d', count=N,
#                        dividend_type=...) :81/:96；dividend_type 枚举 :122
#   （'front' 前复权 / 'none' 不复权；'back' 后复权在枚举表内 →【M5 实测项】）。
# 【E1 铁律（AGENTS.md 2026-09-22）】执行日以 include=False 读取前一交易日（D-1）
#   日线——QMT 侧实现 = count+1 根取数后剔最末根（当日 bar，对齐 source_import.py
#   侧 count>=2 取 [-2] 惯例），取数 API 口径层恒不返回当前回调所在交易日日线。
#   「handlebar 语境 get_market_data_ex 最末根为当日 bar」→【M5 实测项】。
# 字段别名映射（对齐本地 ptrade_api.py:1441 field_map）：money→amount、price→close、
#   factor→pctChg、preclose→preClose；pctChg/preClose 在 get_market_data_ex 的
#   可用性未入行级索引 →【M5 实测项】（缺列时不在 wrapper 内发明合成语义）。

_QS_QMT_FQ_MAP = {
    # fq → dividend_type（07:122；'post'→'back' 与 'none' 透传 →【M5 实测项】）
    'pre': 'front',
    'post': 'back',
    None: 'none',
    '': 'none',
    'none': 'none',
}


def _qs_norm_history_fields(fields):
    """字段请求归一：None→['close']；str→list；本地别名映射（ptrade_api.py:1441 同款）。"""
    if fields is None:
        return ['close']
    if isinstance(fields, str):
        fields = [fields]
    alias = {'money': 'amount', 'amount': 'amount', 'price': 'close',
             'factor': 'pctChg', 'pctChg': 'pctChg',
             'preclose': 'preClose', 'is_open': 'volume'}
    return [alias.get(f, f) for f in fields]


def _qs_fetch_bars(C, codes, fields, fq, count, period):
    """D-1 修复（2026-10-10）：count 形态实测=全局最新 N 根（回测未来函数，
    探针证据 docs/evidence/qmt-m5-batch2-probe-20261010.md M5-2/D-11 探针）。
    改 start_time/end_time 显式窗口：end=当前 bar 前一自然日（E1 语义），
    start=自然日粗窗（2x+30，宁可多取仅更早历史，方向安全），取回 tail 截尾。
    审计要点①（M5.1b ②审）：实际取得数 < count 时如实返回（停牌/长假/上市晚），
    不凑数；E1 端点含当日与否未定谳 → 末行日期 >= bar 当日则剔（防御性双保险）。
    """
    div = _QS_QMT_FQ_MAP.get(fq, 'front')  # 未知 fq 按本地默认 fq='pre' → front
    want = max(int(count or 1), 1)
    try:
        import datetime as _qs_dtf
        ts = C.get_bar_timetag(C.barpos)
        bar_dt = _qs_dtf.datetime.fromtimestamp(ts / 1000.0)
        end_dt = bar_dt - _qs_dtf.timedelta(days=1)
        start_dt = end_dt - _qs_dtf.timedelta(days=want * 2 + 30)
        mkt = C.get_market_data_ex(list(fields), [str(c) for c in codes],
                                   period=period,
                                   start_time=start_dt.strftime('%Y%m%d'),
                                   end_time=end_dt.strftime('%Y%m%d'),
                                   dividend_type=div)
    except Exception as exc:
        print('QS_QMT_WRAPPER_FETCH_FAIL: get_market_data_ex window period=%s: %s'
              % (period, exc))
        return {}
    out = {}
    for code, df in (mkt or {}).items():
        try:
            if df is None or len(df) == 0:
                out[code] = df
                continue
            # D-16a 修复（2026-10-11）：QMT volume 单位=手（docs/qmt/inner-api/
            # 04-数据结构.md:24「成交总量（手）」，pvolume=原始股数但文档标注不
            # 推荐），本地/PTrade 语义 volume=股——成交额类计算 close*volume 低估
            # 100 倍（D-15/D-16 探针实证：平安银行 amt20=1123万 vs 真实~15亿；
            # 修正后过线率 23/500→491/500）。wrapper 口径层统一 ×100 股数换算。
            if 'volume' in fields:
                try:
                    if 'volume' in df.columns:
                        df = df.copy()
                        df['volume'] = df['volume'] * 100
                except Exception as vexc:
                    print('QS_QMT_WRAPPER_VOLUME_CONV_FAIL: %s %r' % (code, vexc))
            if period == '1d':
                # E1 防御：QMT end_time 端点语义（含不含当日）未定谳——
                # 末行日期串 >= bar 当日则剔末根（索引兼容 int/str/时间戳形态）
                try:
                    _last = ''.join(ch for ch in str(df.index[-1])
                                    if ch.isdigit())[:8]
                    if len(_last) == 8 and _last >= bar_dt.strftime('%Y%m%d'):
                        df = df.iloc[:-1]
                except Exception:
                    pass
            if len(df) > want:
                df = df.tail(want)  # 只截多不凑少（审计要点①）
            out[code] = df
        except Exception:
            out[code] = df
    return out


def _qs_get_history(codes, count, unit='1d', fields=None, fq='pre', include=False,
                    C=None):
    """历史行情（{code: DataFrame}；E1：日线恒不含执行日 bar）。

    include 参数仅为签名兼容——日线一律剔当日（E1 铁律，API 口径层杜绝未来
    函数，不依赖调用方自觉）；分钟周期 include 语义 →【M5 实测项】（块2 不做
    分钟合成）。
    """
    C = _qs_pick_ctx(C)
    period = str(unit or '1d')
    if period in ('daily', 'day'):
        period = '1d'
    if period == '1min':
        period = '1m'
    flds = _qs_norm_history_fields(fields)
    return _qs_fetch_bars(C, codes, flds, fq, count, period)


def _qs_get_history_batch(codes, count, unit='1d', fields=None, fq='pre',
                          include=False, C=None):
    """批式历史行情（同构：循环 + 合并；本地 B1 契约 {code: DataFrame}）。"""
    C = _qs_pick_ctx(C)
    out = {}
    for code in list(codes):
        try:
            out.update(_qs_get_history([code], count, unit=unit, fields=fields,
                                       fq=fq, include=include, C=C))
        except Exception as exc:
            print('QS_QMT_WRAPPER_SKIP: get_history_batch %s: %s' % (code, exc))
    return out
# ---- QS-QMT position 视图 ---------------------------------------------------
# D-18 修复（2026-10-11）：QMT 以模块 globals 预注入原生回测交易函数族（探针
# v3 15:54 实证 order_target_value/order_value/order_shares/order_lots/
# order_target_percent/get_trade_detail_data in_globals=True、in_builtins=
# False，Boost.Python C++ 绑定）。机制②同名遮蔽 def 执行后原生引用丢失——
# 在首个遮蔽 def 之前捕获原生 order_target_value 供回测语境转发（引擎虚拟
# 账户自管持仓差额/可卖仓位，08-交易函数.md:1517「仅回测可用」段）。
_qs_native_otv = globals().get('order_target_value')

# 行级依据（docs/qmt/inner-api/08-交易函数.md）：
#   get_trade_detail_data(accountID, 'STOCK', 'POSITION') :583；
#   m_nVolume（总持仓量）/ m_nCanUseVolume（可用持仓量） :648；m_dOpenPrice（开仓价） :649。
# D-18 修复：账号解析链 C.accountID（实盘属性）→ 模块级 account 变量（08:623
# 文档形态：策略交易界面运行时被赋值）→ '' 兜底；回测语境两者皆无（探针 v1/v2
# 实证）→ 查询返回空，交易走原生函数转发分支。


def _qs_get_trade_detail(C, biz_type):
    acc = getattr(C, 'accountID', None) or globals().get('account') or ''
    return get_trade_detail_data(acc, 'STOCK', biz_type)


class _QmtPositionView(object):
    """单标的持仓视图（PTrade Position 语义子集）。"""

    def __init__(self, code, amount, closeable_amount, avg_cost):
        self.code = code
        self.sid = code
        self.amount = float(amount or 0.0)
        self.closeable_amount = float(closeable_amount or 0.0)
        self.avg_cost = float(avg_cost or 0.0)

    @property
    def total_amount(self):
        return self.amount

    @property
    def enable_amount(self):
        return self.closeable_amount  # m_nCanUseVolume（08:649）别名（本地 enable_amount 契约）

    def __getattr__(self, name):
        return None  # 未映射字段显式 None（不抛错）


def _qs_get_positions(C=None):
    """全持仓 dict {code: _QmtPositionView}（08:583/:648/:649）。"""
    C = _qs_pick_ctx(C)
    out = {}
    try:
        details = _qs_get_trade_detail(C, 'POSITION')
    except Exception:
        return out
    for d in details or []:
        code = getattr(d, 'm_strInstrumentID', '') or ''
        exch = getattr(d, 'm_strExchangeID', '') or ''
        if exch and '.' not in code:
            code = '%s.%s' % (code, exch)  # 交易所后缀拼装 →【M5 实测项】字段名/后缀形态复核
        out[code] = _QmtPositionView(code,
                                     getattr(d, 'm_nVolume', 0),
                                     getattr(d, 'm_nCanUseVolume', 0),
                                     getattr(d, 'm_dOpenPrice', 0.0))
    return out


def get_position(code, C=None):
    """PTrade get_position 同名遮蔽（机制②）。

    本地契约（ptrade_api.py:1832-1835）：空仓返回 amount=0 的 Position 对象而非
    None——策略常写 get_position(code).amount == 0 判空仓，据此返回零持仓视图。
    """
    pos = _qs_get_positions(C).get(code)
    if pos is not None:
        return pos
    return _QmtPositionView(code, 0, 0, 0.0)
# ---- QS-QMT order wrapper ---------------------------------------------------
# 行级依据：
#   passorder(opType, orderType, accountID, orderCode, prType, price, volume, ContextInfo)
#     docs/qmt/inner-api/08-交易函数.md:8/:27/:84/:85；
#   opType 23=买入 / 24=卖出：05-枚举常量.md:43-44；
#   单股交易 orderType：05-枚举常量.md:135-143（具体枚举值未摘录 →【M5 实测项】，
#     此处取迅投示例常用单股单代码形态 1101）；
#   prType=5 最新价：05-枚举常量.md:132 示例（价格参数传 0，由平台按最新价撮合）。

_QS_QMT_OPTYPE_BUY = 23    # 05-枚举常量.md:43
_QS_QMT_OPTYPE_SELL = 24   # 05-枚举常量.md:44
_QS_QMT_ORDERTYPE_STOCK = 1101  # 【M5 实测项】05:135-143（单股交易 orderType 值未摘录）
_QS_QMT_PRTYPE_LATEST = 5  # 05-枚举常量.md:132（最新价）


def _qs_passorder(op_type, order_code, volume, price=0, C=None, pr_type=_QS_QMT_PRTYPE_LATEST):
    C = _qs_pick_ctx(C)
    passorder(op_type, _QS_QMT_ORDERTYPE_STOCK, getattr(C, 'accountID', ''),
              order_code, pr_type, price, int(volume), C)


def _qs_order(code, amount, C=None):
    """PTrade order(code, amount) 语义：amount>0 买入 / <0 卖出（股数）。

    取整规则：买入向下取整、卖出取可用上限（零股可全清）→【M5 实测项】（整手/零股
    规则以实盘复核为准）。
    """
    C = _qs_pick_ctx(C)
    if not amount:
        return
    if amount > 0:
        _qs_passorder(_QS_QMT_OPTYPE_BUY, code, int(amount), C=C)
    else:
        pos = get_position(code, C)
        closeable = pos.closeable_amount if pos is not None else 0
        sell = min(int(-amount), int(closeable))
        if sell > 0:
            _qs_passorder(_QS_QMT_OPTYPE_SELL, code, sell, C=C)


def _qs_order_target_value(code, value, C=None):
    """PTrade order_target_value(code, value) 语义：调仓至目标市值（元）。

    回测语境（D-18 修复）：转发 QMT 原生 order_target_value（08:1517「仅回测
    可用」）——引擎虚拟账户自管持仓差额与可卖仓位；现 passorder 链在回测语境
    positions 恒空（accountID/account 双无，探针 v1/v2 实证）→ 每 bar 重复
    建仓买入 + closeable=0 卖单全灭（03:24 复测 buy_submitted=0 实证）。
    实盘语境：现价以最新收盘价近似（get_market_data_ex count=1 不复权，
    07:81/:96/:122）；目标卖超可用持仓时按可用上限卖出（清仓零股全清）。
    取整规则【M5 实测项】。静默跳过分支（_qs_noop_target 式）：现价不可得 /
    delta=0 / 目标卖超空仓 → 原地返回不下单（PTrade noop 语义，无声不抛错）。
    """
    C = _qs_pick_ctx(C)
    # D-18 修复：回测语境（无实盘账号——C.accountID 探针 v1 实证不存在）且有
    # 原生引用（模块加载期捕获，本地/异常语境 None 回退实盘链）→ 转发原生
    # （Boost.Python 签名 (str)code, (float)value, (object)C，探针 v3 实证）。
    if getattr(C, 'accountID', None) is None and _qs_native_otv is not None:
        _qs_native_otv(code, float(value), C)
        return
    price = current_price(code, C)
    if price is None or price != price:
        return
    pos = get_position(code, C)
    cur_val = (pos.amount if pos is not None else 0.0) * float(price)
    delta = float(value) - cur_val
    if delta > 0:
        shares = int(delta // float(price))
        if shares > 0:
            _qs_passorder(_QS_QMT_OPTYPE_BUY, code, shares, C=C)
    elif delta < 0:
        if pos is None:
            return
        want = int((-delta) // float(price))
        sell = min(want, int(pos.closeable_amount))
        if sell > 0:
            _qs_passorder(_QS_QMT_OPTYPE_SELL, code, sell, C=C)
# ---- QS-QMT A 股池 ----------------------------------------------------------
# 行级依据（docs/qmt/inner-api/07-行情函数.md）：
#   C.get_stock_list_in_sector(sectorname, realtime) :3288/:3297。
# 板块名 '沪深A股' 与 realtime=1（实时）取值 →【M5 实测项】（以迅投知识库板块表为准）。


def _qs_get_ashares(date=None, exclude_bse=None, include_delisted=None, C=None):
    """全 A 股池（07:3288/:3297）。

    date 非 None → 打印 QS_QMT_SEMANTIC_DIFF PIT 登记行（QMT sector=当前上市
    名单，无历史 PIT——历史时点池需编排层/数据层另行固化，不静默）。
    exclude_bse/include_delisted 不消费（'沪深A股' 板块自身即沪深口径，北交不在
    该 sector 内；本地默认剔北交口径 ptrade_api.py:1859）→【M5 实测项】
    （板块成员以迅投知识库板块表为准）。
    """
    C = _qs_pick_ctx(C)
    if date is not None:
        print('QS_QMT_SEMANTIC_DIFF: get_Ashares date=%r 不消费'
              '（QMT sector=当前上市名单，无历史 PIT）' % (date,))
    # D-30 快照层：loaded 时直接返回 sorted 冻结名单（零 live；严禁 realtime）。
    # 登记行在快照分支之上——冻结名单同样非历史 PIT，登记语义仍真（设计 §三⑤）。
    lst = _qs_instr_list()
    if lst is not None:
        return lst
    try:
        # D-12 final（2026-10-11 PROBE 定谳）：realtime 实参传任何值（毫秒时间戳
        # 或数字 1）回测模式均返回空；唯一可用形态 = 单参调用（板块当前全名单
        # 5227 只）。与上方 SEMANTIC_DIFF 声明一致（sector=当前上市名单，无 PIT）。
        return list(C.get_stock_list_in_sector('沪深A股'))
    except Exception:
        return []
# ---- QS-QMT 交易日历 --------------------------------------------------------
# 行级依据（docs/qmt/inner-api/07-行情函数.md）：
#   C.get_trading_dates(stockcode, start_date, end_date, count, period='1d') :3341/:3350。
# 【init 内不可用】（06-系统函数.md:12）——调用点必须在 handlebar 阶段，init 阶段取不到；
# 锚定证券默认 '000001.SH'、count=-1（区间语义）→【M5 实测项】（参数形态以实测复核）。


def _qs_get_trade_days(start_date=None, end_date=None, count=-1, C=None):
    """交易日历（07:3341/:3350）。

    【init 内不可用】（06-系统函数.md:12）→ 捕获异常打印 QS_QMT_SEMANTIC_DIFF
    登记行 + 安全回退 []（不抛错）。返回元素归一 'YYYY-MM-DD' 字符串；数值元素
    按毫秒时间戳转换（get_trading_dates 返回元素形态 →【M5 实测项】）。
    本地契约返回 ndarray（ptrade_api.py:1801-1803）→ 此处返回 list（索引/len/
    成员判定同构；numpy 向量化用法不保证 →【M5 实测项】，如实登记）。
    """
    C = _qs_pick_ctx(C)
    try:
        raw = list(C.get_trading_dates('000001.SH', start_date or '', end_date or '',
                                       count, period='1d'))
    except Exception as exc:
        print('QS_QMT_SEMANTIC_DIFF: get_trade_days 不可用（init 阶段禁用，06:12）：%s'
              % (exc,))
        return []
    from datetime import datetime as _qs_dtd
    out = []
    for t in raw:
        try:
            if isinstance(t, (int, float)):
                out.append(_qs_dtd.fromtimestamp(float(t) / 1000.0).strftime('%Y%m%d'))
            else:
                out.append(str(t))
        except Exception:
            out.append(str(t))
    return out
# ---- QS-QMT stock info / status / current price ------------------------------
# 行级依据（docs/qmt/inner-api/07-行情函数.md）：
#   C.get_instrument_detail(stockcode) :2446/:2459——M5-8 实测签名：单位置参数
#   （iscomplete 非位置参数，3 参 TypeError；文档签名与实测不符，实测优先）；
#   字段 :2479-2511：InstrumentName :2481、OpenDate :2488、ExpireDate :2489、
#   UpStopPrice :2492、DownStopPrice :2493、FloatVolume :2494、
#   InstrumentStatus（停牌状态）:2502、IsTrading :2503；
#   行情现价：C.get_market_data_ex(['close'], ...) :81/:96；dividend_type 枚举 :122。


def _qs_norm_ymd(value):
    """QMT 日期字段归一为本地契约形态 'YYYY-MM-DD'（取不到 → None）。

    D-13（2026-10-11 PROBE 实测）定谳：QMT C.get_instrument_detail 的
    OpenDate（:2488）/ ExpireDate（:2489）为 **int YYYYMMDD**
    （OpenDate=19910403、ExpireDate=99999999=永续标记）。
    本地契约（ptrade_api.py:2481-2523 get_stock_info）：
      listed_date = pd.Timestamp(start_date).strftime('%Y-%m-%d')
    即 'YYYY-MM-DD' 字符串；本函数把 QMT 形态映射为该契约形态。
    以下一律返回 None（=本地「取不到」语义，策略侧 `if not listed: continue`
    保守跳过，双端同判）：0 / 负数 / None / bool / 非 8 位整数 /
    非法月日（含 99999999 永续标记 → strptime 自然 ValueError）。
    L1 阻断根因（2026-10-12 QMT_WEEKLY_SMALLCAP 复测 L1_listed=0）：
    本模块 get_stock_info 原返回字典无 'listed_date' 键，field=['listed_date']
    白名单取键恒 None → 全部标的被跳过（L0_all=5227 → L1_listed=0）。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            iv = int(value)
            if iv <= 0:
                return None
            txt = str(iv)
            if len(txt) != 8:
                return None
        else:
            txt = str(value).strip().replace('-', '').replace('/', '')
            if len(txt) != 8 or not txt.isdigit():
                return None
        from datetime import datetime as _qs_ymd_dt
        return _qs_ymd_dt.strptime(txt, '%Y%m%d').strftime('%Y-%m-%d')
    except Exception:
        return None


def _qs_get_stock_info(code, C=None):
    C = _qs_pick_ctx(C)
    d = _qs_instr_detail(code, C)  # D-30 快照层：非 None 即冻结值（跳过 live）
    if d is None:
        try:
            d = C.get_instrument_detail(code) or {}  # M5-8 实测签名：单位置参数
        except Exception:
            d = {}
    return {
        'display_name': d.get('InstrumentName'),        # :2481
        'name': d.get('InstrumentName'),                # :2481
        'start_date': d.get('OpenDate'),                # :2488
        'end_date': d.get('ExpireDate'),                # :2489
        'high_limit': d.get('UpStopPrice'),             # :2492
        'low_limit': d.get('DownStopPrice'),            # :2493
        'float_shares': d.get('FloatVolume'),           # :2494
        'instrument_status': d.get('InstrumentStatus'), # :2502（停牌状态）
        'is_trading': d.get('IsTrading'),               # :2503
        # ---- 本地契约键（ptrade_api.py:2481-2523）——上列键名系 QMT 原生字段名，
        #      本地 get_stock_info 契约键名为 listed_date / de_listed_date
        #      （'YYYY-MM-DD' 字符串）。D-14（2026-10-12）：补契约键以消 L1 阻断；
        #      既有键一律不动（零回归），键名差异其余项登记 docs/ISSUES.md 待批。
        'listed_date': _qs_norm_ymd(d.get('OpenDate')),      # ← 本地契约键
        'de_listed_date': _qs_norm_ymd(d.get('ExpireDate')),  # ← 本地契约键
    }


def _qs_status_flags(code, C):
    """状态三判（ST/HALT/DELISTING）→ ({type: bool}, 可判定否)。

    数据源 get_instrument_detail（:2479-2511）：
      ST：InstrumentName 含 'ST'（:2481）；
      HALT：InstrumentStatus >= 1（:2502）或 IsTrading 为 False（:2503）；
      DELISTING：ExpireDate（:2489）有效且已过期（当日 > ExpireDate）。
    详情取不到 → ({}, False)（不可判定，调用方保守处置 + 审计行）。
    ExpireDate 形态：D-13 定谳（2026-10-11 PROBE 实测）= int YYYYMMDD
    （99999999=永续标记，OpenDate 同形态佐证）；>=1e10 数值按毫秒时间戳
    防御解析（实测未现）；日期串双形态保留。
    """
    C = _qs_pick_ctx(C)
    d = _qs_instr_detail(code, C)  # D-30 快照层：非 None 即冻结值（跳过 live）
    if d is None:
        try:
            d = C.get_instrument_detail(code) or {}  # M5-8 实测签名：单位置参数  # 07:2459
        except Exception:
            return {}, False
        if not d:  # live 空结果仍 ({}, False)；快照命中不经本分支（设计 §三：命中即 raw dict）
            return {}, False
    flags = {}
    flags['ST'] = 'ST' in str(d.get('InstrumentName') or '').upper()  # :2481
    halted = False
    try:
        halted = int(d.get('InstrumentStatus') or 0) >= 1            # :2502
    except (TypeError, ValueError):
        halted = False
    if d.get('IsTrading') is False:                                   # :2503
        halted = True
    flags['HALT'] = halted
    dl = False
    exp = d.get('ExpireDate')                                         # :2489
    if exp:
        try:
            from datetime import datetime as _qs_dte
            exp_dt = None
            if isinstance(exp, (int, float)):
                # D-13 定谳（2026-10-11 PROBE 实测，新建策略文件8）：QMT
                # ExpireDate 为 int YYYYMMDD 形态（99999999=永续标记，
                # OpenDate=19910403 同形态佐证）。旧代码误按毫秒 /1000 →
                # fromtimestamp(99999.999)=1970-01-02 → now()>1970 恒真 →
                # 5227 只全部误判退市（复测 F=0）。仅当数值达到毫秒时间戳
                # 量级（>=1e10）才按时间戳解析（防御形态，实测未现）。
                if float(exp) >= 1e10:
                    exp_dt = _qs_dte.fromtimestamp(float(exp) / 1000.0)
                else:
                    txt = str(int(exp))
                    if len(txt) == 8:
                        try:
                            exp_dt = _qs_dte.strptime(txt, '%Y%m%d')
                        except ValueError:
                            # 99999999 永续标记（99 月 99 日非法）及其他非法
                            # 月日 → 不过期（保守保留，不误剔）。
                            exp_dt = None
            else:
                txt = str(exp).replace('-', '').replace('/', '')[:8]
                exp_dt = _qs_dte.strptime(txt, '%Y%m%d') if len(txt) == 8 else None
            if exp_dt is not None and _qs_dte.now() > exp_dt:
                dl = True
        except Exception:
            dl = False
    flags['DELISTING'] = dl
    return flags, True


def get_stock_status(stocks, query_type='ST', query_date=None, C=None):
    """PTrade get_stock_status 同名遮蔽（机制②）：{code: bool}（本地契约
    ptrade_api.py:2552-2566——单码/多码皆返回按调用方原码键控的 dict）。

    query_type：'ST' / 'HALT' / 'DELISTING'（'DELISTING_SORTING' 本地别名 →
    并入 DELISTING）；query_date 不消费（QMT 详情为当前快照，无历史 PIT）→
    非 None 时打印 QS_QMT_SEMANTIC_DIFF 登记行。
    """
    lst = [stocks] if isinstance(stocks, str) else list(stocks or [])
    qt = str(query_type or 'ST').upper()
    if qt == 'DELISTING_SORTING':
        qt = 'DELISTING'
    if query_date is not None:
        print('QS_QMT_SEMANTIC_DIFF: get_stock_status query_date=%r 不消费'
              '（QMT 详情为当前快照，无历史 PIT）' % (query_date,))
    result = {}
    for s in lst:
        flags, ok = _qs_status_flags(s, C)
        if not ok:
            print('QS_QMT_UNDETERMINED: get_stock_status %s 状态不可判定 → False' % (s,))
            result[s] = False
        else:
            result[s] = bool(flags.get(qt, False))
    return result


def current_price(code, C=None):
    """最新收盘价（count=1，不复权；07:81/:96/:122）。取不到返回 None。"""
    C = _qs_pick_ctx(C)
    try:
        mkt = C.get_market_data_ex(['close'], [code], period='1d', **_qs_snap_kwargs(C),
                                   dividend_type='none')
        df = mkt.get(code) if isinstance(mkt, dict) else None
        if df is not None and len(df) > 0:
            return float(df.iloc[-1]['close'])
    except Exception:
        pass
    return None


def _qs_filter_stock_by_status(stocks, filter_type=None, query_date=None, C=None):
    """按交易状态过滤（ST/HALT/DELISTING 三判；机制②遮蔽面委托此实现）。

    filter_type 语义（本地对齐 ptrade_api.py:1103-1105）：None/空 →
    ['ST','HALT','DELISTING'] 全查；单字符串/列表 → 按所列类型剔除非命中项。
    不可判定（详情取不到）→ 保守保留 + QS_QMT_UNDETERMINED 审计行——与本地
    「无数据行→按 DELISTING 剔除」口径不同：QMT 侧取不到详情不等于已退市
    （语义差如实登记，M5 复核）。query_date 同 get_stock_status 不消费。
    """
    C = _qs_pick_ctx(C)
    if not filter_type:
        filter_type = ['ST', 'HALT', 'DELISTING']
    types = [filter_type] if isinstance(filter_type, str) else list(filter_type)
    types = [str(t).upper() for t in types]
    if query_date is not None:
        print('QS_QMT_SEMANTIC_DIFF: filter_stock_by_status query_date=%r 不消费'
              '（QMT 详情为当前快照，无历史 PIT）' % (query_date,))
    out = []
    _src = list(stocks)
    _t0 = _qs_progress_start()  # D-24 进度审计（批 <500 自动静默）
    for _i, s in enumerate(_src):
        flags, ok = _qs_status_flags(s, C)
        _qs_progress('filter_stock_by_status', _i + 1, len(_src), _t0)
        if not ok:
            print('QS_QMT_UNDETERMINED: filter_stock_by_status %s 状态不可判定，'
                  '保守保留' % (s,))
            out.append(s)
            continue
        if any(flags.get(t, False) for t in types):
            continue
        out.append(s)
    return out
# ---- QS-QMT fundamentals 取数映射（M2b 定稿映射表，不得更动）------------------
# 行级依据（docs/qmt/inner-api/07-行情函数.md）：
#   C.get_financial_data(fieldList, stockList, startDate, endDate,
#                        report_type='announce_time')
#     :1839/:1852/:1863/:1867；PIT：report_time 可能取到未来数据 :1875、
#     announce_time 不会 :1877；公告日与报表截止日为毫秒时间戳 :1837。
#   字段表：ASHAREINCOME :2303；CAPITALSTRUCTURE :2371（total_capital :2375、
#     circulating_capital :2376）；PERSHAREINDEX :2382（s_fa_eps_basic :2388、
#     inc_revenue_rate :2395）；公共字段 m_timetag（报告截止日）:2329、
#     m_anntime（公告日）:2330——PERSHAREINDEX 字段表未显式列 m_anntime/m_timetag
#     →【M5 实测项】。
#   行情价：C.get_market_data_ex(['close'], [code], period='1d', count=1,
#     dividend_type='none') :81/:96；dividend_type 枚举 :122（'front' 前复权 /
#     'none' 不复权——市值计算用不复权价）。
# 定稿映射表（不得更动）：
#   table='eps'            → PERSHAREINDEX：eps→s_fa_eps_basic、publ_date→m_anntime、
#                            end_date→m_timetag
#   table='growth_ability' → PERSHAREINDEX：or_yoy→inc_revenue_rate、publ_date→m_anntime、
#                            end_date→m_timetag
#   table='valuation'      → CAPITALSTRUCTURE + 行情价：
#                            float_value(流通市值,元) = circulating_capital(股) × close(元)
# 取数统一 report_type='announce_time'（显式钉死，不依赖默认值）。
# 返回契约：DataFrame(index=code, columns=请求字段本地名)；空→NaN 契约行，不抛错。
# startDate/endDate 窗口语义与返回形态 →【M5 实测项】（:1863/:1867 已核签名，
#   窗口语义按「startDate 起至 endDate 止的公告记录」实现，实测复核）。
import datetime as _qs_dt


def _qs_fin_ms_to_date(ms):
    """毫秒时间戳 → 'YYYYMMDD' 字符串（07:1837：公告日/报表截止日为毫秒时间戳）。"""
    try:
        return _qs_dt.datetime.fromtimestamp(float(ms) / 1000.0).strftime('%Y%m%d')
    except Exception:
        return None


def _qs_last_close(C, code):
    try:
        mkt = C.get_market_data_ex(['close'], [code], period='1d', **_qs_snap_kwargs(C),
                                   dividend_type='none')
        df = mkt.get(code) if isinstance(mkt, dict) else None
        if df is not None and len(df) > 0:
            return float(df.iloc[-1]['close'])
    except Exception:
        pass
    return None


def _qs_close_batch(codes, C=None, batch=400):
    """D-27b 件 C（2026-10-13，方案 §2.3；判型＝纯性能型）：close 批量预取。

    与 _qs_last_close 逐只调用**取数语义逐位对齐**（同 _qs_snap_kwargs 窗口 /
    同 dividend_type='none' / 同 df.iloc[-1]['close'] 末行取法，:1181-1185 同式），
    仅以单批调用替代 N 次逐只（端测 3666 次 × ~39ms ≈ 144s → ~10 批次）。
    五态回落（方案 §2.3 审计 #2 钉死）：
      ① 批内缺码（code 不在返回 dict）→ 该码回落逐只；
      ② 空表（len(df)==0）→ 该码回落逐只；
      ③ 单码取值异常（iloc[-1]['close'] 抛错/非数值——含 NaN，float(NaN) 不抛
         但无可用值，且逐只回落同得 NaN → float_value 同为 NaN，逐位等价）→
         该码回落逐只；
      ④ 返回非 dict（isinstance 判定；_qs_last_close:1183 已防备的真实形态）→
         整批回退逐只；
      ⑤ 批级异常（整批调用抛错）→ 整批回退逐只。
    任何情况下不抛异常冒泡；失败码一律记 None 交调用方回落（回落数计入 miss，
    绝不静默吞掉失败批——D-26b「批量 miss → kept=0 → 零交易」先例）。
    批间审计行 QS_QMT_CLOSE_BATCH 仅首次 valuation 调用打印（_QS_QMT_ONCE
    ['close_batch_log'] 限定，防每 bar ×10 行日志膨胀——方案 §2.3 审计 #10）。
    """
    out = {}
    _scodes = [str(c) for c in (codes or [])]
    if not _scodes:
        return out
    _bs = max(int(batch), 1)
    _nb = (len(_scodes) + _bs - 1) // _bs
    _log = _QS_QMT_ONCE.get('close_batch_log') is None
    _QS_QMT_ONCE['close_batch_log'] = 1
    _asc = True  # M1（2026-10-13 用户批复方案 A）：首调批量帧时间升序机检，
    # 逐帧 df.index.is_monotonic_increasing 取与；结果汇入 CLOSE_EQC 行
    # asc=1|0（-1＝首调无有效帧，未知——防零帧时的 vacuous-true 误导）。
    _asc_n = 0
    try:
        _kw = _qs_snap_kwargs(C)
    except Exception:
        _kw = {'count': 1}  # 与 _qs_snap_kwargs 内部回退同款 fail-safe
    for _k in range(_nb):
        _bc = _scodes[_k * _bs:(_k + 1) * _bs]
        _t0 = _qs_progress_start()
        _hit = 0
        mkt = None
        try:
            mkt = C.get_market_data_ex(['close'], _bc, period='1d', **_kw,
                                       dividend_type='none')
        except Exception:
            mkt = None  # 态⑤批级异常 → 整批回退逐只
        if not isinstance(mkt, dict):  # 态④返回非 dict → 整批回退逐只
            mkt = None
        for _code in _bc:
            _v = None
            if mkt is not None:
                df = mkt.get(_code)  # 态①缺码 → get 得 None → 回落
                try:
                    if df is not None and len(df) > 0:  # 态②空表 → 回落
                        _v = float(df.iloc[-1]['close'])  # 态③非数值 → 回落
                        if _v != _v:  # NaN 视非数值（双哨兵之一）→ 回落
                            _v = None
                except Exception:
                    _v = None
            out[_code] = _v
            if _v is not None:
                _hit += 1
                if _log:  # M1：升序机检独立 try（异常不污染取值路径）
                    try:
                        if not bool(df.index.is_monotonic_increasing):
                            _asc = False
                    except Exception:
                        _asc = False
                    _asc_n += 1
        if _log:
            _el = 0.0
            _t1 = _qs_progress_start()
            if _t0 is not None and _t0 > 0 and _t1 is not None and _t1 > 0:
                _el = _t1 - _t0
            print('[QS-QMT log.info] QS_QMT_CLOSE_BATCH i=%d/%d size=%d hit=%d '
                  'miss=%d elapsed=%.1fs'
                  % (_k + 1, _nb, len(_bc), _hit, len(_bc) - _hit, _el))
    if _log:
        _QS_QMT_ONCE['close_asc'] = (1 if _asc else 0) if _asc_n else -1
    return out


def _qs_fin_to_rows(raw, shape=None, codes=None):
    """get_financial_data 原始返回 → {code: {field: value}} 归一（M3.1 双轨修复 F-1）。

    轨 A（主）**显式形态声明**：``shape='code_major'`` / ``'field_major'`` 时按声明解析、
    **零猜测**——M5-1 钉死真实返回形态后由调用方经此路径接入（**声明优先于识别**）。

    轨 B（兜底）**证券代码模式识别**：``shape=None`` 时以「六位数字 + 点 + SH/SZ/BJ 后缀」的
    正则（函数体 ``code_re`` 处，完整形态见该行代码）判定哪一层是 code 层（抽样 ≤5 键）。

    **不确定即保守**（②审裁定）：两层均不命中代码形态、**或两层同时命中（歧义）** → 返回 ``{}``
    并打印 ``QS_QMT_FIN_SHAPE_UNDETERMINED`` 审计行——**绝不猜**。原实现按「两层 value 是否 dict」
    探测，而 ``{code:{field:v}}`` 与 ``{field:{code:v}}`` 结构同构不可区分 → 纯 dict 形态被误判，
    致全 NaN 且与「真的缺数」不可区分（静默错值；M3 桩冒烟发现 F-1）。

    D-9 轨（2026-10-10，M5.1b ②审要点②）：get_financial_data 实测两形态——多代码 =
    ``pandas.Panel``（items=codes × major=dates × minor=fields）、单代码 = ``DataFrame``
    （index=date_str，columns=无前缀字段名；证据 probe 17:18 / M5-1b）。原 ``to_dict('index')``
    轨对两形态均产出 **date 层嵌套**，与消费方 ``{code:{field:v}}`` 契约错位 → 全 NaN
    （不可与真缺数区分）。新轨：逐 code 取其 DataFrame **末行**（report_type='announce_time'
    过滤后的最新宣告记录，as-of 近似）投影为字段映射；Panel 判定用 duck-typing
    （``major_axis`` 为 Panel 独有签名，QMT 内置旧版 pandas 有 Panel、本仓新版无——避免直接 import）。
    **不放宽轨 A/B 与 UNDETERMINED 保守语义**（第三形态扩展，原 dict 路径零改动）。

    DataFrame 形态（``to_dict('index')``，依据 07-行情函数.md:1889 投影）路径**不变**
    （D-9 轨在前拦截实测两形态后，该路径保留给未实测的其余 DataFrame 场景）。
    """
    import re as _qs_re  # 局部 import：不依赖产物头部 import 面（产物仅 import pandas）
    code_re = _qs_re.compile(r'^\d{6}\.(SH|SZ|BJ)$', _qs_re.IGNORECASE)

    rows = {}
    if raw is None:
        return rows
    # ---- D-9 轨：实测形态（Panel / 单代码 DataFrame）→ {code: 末行字段映射} ----
    try:
        if hasattr(raw, 'major_axis'):  # Panel（多代码实测形态，duck-typing）
            for _code in list(raw.items):
                df_one = raw[_code]
                if df_one is not None and len(df_one) > 0:
                    rows[str(_code)] = {str(_k): _v for _k, _v in
                                        df_one.iloc[-1].to_dict().items()}
            return rows
    except Exception:
        return rows
    if codes and hasattr(raw, 'loc') and hasattr(raw, 'columns')             and not isinstance(raw, dict):  # 单代码 DataFrame 实测形态
        try:
            if len(raw) > 0 and len(codes) == 1:
                rows[str(codes[0])] = {str(_k): _v for _k, _v in
                                       raw.iloc[-1].to_dict().items()}
            return rows
        except Exception:
            return rows
    if hasattr(raw, 'to_dict'):
        try:
            return raw.to_dict('index')
        except Exception:
            return rows
    if not isinstance(raw, dict):
        return rows

    def _code_major(d):
        for code, fdict in d.items():
            if isinstance(fdict, dict):
                rows.setdefault(str(code), {}).update(fdict)

    def _field_major(d):
        for field, cmap in d.items():
            if isinstance(cmap, dict):
                for code, v in cmap.items():
                    rows.setdefault(str(code), {})[field] = v

    # 轨 A：显式声明（零猜测）
    if shape == 'code_major':
        _code_major(raw)
        return rows
    if shape == 'field_major':
        _field_major(raw)
        return rows

    # 轨 B：证券代码模式识别（抽样 ≤5 键；歧义/均不命中 → 保守）
    outer_keys = [str(k) for k in list(raw.keys())[:5]]
    outer_is_code = bool(outer_keys) and all(code_re.match(k) for k in outer_keys)
    first = next(iter(raw.values()), None)
    inner_is_code = False
    if isinstance(first, dict) and first:
        inner_keys = [str(k) for k in list(first.keys())[:5]]
        inner_is_code = all(code_re.match(k) for k in inner_keys)

    if outer_is_code and not inner_is_code:
        _code_major(raw)
        return rows
    if inner_is_code and not outer_is_code:
        _field_major(raw)
        return rows
    print('QS_QMT_FIN_SHAPE_UNDETERMINED: get_financial_data 返回形态无法判定'
          '（outer_code=%s inner_code=%s）——保守返回空，不猜'
          % (outer_is_code, inner_is_code))
    return rows


def _qs_hist_fold(_one, table=None, code=None):
    """D-28 报告期折叠 helper（2026-10-15，方案 §三①；判型＝数据修正型——
    QMT 端回归本地「PIT 全报告期」契约口径）。

    hist 表（eps/growth_ability/profit_ability——与 _qs_get_fundamentals 内
    既有 hist 表集一致）原始帧为**日频全历史填充**（值列与 m_anntime 均
    ffill；首只全史 8740 行、均值 ≈2655 行/只），本地契约为「每报告期一行
    （最新公告版）」（首只 79 期、均值 ≈24 期/只）。HISTDBG3 定谳：组合键
    (m_anntime, m_timetag) 一一对应报告期（ua=ut=uk=79 << rows=8740、
    after=uk）→ 以该组合键 drop_duplicates(keep='last') 折叠——keep='last'
    保留组内**末行**＝最新填充值，与本地「最新公告版」语义一致（§9.2 钉死；
    NaN 键视相等——drop_duplicates 口径，组合键全 NaN 行折叠为一行）。

    回落保护（设计 §三①）：缺任一键列 / 折叠表达式异常 → dropna(how='all')
    （D-26d 既有保护语义降为回落路径）；dropna 亦异常或对象非 DataFrame
    （无 drop_duplicates 面）→ 原帧直通（与 D-27a 块的 except-pass 终态
    同款）。任何异常不外抛。

    table/code 仅供 HISTFOLD 审计行标注（每表一次、仅首只、不进热路径——
    设计 §三⑤），不参与取值逻辑（与 _qs_fin_to_rows_hist 的 table 参数
    同款定位；code 为触发首折叠的证券代码，即审计行 code=<首只> 的取值源）：
      [QS-QMT log.info] QS_QMT_HISTFOLD t=<table> code=<code> before=<n>
      after=<n> intravar=<k>
    intravar＝首只**折叠前**帧上 (m_anntime, m_timetag) 组内值列（排除两键）
    不全等（NaN 视相等）的组数——设计 §四「组内恒等」前提的运行时哨兵，
    >0 即披露；无值列/计算异常打 -1（HISTDBG3 uv 哨兵同款）。NaN 键组
    groupby 默认弃组不计入（生产数据 ffill 后无 NaN 键——HISTDBG3 实证，
    该局限如实披露）。审计代码任何异常必须吞掉不得外抛。
    """
    try:
        if _one is None or not hasattr(_one, 'drop_duplicates'):
            return _one
        _cols = list(_one.columns)
        _folded = None
        if 'm_anntime' in _cols and 'm_timetag' in _cols:
            try:
                _folded = _one.drop_duplicates(
                    subset=['m_anntime', 'm_timetag'], keep='last')
            except Exception:
                _folded = None  # 折叠表达式异常 → 回落 dropna（设计 §三①）
            if _folded is not None:
                # HISTFOLD 审计行（每表一次，_QS_QMT_ONCE 范式——与 HISTDBG3
                # 'histdbg3:<table>' 同款；仅折叠路径打印，before/after 语义恒为
                # 「折叠前后行数」；全段 try/except 吞异常，绝不外抛、不进热路径）
                try:
                    if _QS_QMT_ONCE.get('histfold:' + str(table)) is None:
                        _QS_QMT_ONCE['histfold:' + str(table)] = 1
                        _iv = -1
                        try:
                            _vc = [c for c in _cols
                                   if c not in ('m_anntime', 'm_timetag')]
                            if _vc:
                                _bad = 0
                                for _k, _gf in _one.groupby(
                                        ['m_anntime', 'm_timetag'], sort=False):
                                    for _c in _vc:
                                        _nv = _gf[_c].dropna()
                                        if len(_nv) > 1 and                                                 len(_nv.unique()) > 1:
                                            _bad += 1
                                            break
                                _iv = _bad
                        except Exception:
                            _iv = -1
                        print('[QS-QMT log.info] QS_QMT_HISTFOLD t=%s code=%s '
                              'before=%d after=%d intravar=%d'
                              % (table, code, len(_one), len(_folded), _iv))
                except Exception:
                    pass
        if _folded is None:
            try:
                _folded = _one.dropna(how='all')
            except Exception:
                _folded = _one
        return _folded
    except Exception:
        return _one


def _qs_fin_to_rows_hist(raw, shape=None, codes=None, table=None):
    """get_financial_data 原始返回 → {code: [行字段映射, ...]} 全历史行收集
    （D-19 二层修复，2026-10-11）。

    table（D-27b 件 B，方案 §2.3 审计 N5）：缺省 None 保持既有调用兼容；
    调用方 _qs_get_fundamentals 显式传入，仅供 HISTDBG3 探针行标注表名，
    不参与任何取值逻辑。

    与 _qs_fin_to_rows 同一实测形态族（D-9：多代码 Panel / 单代码 DataFrame），
    但按本地契约收集**全部报告期行**而非仅末行——本地 ptrade_api.py:1022-1027
    eps/profit_ability/growth_ability 走 get_financial 返回 PIT 窗口全报告期行
    （index=code 重复多行）；消费方实证：CANSLIM:406 groupby('code') 逐期拆
    单季算 C（当季加速）/A（3 年 CAGR），weekly:138-163 _latest_by_code 按
    (end_date, publ_date) 取最新有值行。实测形态命中即多行收集；其余形态
    （dict / 未实测 DataFrame）委托 _qs_fin_to_rows 单行语义包装单元素列表
    （识别 / 保守语义与其完全一致，不新增猜测路径）。
    """
    rows = {}
    if raw is None:
        return rows
    try:
        if hasattr(raw, 'major_axis'):  # Panel（多代码实测形态，duck-typing）
            # D-26 修复（2026-10-12）：per-code `raw[_code]` + `iterrows()` 双反模式
            # 实测 0.55s/只 × 3666 只 ≈ 35 分钟（21:23-21:37 端测：500→263s /
            # 1000→539s / 1500→848s，近似线性）。改双路径：
            #   path=batch   —— 一次 raw.to_frame()（Panel → MultiIndex DataFrame）后按
            #                    code 取子表，避免 3666 次 Panel 取值；
            #   path=percode —— to_frame 不可用/结构不匹配时回落（仍逐 code 取值）；
            # 两路径的行展开一律以 to_dict('records') 取代 iterrows()（消除逐行 Series
            # 构造）。输出结构（dict-of-list-of-dict、键 str 化、行序）与修复前逐位一致
            # —— 判型＝纯性能型，任何输出 diff 即验收失败回退。
            _codes = list(raw.items)
            _t0 = _qs_progress_start()
            _frame = None
            try:
                _frame = raw.to_frame() if hasattr(raw, 'to_frame') else None
            except Exception:
                _frame = None
            _path = 'batch' if (_frame is not None and hasattr(_frame, 'loc')
                                and hasattr(_frame, 'index')) else 'percode'
            _qs_progress('fin_to_rows_hist', 0, len(_codes), _t0, 'begin',
                         'path=%s' % _path)
            # D-26c 结构探针（2026-10-12）：一次打印、不进热路径。目的＝定位
            #   ① `.loc` 全 miss 的真实结构原因（B 计划依据：frame_levels=2 而非
            #      pandas Panel 的 3 层 (items, major, minor)）；
            #   ② 单只行数量级（是否 2651 行/只——对照「PIT 全报告期」契约是否合理）。
            # 安全约束：frame index 可达千万行，一律切片取值，禁止 list(完整 index)。
            try:
                _c0 = _codes[0] if _codes else None
                _o0 = raw[_c0] if _c0 is not None else None
                _ix = getattr(_frame, 'index', None)
                # 注意：names/columns 可能是 Index 对象，对 Index 求布尔会抛
                # 「truth value of a Index is ambiguous」——一律显式 None 判定，
                # 禁用 `x or []`（2026-10-12 22:59 端测实证的探针自身缺陷）。
                _nm = getattr(_ix, 'names', None)
                _nm = list(_nm) if _nm is not None else None
                _cl = getattr(_frame, 'columns', None)
                _cl = list(_cl)[:4] if _cl is not None else None
                _oix = getattr(_o0, 'index', None)
                _oih = list(_oix[:3]) if _oix is not None else None
                _oic = type(_oix).__name__ if _oix is not None else None
                print('[QS-QMT log.info] QS_QMT_HISTDBG2 raw=%s frame=%s levels=%s '
                      'idx_names=%s idx_head=%s cols=%s one_shape=%s one_idxcls=%s '
                      'one_idx_head=%s'
                      % (type(raw).__name__, type(_frame).__name__,
                         len(_nm) if _nm is not None else -1, _nm,
                         list(_ix[:2]) if _ix is not None else None,
                         _cl, getattr(_o0, 'shape', None), _oic, _oih))
            except Exception as _e0:
                print('[QS-QMT log.info] QS_QMT_HISTDBG2 err=%r' % (_e0,))
            # D-27b 件 B 探针（2026-10-13，方案 §2.2 v1.3 定稿；判型＝新增检测型）：
            #   每表首只 code 一次打印。读数定位「按报告期去重折叠键」（§9.2，D-28
            #   下轮立项）可用性——uk=unique(m_anntime,m_timetag) 组合数为**定谳读数**
            #   （≈报告期数→键可用 / ≈行数→放弃 / 中间值→呈报裁定，三分支不预设）；
            #   after=§9.2 折叠表达式**预演读数**（数值恒等于 uk——drop_duplicates
            #   (subset=K) 行数与 keep 无关，保留仅提前暴露该表达式在真实数据上的
            #   异常路径，审计 N4）。
            #   字段口径（审计 #5 钉死）：ua/ut/uk/uv 一律 drop_duplicates 口径
            #   （NaN 视相等），**禁 nunique()**——其默认弃 NaN，m_anntime 全 NaN
            #   时 ua=0 而 uk≈40 会误判键可用性；uv=值列（排除 m_anntime/m_timetag
            #   两列）上的 drop_duplicates 行数。
            # 安全约束（D-26c 教训，§2.2）：全段 try/except 静默 pass（异常绝不影响
            #   主流程）；切片取值、禁 list(完整 index)、禁对 pandas 对象求布尔
            #   （禁 x or []）；打印前取值显式 None 判定；一次打印、不进热路径。
            try:
                if _QS_QMT_ONCE.get('histdbg3:' + str(table)) is None:
                    _c0 = _codes[0] if _codes else None
                    _o0 = raw[_c0] if _c0 is not None else None
                    if _o0 is not None and hasattr(_o0, 'drop_duplicates'):
                        _ua = len(_o0.drop_duplicates(subset=['m_anntime']))
                        _ut = len(_o0.drop_duplicates(subset=['m_timetag']))
                        _uk = len(_o0.drop_duplicates(
                            subset=['m_anntime', 'm_timetag']))
                        _vc = [c for c in list(_o0.columns)
                               if c not in ('m_anntime', 'm_timetag')]
                        _uv = (len(_o0.drop_duplicates(subset=_vc))
                               if _vc else -1)  # 无值列哨兵 -1（防御）
                        _after = len(_o0.drop_duplicates(
                            subset=['m_anntime', 'm_timetag'], keep='last'))
                        # 单行纪律（验收期局部修复 2026-10-13）：DataFrame repr
                        # 天然多行，须空白折叠为单行，否则审计行断行、grep 失效。
                        print('[QS-QMT log.info] QS_QMT_HISTDBG3 t=%s code=%s '
                              'rows=%d ua=%d ut=%d uk=%d uv=%d after=%d '
                              'head=%s tail=%s'
                              % (table, _c0, len(_o0), _ua, _ut, _uk, _uv,
                                 _after,
                                 ' '.join(repr(_o0.iloc[:3]).split())[:120],
                                 ' '.join(repr(_o0.iloc[-3:]).split())[:120]))
                        _QS_QMT_ONCE['histdbg3:' + str(table)] = 1
            except Exception:
                pass
            _miss = 0
            for _i, _code in enumerate(_codes):
                _qs_progress('fin_to_rows_hist', _i + 1, len(_codes), _t0)
                _one = None
                if _path == 'batch':
                    try:
                        _one = _frame.loc[_code]
                    except Exception:
                        _one = None
                    if _one is None or len(_one) == 0:
                        # D-26b 回落（2026-10-12 端测实证）：batch 仅为加速尝试。
                        # 真实 QMT Panel 的 to_frame() index 结构与本地假设不同 →
                        # .loc[_code] 取不到 → rows 空 → QSFUNDHIST kept=0 cc=0 →
                        # L3v_complete=0（22:19 复测实证）。取不到即退回原路径，
                        # 保证行为不劣于修复前。miss 计数由下方诊断行输出。
                        _miss += 1
                        try:
                            _one = raw[_code]
                        except Exception:
                            _one = None
                else:
                    try:
                        _one = raw[_code]
                    except Exception:
                        _one = None
                if _one is not None and len(_one) > 0:
                    # [沿革·D-26d dropna(how='all') → D-27a m_anntime notna 自保护
                    #  收敛 → D-28 报告期折叠取代（2026-10-15，方案 §三②）] QMT Panel
                    # 按 (日期 × 字段) 展开为**日频全历史**（实测 one_shape=(8739,3)、
                    # 首只全史 8740 行、均值 ≈2655 行/只），而本地「PIT 全报告期」契约
                    # 为每报告期一行（首只 79 期、均值 ≈24 期/只）——行集 ~110× 冗余
                    # 即处理段 226s 的根因。HISTDBG3 定谳：m_anntime 亦被 ffill 填充
                    # → D-27a 的 notna 过滤在当前数据形态下恒为空操作；组合键
                    # (m_anntime, m_timetag) 一一对应报告期（ua=ut=uk=79、after=uk）
                    # → D-28 改按组合键 drop_duplicates(keep='last') 折叠（判型＝
                    # 数据修正型：QMT 端回归本地契约口径；三可达消费点结果逐位
                    # 不变——不变式论证见设计 §四）。缺键列/异常 → helper 内回落
                    # dropna(how='all')（D-26d 保护语义降为回落路径）；HISTFOLD
                    # 审计行（每表一次）在 helper 内打印，不进热路径。
                    _one = _qs_hist_fold(_one, table, _code)
                if _one is not None and len(_one) > 0:
                    try:
                        _rs = _one.to_dict('records')
                        rows[str(_code)] = [{str(_k): _v for _k, _v in _r.items()}
                                            for _r in _rs]
                    except Exception:
                        rows[str(_code)] = [
                            {str(_k): _v for _k, _v in _r.items()}
                            for _, _r in _one.iterrows()]
            # D-26b 诊断行（每批一次）：直接回答「.loc 是否取到」与「回落是否生效」，
            # 用于二分「本地 `.loc` 假设错」与「D-19 既有契约问题」。
            if _path == 'batch':
                print('[QS-QMT log.info] QS_QMT_HISTDBG path=batch '
                      'frame_levels=%d miss=%d rows=%d n=%d'
                      % (len(getattr(getattr(_frame, 'index', None), 'names', []) or []),
                         _miss, len(rows), len(_codes)))
            _qs_progress('fin_to_rows_hist', len(_codes), len(_codes), _t0, 'end')
            return rows
    except Exception:
        return rows
    if codes and hasattr(raw, 'loc') and hasattr(raw, 'columns')             and not isinstance(raw, dict):  # 单代码 DataFrame 实测形态
        try:
            if len(raw) > 0 and len(codes) == 1:
                # D-28 ③（2026-10-15，方案 §三③）：单码 DataFrame 路径对称接入
                # 同一折叠 helper（与 Panel 路径同一口径，消除不对称——此前该
                # 路径无任何收敛）。缺键列帧回落 dropna(how='all') 的二阶变化
                # （此前全 NaN 行存活到策略）见设计 §四⑥——当前无可达单码
                # hist 调用，实害为零，披露入册。
                _one = _qs_hist_fold(raw, table, codes[0])
                rows[str(codes[0])] = [{str(_k): _v for _k, _v in _r.items()}
                                       for _, _r in _one.iterrows()]
            return rows
        except Exception:
            return rows
    single = _qs_fin_to_rows(raw, shape=shape, codes=codes)
    return {str(_c): [_r] for _c, _r in single.items()}


# D-27b 一次性标志（2026-10-13，方案 §2.3 审计 #6 钉死）：产物文件**模块级** dict。
# 局部变量跨调用丢失；g 即 ContextInfo（:308），挂任意属性属 M5 未实测项——均
# 不可用。每产物文件独立模块命名空间 → 多策略同进程加载不串味。
# 键：'histdbg3:<table>'（HISTDBG3 探针每表一次）/ 'close_batch_log'
# （CLOSE_BATCH 批间行仅首次 valuation 调用打印）/ 'close_eqc'（CLOSE_EQC
# 自检仅首次 valuation 调用执行）。
_QS_QMT_ONCE = {}

_QS_QMT_FUND_FIELD_MAP = {
    # table → (QMT 原生表, {本地字段 → 原生字段})
    'eps': ('PERSHAREINDEX', {'eps': 's_fa_eps_basic',          # :2388
                              'publ_date': 'm_anntime',         # :2330【M5 实测项】
                              'end_date': 'm_timetag'}),        # :2329【M5 实测项】
    'growth_ability': ('PERSHAREINDEX', {'or_yoy': 'inc_revenue_rate',  # :2395
                                         'publ_date': 'm_anntime',
                                         'end_date': 'm_timetag'}),
    'valuation': ('CAPITALSTRUCTURE', {'float_value': 'circulating_capital'}),  # :2376 派生
    'profit_ability': ('PERSHAREINDEX', {'roe': 'du_return_on_equity',  # D-29：净资产收益率
                                    'net_profit_margin': 'net_profit',  # D-29：净利率
                                    'publ_date': 'm_anntime',
                                    'end_date': 'm_timetag'}),
    # D-29（2026-10-17）：profit_ability 原生比率字段映射。依据 Context7
    # /websites/dict_thinktrader_net（nativeApi/xtdata.html）：PERSHAREINDEX 比率类字段**无
    # s_fa_ 前缀**（du_return_on_equity/equity_roe/net_roe/net_profit 等）；v5 生产口径探针
    # （docs/probe/qmt_profit_ability_probe_v5-20261017.py，2026-10-17 00:21 端测）实测：
    # du_return_on_equity valid=9460/ratio=0.541/sample=7.81；net_profit 9460/0.541/22.75；
    # equity_roe 7891/0.451/sample=16.0（覆盖率低+整数异常，②审明确排除）；
    # total_roe/gross_profit 0 命中（不纳入）。映射口径未确证（本地 profit_ability 无口径定义）
    # + 消费面仅排序（契约矩阵 L82/RD-2）→ 实施后 A/B 对照一次定口径，见方案
    # docs/qmt-profit-ability-mapping-design.md §三。余项字段（roa/roic/…）本轮不映射。
}


def _qs_get_fundamentals(table, codes, date=None, fields=None, C=None):
    """PTrade get_fundamentals 语义映射（定稿映射表）。"""
    C = _qs_pick_ctx(C)
    if fields is None:
        fields = sorted(_QS_QMT_FUND_FIELD_MAP.get(table, ({}, {}))[1].keys())
    fields = list(fields)
    codes = [str(c) for c in codes]
    frame = pd.DataFrame(index=codes, columns=fields)  # 空→NaN 契约行（不抛错）
    if table not in _QS_QMT_FUND_FIELD_MAP or not codes:
        return frame
    native_table, fmap = _QS_QMT_FUND_FIELD_MAP[table]
    # fieldList 元素须为 '表名.字段名' 前缀形态：07-行情函数.md:1863（示例
    # ['ASHAREBALANCESHEET.fix_assets', '利润表.净利润']）、:1906（'CAPITALSTRUCTURE.total_capital'）；
    # :1837 建议使用对照表中的英文表名和迅投英文字段。
    native_fields = sorted(set([f"{native_table}.{fmap.get(f, f)}" for f in fields]))
    # D-28 键字段恒附（2026-10-15，方案 §三④·审计 P1-1）：hist 表（表集与下方
    # hist 装配段既有 hist 集一致）请求列恒附两折叠键原生字段——实测「返回列=
    # 请求列」结构（one_shape=(8739,3)，weekly 请求 3 字段得 3 列）下，消费点
    # 不请求 publ_date 时帧缺 m_anntime，折叠无从生效（SG-MS-PEG:358
    # fields=['roe','end_date']、F-Score:329 fields=['roe'] 缺键实例）。已存在
    # 则幂等跳过（不重复、不改既有键序，仅在表尾追加）；辅助键经下方既有投影
    # 段（for _lf in fields / columns=fields——只遍历调用方请求列）自动剥离，
    # 返回帧契约零变化（内部帧列数 +2，one_shape 读数变化属披露项，设计 §四
    # 影响面④）。
    if table in ('eps', 'growth_ability', 'profit_ability'):
        for _kf in ('%s.m_anntime' % native_table,
                    '%s.m_timetag' % native_table):
            if _kf not in native_fields:
                native_fields.append(_kf)
    # D-24：单次大批量调用是静默黑洞 → begin/end 哨兵成对（finally 保证配对，
    # 异常路径亦不留孤儿 begin——否则「批失败」与「死锁」在日志上不可区分）。
    _n = len(codes)
    _t0 = _qs_progress_start()
    _qs_progress('get_financial_data', 0, _n, _t0, 'begin', 'table=%s' % (table,))
    _err = 0
    try:
        # endDate 为必填字符串（Context7 /websites/dict_thinktrader_net·
        # innerApi/data_function.html：startDate/endDate required；示例
        # '20171209'→'20231204'）——date 缺省时空串属未定义行为（17:31 复测
        # QSFUNDHIST cc=0 定谳候选：get_financial_data 返回空）。补安全终点
        # '20991231'：report_type='announce_time' 自身保证不取未来数据（文档：
        # 'announce_time' ensures data is not from the future），窗口右界拉大
        # 仅放宽取数范围；PIT 过滤由消费方按 publ_date/end_date 自行执行
        # （本地契约同构：ptrade_api get_financial 回窗口全行、策略侧自滤）。
        raw = C.get_financial_data(native_fields, codes, '19900101',
                                   str(date) if date else '20991231',
                                   report_type='announce_time')  # 显式钉死 :1867/:1877
    except Exception:
        _err = 1  # D-24 新增语句（既有 return 语义零变：仍静默返回空契约行）
        return frame
    finally:
        _qs_progress('get_financial_data', _n, _n, _t0, 'end',
                     'table=%s err=%d' % (table, _err))
    # 形态声明（轨 A，M3.1）：M5-1 钉死真实返回形态后，此处改传 'code_major'/'field_major'
    # 即由显式声明路径解析（声明优先于识别）；当前传 None → 走 D-9 实测形态轨（Panel/
    # 单代码 DataFrame），未命中再落轨 B 证券代码模式识别。
    # D-19 二层修复（2026-10-11 16:05 复测定谳）：eps/growth_ability/profit_ability
    # （本地 ptrade_api.py:1022 get_financial 支持表）契约 = PIT 窗口全报告期多行
    # （index=code 重复）且只回完整行；wrapper 此前「每 code 单行 + 缺数 NaN 契约行」
    # 双重错位：①CANSLIM:406 需跨季度序列拆单季算 C/A，单行使 EPS 因子静默退化
    # 纯价量；②NaN end_date 行令策略 :391-392 to_datetime→NaT→strftime('NaT')→
    # astype('Int64') 即 int('NaT') 崩（16:05 复测每 FUN 行 warning 实证）。
    # 修复：全历史行收集 + 完整行过滤（任一请求字段 float 不可转或 NaN → 整行
    # 剔除，对齐本地只回完整行契约）；valuation（快照语义）路径不动。
    if table in ('eps', 'growth_ability', 'profit_ability'):
        rows_h = _qs_fin_to_rows_hist(raw, shape=None, codes=codes, table=table)
        recs = []
        for _code in codes:
            for _row in rows_h.get(_code) or []:
                _rec = {}
                _ok = True
                for _lf in fields:
                    _v = _row.get(fmap.get(_lf, _lf))
                    try:
                        _fv = float(_v)
                    except (TypeError, ValueError):
                        _ok = False
                        break
                    if _fv != _fv:  # NaN 自不等（None 经 float 已抛 TypeError）
                        _ok = False
                        break
                    _rec[_lf] = _fv
                if _ok:
                    recs.append((_code, _rec))
        if recs:
            frame = pd.DataFrame([_r for _, _r in recs], columns=fields)
            frame.index = [_c for _c, _ in recs]
        else:
            frame = pd.DataFrame(columns=fields)
        # D-19 验收审计行（2026-10-11 17:0x 复测补证，新增检测型·纯增益）：
        # kept=完整行数 / drop=被完整行过滤剔除数 / cc=有完整行的代码数 / n=请求
        # 代码数。消费方 composite 为 NaN 按剩余权重归一（CANSLIM _composite_score
        # :489-494），C/A 全 NaN 与有值在选股结果上不可区分——本行是 C/A 因子
        # 是否真有数据流入的唯一可机检判据（kept>0 即历史表多行契约生效）。
        try:
            _tot = sum(len(rows_h.get(_c) or []) for _c in codes)
            _rt = type(raw).__name__
            _rl = ''
            try:
                if raw is None:
                    _rl = '0'
                elif hasattr(raw, 'major_axis'):
                    _rl = 'items=%d' % len(list(raw.items))
                elif hasattr(raw, '__len__'):
                    _rl = 'len=%d' % len(raw)
            except Exception:
                _rl = '?'
            print('[QS-QMT log.info] QSFUNDHIST t=%s kept=%d drop=%d cc=%d n=%d raw=%s %s'
                  % (table, len(recs), _tot - len(recs),
                     len(set(_c for _c, _ in recs)), len(codes), _rt, _rl))
        except Exception:
            pass
        return frame
    rows = _qs_fin_to_rows(raw, shape=None, codes=codes)
    _t0 = _qs_progress_start()  # D-24 进度审计（批 <500 自动静默）
    # D-27b 件 D（2026-10-13，方案 §2.3；判型＝纯性能型）：valuation 循环前对
    # 「row 非空码集」分批预取 close（批 400），循环内查表——预取命中且值非
    # None 即用之（与 _qs_last_close 同窗口/同末行取法/同 float 转换，值逐位
    # 一致）；未命中或 None → 回落 _qs_last_close 逐只（现行为逐位不变）。
    # 预取集合＝row 非空码集（对齐下方 `if not row: continue` 条件，不对无
    # row 码做无效取数——方案 §2.3 审计 #11；hit/miss 口径与该集合对齐）。
    _close_map = {}
    if table == 'valuation' and 'float_value' in fields:
        _pref = [c for c in codes if rows.get(c)]
        if _pref:
            _close_map = _qs_close_batch(_pref, C=C, batch=400)
        # D-27b 件 E EQC 自检（2026-10-13；判型＝新增检测型，一次性——
        # _QS_QMT_ONCE['close_eqc']）：首次 valuation 调用对**批量命中码集**
        # 前 ≤20 只，批量值 vs _qs_last_close 逐只值真实差分。分母＝批量命中
        # 码集（防回落码自指空转：五态回落后双测同源恒 diff=0——审计 N6）；
        # None/NaN 双哨兵归一记 miss、不计 diff；diff 仅对双侧均有值计数；
        # hit+miss==n 对账；批量命中数为 0 → 显式 n=0 SKIP（禁止误导性
        # diff=0）。自检只 print，不改任何返回值。
        try:
            if _QS_QMT_ONCE.get('close_eqc') is None:
                _QS_QMT_ONCE['close_eqc'] = 1
                _hitc = [c for c in _pref if _close_map.get(c) is not None]
                if not _hitc:
                    print('[QS-QMT log.info] QS_QMT_CLOSE_EQC n=0 SKIP asc=%s'
                          % _QS_QMT_ONCE.get('close_asc', -1))
                else:
                    _smp = _hitc[:20]
                    _h = _d = _m = 0
                    for c in _smp:
                        _b = _close_map.get(c)
                        _r = _qs_last_close(C, c)
                        if _r is None or _r != _r or _b != _b:  # 双哨兵归一
                            _m += 1
                        else:
                            _h += 1
                            if _b != _r:
                                _d += 1
                    print('[QS-QMT log.info] QS_QMT_CLOSE_EQC n=%d hit=%d '
                          'diff=%d miss=%d asc=%s'
                          % (len(_smp), _h, _d, _m,
                             _QS_QMT_ONCE.get('close_asc', -1)))
        except Exception:
            pass
    for _i, code in enumerate(codes):
        row = rows.get(code)
        _qs_progress('query_last_close', _i + 1, len(codes), _t0)
        if not row:
            continue  # 缺数保持 NaN 契约行
        rec = {}
        for lf in fields:
            v = row.get(fmap.get(lf, lf))
            # D-19 修复（2026-10-11）：本地契约 publ_date/end_date = epoch 毫秒
            # 数值（消费方实证：CANSLIM:389 to_datetime(unit='ms') 自转 /
            # weekly_smallcap:154 与 周频三层:150 astype(float)；fall_reversal
            # / tech_etf 不消费）。wrapper 此前 _qs_fin_ms_to_date 过度归一成
            # 'YYYYMMDD' 字符串制造三方错位，且缺失毫秒→None→策略 strftime 产
            # 'NaT' 字符串→astype('Int64') 即 int('NaT') 崩（03:24 复测每 bar
            # get_fundamentals failed: invalid literal for int() 实证）。
            # 修复：原始毫秒值直通（缺失保持 NaN 契约行，消费方 dropna/fillna
            # 天然剔除）；_qs_fin_ms_to_date 函数保留备用但不再于此调用。
            rec[lf] = v
        if table == 'valuation' and 'float_value' in fields:
            cap = row.get('circulating_capital')      # 股 :2376
            # D-27b 件 D：优先查预取表（命中且值非 None → 用之，与逐只取数
            # 同式逐位一致）；未命中或 None → 回落 _qs_last_close 逐只
            # （与现行为逐位一致——五态回落钉死于此）。
            close = _close_map.get(code)
            if close is None:
                close = _qs_last_close(C, code)       # 元（不复权 :122）
            try:
                usable = cap is not None and close is not None
                rec['float_value'] = float(cap) * float(close) if usable else float('nan')
            except (TypeError, ValueError):
                rec['float_value'] = float('nan')
        for k, v in rec.items():
            frame.loc[code, k] = v
    return frame


def _qs_get_fundamentals_batch(table, codes, date=None, fields=None, C=None):
    """批式财务取数（与单式同构）。

    get_financial_data 原生 stockList 批量（07:1839/:1852）——无逐只循环必要，
    直接委托 _qs_get_fundamentals（定稿映射表 + report_type='announce_time'
    钉死口径 + 空→NaN 契约行 + 毫秒→'YYYYMMDD' 归一，全部继承）。
    """
    return _qs_get_fundamentals(table, codes, date=date, fields=fields, C=C)
# ---- QS-QMT 别名视图区（机制①：模块级名字）-----------------------------------
# QMT 无模块级 ContextInfo：g/context/data 的真实绑定在 init(C)/handlebar(C) 壳内
# 经 global 刷新（见策略主体生命周期壳 prologue）；此处占位 None 保证模块导入期可执行、
# 名字先行存在（hasattr(g,'x') 守卫天然可用）。log 无 C 依赖，模块级即成品。
g = None
context = None
data = None
log = _qs_log_view()
# run_daily 注册表（规则 i）：QMT 无盘中定时回调面——日线周期下坍缩为
# 「每根日 bar 各回调一次」；注册入表后由 handlebar 壳尾段统一驱动。
_QS_QMT_RUN_DAILY = []
# ==== QS-QMT wrapper 遮蔽区（机制②：同名遮蔽 + C 捕获）—— M2b 块2 落地 ====
# 同名遮蔽形态参照 source_import.py:448-456（get_history 同名重绑定 + C 捕获）：
# 产物内以 PTrade 原同名定义 wrapper，策略调用点零改动即解析到产物内定义；
# C 捕获统一经 _qs_pick_ctx（显式传参优先，否则模块级 g——init/handlebar 内刷新）。
# 机制③（keyword/位置双形态）：由形参名与本地签名对齐天然承载；
# get_history 双签名识别规则与本地 ptrade_api.py:1407-1423 同款（首参 int →
# count-first 模式）。
# 已接线（14 名）：get_history / get_history_batch / order / order_target_value /
#   get_positions / get_fundamentals / get_fundamentals_batch /
#   filter_stock_by_status / get_Ashares / get_trade_days / get_stock_info（本区）+
#   get_position / get_stock_status / current_price（定义于 EXT 区，不重复）。
# 未接线（转换器 warnings 如实登记「运行期不可用」，不静默）：get_price——
#   日期区间语义（本地签名 ptrade_api.py:1669）超出块2 19 件清单，块3 编排层处置。


def get_history(security=None, count=None, unit='1d', fields=None, frequency=None,
                field=None, security_list=None, fq='pre', include=False,
                fill='nan', is_dict=False, C=None):
    """机制②同名遮蔽：本地 get_history 双签名（ptrade_api.py:1392-1404）。

    签名 A（security-first）：get_history(security, count, unit='1d', fields=None)
    签名 B（count-first，Ptrade 官方）：get_history(count, frequency='1d',
        field='close', security_list=None, ...)——识别规则（本地同款）：
        第一个位置参数为 int → count-first 模式（位置参数重映射）。
    返回：is_dict=True 或多标的 → {code: DataFrame}（E1：日线恒不含执行日 bar）；
    单标的 + is_dict=False → 该标的 DataFrame（无数据 → 空 DataFrame）。
    多标的 + is_dict=False 的本地面板形态未对齐（此处返回 dict）→【M5 实测项】。
    fill 参数仅为签名兼容（QMT fill_data 语义未映射 →【M5 实测项】）。
    """
    if isinstance(security, int):
        _count, _freq, _field, _sec_list = security, count, unit, fields
        count = _count
        unit = _freq or frequency or '1d'
        if field is not None:
            fields = field
        elif _field and _field != '1d':
            fields = _field
        security = security_list if security_list is not None else _sec_list
    if security is None:
        security = security_list
    if security is None:
        return {} if is_dict else pd.DataFrame()
    if count is None:
        count = 20
    if frequency and unit == '1d':
        unit = frequency
    # D-17 修复（2026-10-11）：纯关键字调用形态（count=/frequency=/field=/
    # security_list= 全关键字，无位置参数）不进上方 int 重映射分支——
    # field 关键字被丢弃、fields 恒 None → wrapper 默认 ['close'] →
    # high/volume 根本未请求 → 产物门槛 len(high/volume)<260 全灭（q=0，
    # 03:12 复测实证；H=4414 因 close 列在而自洽）。无条件归一：field
    # 显式给则覆盖 fields（与重映射分支优先级一致，幂等不冲突）。
    if field is not None:
        fields = field
    codes = [security] if isinstance(security, str) else list(security)
    out = _qs_get_history(codes, count, unit=unit, fields=fields, fq=fq,
                          include=include, C=C)
    if not is_dict and len(codes) == 1:
        df = out.get(codes[0])
        return df if df is not None else pd.DataFrame()
    return out


def get_history_batch(security_list, count, unit='1d', fields=None, fq='pre',
                      include=False, is_dict=True, C=None, **kwargs):
    """机制②同名遮蔽（本地 B1 契约 ptrade_api.py:1757-1760）：强制 {code: DataFrame}。"""
    return _qs_get_history_batch(security_list, count, unit=unit, fields=fields,
                                 fq=fq, include=include, C=C)


def order(security, amount, limit_price=None, C=None):
    """机制②同名遮蔽（本地签名 ptrade_api.py:1258）：amount>0 买 / <0 卖（股数）。"""
    return _qs_order(security, amount, C=C)


def order_target_value(security, value, limit_price=None, C=None):
    """机制②同名遮蔽（本地签名 ptrade_api.py:1240）：调仓至目标市值（元）。

    keyword/位置双形态由形参名天然承载（机制③：order_target_value(security=,
    value=) 与位置式皆收）；limit_price 不消费（prType=5 最新价，
    05-枚举常量.md:132）；静默跳过分支（_qs_noop_target 式）见 _qs_order_target_value。
    """
    return _qs_order_target_value(security, value, C=C)


def get_positions(security=None, C=None):
    """机制②同名遮蔽（本地签名 ptrade_api.py:1211）：{code: Position 视图}。"""
    pos = _qs_get_positions(C)
    if security is None:
        return pos
    return dict((k, v) for k, v in pos.items() if k == security)


def get_fundamentals(security, table='valuation', fields=None, date=None,
                     start_year=None, end_year=None, report_types=None,
                     merge_type=None, is_dataframe=False, C=None, **kwargs):
    """机制②同名遮蔽（本地签名 ptrade_api.py:938-940）。

    QMT 映射仅覆盖定稿映射表 3 表（eps/growth_ability/valuation，见
    _QS_QMT_FUNDAMENTALS_EXT）；其余表 → 空 NaN 契约行 + QS_QMT_SEMANTIC_DIFF
    登记行。start_year/end_year/report_types/merge_type 不消费
    （get_financial_data 公告窗口语义，07:1863/:1867）→【M5 实测项】。
    """
    codes = [security] if isinstance(security, str) else list(security or [])
    if table not in ('eps', 'growth_ability', 'valuation'):
        print('QS_QMT_SEMANTIC_DIFF: get_fundamentals table=%r 未入定稿映射表'
              '（eps/growth_ability/valuation）→ 空 NaN 契约行' % (table,))
    return _qs_get_fundamentals(table, codes, date=date, fields=fields, C=C)


def get_fundamentals_batch(security_list, table='valuation', fields=None,
                           date=None, is_dataframe=True, C=None, **kwargs):
    """机制②同名遮蔽（本地签名 ptrade_api.py:1734-1735）。

    get_financial_data 原生 stockList 批量（07:1839/:1852），委托
    _qs_get_fundamentals_batch（定稿映射表 + announce_time 钉死口径全继承）。
    """
    return _qs_get_fundamentals_batch(table, list(security_list or []),
                                      date=date, fields=fields, C=C)


def filter_stock_by_status(stocks, filter_type=None, query_date=None, C=None):
    """机制②同名遮蔽（本地签名 ptrade_api.py:1078）——判定细节见
    _qs_filter_stock_by_status（ST/HALT/DELISTING 三判 + 不可判定保守保留）。"""
    return _qs_filter_stock_by_status(stocks, filter_type=filter_type,
                                      query_date=query_date, C=C)


def get_Ashares(date=None, exclude_bse=None, include_delisted=None, C=None):
    """机制②同名遮蔽（本地签名 ptrade_api.py:1859）。"""
    return _qs_get_ashares(date=date, exclude_bse=exclude_bse,
                           include_delisted=include_delisted, C=C)


def get_trade_days(start_date=None, end_date=None, count=None, C=None):
    """机制②同名遮蔽（本地签名 ptrade_api.py:1801）。

    本地 count=None（区间语义）→ QMT get_trading_dates count=-1（07:3350）。
    """
    n = -1 if count is None else int(count)
    return _qs_get_trade_days(start_date=start_date, end_date=end_date,
                              count=n, C=C)


def get_stock_info(stocks, field=None, C=None):
    """机制②同名遮蔽（本地签名 ptrade_api.py:2472：结果按调用方原码键控）。"""
    lst = [stocks] if isinstance(stocks, str) else list(stocks or [])
    out = {}
    _t0 = _qs_progress_start()  # D-24 进度审计（批 <500 自动静默）
    for _i, s in enumerate(lst):
        out[s] = _qs_get_stock_info(s, C=C)
        _qs_progress('get_stock_info', _i + 1, len(lst), _t0)
    if field:
        keys = [field] if isinstance(field, str) else list(field or [])
        out = dict((c, dict((k, d.get(k)) for k in keys)) for c, d in out.items())
    return out


def _qs_should_run_daily(C):
    """handlebar 前段门控：日线周期=每根日 bar 恒 True（返回 bool）。

    本地 before_trading_start 的「开盘前时点」在 QMT 日线 bar 回调内无对应挂点 →
    每根日 bar 执行一次；时间门控精细化属 M5 实盘域（块2 不发明语义）。
    """
    return True


def _qs_should_run_after(C):
    """handlebar 尾段门控：日线=每根 bar 尾恒 True（返回 bool；M5 实盘域）。"""
    return True

# ============================================================================
# B2 · 共享面 POS 族回填探针（wrapper 装配型）
#
# 目标格：POS-01..17（持仓字段语义 QMT 锚点）
# 取值口径（生产复刻）：wrapper `_qs_get_trade_detail` -> get_trade_detail_data(
#   acc, 'STOCK', biz_type)，acc = C.accountID or globals()['account'] or ''
#
# **语境约束（重要，先读）**：wrapper 注释载明「回测语境 account/accountID 两者皆空
#   -> 查询返回空，交易走原生函数转发分支」（探针 v1/v2 实证）。故本探针在**回测语境**
#   预期返回 0 条记录 —— 那**不是**缺陷，而是语境不适用：
#     · 回测语境跑：只用于确证「回测语境不可探」这一事实（读数 = account='' / n=0）；
#     · 要真正回填 POS-01..17，须在 **QMT 交易端（模拟盘/实盘）语境**跑本件（account 有效）。
#
# 判据：
#   C1 语境：account 取值 + 各 biz_type 记录数（0 => 回测语境不适用，记明即可）
#   C2 属性清单：首条记录的属性全集（=> 可对 POS-01..17 逐格判定哪些字段存在）
#   C3 字段取值：首条记录逐属性取值（=> 语义对照：amount/可用/成本 等）
#   C4 wrapper 视图：_qs_get_trade_detail 返回条数（生产路径一致性）
#
# 用法：QMT 新建回测策略（或交易端策略）-> 粘贴全文 -> 运行 -> 回抄 [POS_PROBE] 全行
# ============================================================================


def init(C):
    C.qs_pos_done = False


def handlebar(C):
    if getattr(C, "qs_pos_done", False):
        return
    C.qs_pos_done = True

    print("[POS_PROBE] begin")
    acc = ""
    try:
        acc = getattr(C, "accountID", None) or globals().get("account") or ""
    except Exception as _e:
        print("[POS_PROBE] account_exc=%r" % (_e,))
    print("[POS_PROBE] C1 account=%r" % (acc,))

    for _biz in ("POSITION", "ACCOUNT", "ORDER", "DEAL"):
        try:
            _recs = get_trade_detail_data(acc, "STOCK", _biz)
        except Exception as _e:
            print("[POS_PROBE] C1 biz=%s EXC=%r" % (_biz, _e))
            continue
        try:
            _n = len(_recs)
        except Exception:
            _n = -1
        print("[POS_PROBE] C1 biz=%s n=%s" % (_biz, _n))
        if _n and _n > 0:
            _r = _recs[0]
            try:
                _attrs = [a for a in dir(_r) if not a.startswith("__")]
            except Exception:
                _attrs = []
            print("[POS_PROBE] C2 biz=%s attrs=%s" % (_biz, _attrs))
            for _a in _attrs:
                try:
                    _v = getattr(_r, _a)
                except Exception as _e:
                    _v = "EXC:%r" % (_e,)
                try:
                    _v = repr(_v)[:60]
                except Exception:
                    _v = "?"
                print("[POS_PROBE] C3 biz=%s %s=%s" % (_biz, _a, _v))

    try:
        _v2 = _qs_get_trade_detail(C, "POSITION")
        print("[POS_PROBE] C4 wrapper_path n=%s" % (len(_v2),))
    except Exception as _e:
        print("[POS_PROBE] C4 EXC=%r" % (_e,))

    print("[POS_PROBE] end")
