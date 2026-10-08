"""QMT（迅投 XtQuant）source 路径转换器 —— M2b 架构 D 独立模块（块 1/3）。

依据 ``docs/qmt/inner-api/`` 转写册行级引用实现（07-行情函数.md / 08-交易函数.md /
05-枚举常量.md / 06-系统函数.md），所有产物侧注释均标注行级依据（无标注视为未查）。

架构定位（M2b 架构 D）：
- 独立于 PTrade 侧 ``source_import.SourceConverter``：本模块**禁止** import
  ``quantstudio.strategy_compiler.source_import``（SourceImportResult 等一律不得引用），
  复用面严格限定为：
      from quantstudio.backtest.libs.security_code_rules import normalize_to_qmt
      from quantstudio.strategy_compiler.design_metadata import find_design_for_strategy
- PTrade 转换器零改动；QMT 侧扩展常量独立命名（_QS_QMT_*_EXT），
  严禁触碰 PTrade 侧 _QS_FUNDAMENTALS_EXT 等模板常量。

块 1 范围（已落盘）：
- [x] 模块骨架 + AST 工具族（自 source_import.py:3350-3452 逐字复制）
- [x] QmtSourceResult / QmtAction 结果承载
- [x] 视图 shim 完整实现：context / data / log / g（机制①别名注入式视图）
- [x] QmtSourceConverter 改写链：(e) 编码头 / (d) .SS→.SH 归一 / (f) DENY 剥除 /
      (h) 生命周期映射 / (i) run_daily 时间坍缩登记；注入序组装 + _apply_replacements 多趟改写
- [x] EXT 模板常量：log/context/data/position/order/ashares/trade_days/stock_info/fundamentals

块 2 范围（本文件当前状态，已落盘）：
- [x] 19 件 wrapper 函数体补全：视图扩展（log 补 warning/critical、portfolio 补
      market_value、data 补 high_limit/low_limit/preclose、position 补 enable_amount）+
      history 家族（E1 口径：count+1 取数剔当日 bar）+ 下单 + 财务批式 + 状态三判
      （ST/HALT/DELISTING）+ 池/日历/信息 + 门控实装（_qs_should_run_daily/_after）
- [x] 机制②同名遮蔽接线：14 个 PTrade 原同名产物内定义（_QS_QMT_WRAPPER_ZONE +
      EXT 区 get_position/get_stock_status/current_price），调用点零改动；
      未接线名（get_price）经转换器 warnings 如实登记「运行期不可用」
- [x] 机制③ keyword/位置双形态：get_history 双签名识别（本地 ptrade_api.py:1407-1423
      同款：首参 int → count-first）+ 各 wrapper 形参名与本地签名对齐
- [x] convert_source_qmt() 模块级编排入口（读源 utf-8-sig→gbk 回退、strategy_id
      缺省推导、design_metadata 解析、不写盘——写盘由编排层负责）
- [ ] 块 3（编排层，不在本文件范围）：产物写盘（GBK 物理编码）/ api_portability
      校验挂接 / quick_validate 联动

产物物理编码契约：converted_code 首行为 ``#coding:gbk``，落盘时写出方（块 3 / orchestrator）
必须以 GBK 物理编码写出（见 QS_QMT_PRODUCT_PHYSICAL_ENCODING），首行声明与物理编码一致。
"""

from __future__ import annotations

import ast
import io
import re
import textwrap
import tokenize
from dataclasses import asdict, dataclass
from pathlib import Path

from quantstudio.backtest.libs.security_code_rules import normalize_to_qmt
from quantstudio.strategy_compiler.design_metadata import find_design_for_strategy

__all__ = [
    "QS_QMT_PRODUCT_PHYSICAL_ENCODING",
    "QmtAction",
    "QmtSourceResult",
    "QmtSourceConverter",
    "convert_source_qmt",
]

QS_QMT_PRODUCT_PHYSICAL_ENCODING = "gbk"

_QS_QMT_MODULE_VERSION = "M2b-block2-rev1"


# ============================================================================
# AST 工具族 —— 自 source_import.py:3350-3452 逐字复制（不得改动其逻辑）。
# 其依赖常量 LOCAL_ONLY_PASSTHROUGH_BLOCK 的原文定义不在授权行号区间（3350-3459）内，
# 且 PTrade 侧 curated 清单不适用于 QMT 目标——QMT 侧以空集起底（引用级拦截零误杀）。
# 块2 任务书（19 件清单）未含 QMT curated 清单填充 → 维持空集，如实登记为待定项
# （块3/编排层按 QMT 侧平台差异清单另行立项，不静默默认无差异）。
# ============================================================================
LOCAL_ONLY_PASSTHROUGH_BLOCK: set[str] = set()


def _line_of(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 1))


def _analyze_aliases(tree: ast.AST) -> dict[str, str]:
    """H3：构建 import 别名映射（别名 → 原名）。"""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
    return aliases


def _collect_defined_names(tree: ast.AST) -> set[str]:
    """收集源码内全部绑定名（2026-09-08 转换门禁通用 else 的排除集来源，终审补强 1 完整配方）。

    配方：
    - FunctionDef / AsyncFunctionDef / ClassDef 名；
    - Import/ImportFrom 绑定：asname 或原名（点号导入无 asname 时取首段，import os.path → os）；
    - 全部 Store 上下文的 ast.Name（一网打尽 Assign/For/With/walrus/except-as/推导式绑定目标）；
    - 全部 ast.arg 形参名（含 lambda、*args/**kwargs）。

    设计取舍：收集为全局集合、不做 per-scope 精确化——过近似（宁多排不误杀）是
    转换门禁的正确方向（宁让个别未定义名漏过通用 else，也不误杀合法源码定义名）。
    """
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                defined.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                defined.add(a.asname or a.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    return defined


def _collect_local_only_refs(tree: ast.AST, aliases: dict[str, str]) -> list[ast.AST]:
    """收集本地专用 API 的全部 Load 上下文引用（终审补强 2 引用级拦截）。

    不限 Call func 位置：调用/别名赋值（f = get_index_day_bar）/传参（df.apply(get_index_day_bar)）
    任一引用形态均捕获——平台不存在的名字任何引用都通向 NameError，对 curated 集合
    引用级拦截无误杀面。经别名归一化匹配（H3）。
    """
    refs: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = aliases.get(node.id, node.id)
            if name in LOCAL_ONLY_PASSTHROUGH_BLOCK:
                refs.append(node)
    return refs


def _else_block_excluded(defined_names: set[str]) -> set[str]:
    """通用 else BLOCK 的排除集：Python builtins ∪ 源码定义名（含 import/赋值/形参）。"""
    import builtins
    return set(dir(builtins)) | defined_names


def _apply_replacements(src: str, replacements: list[tuple[int, int, int, int, str]]) -> str:
    """按 (start_line, start_col, end_line, end_col, new_text)（1-based 行号、0-based 列）
    从后往前应用替换，避免行号/偏移漂移。"""
    if not replacements:
        return src
    lines = src.splitlines(keepends=True)
    offsets: list[int] = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln)

    def abs_pos(line: int, col: int) -> int:
        return offsets[line - 1] + col

    ordered = sorted(replacements, key=lambda r: -abs_pos(r[0], r[1]))
    for sl, sc, el, ec, new in ordered:
        s, e = abs_pos(sl, sc), abs_pos(el, ec)
        src = src[:s] + new + src[e:]
    return src


