# -*- coding: utf-8 -*-
"""QMT ContextInfo 最小桩（M3 块1/2 · 测试域专用）。

定位与边界（docs/qmt-pipeline-m3-plan.md §⑤ 桩件规格 / §⑥ 形状判据）：
- 用**确定性合成数据**模拟 QMT 内置 Python 的 ContextInfo 消费面，驱动 M2b 产物
  （output/qmt_export/<id>/qmt/<id>_qmt.py，物理编码 GBK）做桩冒烟（方案 B）；
- 只证「产物可被驱动 + 序列形状自洽」（S1-S7），**不做数值对照**（数值对照归 M5 真实环境）；
- 测试域专用（用户条件①：桩件禁入 strategy_compiler/ 生产路径）；对产物**只读**（不修改产物）。

确定性保证：全部合成数据由 zlib.crc32 + 三角函数纯公式生成（**不使用 random**），
同一产物 + 同一参数两次运行结果逐位一致（可复现）。

平台文档依据（项目铁律「平台代码前置查询纪律」：每个模拟 API 附
docs/qmt/inner-api/ 行级依据，无标注视为未查）：
- ContextInfo.barpos .......................... 03-变量约定.md:262/:275/:278（int，从 0 起）
- get_bar_timetag 用法 ......................... 03-变量约定.md:149、07-行情函数.md:2064
- opType 23=买入 / 24=卖出 .................... 05-枚举常量.md:43-44
- orderType 1101（单股单账号股/手方式）........ 05-枚举常量.md:139
- prType=5 最新价 ............................. 05-枚举常量.md:196（另 :768 PRTP_LATEST）
- init 完成前 get_trading_dates 不可用 ........ 06-系统函数.md:12（after_init 可用 :61）
- get_market_data_ex .......................... 07-行情函数.md:81/:96（签名），:118-124（参数），
                                                   :126-140（field 表），:161-165（返回 {code: DataFrame}）
- get_financial_data .......................... 07-行情函数.md:1839/:1852（签名），:1863（'表.字段'
                                                   fieldList），:1837（公告日/报表截止日=毫秒时间戳），
                                                   :1867/:1875/:1877（report_type 语义），:1885-1890（返回形态）
- get_instrument_detail ....................... 07-行情函数.md:2446/:2459（签名），:2479-2511（字段表），
                                                   :2515（ExpireDate 特殊值）
- get_stock_list_in_sector .................... 07-行情函数.md:3288/:3297（签名），:3313（返回
                                                   'stockcode.market' list）
- get_trading_dates ........................... 07-行情函数.md:3335-3339（仅 after_init/handlebar 可用），
                                                   :3341/:3350（签名），:3361-3365（参数语义），:3369（日线
                                                   返回 ['YYYYMMDD',...] 字符串）
- passorder .................................. 08-交易函数.md:8/:25-30（11 参签名），:81（8 参形态示例），
                                                   :80-90（参数表），:85（prType 11/49 时 price 有效），
                                                   :94（无返回）
- get_trade_detail_data ....................... 08-交易函数.md:583/:585-587（签名），:594-595（类型枚举），
                                                   :598（返回 list），:648-649（持仓 m_nVolume/
                                                   m_nCanUseVolume/m_dOpenPrice），:652-656（ACCOUNT 字段），
                                                   :683-684（示例：持仓 2500 可用 0 —— 当日买入不可用）

已知如实登记项（不确定点，均以【M5 实测项】标注在对应代码处）：
1. get_financial_data 返回形态：任务规格字面要求 {code: {field: value}} 纯 dict；但产物
   `_qs_fin_to_rows` 的 dict 探测分支对标量值会把 {code:{field:标量}} 误路由为
   {field:{code:标量}}（其 DataFrame 分支 to_dict('index') 才是正确路径）。桩返回 **dict 子类
   QmtStubFinData**（本身即 {code: {field: value}}，附 to_dict('index') 协议，对齐
   07:1889 DataFrame(index=代码, columns=字段) 形态的 to_dict 投影）——两 READ 兼容，语义未降级。
2. m_dCash（ACCOUNT）与 bar_date/pre_bar_date（ContextInfo）为产物 wrapper 消费字段/属性，
   产物侧标注【M5 实测项】——桩为驱动冒烟提供该名字，真实字段名/属性面以 M5 实测为准。
3. get_bar_timetag 时间单位：按毫秒 int 返回（03:149 用法 + 07:2064 `fromtimestamp(int(t)/1000)`
   佐证）→【M5 实测项】。
4. 财务多记录面板形态（07:1890 Panel）不合成：每码仅返回 as-of 当前 bar 已公告的最新一条记录
   （PIT：gate 戳 ≤ 当前 bar 时点）。
5. 分钟/tick 周期、期货字段（settle/openInterest 等）不合成（M3 Out of scope：分钟域）。
"""

from __future__ import annotations

import bisect
import contextlib
import math
import sys
import traceback
import types
import zlib
from datetime import datetime, timedelta

import pandas as pd

__all__ = [
    'QmtStubDataStore',
    'QmtStubFinData',
    'QmtStubAccount',
    'QmtStubContext',
    'QmtStubRunner',
    'passorder',
    'get_trade_detail_data',
]

# 桩内登记行前缀：刻意**不用** QS_QMT_ 前缀（该前缀属产物审计面，S7 判据按其计数，
# 桩自身登记行混入会污染审计面归属）。
_STUB_NOTE_PREFIX = 'QMT_STUB_NOTE'
# 产物审计行前缀（S7 判据面）：QS_QMT_DENY_REMOVED / QS_QMT_SEMANTIC_DIFF /
# QS_QMT_LIFECYCLE_MOVED / QS_QMT_UNDETERMINED / QS_QMT_WRAPPER_FETCH_FAIL /
# QS_QMT_WRAPPER_SKIP（六产物 print 面实测清点）。
_AUDIT_PREFIX = 'QS_QMT_'


def _stub_note(msg):
    """桩自身登记行（独立前缀，不混入产物 QS_QMT_* 审计面）。"""
    print('%s: %s' % (_STUB_NOTE_PREFIX, msg))