def _is_bare_expr_stmt(node: ast.AST, tree: ast.AST) -> bool:
    """该 Call 节点是否直接作为裸表达式语句（档 1 判定）。"""
    parent = None
    for n in ast.walk(tree):
        for child in ast.iter_child_nodes(n):
            if child is node:
                parent = n
                break
        if parent is not None:
            break
    return isinstance(parent, ast.Expr)


# ============================================================================
# 结果承载（与 PTrade 侧 SourceImportResult 同构，独立定义、零引用）
# ============================================================================


@dataclass
class QmtAction:
    """转换动作审计记录（轻量）。"""

    action_type: str
    rule_id: str
    api_name: str
    line: int
    severity: str
    message: str


@dataclass
class QmtSourceResult:
    """QMT source 转换结果（字段与 PTrade 侧 SourceImportResult 同构）。"""

    converted_code: str
    errors: list[str]
    warnings: list[str]
    actions: list
    design_metadata_resolution: dict | None = None


# ============================================================================
# QMT 扩展常量（独立；严禁触碰 PTrade 侧 _QS_FUNDAMENTALS_EXT 等模板常量）
# 每件 EXT 串内含行级依据注释（docs/qmt/inner-api/）。
# ============================================================================

# ---- log 视图（机制①：模块级名字 log，别名注入式视图）------------------------
_QS_QMT_LOG_EXT = '''
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
'''

# ---- context 视图 + C 捕获底座（机制①；_qs_pick_ctx 为机制② wrapper 的 C 捕获源）----
_QS_QMT_CONTEXT_VIEW_EXT = '''
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
            mkt = self._qs_C.get_market_data_ex(['close'], [code], period='1d', count=1,
                                                dividend_type='none')
            df = mkt.get(code) if isinstance(mkt, dict) else None
            if df is not None and len(df) > 0:
                return float(df.iloc[-1]['close'])
        except Exception:
            pass
        return None

    @property
    def cash(self):
        # 【M5 实测项】ACCOUNT 明细字段（如 m_dCash）未入本次行级索引，失败回退 0.0。
        try:
            acc = get_trade_detail_data(getattr(self._qs_C, 'accountID', ''), 'STOCK', 'ACCOUNT')
            for a in acc or []:
                v = getattr(a, 'm_dCash', None)
                if v is not None:
                    return float(v)
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

    显式映射：portfolio；current_dt / previous_date 为【M5 实测项】（bar_date /
    pre_bar_date 属性未入本次行级索引，取不到返回 None 不抛错）。
    其余属性 __getattr__ 透传 C。
    """

    def __init__(self, C):
        object.__setattr__(self, '_qs_C', C)

    @property
    def portfolio(self):
        return _QmtPortfolioView(self._qs_C)

    @property
    def current_dt(self):
        return getattr(self._qs_C, 'bar_date', None)  # 【M5 实测项】

    @property
    def previous_date(self):
        return getattr(self._qs_C, 'pre_bar_date', None)  # 【M5 实测项】

    @property
    def portfolio_value(self):
        return self.portfolio.total_value  # PTrade context.portfolio_value 别名

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_qs_C'), name)


def _qs_context_view(C):
    return _QmtContextView(C)
'''

# ---- data 视图（机制①；E1 铁律：仅限执行层判断）------------------------------
_QS_QMT_DATA_VIEW_EXT = '''
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
            mkt = C.get_market_data_ex(list(self._FIELDS), [code], period='1d', count=1,
                                       dividend_type='none')
            df = mkt.get(code) if isinstance(mkt, dict) else None
            if df is not None and len(df) > 0:
                last = df.iloc[-1]
                for k in self._FIELDS:
                    vals[k] = float(last[k])
        except Exception:
            vals = {}
        det = {}
        try:
            det = C.get_instrument_detail(code, False) or {}  # 07:2459
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
'''

# ---- history 取数（E1 铁律口径；机制② get_history/get_history_batch 底座）------
_QS_QMT_HISTORY_EXT = '''
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
    """count+1 根取数 → 剔最末根当日 bar（仅日线）→ 至多 count 根（E1）。

    返回 {code: DataFrame}；取数异常 → 打印登记行并返回已得部分（不抛错）。
    """
    div = _QS_QMT_FQ_MAP.get(fq, 'front')  # 未知 fq 按本地默认 fq='pre' → front
    want = max(int(count or 1), 1)
    fetch_n = want + 1 if period == '1d' else want  # 仅日线留 E1 剔除余量
    try:
        mkt = C.get_market_data_ex(list(fields), [str(c) for c in codes],
                                   period=period, count=fetch_n, dividend_type=div)
    except Exception as exc:
        print('QS_QMT_WRAPPER_FETCH_FAIL: get_market_data_ex period=%s: %s'
              % (period, exc))
        return {}
    out = {}
    for code, df in (mkt or {}).items():
        try:
            if df is None or len(df) == 0:
                out[code] = df
                continue
            if period == '1d' and len(df) > want:
                df = df.iloc[:-1].tail(want)  # E1：剔最末根（当日 bar）
            else:
                df = df.tail(want)
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
'''

# ---- 持仓视图 + get_position（机制②同名遮蔽面之一）----------------------------
_QS_QMT_POSITION_EXT = '''
# ---- QS-QMT position 视图 ---------------------------------------------------
# 行级依据（docs/qmt/inner-api/08-交易函数.md）：
#   get_trade_detail_data(accountID, 'STOCK', 'POSITION') :583；
#   m_nVolume（总持仓量）/ m_nCanUseVolume（可用持仓量） :648；m_dOpenPrice（开仓价） :649。
# accountID 取 C.accountID →【M5 实测项】（该属性未入本次行级索引）。


def _qs_get_trade_detail(C, biz_type):
    return get_trade_detail_data(getattr(C, 'accountID', ''), 'STOCK', biz_type)


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
'''

# ---- 下单 wrapper（passorder 底座）-------------------------------------------
_QS_QMT_ORDER_EXT = '''
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

    现价以最新收盘价近似（get_market_data_ex count=1 不复权，07:81/:96/:122）；
    目标卖超可用持仓时按可用上限卖出（清仓零股全清）。取整规则【M5 实测项】。
    静默跳过分支（_qs_noop_target 式）：现价不可得 / delta=0 / 目标卖超空仓 →
    原地返回不下单（PTrade noop 语义，无声不抛错）。
    """
    C = _qs_pick_ctx(C)
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
'''

# ---- A 股池 ------------------------------------------------------------------
_QS_QMT_ASHARES_EXT = '''
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
    try:
        return list(C.get_stock_list_in_sector('沪深A股', 1))
    except Exception:
        return []
'''

# ---- 交易日历 ----------------------------------------------------------------
_QS_QMT_TRADE_DAYS_EXT = '''
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
                out.append(_qs_dtd.fromtimestamp(float(t) / 1000.0).strftime('%Y-%m-%d'))
            else:
                out.append(str(t))
        except Exception:
            out.append(str(t))
    return out
'''

# ---- 证券信息 / 状态 / 现价 ---------------------------------------------------
_QS_QMT_STOCK_INFO_EXT = '''
# ---- QS-QMT stock info / status / current price ------------------------------
# 行级依据（docs/qmt/inner-api/07-行情函数.md）：
#   C.get_instrument_detail(stockcode, iscomplete=False) :2446/:2459；
#   字段 :2479-2511：InstrumentName :2481、OpenDate :2488、ExpireDate :2489、
#   UpStopPrice :2492、DownStopPrice :2493、FloatVolume :2494、
#   InstrumentStatus（停牌状态）:2502、IsTrading :2503；
#   行情现价：C.get_market_data_ex(['close'], ...) :81/:96；dividend_type 枚举 :122。


def _qs_get_stock_info(code, C=None):
    C = _qs_pick_ctx(C)
    try:
        d = C.get_instrument_detail(code, False) or {}
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
    }


def _qs_status_flags(code, C):
    """状态三判（ST/HALT/DELISTING）→ ({type: bool}, 可判定否)。

    数据源 get_instrument_detail（:2479-2511）：
      ST：InstrumentName 含 'ST'（:2481）；
      HALT：InstrumentStatus >= 1（:2502）或 IsTrading 为 False（:2503）；
      DELISTING：ExpireDate（:2489）有效且已过期（当日 > ExpireDate）。
    详情取不到 → ({}, False)（不可判定，调用方保守处置 + 审计行）。
    ExpireDate 形态（数值毫秒/日期串）→【M5 实测项】，双形态防御解析。
    """
    C = _qs_pick_ctx(C)
    try:
        d = C.get_instrument_detail(code, False) or {}  # 07:2459
    except Exception:
        return {}, False
    if not d:
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
            if isinstance(exp, (int, float)):
                exp_dt = _qs_dte.fromtimestamp(float(exp) / 1000.0)  # 毫秒 →【M5 实测项】
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
        mkt = C.get_market_data_ex(['close'], [code], period='1d', count=1,
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
    for s in list(stocks):
        flags, ok = _qs_status_flags(s, C)
        if not ok:
            print('QS_QMT_UNDETERMINED: filter_stock_by_status %s 状态不可判定，'
                  '保守保留' % (s,))
            out.append(s)
            continue
        if any(flags.get(t, False) for t in types):
            continue
        out.append(s)
    return out
'''

# ---- 财务取数映射（M2b 定稿映射表，不得更动）----------------------------------
_QS_QMT_FUNDAMENTALS_EXT = '''
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
        mkt = C.get_market_data_ex(['close'], [code], period='1d', count=1,
                                   dividend_type='none')
        df = mkt.get(code) if isinstance(mkt, dict) else None
        if df is not None and len(df) > 0:
            return float(df.iloc[-1]['close'])
    except Exception:
        pass
    return None


def _qs_fin_to_rows(raw):
    """get_financial_data 原始返回 → {code: {field: value}} 归一。

    返回形态未入本次行级索引 →【M5 实测项】：兼容 {field: {code: v}} /
    {code: {field: v}} / DataFrame(index=code) 三形态。
    """
    rows = {}
    if raw is None:
        return rows
    if hasattr(raw, 'to_dict'):
        try:
            return raw.to_dict('index')
        except Exception:
            return rows
    if not isinstance(raw, dict):
        return rows
    first = next(iter(raw.values()), None)
    if isinstance(first, dict):
        probe = next(iter(first.values()), None)
        if isinstance(probe, dict):
            for code, fdict in raw.items():          # 形态：{code: {field: v}}
                rows.setdefault(str(code), {}).update(fdict)
        else:
            for field, cmap in raw.items():          # 形态：{field: {code: v}}
                for code, v in cmap.items():
                    rows.setdefault(str(code), {})[field] = v
    return rows


_QS_QMT_FUND_FIELD_MAP = {
    # table → (QMT 原生表, {本地字段 → 原生字段})
    'eps': ('PERSHAREINDEX', {'eps': 's_fa_eps_basic',          # :2388
                              'publ_date': 'm_anntime',         # :2330【M5 实测项】
                              'end_date': 'm_timetag'}),        # :2329【M5 实测项】
    'growth_ability': ('PERSHAREINDEX', {'or_yoy': 'inc_revenue_rate',  # :2395
                                         'publ_date': 'm_anntime',
                                         'end_date': 'm_timetag'}),
    'valuation': ('CAPITALSTRUCTURE', {'float_value': 'circulating_capital'}),  # :2376 派生
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
    try:
        raw = C.get_financial_data(native_fields, codes, '19900101',
                                   str(date) if date else '',
                                   report_type='announce_time')  # 显式钉死 :1867/:1877
    except Exception:
        return frame
    rows = _qs_fin_to_rows(raw)
    for code in codes:
        row = rows.get(code)
        if not row:
            continue  # 缺数保持 NaN 契约行
        rec = {}
        for lf in fields:
            v = row.get(fmap.get(lf, lf))
            if lf in ('publ_date', 'end_date'):
                v = _qs_fin_ms_to_date(v)  # 毫秒时间戳 :1837 → 'YYYYMMDD'
            rec[lf] = v
        if table == 'valuation' and 'float_value' in fields:
            cap = row.get('circulating_capital')      # 股 :2376
            close = _qs_last_close(C, code)           # 元（不复权 :122）
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
'''

# ---- wrapper 遮蔽区（机制②：PTrade 原同名遮蔽 + 机制③：keyword/位置双形态）------
_QS_QMT_WRAPPER_ZONE = '''
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
    for s in lst:
        out[s] = _qs_get_stock_info(s, C=C)
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
'''

# ---- 别名视图区（机制①；位于 EXT 定义之后保证模块导入期可执行）------------------
_QS_QMT_ALIAS_ZONE = '''
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
'''

# ---- 模块头（注入序第一段：gbk 头由组装层prepend/替换 + import）------------------
_QS_QMT_MODULE_HEADER = '''\
# ==== QS-QMT auto-generated header（source_import_qmt.py · M2b 架构 D · 块1骨架）====
# 产物落盘物理编码必须为 GBK（与首行 #coding:gbk 声明一致），由写出方负责。
import pandas as pd  # _QS_QMT_FUNDAMENTALS_EXT 消费
'''