def _crc(code):
    """确定性代码哈希（zlib.crc32 跨运行/跨平台稳定；random/hash() 均不可用）。"""
    return zlib.crc32(str(code).encode('utf-8')) & 0xFFFFFFFF


_EPOCH_NAIVE = datetime(1970, 1, 1)


def _ms_of(dt):
    """朴素 datetime → 毫秒 int。

    用 naive 差值而非 datetime.timestamp()：后者按机器本地时区解释，跨时区机器
    数值漂移（破坏「两次运行逐位一致」的跨机确定性）；桩内所有 ms 戳自洽使用
    同一口径（真实平台纪元口径 →【M5 实测项】；产物侧 fromtimestamp 回转在本机
    中国时区下日期不跨日，见 _ms_of 与 07:1837 配注）。
    """
    return int((dt - _EPOCH_NAIVE).total_seconds() * 1000)


def _norm_date_int(value):
    """'YYYYMMDD' / 'YYYYMMDDHHMMSS' / int / None / '' → int YYYYMMDD 或 None。

    依据 07-行情函数.md:119-120（start_time/end_time 格式 %Y%m%d 或 %Y%m%d%H%M%S，
    空串=不限）。
    """
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        s = str(int(value))
    else:
        s = str(value).replace('-', '').replace('/', '').replace(' ', '')
    s = s.strip()
    if not s:
        return None
    return int(s[:8])


# =============================================================================
# ---- 合成数据存储（行情/财务/合约详情，纯公式确定性生成）-----------------------
# =============================================================================