# ---- run_daily 驱动段（规则 i；0 缩进块，由 _shell_body 统一施加缩进）--------------
def _qs_run_daily_drive(func_names: list[str]) -> str:
    """run_daily 驱动段（规则 i；0 缩进块）——逐**函数名字面量**调用。

    不采用「注册表 + 循环变量调用」形态：portability 白名单只认产物内 def /
    白名单 API / builtin，对循环变量发起的调用会被 QMT-API-WHITELIST BLOCK；
    该门禁**不得为通过而放宽**，故驱动以函数名字面量逐个生成
    （与本地 run_daily 每交易日调用一次语义等价）。
    """
    return "\n".join("%s(context)" % n for n in func_names)


# ============================================================================
# QmtSourceConverter —— 改写链（块1 核心）
# 规则编号沿用 M2b 方案：(e) 编码头 / (d) .SS→.SH 归一 / (f) DENY 剥除 /
# (h) 生命周期映射 / (i) run_daily 时间坍缩登记。
# 改写经 _apply_replacements 多趟实施（趟内替换区间互不重叠、自后向前应用），
# 保留原始行序语义。
# ============================================================================

# ---- 注释区不可 gbk 字符的等价 ASCII 转写表（规则 e 扩展；**仅作用于注释**）------
# 动因：策略源码零改动铁律下，源注释含非 gbk 符号（如 ⇒）时只能由转换器转写；
# 源本身合法（非 M2a BOM 式数据损坏），故不触及 fail-closed 门——字符串字面量/
# 标识符内的不可编码字符仍由写盘点抛错（不静默）。
_QS_GBK_ASCII_MAP = {
    "\u21d2": "=>", "\u21d0": "<=", "\u2192": "->", "\u2190": "<-",
    "\u2265": ">=", "\u2264": "<=", "\u2260": "!=", "\u2248": "~=",
    "\u00d7": "x", "\u00f7": "/", "\u00b1": "+/-", "\u221a": "sqrt",
    "\u221e": "inf", "\u2211": "sum", "\u2206": "delta",
    "\u03b1": "alpha", "\u03b2": "beta", "\u03b3": "gamma", "\u03b8": "theta",
    "\u03bb": "lambda", "\u03bc": "mu", "\u03c3": "sigma", "\u03c1": "rho",
    "\u03c9": "omega", "\u0394": "Delta", "\u03a3": "Sigma",
    "\u3000": " ", "\u200b": "",
}

_DENY_APIS = ("set_benchmark", "set_commission")
_LIFECYCLE_INIT = "initialize"
_LIFECYCLE_HANDLE = "handle_data"
_LIFECYCLE_BEFORE = "before_trading_start"
_LIFECYCLE_AFTER = "after_trading_end"

# 机制②接线状态（块2 落地后）：
# 已接线 = 产物内存在同名遮蔽 def（_QS_QMT_WRAPPER_ZONE / EXT 区），调用点零改动直达；
# 未接线 = 产物内无定义 → 调用点运行期 NameError，warnings 必须如实登记（不静默）。
_WIRED_WRAPPER_NAMES: frozenset = frozenset({
    "get_history", "get_history_batch", "order", "order_target_value",
    "get_positions", "get_position", "get_fundamentals", "get_fundamentals_batch",
    "filter_stock_by_status", "get_stock_status", "get_Ashares",
    "get_trade_days", "get_stock_info", "current_price",
})
_PENDING_WRAPPER_NAMES: dict[str, str] = {
    # 块2 明确未实现：日期区间语义（本地签名 ptrade_api.py:1669）超出 19 件清单；
    # 调用面命中时 warning「运行期不可用」——不静默（块3 编排层可做调用点改写）。
    "get_price": "_qs_get_price",
}

# PEP263 编码声明探测（仅前两行有效，PEP 263）
_PEP263_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-_.a-zA-Z0-9]+)")

# 证券代码字面量（形如 '600000.SS'；大小写后缀兼容）
_SEC_CODE_TOKEN_RE = re.compile(r"\d{6}\.(?:SS|SZ|SH|BJ|XSHG|XSHE|XBJ|XBSE|ss|sz|sh|bj)\b")


def _normalize_code_text(value: str) -> tuple[str, int]:
    """字符串常量内的证券代码 token 经 normalize_to_qmt 归一（.SS→.SH 等）。"""

    def _sub(m):
        new = normalize_to_qmt(m.group(0))
        if new != m.group(0):
            _sub.changed += 1
            return new
        return m.group(0)

    _sub.changed = 0
    return _SEC_CODE_TOKEN_RE.sub(_sub, value), _sub.changed