class QmtStubDataStore(object):
    """确定性合成数据存储：交易日历 + 20 只合成代码的日线与财务记录。

    交易日历：固定起始日（默认 2020-01-02）起仅保留工作日（周一至周五），无节假日
    （合成日历，M5 真实日历差异如实登记）。总长度 = warmup_bars + n_bars：
    前 warmup_bars 根为「预热历史」（供长窗口回看取数，如 fall_reversal 的 756 日），
    后 n_bars 根为驱动窗口（init 一次 + handlebar N 次，plan §⑤ 驱动方式）。
    """

    #: 日线全列。'time/open/high/low/close/volume/amount/preClose/suspendFlag' 为
    #: 07-行情函数.md:126-140 field 表字段（preClose 大小写按 :139 原文）；
    #: 'preclose/high_limit/low_limit' 为桩便利列（任务 §⑤ 要求列含；非 07 文档
    #: 字段——QMT 侧涨跌停经 get_instrument_detail(:2492/:2493) 取，此处冗余提供
    #: 仅供块2 形状断言参考）。
    FULL_COLUMNS = ['time', 'open', 'high', 'low', 'close', 'volume', 'amount',
                    'preClose', 'suspendFlag', 'preclose', 'high_limit',
                    'low_limit']

    DEFAULT_CALENDAR_START = datetime(2020, 1, 2)

    def __init__(self, n_bars=20, warmup_bars=900, calendar_start=None):
        n_bars = int(n_bars)
        warmup_bars = int(warmup_bars)
        if n_bars < 1:
            raise ValueError('n_bars 必须 >= 1（驱动 bar 数）')
        if warmup_bars < 1:
            raise ValueError('warmup_bars 必须 >= 1（预热历史需覆盖策略回看窗口）')
        self.n_bars = n_bars
        self.warmup_bars = warmup_bars
        start = calendar_start or self.DEFAULT_CALENDAR_START
        total = warmup_bars + n_bars
        dates = []
        d = datetime(start.year, start.month, start.day, 15, 0, 0)
        while len(dates) < total:
            if d.weekday() < 5:  # 周一~周五（合成日历，无节假日）
                dates.append(d)
            d = d + timedelta(days=1)
        self.dates = dates
        self.date_ints = [int(x.strftime('%Y%m%d')) for x in dates]
        self.date_strs = [x.strftime('%Y%m%d') for x in dates]
        self.ms_tags = [_ms_of(x) for x in dates]
        # 合成宇宙：20 只（10 沪 + 10 深），代码形态 'stockcode.market'（07:3313）。
        # '600003.SH' 名称含 'ST'（可控行使 ST 过滤路径，07:2481 InstrumentName 判据）。
        self.universe = (['6000%02d.SH' % i for i in range(1, 11)]
                         + ['0000%02d.SZ' % i for i in range(1, 11)])
        self._names = {}
        for i, code in enumerate(self.universe):
            if code == '600003.SH':
                self._names[code] = 'ST合成03'
            else:
                self._names[code] = '合成股票%02d' % (i + 1,)
        self._rows_cache = {}
        self._fin_cache = {}

    # ---- 基础访问 -----------------------------------------------------------

    @property
    def total_bars(self):
        return len(self.dates)

    def name(self, code):
        return self._names.get(str(code))

    def close(self, code, barpos):
        """指定 bar 的收盘价（撮合/市值标记用；越界截断）。"""
        rows = self.rows(code)
        if rows is None:
            return 0.0
        i = max(0, min(int(barpos), self.total_bars - 1))
        return rows['close'][i]

    def rows(self, code):
        """全量日线列存（dict col -> list）；未知代码 → None。

        价格公式（纯确定性）：close = base × (1 + 0.20·sin(i/53+φ) + 0.05·sin(i/9+2φ))，
        base/φ 由 crc32(code) 派生；open 锚定昨收，high/low 由当日 open/close 外扩 1%，
        涨跌停 = 昨收 ±10%（合成规则，非交易所精确口径 →【M5 实测项】）。
        """
        code = str(code)
        if code not in self._names:
            return None
        if code in self._rows_cache:
            return self._rows_cache[code]
        seed = _crc(code)
        base = 5.0 + (seed % 460) / 23.0          # ≈ 5.00 ~ 24.99 元
        phase = (seed % 628) / 100.0              # 0 ~ 6.27
        n = self.total_bars
        cols = dict((c, []) for c in self.FULL_COLUMNS)
        prev_close = base
        for i in range(n):
            wave = (1.0 + 0.20 * math.sin(i / 53.0 + phase)
                    + 0.05 * math.sin(i / 9.0 + 2.0 * phase))
            close = round(base * wave, 2)
            open_ = round(prev_close * (1.0 + 0.004 * math.sin(i / 3.0 + phase)), 2)
            high = round(max(open_, close) * 1.01, 2)
            low = round(min(open_, close) * 0.99, 2)
            volume = int(1000000.0 + 500000.0 * math.sin(i / 17.0 + phase))
            di = self.date_ints[i]
            cols['time'].append(di)
            cols['open'].append(open_)
            cols['high'].append(high)
            cols['low'].append(low)
            cols['close'].append(close)
            cols['volume'].append(volume)
            cols['amount'].append(round(close * volume, 2))
            cols['preClose'].append(prev_close)                       # 07:139
            cols['suspendFlag'].append(0)                             # 07:140（0 不停牌）
            cols['preclose'].append(prev_close)                       # 桩便利别名列
            cols['high_limit'].append(round(prev_close * 1.10, 2))    # 桩便利列（非文档字段）
            cols['low_limit'].append(round(prev_close * 0.90, 2))     # 桩便利列（非文档字段）
            prev_close = close
        self._rows_cache[code] = cols
        return cols

    def asof_frame(self, code, barpos, fields=None, start_time=None,
                   end_time=None, count=-1):
        """as-of 当前 bar 的日线切片 → pd.DataFrame（index=int YYYYMMDD）。

        语义（07:161-165：返回 {code: DataFrame}，index 为 time_list）：取
        calendar[0..barpos]（**含当前 bar**——产物 wrapper 自行剔最末根实现 E1
        D-1 口径，桩保持平台原始行为）；start_time/end_time 按 07:119-120 过滤；
        count>0 取尾部 count 根（07:121 数据个数）。未知代码 → 空 DataFrame。
        """
        if isinstance(fields, str):
            fields = [fields]
        avail_src = self.FULL_COLUMNS if not fields else list(fields)
        avail = [c for c in avail_src if c in self.FULL_COLUMNS]
        src = self.rows(code)
        if src is None:
            return pd.DataFrame(columns=avail)
        hi = max(0, min(int(barpos), self.total_bars - 1))
        lo = 0
        s = _norm_date_int(start_time)
        e = _norm_date_int(end_time)
        if s is not None:
            lo = bisect.bisect_left(self.date_ints, s, 0, hi + 1)
        if e is not None:
            hi = bisect.bisect_right(self.date_ints, e, 0, hi + 1) - 1
        if hi < lo:
            return pd.DataFrame(columns=avail)
        cnt = int(count) if count is not None else -1
        if cnt > 0 and hi - lo + 1 > cnt:
            lo = hi + 1 - cnt
        data = dict((c, src[c][lo:hi + 1]) for c in self.FULL_COLUMNS)
        df = pd.DataFrame(data, index=self.date_ints[lo:hi + 1])
        if fields:
            missing = [f for f in fields if f not in self.FULL_COLUMNS]
            if missing:
                _stub_note('get_market_data_ex 请求字段 %r 不在桩合成列集内'
                           '（仅返回可用列）' % (missing,))
            return df[avail]
        return df

    # ---- 合约详情 -----------------------------------------------------------

    def instrument_detail(self, code, barpos):
        """合约详情 dict（键面按 07:2479-2511；动态项 as-of 当前 bar）。

        停牌/退市可控面：InstrumentStatus 恒 0（:2502 <=0 正常交易）、IsTrading 恒
        True（:2503）、ExpireDate 恒 0（:2515 特殊值：0=暂无退市日）——六产物冒烟
        不需要停牌/退市正例（属 M5 实测域），仅 ST 面可控（600003.SH 名称含 ST）。
        iscomplete 参数接受但不区分（桩恒返回同键面）。
        """
        code = str(code)
        src = self.rows(code)
        if src is None:
            return {}
        seed = _crc(code)
        i = max(0, min(int(barpos), self.total_bars - 1))
        pre = src['preClose'][i]
        inst, _, exch = code.rpartition('.')
        return {
            'ExchangeID': exch,                    # :2479
            'InstrumentID': inst,                  # :2480
            'InstrumentName': self._names[code],   # :2481（ST 可控：600003.SH）
            'ProductType': -1,                     # :2484（默认 -1）
            'OpenDate': '20100104',                # :2488（str；早于日历起点即可）
            'ExpireDate': 0,                       # :2489 + :2515（0=暂无退市/到期日）
            'PreClose': pre,                       # :2490（as-of 当前 bar 昨收）
            'SettlementPrice': 0.0,                # :2491
            'UpStopPrice': round(pre * 1.10, 2),   # :2492 当日涨停价
            'DownStopPrice': round(pre * 0.90, 2),  # :2493 当日跌停价
            'FloatVolume': float(200000000 + (seed % 97) * 1000000),   # :2494 流通股本
            'TotalVolume': float(300000000 + (seed % 89) * 1000000),   # :2495 总股本
            'PriceTick': 0.01,                     # :2498
            'VolumeMultiple': 1,                   # :2499（非期货默认 1）
            'InstrumentStatus': 0,                 # :2502（<=0 正常交易）
            'IsTrading': True,                     # :2503
            'IsRecent': False,                     # :2504
        }

    # ---- 财务记录 -----------------------------------------------------------

    #: 财务合成字段白名单（裸字段名——返回键为裸名，07:1921 Minor_axis 裸字段佐证；
    #: 请求侧 '表.字段' 前缀形态见 07:1863）。白名单外字段 → None（缺料如实，不发明）。
    FIN_SYNTH_FIELDS = ('s_fa_eps_basic', 'inc_revenue_rate',
                        'circulating_capital', 'total_capital', 'fix_assets')

    def financial_records(self, code):
        """每码季度财务记录列表（按报告期升序，确定性公式）。

        m_timetag=报告截止日、m_anntime=公告日，均为毫秒时间戳（07:1837：公告日期
        与报表截止日为时间戳毫秒格式）；公告滞后固定 +25 天（合成规则）。
        """
        code = str(code)
        if code in self._fin_cache:
            return self._fin_cache[code]
        seed = _crc(code)
        recs = []
        qidx = 0
        for year in range(2019, 2024):  # 固定年份域（覆盖任何日历窗口，确定性）
            for (m, dd) in ((3, 31), (6, 30), (9, 30), (12, 31)):
                rpt = datetime(year, m, dd, 15, 0, 0)
                ann = rpt + timedelta(days=25)
                recs.append({
                    'm_timetag': _ms_of(rpt),
                    'm_anntime': _ms_of(ann),
                    '_rpt_d': int(rpt.strftime('%Y%m%d')),
                    '_ann_d': int(ann.strftime('%Y%m%d')),
                    's_fa_eps_basic': round(0.20 + 0.03 * qidx
                                            + (seed % 13) / 100.0, 4),
                    'inc_revenue_rate': round(5.0 + 0.8 * qidx + (seed % 7), 2),
                    'circulating_capital': float(200000000
                                                 + (seed % 97) * 1000000),
                    'total_capital': float(300000000 + (seed % 89) * 1000000),
                    'fix_assets': float(1000000000.0 + (seed % 211) * 1000000.0
                                        + qidx * 5000000.0),
                })
                qidx += 1
        self._fin_cache[code] = recs
        return recs