def _requote_literal(new_value: str, seg: str) -> str:
    """以原字面量的引号风格重建字符串字面量（保守转义）。"""
    if "\n" in new_value or "'''" in seg[:4] or '"""' in seg[:4]:
        return repr(new_value)
    quote = "'" if seg[0] == "'" else '"'
    escaped = (
        new_value.replace("\\", "\\\\")
        .replace(quote, "\\" + quote)
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return quote + escaped + quote


class QmtSourceConverter:
    """QMT source 转换器：持有原源码，产出 QMT 源码（M2b 块1 改写链）。

    三机制：
    ① 别名注入式视图 shim —— 模块级 g/context/data/log 名字 + 视图 EXT，
       天然覆盖 hasattr(g,'name') 守卫；
    ② wrapper 同名遮蔽 + C 捕获 —— 形态参照 source_import.py:448-456
       （get_history 同名重绑定；C 捕获源为 _qs_pick_ctx → 模块级 g）；
    ③ 参数兼容 keyword/位置双形态 —— 块2 随 wrapper 逐件落地。
    """

    def __init__(self, source: str, *, strategy_path: str | None = None):
        self.source = source
        self.strategy_path = strategy_path
        self._errors: list[str] = []
        self._warnings: list[str] = []
        self._actions: list[QmtAction] = []
        # 规则 i：run_daily 注册回调名（pass2 收集 → pass3 生成 handlebar 驱动段）
        self._run_daily_funcs: list[str] = []

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _toplevel_func(tree: ast.Module, name: str) -> ast.FunctionDef | None:
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        return None

    def _action(self, action_type: str, rule_id: str, api_name: str,
                line: int, severity: str, message: str) -> None:
        self._actions.append(QmtAction(action_type, rule_id, api_name, line, severity, message))

    # ------------------------------------------------------------------ pass1

    def _pass0_gbk_translit(self, src: str) -> str:
        """(e 扩展) 非 gbk 字符等价 ASCII 转写——范围**严格限定文档载体**：
        ① tokenize COMMENT token；② docstring 节点（模块/类/函数首语句的裸字符串）。

        运行期消费的字符串字面量内的不可编码字符**保持 fail-closed**（写盘点抛错，
        不静默改语义）；仅替换字符、不重建引号。动因：策略源码零改动铁律下，源
        注释/docstring 含非 gbk 符号（如 ⇒）时只能由转换器转写；源本身合法
        （非 M2a BOM 式数据损坏），故不触及 fail-closed 门。
        """
        lines = src.splitlines(keepends=True)
        offsets: list[int] = []
        pos = 0
        for ln in lines:
            offsets.append(pos)
            pos += len(ln)

        spans: list[tuple[int, int]] = []

        def _abs(line: int, col: int) -> int:
            if line - 1 < len(offsets):
                return offsets[line - 1] + col
            return len(src)

        try:
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type == tokenize.COMMENT:
                    spans.append((_abs(tok.start[0], tok.start[1]),
                                  _abs(tok.end[0], tok.end[1])))
        except Exception as exc:
            self._warnings.append("注释 gbk 转写：tokenize 失败 %r——注释面跳过" % (exc,))

        try:
            tree = ast.parse(src)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                body = getattr(node, "body", None)
                if not isinstance(body, list) or not body:
                    continue
                first = body[0]
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    doc = first.value
                    spans.append((_abs(doc.lineno, doc.col_offset),
                                  _abs(doc.end_lineno, doc.end_col_offset)))

        replaced: dict[str, int] = {}
        out = src
        for s, e in sorted(set(spans), key=lambda t: -t[0]):
            seg = out[s:e]
            new = seg
            for ch, rep in _QS_GBK_ASCII_MAP.items():
                if ch in new:
                    replaced[ch] = replaced.get(ch, 0) + new.count(ch)
                    new = new.replace(ch, rep)
            if new != seg:
                out = out[:s] + new + out[e:]
        if replaced:
            self._action("gbk_translit", "e", "gbk", 0, "warn",
                         "非 gbk 字符等价 ASCII 转写（注释/docstring）：%s"
                         % ", ".join("%s×%d" % (c, n) for c, n in sorted(replaced.items())))
        return out

    def _pass1_header_codes(self, tree: ast.Module) -> tuple[str, bool]:
        """(e) 编码头替换 + (d) 证券代码字面量归一。返回 (改写后文本, PEP263 是否已替换)。"""
        reps: list[tuple[int, int, int, int, str]] = []
        pep263_replaced = False
        lines = self.source.splitlines(keepends=True)
        for idx in (0, 1):
            if idx >= len(lines):
                break
            m = _PEP263_RE.match(lines[idx].rstrip("\r\n"))
            if m:
                reps.append((idx + 1, 0, idx + 1, len(lines[idx].rstrip("\r\n")),
                             "# (原 PEP263 声明已上移至产物首行 #coding:gbk)"))
                pep263_replaced = True
                self._action("coding_header", "e", m.group(1), idx + 1, "info",
                             "PEP263 编码声明替换为 #coding:gbk（替换非追加）")
                break
        # (d) 字符串常量内证券代码归一（AST 精确到常量 span）
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            new_value, changed = _normalize_code_text(node.value)
            if not changed:
                continue
            seg = ast.get_source_segment(self.source, node)
            if seg is None:
                self._warnings.append(
                    "证券代码常量 span 提取失败，跳过归一（line %d）" % _line_of(node))
                continue
            reps.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                         _requote_literal(new_value, seg)))
            self._action("code_normalized", "d", "normalize_to_qmt", _line_of(node), "info",
                         "证券代码字面量归一（%d 处，如 .SS→.SH）" % changed)
        return _apply_replacements(self.source, reps), pep263_replaced

    # ------------------------------------------------------------------ pass2

    def _pass2_deny_rundaily(self, src: str) -> str:
        """(f) DENY 剥除 + (i) run_daily 时间坍缩登记（裸语句整句替换为审计行）。"""
        tree = ast.parse(src)
        aliases = _analyze_aliases(tree)
        reps: list[tuple[int, int, int, int, str]] = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            raw = aliases.get(node.func.id, node.func.id)
            if raw in _DENY_APIS:
                if _is_bare_expr_stmt(node, tree):
                    reps.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                                 "print('QS_QMT_DENY_REMOVED: %s')" % raw))
                    self._action("deny_removed", "f", raw, _line_of(node), "info",
                                 "DENY API 调用整句剥除并留审计行（QMT 无对应配置面）")
                else:
                    self._warnings.append(
                        "非裸语句形态的 %s(...) 未剥除（line %d）——人工复核" % (raw, _line_of(node)))
            elif raw == "run_daily":
                if _is_bare_expr_stmt(node, tree):
                    ind = " " * node.col_offset
                    func_src = self._run_daily_func_src(node, src)
                    audit = ("print('QS_QMT_SEMANTIC_DIFF: run_daily time collapsed"
                             " to per-daily-bar')")
                    if func_src is None:
                        # 回调实参无法解析：保留审计行并如实登记（不静默丢注册）
                        reps.append((node.lineno, node.col_offset,
                                     node.end_lineno, node.end_col_offset, audit))
                        self._warnings.append(
                            "run_daily 回调实参无法解析（line %d）——回调未注册，"
                            "策略主体在 QMT 侧不会被驱动" % _line_of(node))
                    else:
                        # 规则 i（不丢注册）：记录回调名，由 handlebar 壳尾段逐名驱动
                        # （每根日 bar 一次）；时间坍缩语义差异仍以审计行登记。
                        if func_src.isidentifier():
                            if func_src not in self._run_daily_funcs:
                                self._run_daily_funcs.append(func_src)
                            reps.append((node.lineno, node.col_offset,
                                         node.end_lineno, node.end_col_offset, audit))
                        else:
                            reps.append((node.lineno, node.col_offset,
                                         node.end_lineno, node.end_col_offset, audit))
                            self._warnings.append(
                                "run_daily 回调为非简单标识符表达式 %r（line %d）——"
                                "未驱动，人工复核" % (func_src, _line_of(node)))
                    self._action("semantic_diff", "i", "run_daily", _line_of(node), "warn",
                                 "run_daily 时间坍缩为日频 bar（回调保留注册 + 审计行；"
                                 "QMT 无盘中定时回调面）")
                else:
                    self._warnings.append(
                        "非裸语句形态的 run_daily(...) 未登记坍缩（line %d）——人工复核" % _line_of(node))
        return _apply_replacements(src, reps)

    # ------------------------------------------------------------------ pass3

    @staticmethod
    def _run_daily_func_src(node: ast.Call, src: str) -> str | None:
        """提取 run_daily 的回调实参源码。

        PTrade 形态：run_daily(context, func, time='15:00')——回调为**第 2 个位置实参**；
        亦兼容 func=/func_name=/function= 关键字形态与单实参形态。取不到即返回 None
        （调用方按「未注册」如实登记，不静默丢）。
        """
        cand: ast.AST | None = None
        if len(node.args) >= 2:
            cand = node.args[1]
        else:
            for kw in node.keywords:
                if kw.arg in ("func", "func_name", "function", "callback"):
                    cand = kw.value
                    break
            if cand is None and len(node.args) == 1:
                cand = node.args[0]
        if cand is None:
            return None
        return ast.get_source_segment(src, cand)

    @staticmethod
    def _name_rep(fn: ast.FunctionDef, new_name: str) -> tuple[int, int, int, int, str]:
        prefix_len = 4 if isinstance(fn, ast.FunctionDef) else 10  # 'def ' / 'async def '
        return (fn.lineno, fn.col_offset + prefix_len,
                fn.lineno, fn.col_offset + prefix_len + len(fn.name), new_name)

    @staticmethod
    def _simple_args(fn: ast.FunctionDef) -> list[ast.arg] | None:
        """仅处理纯位置形参（无默认值/可变参/keyword-only）；否则返回 None 不误杀。"""
        a = fn.args
        pos = list(getattr(a, "posonlyargs", [])) + list(a.args)
        if a.defaults or a.vararg or a.kwarg or a.kwonlyargs:
            return None
        if any(kwd is not None for kwd in a.kw_defaults):
            return None
        return pos or None

    def _args_rep(self, fn: ast.FunctionDef) -> tuple[tuple[int, int, int, int, str],
                                                      list[str]] | tuple[None, list[str]]:
        """形参列表整体替换为 C；返回 (替换元组, 原形参名列表)。"""
        pos = self._simple_args(fn)
        if pos is None:
            self._warnings.append("%s 形参形态非纯位置参数，保留原签名（line %d）——人工复核"
                                  % (fn.name, _line_of(fn)))
            return None, [a.arg for a in fn.args.args]
        first, last = pos[0], pos[-1]
        return ((first.lineno, first.col_offset, last.end_lineno, last.end_col_offset, "C"),
                [a.arg for a in pos])

    @staticmethod
    def _prologue(ctx_alias: str | None, data_alias: str | None) -> str:
        """别名视图绑定块（**0 缩进**；由 _shell_body 按函数体缩进统一施加）。"""
        lines = [
            "global g, context, data",
            "g = C",
            "context = _qs_context_view(C)",
            "data = _qs_data_view(C)",
        ]
        if ctx_alias and ctx_alias != "context":
            lines.append(ctx_alias + " = context")
        if data_alias and data_alias != "data":
            lines.append(data_alias + " = data")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _extract_func_body(src: str, fn: ast.FunctionDef) -> str:
        """提取函数体源码并 dedent 到 0 缩进。

        2026-10-08 阻断修复：切片自 body[0] **行首**（含原缩进），dedent 方可正确
        归零；并 strip 首尾空行，供调用方统一 indent。
        """
        lines = src.splitlines()
        seg = "\n".join(lines[fn.body[0].lineno - 1: fn.body[-1].end_lineno])
        return textwrap.dedent(seg).strip("\n")

    @staticmethod
    def _body_rep(src: str, fn: ast.FunctionDef,
                  new_body: str) -> tuple[int, int, int, int, str]:
        """生成「整段函数体替换」元组：锚点起于 body[0] 行首 col 0、止于 body[-1] 行末。

        2026-10-08 阻断修复：原实现以 (body[0].lineno, body[0].col_offset) 为锚点，
        会保留行首缩进并与注入块自身缩进叠加（→8 空格）；且 get_source_segment 返回段
        不含首行缩进，拼接后语句脱体（0 缩进）→ 产物 IndentationError。
        """
        lines = src.splitlines()
        return (fn.body[0].lineno, 0,
                fn.body[-1].end_lineno, len(lines[fn.body[-1].end_lineno - 1]),
                new_body.rstrip("\n"))

    def _gate(self, src: str, fn: ast.FunctionDef, guard: str,
              ctx_alias: str | None, data_alias: str | None) -> str:
        """构造门控块（**0 缩进**）：if <guard>(C): + 别名绑定 + 重缩进的函数体。"""
        alias_lines = []
        if ctx_alias and ctx_alias != "context":
            alias_lines.append(ctx_alias + " = context")
        if data_alias and data_alias != "data":
            alias_lines.append(data_alias + " = data")
        body = self._extract_func_body(src, fn)
        inner = (("\n".join(alias_lines) + "\n") if alias_lines else "") + body
        return "if %s(C):\n" % guard + textwrap.indent(inner, "    ") + "\n"

    def _shell_body(self, src: str, fn: ast.FunctionDef, *,
                    prologue: str = "", gate_before: str = "",
                    gate_after: str = "", drive: str = "") -> str:
        """构造生命周期壳函数体：prologue → 前段门控 → 原函数体 → 尾段门控 → run_daily 驱动。

        各段以 **0 缩进**输入，整体按 fn.body[0].col_offset 统一缩进后返回；
        调用方以 _body_rep 做整段替换（规避局部锚点缩进叠加）。
        """
        pad = " " * fn.body[0].col_offset
        parts: list[str] = []
        if prologue:
            parts.append(prologue.strip("\n"))
        if gate_before:
            parts.append(gate_before.strip("\n"))
        parts.append(self._extract_func_body(src, fn))
        if gate_after:
            parts.append(gate_after.strip("\n"))
        if drive:
            parts.append(drive.strip("\n"))
        return textwrap.indent("\n".join(parts), pad) + "\n"

    def _pass3_lifecycle(self, src: str) -> str:
        """(h) 生命周期映射：initialize→init(C)、handle_data→handlebar(C)、
        before_trading_start→handlebar 前段（_qs_should_run_daily 门控）、
        after_trading_end→handlebar 尾段（_qs_should_run_after 门控）。
        按实有回调映射，缺件不报错不误杀。"""
        tree = ast.parse(src)
        reps: list[tuple[int, int, int, int, str]] = []
        fn_init = self._toplevel_func(tree, _LIFECYCLE_INIT)
        fn_handle = self._toplevel_func(tree, _LIFECYCLE_HANDLE)
        fn_before = self._toplevel_func(tree, _LIFECYCLE_BEFORE)
        fn_after = self._toplevel_func(tree, _LIFECYCLE_AFTER)

        if fn_init is None and fn_handle is None:
            self._warnings.append(
                "未发现生命周期回调（initialize/handle_data 均缺）——策略主体保持原样（S3 缺件不误杀）")

        # --- initialize → def init(C): ---
        if fn_init is not None:
            if fn_init.decorator_list:
                self._warnings.append("initialize 带装饰器，跳过映射（line %d）——人工复核"
                                      % _line_of(fn_init))
            else:
                reps.append(self._name_rep(fn_init, "init"))
                arg_rep, pnames = self._args_rep(fn_init)
                if arg_rep is not None:
                    reps.append(arg_rep)
                prologue = self._prologue(pnames[0] if pnames else None, None)
                reps.append(self._body_rep(
                    src, fn_init,
                    self._shell_body(src, fn_init, prologue=prologue)))
                self._action("lifecycle_rename", "h", "initialize", _line_of(fn_init), "info",
                             "initialize → init(C)，注入别名视图 prologue（g/context/data）")

        # --- before/after 迁移（仅当 handlebar 存在；缺 handle_data 则原地保留 + warning）---
        gate_before = ""
        gate_after = ""
        if fn_handle is not None and (fn_before is not None or fn_after is not None):
            if fn_before is not None and not fn_before.decorator_list:
                _, bp = self._args_rep(fn_before)
                gate_before = self._gate(src, fn_before, "_qs_should_run_daily",
                                         bp[0] if bp else None,
                                         bp[1] if len(bp) > 1 else None)
                reps.append((fn_before.lineno, fn_before.col_offset,
                             fn_before.end_lineno, fn_before.end_col_offset,
                             "# QS_QMT_LIFECYCLE_MOVED: before_trading_start -> handlebar 前段"
                             "（_qs_should_run_daily 门控）"))
                self._action("lifecycle_move", "h", "before_trading_start",
                             _line_of(fn_before), "info",
                             "迁移至 handlebar 前段（_qs_should_run_daily 门控）")
            if fn_after is not None and not fn_after.decorator_list:
                _, ap = self._args_rep(fn_after)
                gate_after = self._gate(src, fn_after, "_qs_should_run_after",
                                        ap[0] if ap else None,
                                        ap[1] if len(ap) > 1 else None)
                reps.append((fn_after.lineno, fn_after.col_offset,
                             fn_after.end_lineno, fn_after.end_col_offset,
                             "# QS_QMT_LIFECYCLE_MOVED: after_trading_end -> handlebar 尾段"
                             "（_qs_should_run_after 门控）"))
                self._action("lifecycle_move", "h", "after_trading_end",
                             _line_of(fn_after), "info",
                             "迁移至 handlebar 尾段（_qs_should_run_after 门控）")
        elif fn_before is not None or fn_after is not None:
            self._warnings.append(
                "存在 before_trading_start/after_trading_end 但缺 handle_data——二者原地保留不迁移"
                "（S3 缺件不误杀）")

        # --- handle_data → def handlebar(C): + prologue + 前段门控 + 尾段门控 ---
        if fn_handle is not None:
            if fn_handle.decorator_list:
                self._warnings.append("handle_data 带装饰器，跳过映射（line %d）——人工复核"
                                      % _line_of(fn_handle))
            else:
                reps.append(self._name_rep(fn_handle, "handlebar"))
                arg_rep, pnames = self._args_rep(fn_handle)
                if arg_rep is not None:
                    reps.append(arg_rep)
                prologue = self._prologue(pnames[0] if pnames else None,
                                          pnames[1] if len(pnames) > 1 else None)
                reps.append(self._body_rep(
                    src, fn_handle,
                    self._shell_body(src, fn_handle, prologue=prologue,
                                     gate_before=gate_before,
                                     gate_after=gate_after,
                                     drive=_qs_run_daily_drive(self._run_daily_funcs))))
                self._action("lifecycle_rename", "h", "handle_data", _line_of(fn_handle), "info",
                             "handle_data → handlebar(C)，注入别名视图 prologue 与前后段门控")
        else:
            # 规则 h（S3 等缺件形态）：源无 handle_data——QMT 侧必须有 handlebar 入口，
            # 否则 run_daily 注册的回调永不驱动、且 portability QMT-LIFECYCLE-SHAPE BLOCK。
            # 合成 handlebar 壳（prologue + run_daily 驱动段），**不改写源内任何函数**。
            if not src.splitlines():
                self._warnings.append("源为空，跳过 handlebar 合成")
            else:
                shell = self._prologue(None, None).strip("\n")
                _drive = _qs_run_daily_drive(self._run_daily_funcs)
                if _drive:
                    shell += "\n" + _drive
                synth = ("\n\ndef handlebar(C):\n"
                         + textwrap.indent(shell, "    ") + "\n")
                last_ln = len(src.splitlines())
                end_col = len(src.splitlines()[last_ln - 1])
                reps.append((last_ln, end_col, last_ln, end_col, synth))
                self._action("lifecycle_synth", "h", "handlebar", last_ln, "warn",
                             "源无 handle_data：合成 handlebar 壳驱动 run_daily 注册回调"
                             "（M5 实测项：真实 bar 节拍与运行期可用性）")
                if fn_init is None:
                    self._warnings.append(
                        "源内 initialize 与 handle_data 均缺——产物仅有合成 handlebar，"
                        "init 需人工补（不误杀）")

        self._check_no_overlap(reps)
        return _apply_replacements(src, reps)

    @staticmethod
    def _check_no_overlap(reps: list[tuple[int, int, int, int, str]]) -> None:
        spans = sorted((sl, sc, el, ec) for sl, sc, el, ec, _ in reps)
        for prev, cur in zip(spans, spans[1:]):
            if (cur[0], cur[1]) < (prev[2], prev[3]):
                raise RuntimeError("改写区间重叠：%r 与 %r" % (prev, cur))

    # ------------------------------------------------------------- assembly

    def _assemble(self, body_src: str, *, pep263_replaced: bool) -> str:
        """注入序组装：模块头（gbk 头 + import）→ 视图 EXT（先于别名区，保证导入期可执行）
        → 别名视图区 → wrapper 遮蔽区 → 生命周期壳（改写后策略主体）。"""
        parts: list[str] = []
        # (e) 产物首行**恒为** #coding:gbk：PEP 263 声明必须位于首行。原实现对源内
        # 已含声明者不追加（指望被替换的声明就是首行），但策略主体拼接在模块头之后，
        # 致首行被模块头注释占据、声明沉到中部 → portability QMT-GBK-HEADER BLOCK。
        parts.append("#coding:gbk")
        parts.append(_QS_QMT_MODULE_HEADER.strip("\n"))
        for ext in (_QS_QMT_LOG_EXT, _QS_QMT_CONTEXT_VIEW_EXT, _QS_QMT_DATA_VIEW_EXT,
                    _QS_QMT_HISTORY_EXT, _QS_QMT_POSITION_EXT, _QS_QMT_ORDER_EXT,
                    _QS_QMT_ASHARES_EXT, _QS_QMT_TRADE_DAYS_EXT,
                    _QS_QMT_STOCK_INFO_EXT, _QS_QMT_FUNDAMENTALS_EXT):
            parts.append(ext.strip("\n"))
        parts.append(_QS_QMT_ALIAS_ZONE.strip("\n"))
        parts.append(_QS_QMT_WRAPPER_ZONE.strip("\n"))
        parts.append("# " + "=" * 74)
        parts.append("# ==== 策略主体（QmtSourceConverter 改写；规则 e/d/f/h/i 见 actions 审计） ====")
        parts.append(body_src.strip("\n"))
        return "\n".join(parts) + "\n"

    # ------------------------------------------------------------- aux scans

    def _scan_wrapper_wiring(self, src: str) -> None:
        """机制②接线面扫描（块2 落地后）：
        命中已接线名（_WIRED_WRAPPER_NAMES）→ info action（接线生效审计，逐名一条）；
        命中未接线名（_PENDING_WRAPPER_NAMES）→ warning「运行期不可用」（不静默）。"""
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return
        aliases = _analyze_aliases(tree)
        wired_hit: set[str] = set()
        pending_hit: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                raw = aliases.get(node.func.id, node.func.id)
                if raw in _PENDING_WRAPPER_NAMES:
                    pending_hit.add(raw)
                elif raw in _WIRED_WRAPPER_NAMES:
                    wired_hit.add(raw)
        for name in sorted(wired_hit):
            self._action("wrapper_wired", "m2", name, 0, "info",
                         "机制②同名遮蔽已接线（产物内定义，调用点零改动）")
        for name in sorted(pending_hit):
            self._warnings.append(
                "API %s(...) 未接线机制②同名遮蔽（%s 未实现）——运行期不可用"
                % (name, _PENDING_WRAPPER_NAMES[name]))

    def _resolve_design(self) -> dict | None:
        if not self.strategy_path:
            return None
        try:
            res = find_design_for_strategy(self.strategy_path)
        except Exception as exc:  # 设计档案缺失不阻断转换
            self._warnings.append("design_metadata 解析失败：%r" % (exc,))
            return None
        try:
            return asdict(res)
        except TypeError:
            return getattr(res, "__dict__", {"repr": repr(res)})

    # ------------------------------------------------------------------ entry

    def convert(self) -> QmtSourceResult:
        """执行改写链：pass1(e+d) → pass2(f+i) → pass3(h) → 组装 → 自检。"""
        # pass0：注释/docstring 区不可 gbk 字符 → 等价 ASCII 转写（须先于其余各趟——
        # 后续 pass 均以 self.source 为替换基底，故此处原地更新）
        self.source = self._pass0_gbk_translit(self.source)
        try:
            tree0 = ast.parse(self.source)
        except SyntaxError as exc:
            self._errors.append("源码 AST 解析失败：%s（line %s）" % (exc.msg, exc.lineno))
            return QmtSourceResult(converted_code="", errors=self._errors,
                                   warnings=self._warnings, actions=self._actions,
                                   design_metadata_resolution=None)
        try:
            src1, pep263_replaced = self._pass1_header_codes(tree0)
            src2 = self._pass2_deny_rundaily(src1)
            src3 = self._pass3_lifecycle(src2)
            code = self._assemble(src3, pep263_replaced=pep263_replaced)
        except RuntimeError as exc:
            self._errors.append("改写链失败：%s" % (exc,))
            return QmtSourceResult(converted_code="", errors=self._errors,
                                   warnings=self._warnings, actions=self._actions,
                                   design_metadata_resolution=None)
        try:
            ast.parse(code)
        except SyntaxError as exc:
            self._errors.append("产物 AST 自检失败：%s（line %s）" % (exc.msg, exc.lineno))
        self._scan_wrapper_wiring(src3)
        design = self._resolve_design()
        return QmtSourceResult(converted_code=code, errors=self._errors,
                               warnings=self._warnings, actions=self._actions,
                               design_metadata_resolution=design)


# ============================================================================
# 模块级编排入口（块2/3）—— source 路径主径的 QMT 分支
# ============================================================================

def convert_source_qmt(source_path: str | Path, *, strategy_id: str | None = None,
                       verbose: bool = True,
                       etf_pool_start_date: str | None = None,
                       db_path: str | Path | None = None,
                       exclude_bse: bool = False,
                       engine_profile: str | None = None) -> QmtSourceResult:
    """本地 QuantStudio 策略 → QMT（迅投 XtQuant 内置 Python）source 转换入口。

    编排面与 PTrade 侧 convert_source（source_import.py:5191）同构：
    - 读源：utf-8-sig → gbk 回退（BOM 兼容，PTrade 侧 N1 同款，source_import.py:5210-5216）；
    - strategy_id 缺省 = path.stem.replace('_quantstudio', '')；
    - **不写盘**（写盘由编排层负责，GBK 物理转码在写盘点，见
      QS_QMT_PRODUCT_PHYSICAL_ENCODING）；
    - design_metadata 经 find_design_for_strategy 解析并填入
      design_metadata_resolution（缺失不阻断，warning 登记——见 _resolve_design）；
    - 产出 converted_code（unicode）+ errors / warnings / actions。

    块2 未消费参数（warning 如实登记，不静默）：
    - etf_pool_start_date / db_path：ETF 静态池固化为 PTrade 转换面特性，
      QMT 侧沿用 get_Ashares / sector 动态池（见 _QS_QMT_ASHARES_EXT）；
    - exclude_bse：'沪深A股' sector 自身即沪深口径（07:3297）；
    - engine_profile：QMT 侧 profile 门禁为块3 接线项。
    """
    path = Path(source_path)
    try:
        source_code = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            source_code = path.read_text(encoding="gbk")
        except UnicodeDecodeError as exc:
            return QmtSourceResult(converted_code="",
                                   errors=["文件编码无法识别: %s" % (exc,)],
                                   warnings=[], actions=[])
    if strategy_id is None:
        strategy_id = path.stem.replace("_quantstudio", "")
    converter = QmtSourceConverter(source_code, strategy_path=str(path))
    result = converter.convert()
    notes = []
    if etf_pool_start_date is not None:
        notes.append("etf_pool_start_date=%r 未消费（QMT 侧 ETF 池沿用 sector 动态池，"
                     "静态池固化为 PTrade 转换面特性）" % (etf_pool_start_date,))
    if db_path is not None:
        notes.append("db_path=%r 未消费（QMT 侧无 etf_basic 固化查询面）" % (str(db_path),))
    if exclude_bse:
        notes.append("exclude_bse=True 未消费（'沪深A股' sector 自身即沪深口径，07:3297）")
    if engine_profile is not None:
        notes.append("engine_profile=%r 未消费（QMT 侧 profile 门禁为块3 接线项）"
                     % (engine_profile,))
    for note in notes:
        result.warnings.append("convert_source_qmt: " + note)
    if verbose:
        print("[convert_source_qmt] strategy_id=%s errors=%d warnings=%d actions=%d "
              "code=%d chars" % (strategy_id, len(result.errors), len(result.warnings),
                                 len(result.actions), len(result.converted_code)))
    return result