class QmtStubFinData(dict):
    """get_financial_data 返回体：{code: {field: value}} 的 dict 子类。

    附 to_dict(orient) 协议（对齐 07:1889 DataFrame(index=代码, columns=字段) 形态
    的 to_dict 投影），供产物 `_qs_fin_to_rows` 的 DataFrame 兼容分支
    （hasattr(raw, 'to_dict') → raw.to_dict('index')）正确解析——模块 docstring
    已知登记项 1：纯 dict 形态会被产物 dict 探测分支对标量值误路由。
    """

    def to_dict(self, orient='index'):
        if orient in ('index', None):
            return dict((k, dict(v)) for k, v in self.items())
        if orient == 'dict':
            out = {}
            for code, fmap in self.items():
                for f, v in fmap.items():
                    out.setdefault(f, {})[code] = v
            return out
        raise ValueError('QmtStubFinData.to_dict 不支持 orient=%r' % (orient,))


# =============================================================================
# ---- 账户 / 订单记录面 --------------------------------------------------------
# =============================================================================

class QmtStubAccount(object):
    """账户状态面：订单记录 + 持仓 + 现金 + 总值（任务 §⑤ 记录面规格）。

    - orders：每笔 {'barpos','opType','orderType','orderCode','prType','price',
      'volume','filled_volume','accountID'}（任务六键齐备 + 三补充键）；
    - positions：{code: {'amount','enable_amount','avg_cost'}}；
    - 买入增持仓减现金、卖出反向（passorder 语义，任务要点）；现金不足按 100 股整手
      向下截断（A股整手常识，桩侧建模 →【M5 实测项】）；卖出截断至可用量；
    - T+1：买入当根 bar 不入 enable_amount，下一根 bar 前 roll_t1() 放开
      （08:683-684 示例实证：持仓 2500 可用 0；A股 T+1 交易规则 →【M5 实测项】）；
    - 无费用/滑点/撮合延迟建模（方案 B 桩冒烟，不做数值对照）。
    """

    def __init__(self, init_cash=1000000.0, store=None):
        self.init_cash = float(init_cash)
        self.cash = float(init_cash)
        self.orders = []
        self.positions = {}
        self.total_value = float(init_cash)
        self._store = store

    # ---- 内部成交处理 -------------------------------------------------------

    def _buy(self, code, volume, price, barpos):
        filled = int(volume)
        if filled <= 0 or price <= 0:
            return 0
        if filled * price > self.cash:
            lots = int(self.cash // price // 100) * 100
            if lots <= 0:
                return 0
            _stub_note('passorder 买入 %s 现金不足，整手截断 %d -> %d 股'
                       % (code, filled, lots))
            filled = lots
        cost = filled * price
        self.cash = round(self.cash - cost, 2)
        pos = self.positions.setdefault(
            code, {'amount': 0, 'enable_amount': 0, 'avg_cost': 0.0})
        new_amt = pos['amount'] + filled
        pos['avg_cost'] = round((pos['amount'] * pos['avg_cost'] + cost)
                                / new_amt, 6)
        pos['amount'] = new_amt  # enable_amount 不动（T+1，roll_t1 放开）
        return filled

    def _sell(self, code, volume, price):
        filled = int(volume)
        if filled <= 0 or price <= 0:
            return 0
        pos = self.positions.get(code)
        enable = int(pos['enable_amount']) if pos else 0
        if filled > enable:
            _stub_note('passorder 卖出 %s 超可用 %d，截断 %d -> %d 股'
                       % (code, filled, enable, enable))
            filled = enable
        if filled <= 0:
            return 0
        self.cash = round(self.cash + filled * price, 2)
        pos['amount'] -= filled
        pos['enable_amount'] -= filled
        if pos['amount'] <= 0:
            del self.positions[code]
        return filled

    def submit(self, op_type, order_type, account_id, order_code, pr_type,
               price, volume, barpos):
        """passorder 落账入口：记录订单（opType 原值 23/24）+ 维护账户状态。

        成交价：prType ∈ {11, 49} 时用传入 price（08:85），否则用桩内当前 bar
        收盘价（prType=5 最新价，05:196；传入 price 为 0/-1 占位时同样回退）。
        非 23/24 的 opType 只登记不成交（六产物 wrapper 只产 23/24——
        05:43-44）。
        """
        order_code = str(order_code)
        store = self._store
        px = None
        try:
            if pr_type in (11, 49) and price not in (None, 0, -1):
                px = float(price)
            else:
                px = float(store.close(order_code, barpos))
        except (TypeError, ValueError):
            px = 0.0
        rec = {
            'barpos': int(barpos),
            'opType': int(op_type),
            'orderType': order_type,
            'orderCode': order_code,
            'prType': pr_type,
            'price': px,
            'volume': int(volume),
            'filled_volume': 0,
            'accountID': account_id,
        }
        if int(op_type) == 23:      # 买入（05:43）
            rec['filled_volume'] = self._buy(order_code, int(volume), px, barpos)
        elif int(op_type) == 24:    # 卖出（05:44）
            rec['filled_volume'] = self._sell(order_code, int(volume), px)
        else:
            _stub_note('passorder opType=%r 非 23/24，仅登记不成交'
                       '（05-枚举常量.md:43-44 股票买卖面）' % (op_type,))
        self.orders.append(rec)
        return None  # 08:94 无返回

    # ---- bar 推进 / 估值 -----------------------------------------------------

    def roll_t1(self):
        """新 bar 前放开 T+1 可用量：enable_amount ← amount（见类 docstring 依据）。"""
        for pos in self.positions.values():
            pos['enable_amount'] = pos['amount']

    def positions_market_value(self, barpos):
        total = 0.0
        for code, pos in self.positions.items():
            total += pos['amount'] * self._store.close(code, barpos)
        return total

    def mark_to_market(self, barpos):
        mv = self.positions_market_value(barpos)
        self.total_value = round(self.cash + mv, 2)
        return self.total_value

    def snapshot_positions(self):
        return dict((k, dict(v)) for k, v in self.positions.items())


# =============================================================================
# ---- 查询明细对象（get_trade_detail_data 返回元素）----------------------------
# =============================================================================

class QmtStubPositionDetail(object):
    """持仓明细对象（08:583/:645 POSITION；字段 :648-649 + 示例 :683-684）。

    m_nVolume 总持仓量 / m_nCanUseVolume 可用量 / m_dOpenPrice 成本价（开仓价）；
    m_strInstrumentID 取完整 'stockcode.market' 形态、m_strExchangeID 取后缀——
    产物 wrapper 含 `'.' not in code` 后缀拼装守卫，完整形态直接通过。
    """

    def __init__(self, code, amount, enable_amount, avg_cost, name,
                 instrument_value, position_cost):
        self.m_strInstrumentID = code           # 08:648
        self.m_strExchangeID = code.rpartition('.')[2]
        self.m_strInstrumentName = name
        self.m_nVolume = int(amount)            # 08:648 持仓量
        self.m_nCanUseVolume = int(enable_amount)  # 08:648 可用数量
        self.m_dOpenPrice = float(avg_cost)     # 08:649 成本价
        self.m_dInstrumentValue = float(instrument_value)   # 08:649 市值
        self.m_dPositionCost = float(position_cost)         # 08:649 持仓成本


class QmtStubAccountDetail(object):
    """账号明细对象（08:583/:652 ACCOUNT）。

    m_dCash 为产物 wrapper 消费字段（产物侧标注【M5 实测项】——08:652-656 示例打印
    m_dBalance/m_dAvailable 等而未列 m_dCash；08:598 明示完整属性面以 dir() 为准）；
    桩提供 m_dCash 以驱动冒烟，真实字段名以 M5 实测为准。
    """

    def __init__(self, cash, balance, instrument_value, available,
                 position_profit):
        self.m_dCash = float(cash)                    # 【M5 实测项】产物消费名
        self.m_dBalance = float(balance)              # 08:655 总资产
        self.m_dAssureAsset = float(balance)          # 08:655 净资产（无负债=总资产）
        self.m_dInstrumentValue = float(instrument_value)  # 08:655 总市值
        self.m_dTotalDebit = 0.0                      # 08:655 总负债（合成无负债）
        self.m_dAvailable = float(available)          # 08:655 可用金额
        self.m_dPositionProfit = float(position_profit)   # 08:656 盈亏


class QmtStubOrderDetail(object):
    """委托明细对象（08:608 ORDER；字段面按示例 :635-636）。"""

    def __init__(self, rec, name):
        self.m_strInstrumentID = rec['orderCode']
        self.m_strExchangeID = rec['orderCode'].rpartition('.')[2]
        self.m_strInstrumentName = name
        # 08:679 示例委托买向 48；卖向取 49（未入示例摘录，桩侧取值 →【M5 实测项】）
        self.m_nOffsetFlag = 48 if rec['opType'] == 23 else 49
        self.m_nVolumeTotalOriginal = int(rec['volume'])   # 委托数量
        self.m_nVolumeTraded = int(rec.get('filled_volume', 0))  # 成交数量
        self.m_dTradedPrice = float(rec['price'])          # 成交均价
        self.m_dTradeAmount = round(self.m_nVolumeTraded
                                    * float(rec['price']), 2)   # 成交金额


class QmtStubDealDetail(object):
    """成交明细对象（08:610 DEAL；字段面按示例 :642-643）。"""

    def __init__(self, rec):
        self.m_strInstrumentID = rec['orderCode']
        self.m_strExchangeID = rec['orderCode'].rpartition('.')[2]
        self.m_dPrice = float(rec['price'])     # 08:643 成交价格
        self.m_nVolume = int(rec.get('filled_volume', 0))  # 08:643 成交数量
        self.m_dTradeAmount = round(self.m_nVolume
                                    * float(rec['price']), 2)


# =============================================================================
# ---- ContextInfo 最小桩 -------------------------------------------------------
# =============================================================================

class QmtStubContext(object):
    """QMT ContextInfo 最小桩（约定形参名 C；六产物实测消费面 = 五方法 + 属性三件）。

    属性面：
    - barpos：int，当前 K 线索引号从 0 起（03-变量约定.md:262/:275/:278；驱动器递增）；
    - stock_list：策略 init 可覆写的静态池（默认 20 只合成代码，07:3313 代码形态）；
    - accountID：占位 'testS'（六产物经 getattr(C,'accountID','') 消费；
      真实属性名/注入方式未入行级索引 →【M5 实测项】）；
    - bar_date / pre_bar_date：datetime——产物 context 视图 current_dt/
      previous_date 的取数属性（产物侧标注【M5 实测项】），桩提供以驱动冒烟；
      参照 01-快速开始.md:139 bar_date = timetag_to_datetime(get_bar_timetag(...))。
    生命周期门控：_phase 'init' → get_trading_dates 忠实抛错（06-系统函数.md:12，
    不得为让策略跑通而放宽）；init 完成后置 'run'。
    【如实登记·与真实平台的已知分歧】：06-系统函数.md:8 明示真实平台
    「ContextInfo 会随着 bar 的切换而重置到上一根 bar 的结束状态（不建议添加
    自定义属性）」；而 M2b 产物将运行态直接挂在 g=C 上（如 g.target_list），桩
    为驱动产物**跨 bar 持久化同一 ContextInfo 对象**——重置语义差异 →【M5 实测项】。
    """

    def __init__(self, store, account, barpos=0, account_id='testS'):
        self._store = store
        self._account = account
        self._phase = 'init'
        self.barpos = int(barpos)
        self.accountID = account_id
        self.stock_list = list(store.universe)
        self.bar_date = store.dates[self.barpos]
        self.pre_bar_date = store.dates[max(self.barpos - 1, 0)]

    # ---- 行情（07-行情函数.md:81 get_market_data_ex）------------------------

    def get_market_data_ex(self, field_list=None, stock_list=None,
                           period='1d', start_time=None, end_time=None,
                           count=-1, dividend_type=None, **kwargs):
        """行情取数 → {code: DataFrame}（07:81/:96/:161-165；本块核心消费面）。

        - period 仅支持 '1d'（分钟/tick 属 M3 Out of scope）→ 抛 ValueError
          （产物 wrapper 捕获后打 QS_QMT_WRAPPER_FETCH_FAIL 登记行，不中断冒烟）；
        - count>0 取尾部 count 根 / start_time+end_time 区间（07:119-121）；
        - 返回**含当前 bar**（平台原始行为；E1 D-1 剔除由产物 wrapper 负责）；
        - dividend_type 接受但忽略（合成数据不复权；07:122 枚举）；
        - fill_data/subscribe 等其余形参经 **kwargs 吞掉（桩无订阅语义）；
        - init 阶段可用（07:85：init 中仅能取到本地数据——桩数据即本地合成数据）。
        """
        if period != '1d':
            _stub_note('get_market_data_ex period=%r 桩仅合成日线（M3 范围外）'
                       % (period,))
            raise ValueError('QMT stub only synthesizes 1d bars, got period=%r'
                             % (period,))
        if stock_list is None:
            stock_list = []
        if isinstance(stock_list, str):
            stock_list = [stock_list]
        out = {}
        for code in list(stock_list):
            out[str(code)] = self._store.asof_frame(
                str(code), self.barpos, fields=field_list,
                start_time=start_time, end_time=end_time, count=count)
        return out

    # ---- 财务（07-行情函数.md:1839 get_financial_data）----------------------

    def get_financial_data(self, field_list, stock_list, start_date, end_date,
                           report_type='announce_time'):
        """财务取数 → QmtStubFinData（{code: {裸字段: 值}} 的 dict 子类）。

        - fieldList 元素为 '表.字段' 前缀形态（07:1863）；返回键为**裸字段名**
          （07:1921 Minor_axis 裸字段佐证；产物 wrapper 按裸名取值）；
        - m_anntime（公告日）/ m_timetag（报告截止日）为毫秒时间戳（07:1837）；
        - report_type='announce_time' 按公告日门控（07:1867/:1877 不会取到未来
          数据）、'report_time' 按报告期门控（07:1875 可能取到未来数据——桩同样
          as-of 当前 bar 门控，未来数据面不合成 →【M5 实测项】）；
        - 每码返回 as-of 当前 bar 已公告的**最新一条**记录（多记录面板形态
          07:1890 不合成）；白名单外字段 → None（缺料如实）；
        - start_date/end_date 窗口按门控戳日期过滤（空串不限，07:119-120 同构）。
        """
        fields = [str(f) for f in (field_list or [])]
        bare = [f.split('.', 1)[1] if '.' in f else f for f in fields]
        if str(report_type) == 'announce_time':
            gate_ms_field, gate_d_field = 'm_anntime', '_ann_d'
        else:
            gate_ms_field, gate_d_field = 'm_timetag', '_rpt_d'
        asof_ms = self._store.ms_tags[min(self.barpos,
                                          self._store.total_bars - 1)]
        s = _norm_date_int(start_date)
        e = _norm_date_int(end_date)
        if stock_list is None:
            stock_list = []
        if isinstance(stock_list, str):
            stock_list = [stock_list]
        result = QmtStubFinData()
        for code in list(stock_list):
            code = str(code)
            picked = None
            for rec in self._store.financial_records(code):
                if rec[gate_ms_field] > asof_ms:
                    continue
                if s is not None and rec[gate_d_field] < s:
                    continue
                if e is not None and rec[gate_d_field] > e:
                    continue
                picked = rec  # 升序遍历，末次命中即最新
            result[code] = dict((f, (picked.get(f) if picked else None))
                                for f in bare)
        return result

    # ---- 板块成份（07-行情函数.md:3288 get_stock_list_in_sector）------------

    def get_stock_list_in_sector(self, sector_name, realtime=None):
        """板块成份股 → list['stockcode.market']（07:3288/:3297/:3313）。

        桩对任意板块名返回同一合成宇宙（20 只；真实板块成员差异 →【M5 实测项】）；
        realtime 参数接受但不区分（合成数据无实时/本地之别）。
        """
        return list(self._store.universe)

    # ---- 交易日历（07-行情函数.md:3341 get_trading_dates）--------------------

    def get_trading_dates(self, stockcode='', start_date='', end_date='',
                          count=-1, period='1d'):
        """交易日历 → list['YYYYMMDD'] 字符串（07:3369 日线形态）。

        - **init 阶段忠实抛错**（06-系统函数.md:12「init 函数执行完成前部分接口
          无法使用，如交易日获取函数 get_trading_dates」；07:3339 该函数只能在
          after_init/handlebar 运行）——产物 wrapper 捕获后打印
          QS_QMT_SEMANTIC_DIFF 登记行并回退 []，桩不得为跑通而放宽；
        - count>0：含 end_date 往前 count 根、最早不早于 start_date（07:3364）；
          count<=0（含产物 wrapper 的 -1 区间语义）：返回全 as-of 窗口；
        - end_date 空 → 默认当前 bar 时间（07:3363）；as-of 上界=当前 bar；
        - period 仅 '1d'（其余周期 M3 范围外，抛 ValueError）。
        """
        if self._phase == 'init':
            _stub_note('get_trading_dates 于 init 阶段调用 → 忠实抛错'
                       '（06-系统函数.md:12 / 07-行情函数.md:3339）')
            raise RuntimeError('QMT stub: get_trading_dates 不可用于 init 阶段'
                               '（06-系统函数.md:12；after_init/handlebar 可用）')
        if period != '1d':
            _stub_note('get_trading_dates period=%r 桩仅合成日线' % (period,))
            raise ValueError('QMT stub only synthesizes 1d trading dates,'
                             ' got period=%r' % (period,))
        hi = max(0, min(self.barpos, self._store.total_bars - 1))
        lo = 0
        s = _norm_date_int(start_date)
        e = _norm_date_int(end_date)
        if s is not None:
            lo = bisect.bisect_left(self._store.date_ints, s, 0, hi + 1)
        if e is not None:
            hi = bisect.bisect_right(self._store.date_ints, e, 0, hi + 1) - 1
        if hi < lo:
            return []
        cnt = int(count) if count is not None else -1
        if cnt > 0 and hi - lo + 1 > cnt:
            lo = hi + 1 - cnt
        return list(self._store.date_strs[lo:hi + 1])

    # ---- 合约详情（07-行情函数.md:2446 get_instrument_detail）----------------

    def get_instrument_detail(self, stockcode, iscomplete=False):
        """合约详情 → dict（07:2446/:2459 签名；键面 :2479-2511，见存储层注释）。

        未知代码 → {}（真实平台无效代码行为 →【M5 实测项】；产物 _qs_status_flags
        对空 dict 返回不可判定 → 保守保留 + QS_QMT_UNDETERMINED 审计行）。
        """
        return self._store.instrument_detail(str(stockcode), self.barpos)

    # ---- bar 时间戳（03-变量约定.md:149 用法）--------------------------------

    def get_bar_timetag(self, barpos=None):
        """bar 时间戳 → int 毫秒（03:149 timetag_to_datetime(get_bar_timetag(
        barpos)) 用法；07:2064 fromtimestamp(int(t)/1000) 佐证整除 1000——
        精确单位/纪元口径 →【M5 实测项】）。缺省取当前 barpos。
        """
        idx = self.barpos if barpos is None else int(barpos)
        idx = max(0, min(idx, self._store.total_bars - 1))
        return self._store.ms_tags[idx]


# =============================================================================
# ---- 平台全局函数（注入产物模块命名空间）---------------------------------------
# =============================================================================

# 当前活动会话（单线程测试域使用；passorder/get_trade_detail_data 为平台全局函数，
# 产物以模块级裸名引用，驱动时注入产物命名空间并经此转发到活动会话的账户/上下文）。
_ACTIVE_SESSION = None


def _set_active_session(session):
    global _ACTIVE_SESSION
    _ACTIVE_SESSION = session


def _clear_active_session():
    global _ACTIVE_SESSION
    _ACTIVE_SESSION = None


def passorder(opType, orderType, accountID, orderCode, prType, price, volume,
              C=None, *args, **kwargs):
    """综合下单（08-交易函数.md:8/:25-30 11 参签名；:81 8 参形态——首 8 参同位，
    strategyName/quickTrade/userOrderId 落 *args）。

    桩语义（任务要点）：**记录**订单（不撮合真实市场）+ 维护基本账户状态——
    买入(23)持仓增/现金减、卖出(24)反向（成交价取传入 price 或桩内最新价，
    prType 11/49 时传入价有效 08:85、prType=5 最新价 05:196）；opType 记录原值
    （05:43-44）。返回 None（08:94 无返回）。
    """
    if _ACTIVE_SESSION is None:
        raise RuntimeError('QMT stub: 无活动会话（须在 QmtStubRunner.run() 内'
                           '调用 passorder）')
    return _ACTIVE_SESSION.account.submit(
        opType, orderType, accountID, orderCode, prType, price, volume,
        _ACTIVE_SESSION.context.barpos)


def get_trade_detail_data(accountID, data_type, sub_type=None,
                          strategyName=None, *args, **kwargs):
    """账号/持仓/委托/成交查询 → list（08-交易函数.md:583/:585-587 三参签名；
    strategyName 选填 :596——桩不区分策略）。

    data_type 大小写不敏感归一（参数表 08:594 'STOCK' vs 示例 :632 'stock' 双形态
    并存，归一处理）；仅维护 'STOCK'（其余账号类型 → 空表 + 登记行）。
    sub_type：'POSITION'（:648-649 字段）/ 'ACCOUNT'（:652-656 + m_dCash）/ 
    'ORDER'（:635-636）/ 'DEAL'（:642-643）；持仓由桩内账户状态即时生成。
    """
    if _ACTIVE_SESSION is None:
        raise RuntimeError('QMT stub: 无活动会话（须在 QmtStubRunner.run() 内'
                           '调用 get_trade_detail_data）')
    session = _ACTIVE_SESSION
    account = session.account
    store = session.store
    barpos = session.context.barpos
    acc_type = str(data_type or '').upper()
    dtype = str(sub_type or '').upper()
    if acc_type != 'STOCK':
        _stub_note('get_trade_detail_data 账号类型 %r 桩仅维护 STOCK → 空表'
                   % (data_type,))
        return []
    if dtype == 'POSITION':
        out = []
        for code, pos in account.positions.items():
            mv = pos['amount'] * store.close(code, barpos)
            out.append(QmtStubPositionDetail(
                code, pos['amount'], pos['enable_amount'], pos['avg_cost'],
                store.name(code), mv, pos['amount'] * pos['avg_cost']))
        return out
    if dtype == 'ACCOUNT':
        mv = account.positions_market_value(barpos)
        cost = sum(p['amount'] * p['avg_cost']
                   for p in account.positions.values())
        balance = round(account.cash + mv, 2)
        return [QmtStubAccountDetail(account.cash, balance, mv, account.cash,
                                     round(mv - cost, 2))]
    if dtype == 'ORDER':
        return [QmtStubOrderDetail(r, store.name(r['orderCode']))
                for r in account.orders]
    if dtype == 'DEAL':
        return [QmtStubDealDetail(r) for r in account.orders
                if r.get('filled_volume', 0) > 0]
    _stub_note('get_trade_detail_data sub_type=%r 不在桩维护面'
               '（ACCOUNT/POSITION/ORDER/DEAL）→ 空表' % (sub_type,))
    return []


# =============================================================================
# ---- stdout 捕获（审计行收集）--------------------------------------------------
# =============================================================================

class _LineCollector(object):
    """stdout 行收集器：逐行缓存 + 可选回显；供 S7 提取 QS_QMT_* 审计行。"""

    def __init__(self, echo=False):
        self.lines = []
        self.echo = bool(echo)
        self._buf = ''

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        self._buf += s
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            self._record(line)

    def _record(self, line):
        self.lines.append(line)
        if self.echo:
            try:
                sys.__stdout__.write(line + '\n')
                sys.__stdout__.flush()
            except Exception:
                pass

    def flush(self):
        if self._buf:
            line = self._buf
            self._buf = ''
            self._record(line)

    def writable(self):
        return True


# =============================================================================
# ---- 驱动器（块2 直接使用）-----------------------------------------------------
# =============================================================================

class QmtStubRunner(object):
    """桩冒烟驱动器（plan §⑤ 驱动方式）：init(C) ×1 → handlebar(C) ×N。

    用法（块2 tests/test_qmt_stub_smoke.py）：
        runner = QmtStubRunner(r'output/qmt_export/<id>/qmt/<id>_qmt.py',
                               n_bars=20)
        result = runner.run()
        # result: {'bars_run','init_calls','orders','positions_series',
        #          'nav_series','audit_lines','error'}（+补充键 'stdout_lines'）
    - 产物以 GBK 物理编码读入（产物首行 #coding:gbk 契约），exec 入独立模块命名
      空间，并注入 passorder / get_trade_detail_data（产物以模块级裸名引用）；
    - 运行期异常**捕获不抛**：'error' 存 traceback 字符串（S1 断言面），
      已完成 bar 数计入 bars_run（S2 断言面）；
    - 每根 bar 前 roll_t1()（T+1 放开）→ 更新 barpos/bar_date → handlebar →
      mark_to_market（nav_series / positions_series 逐 bar 落档，S4/S5/S6 面）；
    - run() 可重复调用：每次重建账户与上下文（确定性复现）。
    """

    def __init__(self, product_path, *, n_bars=20, init_cash=1000000.0,
                 warmup_bars=900, calendar_start=None, echo_stdout=False):
        self.product_path = str(product_path)
        self.n_bars = int(n_bars)
        self.init_cash = float(init_cash)
        self.warmup_bars = int(warmup_bars)
        self.echo_stdout = bool(echo_stdout)
        self.store = QmtStubDataStore(n_bars=n_bars, warmup_bars=warmup_bars,
                                      calendar_start=calendar_start)
        self.account = None
        self.context = None
        self.print_lines = []
        self.audit_lines = []

    # ---- 产物加载 -----------------------------------------------------------

    def _load_product_module(self):
        """读入产物源（GBK）→ exec 入独立模块 → 注入平台全局函数。"""
        with open(self.product_path, 'rb') as fh:
            raw = fh.read()
        try:
            src = raw.decode('gbk')
        except UnicodeDecodeError:
            src = raw.decode('utf-8')
            _stub_note('产物非 GBK 物理编码，按 UTF-8 回退解码（与首行'
                       '#coding:gbk 契约不符，如实登记）')
        mod = types.ModuleType('qmt_product_under_test')
        mod.__file__ = self.product_path
        code = compile(src, self.product_path, 'exec')
        exec(code, mod.__dict__)  # noqa: S102 —— 测试域驱动产物（只读产物文件）
        mod.passorder = passorder
        mod.get_trade_detail_data = get_trade_detail_data
        return mod

    # ---- 驱动主循环 ---------------------------------------------------------

    def run(self):
        """驱动产物跑桩冒烟，返回形状判据所需结构化结果（异常捕获不抛）。"""
        result = {
            'bars_run': 0,
            'init_calls': 0,
            'orders': [],
            'positions_series': [],
            'nav_series': [],
            'audit_lines': [],
            'error': None,
            'stdout_lines': [],   # 补充键（规格七键之外的调试面，块2 归因用）
        }
        # 每次运行全新账户/上下文（可重复 run，确定性复现）
        self.account = QmtStubAccount(self.init_cash, store=self.store)
        self.context = QmtStubContext(self.store, self.account,
                                      barpos=self.warmup_bars)
        collector = _LineCollector(echo=self.echo_stdout)
        _set_active_session(self)
        try:
            with contextlib.redirect_stdout(collector):
                mod = self._load_product_module()
                init_fn = getattr(mod, 'init', None)
                if not callable(init_fn):
                    raise AttributeError('产物缺 init(C) 生命周期函数')
                handlebar_fn = getattr(mod, 'handlebar', None)
                if not callable(handlebar_fn):
                    raise AttributeError('产物缺 handlebar(C) 生命周期函数')
                # init ×1（barpos 停在首根驱动 bar；init 阶段 get_trading_dates
                # 忠实不可用——06:12）
                self.context._phase = 'init'
                init_fn(self.context)
                self.context._phase = 'run'
                result['init_calls'] = 1
                # handlebar ×N
                for i in range(self.n_bars):
                    b = self.warmup_bars + i
                    self.account.roll_t1()
                    self.context.barpos = b
                    self.context.bar_date = self.store.dates[b]
                    self.context.pre_bar_date = self.store.dates[max(b - 1, 0)]
                    handlebar_fn(self.context)
                    result['bars_run'] += 1
                    self.account.mark_to_market(b)
                    result['nav_series'].append(self.account.total_value)
                    result['positions_series'].append(
                        self.account.snapshot_positions())
        except Exception:
            result['error'] = traceback.format_exc()
        finally:
            _clear_active_session()
        self.print_lines = list(collector.lines)
        self.audit_lines = [ln for ln in collector.lines
                            if _AUDIT_PREFIX in ln]
        result['orders'] = list(self.account.orders) if self.account else []
        result['audit_lines'] = list(self.audit_lines)
        result['stdout_lines'] = list(self.print_lines)
        return result
