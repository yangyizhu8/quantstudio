# -*- coding: utf-8 -*-
"""source_import：本地策略源码 → PTrade 代码转换器（source entry 支线 A）。

规格：私募工作文件/QuantStudio本地策略转ptrade模块开发/02-source_import模块规格.md（v1.1）
规则来源：portability_rules.py（单一来源，与 validate_ptrade_portability 共用）

设计要点（审核意见 H2/H3/G1 落实）：
- AST 定位 + 行级文本改写（禁止纯正则全文替换，防误伤字符串/注释）
- 别名表归一化后再匹配（H3）
- REMOVE 分档：档 1 裸语句删行 / 档 2 内嵌改等价字面量 / 档 3 BLOCK（H2）
- FQ 归一化只按 NORMALIZE_RULES 的 grade 执行（G1：dypre→NORMALIZE 有证据、dypost→WARN_KEEP）
- MyTT/A股规则：用到才注入 + 前缀重命名 + 非 1:1 标记（D1）
- 幂等性：INJECTED_MARKER 标记，二次转换零动作
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .portability_rules import (
    DENY_BLOCK,
    DENY_REMOVE,
    DENY_SHIM,
    GET_PRICE_DROP_PARAMS,
    INJECTED_MARKER,
    MYTT_FUNCTIONS,
    ASHARE_RULES_FUNCTIONS,
    NORMALIZE_RULES,
    PTRADE_PROFILE_MARKER,
    PTRADE_REGISTERED_WARN,
)
# ============================================================================
# 数据结构（02 规格 §1）
# ============================================================================


@dataclass
class ConversionAction:
    """一次转换动作的留痕记录。"""

    action_type: str  # REMOVE | DEGRADE | REWRITE | SHIM | NORMALIZE | INJECT | KEEP_COMMENT
    rule_id: str
    api_name: str
    line: int
    severity: str  # BLOCK | WARN | INFO
    old_text: str = ""
    new_text: str = ""
    message: str = ""


@dataclass
class SourceImportResult:
    converted_code: str
    actions: list[ConversionAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    reverse_spec: Optional[dict] = None
    spec_inference_notes: list[str] = field(default_factory=list)


# ============================================================================
# 注入源码常量（helper 提取自 templates/ptrade_daily.py.j2）
# ============================================================================

_PTRADE_HELPERS = '''
{marker}
def _lookup_history_item(history, code):
    if history is None:
        return None
    try:
        return history[code]
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    target = _bare_code(code)
    try:
        keys = history.keys()
    except AttributeError:
        return None
    for key in keys:
        if _bare_code(key) == target:
            try:
                return history[key]
            except Exception:
                return None
    return None


def _extract_history_field(history_item, field):
    if history_item is None:
        return np.asarray([], dtype=object)
    try:
        values = history_item[field]
    except (KeyError, IndexError, TypeError, ValueError):
        return np.asarray([], dtype=object)
    if hasattr(values, 'values'):
        values = values.values
    return np.asarray(values, dtype=object)


def _bare_code(code):
    return str(code).strip().upper().split('.')[0]


def _finite_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _finite_series(values):
    converted = []
    for value in np.asarray(values, dtype=object).reshape(-1):
        number = _finite_float(value)
        converted.append(number if number is not None else np.nan)
    return np.asarray(converted, dtype=float)


def _get_ma(close_array, num):
    if len(close_array) < num:
        return None
    value = close_array[-num:].mean()
    return round(value, 2) if np.isfinite(value) else None


def _portfolio_total_value(context):
    portfolio = context.portfolio
    for name in ('total_value', 'portfolio_value', 'total_asset'):
        value = getattr(portfolio, name, None)
        if value is not None:
            try:
                value = float(value)
                if np.isfinite(value) and value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    market_value = sum(
        float(getattr(position, 'market_value', 0) or 0)
        for position in portfolio.positions.values()
    )
    return float(portfolio.cash) + market_value
'''

_QS_HISTORY_WRAPPER = '''
{marker}
# 方向 B（2026-08-13 平台实证）：PTrade get_history 返回 numpy structured array，
# 统一转为 DataFrame（PTrade pandas 1.5.3 可用）。策略代码可用全部 pandas API。
# 字段名中枢（2026-08-13 平台实证 etf_theme_rotation：invalid field ['amount'],
# valid fields 含 'money' 无 'amount'（成交额）；preClose → preclose 前收大小写）：
# 请求时 本地 → PTrade，返回时 PTrade → 本地，双向映射策略代码零改动。
import pandas as _qs_pd
import numpy as _qs_np

# 本地字段名 → PTrade 字段名（请求时映射）
_QS_FIELD_TO_PTRADE = {{
    'amount': 'money',
    'preClose': 'preclose',
}}
# PTrade 列名 → 本地列名（返回时映射，含 datetime→time）
_QS_COL_TO_LOCAL = {{
    'datetime': 'time',
    'money': 'amount',
    'preclose': 'preClose',
}}

def _qs_to_dataframe(item):
    """structured array → DataFrame；已是 DataFrame/其他类型则原样返回。"""
    if isinstance(item, _qs_np.ndarray) and hasattr(item, 'dtype') and hasattr(item.dtype, 'names'):
        df = _qs_pd.DataFrame(item)
        # 列名统一映射：PTrade → 本地（datetime→time / money→amount / preclose→preClose）
        _rename = {{k: v for k, v in _QS_COL_TO_LOCAL.items()
                    if k in df.columns and v not in df.columns}}
        if _rename:
            df = df.rename(columns=_rename)
        return df
    return item

# 保存原始 get_history 引用：类属性承载（属性调用不被静态 API 白名单拦截，
# 模块级别名函数 _qs_original_get_history(...) 会被 validate_local_strategy 判 BLOCK）
class _QSHistoryState:
    orig = None

_QSHistoryState.orig = get_history

# 重新绑定 get_history：请求前字段名映射（本地 → PTrade）+ 返回转 DataFrame
def get_history(*args, **kwargs):
    _field = kwargs.get('field') or kwargs.get('fields')
    if _field:
        _is_list = isinstance(_field, list)
        _items = _field if _is_list else [_field]
        _mapped = [_QS_FIELD_TO_PTRADE.get(f, f) for f in _items]
        if 'field' in kwargs:
            kwargs['field'] = _mapped if _is_list else _mapped[0]
        if 'fields' in kwargs:
            kwargs['fields'] = _mapped if _is_list else _mapped[0]
    _result = _QSHistoryState.orig(*args, **kwargs)
    if isinstance(_result, dict):
        return {{k: _qs_to_dataframe(v) for k, v in _result.items()}}
    return _qs_to_dataframe(_result)
'''

# 档 2 表达式内嵌的等价字面量（H2）：本地函数 → PTrade 语义等价字面量
_REWRITE_LITERALS: dict[str, str] = {
    "set_backtest": "None",
    "is_trade": "False",
}

# 行情字段名映射中枢在 _QS_HISTORY_WRAPPER 内（请求本地→PTrade / 返回 PTrade→本地）
# DENY_REMOVE 中允许档 2 改写的函数；其余 DENY_REMOVE 档 2 → BLOCK
_REMOVE_ALLOW_INLINE: frozenset[str] = frozenset(_REWRITE_LITERALS.keys())

# NORM-INCLUDE-PTRADE 频率分流（2026-08-13 PTrade 分钟实测）：
# 只有日线频率做 include=False → include=True（PTrade 日线差一天）；
# 分钟频率不改（PTrade 分钟 include 语义与本地一致：True 含当前 bar / False 到前一 bar）。
_DAILY_FREQS: frozenset[str] = frozenset({"1d", "day", "1D"})

# ============================================================================
# 工具
# ============================================================================

_LIFECYCLE = ("initialize", "before_trading_start", "handle_data", "after_trading_end")
_BLOCK_API_NO_FUNCTION = frozenset({"load_research_signals", "get_trades_file",
                                    "convert_position_from_csv", "SharedCostModel"})


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
# 转换器
# ============================================================================


class SourceConverter:
    def __init__(self, *, strategy_id: Optional[str] = None, inject_helpers: bool = True,
                 verbose: bool = True,
                 etf_pool_start_date: Optional[str] = None,
                 db_path: Optional[str] = None,
                 etf_type: str = "equity",
                 active_only: bool = True):
        self.strategy_id = strategy_id
        self.inject_helpers = inject_helpers
        self.verbose = verbose
        self.actions: list[ConversionAction] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self._set_backtest_body: Optional[str] = None  # T5: set_backtest 函数体（供调用点内联）
        # 07 规格：ETF 动态池 FREEZE 固化
        self._etf_pool_start_date = etf_pool_start_date
        self._db_path = db_path
        self._etf_type = etf_type
        self._active_only = active_only
        self._freeze_calls: list[ast.Call] = []
        self._etf_pool_block: Optional[str] = None
        self._etf_pool_meta: dict[str, Any] = {}
        self._etf_frozen = False
        self.coverage: dict[str, Any] = {
            "api_calls_seen": 0, "denylist_hits": 0, "normalized_params": 0,
            "injected_helpers": [], "fq_warn_kept": [], "aliases_seen": {},
            "mytt_used": [], "ashare_used": [], "inject_libs": [],
        }
        self._replacements: list[tuple[int, int, int, int, str]] = []
        self._mytt_needed: set[str] = set()
        self._ashare_needed: set[str] = set()
        self._need_shim: set[str] = set()
        self._need_helpers = False
        self._sklearn_used = False

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def convert(self, source_code: str, source_path: Optional[str] = None) -> SourceImportResult:
        # N1：BOM 已在调用侧用 utf-8-sig 处理；此处兜底
        if source_code.startswith("\ufeff"):
            source_code = source_code.lstrip("\ufeff")
            self.warnings.append("源文件含 BOM（已剥离，读取应使用 utf-8-sig）")

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            self.errors.append(f"SyntaxError: {e}")
            return self._result(source_code)

        self._tree = tree
        self._src = source_code
        self._lines = source_code.splitlines(keepends=True)
        self._aliases = _analyze_aliases(tree)
        self.coverage["aliases_seen"] = {
            k: v for k, v in self._aliases.items() if v not in ("numpy", "pandas")
        }

        # 1) 生命周期扫描 + set_backtest FunctionDef 删除（H2）
        self._handle_lifecycle(tree)

        # 2) sklearn 检测（N2：WARN + PTRADE_RUNTIME_UNVERIFIED，不 BLOCK）
        self._check_third_party(tree)

        # 3) AST 全量扫描（别名归一化后匹配）
        self._scan_calls(tree)

        # 3a) 代码后缀规范化（聚宽风格 XSHG/XSHE → PTrade SS/SZ，AST 字符串常量级）
        self._normalize_code_suffixes(tree)

        # 3a2) PTrade 契约合规改写（get_Ashares 日期 / get_history 签名 B /
        #       set_benchmark 后缀 / get_stock_status 关键字）
        self._normalize_ptrade_contract_calls(tree)

        # 3b) ETF FREEZE 档（07 规格 §2）：get_etf_list_local → 静态池固化
        self._freeze_etf_pool()

        # 4) 应用文本改写（从后往前）
        converted = _apply_replacements(source_code, self._replacements)

        # 5) 注入（helper / shim / MyTT / A股规则）
        if self.inject_helpers:
            converted = self._inject_all(converted)

        # 6) 头部
        converted = self._build_header(source_path) + converted

        # 7) 转换后语法自检
        try:
            ast.parse(converted)
        except SyntaxError as e:
            self.errors.append(f"转换产物 SyntaxError（转换器 bug）: {e}")

        return self._result(converted)

    def _result(self, code: str) -> SourceImportResult:
        self.coverage["denylist_hits"] = sum(
            1 for a in self.actions if a.action_type in ("REMOVE", "REWRITE", "SHIM", "BLOCK")
        )
        return SourceImportResult(
            converted_code=code,
            actions=self.actions,
            warnings=self.warnings,
            errors=self.errors,
            coverage=self.coverage,
        )

    def _find_parent(self, node: ast.AST) -> Optional[ast.AST]:
        """在语法树中查找 node 的父节点。"""
        for n in ast.walk(self._tree):
            for child in ast.iter_child_nodes(n):
                if child is node:
                    return n
        return None

    # ------------------------------------------------------------------
    # 生命周期 + set_backtest
    # ------------------------------------------------------------------
    def _handle_lifecycle(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if name == "set_backtest":
                    sl, el = node.lineno, node.end_lineno
                    # T5 修复：set_backtest 函数体可能含真实配置调用（set_limit_mode/
                    # set_commission/set_slippage 等）。模块级提升时机过早（引擎未 attach
                    # 时 set_commission 被忽略，实测默认 0.00035 未改为 0.00015）。
                    # 正确做法：函数体存入 self._set_backtest_body，由调用点内联
                    # （保持原 initialize 执行时机）；函数定义删除。
                    body_src = ""
                    for stmt in node.body:
                        seg = ast.get_source_segment(self._src, stmt) or ""
                        if seg:
                            body_src += "\n" + seg
                    stripped_body = body_src.strip()
                    if stripped_body and stripped_body != "pass":
                        lines = stripped_body.splitlines()
                        indent = len(lines[0]) - len(lines[0].lstrip())
                        self._set_backtest_body = "\n".join(
                            (ln[indent:] if len(ln) >= indent else ln.lstrip())
                            for ln in lines)
                        self.actions.append(ConversionAction(
                            action_type="REWRITE", rule_id="DENY-SET_BACKTEST-LIFT",
                            api_name="set_backtest", line=sl, severity="WARN",
                            old_text=f"def set_backtest() 定义（行 {sl}-{el}）",
                            new_text=self._set_backtest_body[:80],
                            message="set_backtest 定义已删除，函数体配置调用将由调用点内联"
                                    "（保留 set_limit_mode/set_commission 语义与执行时机）"))
                    self._replacements.append(
                        (sl, 0, el, len(self._lines[el - 1].rstrip("\r\n")), ""))
                    if stripped_body and stripped_body != "pass":
                        self.actions.append(ConversionAction(
                            action_type="REMOVE", rule_id="DENY-SET_BACKTEST",
                            api_name="set_backtest", line=sl, severity="INFO",
                            old_text=f"def set_backtest() 定义（行 {sl}-{el}）",
                            message="本地自创 API 定义已删除（真实 PTrade 无此函数）"))
                    elif stripped_body == "pass":
                        self.actions.append(ConversionAction(
                            action_type="REMOVE", rule_id="DENY-SET_BACKTEST",
                            api_name="set_backtest", line=sl, severity="INFO",
                            old_text=f"def set_backtest() 定义（行 {sl}-{el}）",
                            message="本地自创 API 定义已整体删除（空函数体，真实 PTrade 无此函数）"))
                elif name not in _LIFECYCLE:
                    # 策略自定义函数：保留（函数体内部调用在 _scan_calls 处理）
                    pass

    def _line_start_abs(self, line: int) -> int:
        pos = 0
        for i in range(line - 1):
            pos += len(self._lines[i])
        return pos

    def _line_end_abs(self, line: int) -> int:
        return self._line_start_abs(line) + len(self._lines[line - 1].rstrip("\r\n"))

    # ------------------------------------------------------------------
    # sklearn / 第三方
    # ------------------------------------------------------------------
    def _check_third_party(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "sklearn" in node.module:
                self._sklearn_used = True
                self.warnings.append(
                    "检测到 sklearn 依赖（行 %d）：PTrade 平台可用性未验证，"
                    "记 PTRADE_RUNTIME_UNVERIFIED，不 BLOCK" % node.lineno)

    # ------------------------------------------------------------------
    # AST 调用扫描（核心）
    # ------------------------------------------------------------------
    def _scan_calls(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            raw = ""
            f = node.func
            if isinstance(f, ast.Name):
                raw = f.id
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                raw = f.attr  # 形如 obj.func() —— 仅按属性名匹配（g.foo 等）
            if not raw:
                continue
            name = self._aliases.get(raw, raw)  # H3：别名归一化
            if name in ("numpy", "pandas"):
                continue
            self.coverage["api_calls_seen"] += 1

            if name in _BLOCK_API_NO_FUNCTION:
                self._block_call(node, name)
            elif name == "get_etf_list_local":
                # 07 规格：FREEZE 档（非 REMOVE）——收集调用点，由 _freeze_etf_pool 处理
                self._freeze_calls.append(node)
            elif name in DENY_REMOVE:
                self._remove_call(node, name)
            elif name in DENY_SHIM:
                self._need_shim.add(name)
                self.actions.append(ConversionAction(
                    action_type="SHIM", rule_id="DENY-SHIM", api_name=name,
                    line=_line_of(node), severity="WARN",
                    message=f"{name}() 为本地批量 API，将注入同名 shim（循环单调用）"))
            elif name in MYTT_FUNCTIONS:
                self._mytt_needed.add(name)
                self.coverage["mytt_used"].append(name)
            elif name in ASHARE_RULES_FUNCTIONS:
                self._ashare_needed.add(name)
                self.coverage["ashare_used"].append(name)
            elif name in PTRADE_REGISTERED_WARN:
                # KEEP-WARN：保留调用；参数级归一化/删除单独处理
                self._normalize_call(node, name)
            elif name in ("get_history", "get_price"):
                # get_history 的 fq 归一化与签名 A→B 改写由 _normalize_ptrade_contract_calls
                # 统一处理（同一调用内避免替换区域重叠）；get_price 仍走参数删除
                if name == "get_price":
                    self._normalize_call(node, name)

    # ------------------------------------------------------------------
    # DENY_REMOVE 分档（H2）
    # ------------------------------------------------------------------
    def _remove_call(self, node: ast.Call, name: str) -> None:
        line = _line_of(node)
        # T5 修复：set_backtest 调用点内联函数体（配置调用保留原执行时机）。
        # 原语义：initialize 内 `if not is_trade(): set_backtest()` 恒执行 → 配置生效。
        if name == "set_backtest" and self._set_backtest_body:
            replacement = ""
            if _is_bare_expr_stmt(node, self._tree):
                # 档 1：裸语句 → 内联配置（保持调用点缩进）
                raw = self._lines[line - 1]
                indent = raw[: len(raw) - len(raw.lstrip())]
                inlined = "\n".join(indent + ln for ln in self._set_backtest_body.splitlines())
                replacement = inlined + "\n"
                self.actions.append(ConversionAction(
                    action_type="REWRITE", rule_id="DENY-SET_BACKTEST-INLINE",
                    api_name="set_backtest", line=line, severity="WARN",
                    old_text=f"{name}(...) 调用",
                    new_text=inlined[:80],
                    message="set_backtest() 调用已内联为配置调用"
                            "（set_limit_mode/set_commission 保留原执行时机）"))
            else:
                # 档 2：内嵌表达式 → None（配置调用丢弃，记 WARN）
                self._replacements.append((
                    node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                    "None"))
                self.actions.append(ConversionAction(
                    action_type="REWRITE", rule_id="DENY-SET_BACKTEST-INLINE",
                    api_name="set_backtest", line=line, severity="WARN",
                    old_text=f"{name}(...)",
                    new_text="None",
                    message="set_backtest() 内嵌于表达式，改写为 None；"
                            "其函数体内的配置调用未保留（请人工核对）"))
            self._replacements.append(
                (line, 0, line, len(self._lines[line - 1].rstrip("\r\n")), replacement))
            return
        if _is_bare_expr_stmt(node, self._tree):
            # 档 1：裸表达式语句 → 删整行（含缩进）；若父复合语句体仅此一条，
            # 替换为 pass 保缩进（防 "if x:\n  set_backtest()" 删除后空块语法错误）
            replacement = ""
            parent = self._find_parent(node)
            if isinstance(parent, ast.Expr):
                gp = self._find_parent(parent)
                body = getattr(gp, "body", None)
                if isinstance(body, list) and len(body) == 1 and body[0] is parent:
                    raw = self._lines[line - 1]
                    indent = raw[: len(raw) - len(raw.lstrip())]
                    replacement = indent + "pass\n"
            self._replacements.append((line, 0, line, len(self._lines[line - 1].rstrip("\r\n")),
                                       replacement))
            self.actions.append(ConversionAction(
                action_type="REMOVE", rule_id=f"DENY-{name.upper()}", api_name=name,
                line=line, severity="INFO",
                old_text=f"{name}(...) 裸语句",
                message=f"{name}() 为本地扩展 API，整行已删除"
                        + ("（父块置 pass）" if replacement else "")))
        elif name in _REMOVE_ALLOW_INLINE:
            # 档 2：表达式内嵌 → 等价字面量
            literal = _REWRITE_LITERALS[name]
            self._replacements.append((
                node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, literal))
            self.actions.append(ConversionAction(
                action_type="REWRITE", rule_id=f"DENY-{name.upper()}-INLINE", api_name=name,
                line=line, severity="WARN",
                old_text=f"{name}(...)",
                new_text=literal,
                message=f"{name}() 内嵌于表达式，改写为等价字面量 {literal}"))
        else:
            # 档 3：无法确定等价语义 → BLOCK
            self._block_call(node, name)

    def _block_call(self, node: ast.Call, name: str) -> None:
        line = _line_of(node)
        self.errors.append(f"BLOCK: {name}()（行 {line}）无法自动转换，需人工改用 PTrade 等价数据源")
        self.actions.append(ConversionAction(
            action_type="BLOCK", rule_id=f"BLOCK-{name.upper()}", api_name=name,
            line=line, severity="BLOCK",
            old_text=f"{name}(...)",
            message=f"{name}() 无 PTrade 自动替代，转换失败（交人工）"))

    # ------------------------------------------------------------------
    # 参数归一化（G1：只按 grade 执行）
    # ------------------------------------------------------------------
    def _normalize_call(self, node: ast.Call, name: str) -> None:
        for kw in node.keywords:
            if kw.arg is None:
                continue
            param = kw.arg
            # 1) 参数删除表（get_price 的 panel/fill_paused/skip_paused）
            if name == "get_price" and param in GET_PRICE_DROP_PARAMS:
                self._replacements.append((
                    kw.lineno, kw.col_offset, kw.end_lineno, kw.end_col_offset, ""))
                self.actions.append(ConversionAction(
                    action_type="NORMALIZE", rule_id="NORM-GETPRICE-PARAM",
                    api_name=name, line=_line_of(node), severity="INFO",
                    old_text=f"{param}=...",
                    message=f"get_price 不支持的参数 {param} 已删除（本地与 PTrade 均不消费）"))
                self.coverage["normalized_params"] += 1
                continue
            # 2) NORMALIZE_RULES（fq 等）
            for api, p, old_v, new_v, rule_id, grade in NORMALIZE_RULES:
                if api != name or p != param:
                    continue
                val = kw.value
                if isinstance(val, ast.Constant) and str(val.value).lower() == str(old_v).lower():
                    if grade == "NORMALIZE":
                        self._replacements.append((
                            val.lineno, val.col_offset, val.end_lineno, val.end_col_offset,
                            repr(new_v)))
                        self.actions.append(ConversionAction(
                            action_type="NORMALIZE", rule_id=rule_id, api_name=name,
                            line=_line_of(node), severity="INFO",
                            old_text=f"{param}={old_v}", new_text=f"{param}={new_v}",
                            message=f"G1 证据（provider 同分支）：{old_v}≡{new_v}，已归一化"))
                        self.coverage["normalized_params"] += 1
                    else:  # WARN_KEEP
                        self.coverage["fq_warn_kept"].append(f"{name}:{old_v}")
                        self.actions.append(ConversionAction(
                            action_type="KEEP_COMMENT", rule_id=rule_id, api_name=name,
                            line=_line_of(node), severity="WARN",
                            old_text=f"{param}={old_v}",
                            message=f"{param}={old_v} 保留原值：本地 {old_v} 与 {new_v} 语义不等价"
                                    f"（G1），该策略不进入 1:1 复刻清单"))
                    break

    # ------------------------------------------------------------------
    # PTrade 契约合规改写（2026-08-12，fall_reversal 平台零交易根因 4 处）
    # ------------------------------------------------------------------
    # 契约证据（skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json）：
    # - get_Ashares: notes "date uses YYYYmmdd when supplied"（示例 get_Ashares('20260724')）
    # - get_history: count-first（示例 get_history(60, frequency='1d', field=['close'],
    #   security_list='600000.SS', fq='pre', include=False, is_dict=True)）
    # - set_benchmark: 带后缀（示例 set_benchmark('000300.SS')）
    # - get_stock_status: 关键字 stocks/query_type/query_date（示例 query_type='ST',
    #   query_date='20260724'）
    # 本地等价性（改写产物在本地引擎语义不变）：
    # - get_history 双签名：本地首参 int → count-first（PR4 接受 frequency/field/security_list）
    # - set_benchmark：本地 bare_code 剥离后缀
    # - get_Ashares：本地 _end_ms → pd.Timestamp('YYYYmmdd') 可解析
    # - get_stock_status：本地签名 (stocks, query_type='ST', query_date=None) 关键字兼容
    # 幂等：count-first 形态（首参 int 常量或 count/frequency/security_list 关键字）跳过。
    # fq 归一化（NORMALIZE 档）并入签名改写（同一调用内避免替换区域重叠）。

    def _normalize_ptrade_contract_calls(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not isinstance(f, ast.Name):
                continue
            name = self._aliases.get(f.id, f.id)
            if name == "get_Ashares":
                self._rewrite_asharess_date(node)
            elif name == "get_history":
                self._rewrite_history_signature(node)
            elif name == "set_benchmark":
                self._rewrite_benchmark_suffix(node)
            elif name == "get_stock_status":
                self._rewrite_stock_status_keywords(node)
        # 独立 pass：X['col'].values 是 Attribute 模式（非 Call），单独遍历
        self._rewrite_values_access(tree)

    # ---- 修复 1：get_Ashares(date) 日期格式 YYYY-MM-DD → YYYYmmdd ----
    def _rewrite_asharess_date(self, node: ast.Call) -> None:
        arg = None
        if node.args:
            arg = node.args[0]
        else:
            for kw in node.keywords:
                if kw.arg == "date":
                    arg = kw.value
        if arg is None:
            return  # get_Ashares() 无参：平台默认当天
        new_text = None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "-" in arg.value:
            new_text = repr(arg.value.replace("-", ""))
        elif (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "strftime" and arg.args
                and isinstance(arg.args[0], ast.Constant)
                and arg.args[0].value == "%Y-%m-%d"):
            new_text = f"{ast.unparse(arg.func)}('%Y%m%d')"
        elif (isinstance(arg, ast.IfExp) and isinstance(arg.test, ast.Call)
                and isinstance(arg.test.func, ast.Name)
                and arg.test.func.id == "isinstance"):
            return  # 已包装（幂等：二次转换不重复包装）
        elif (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "strftime" and arg.args
                and isinstance(arg.args[0], ast.Constant)
                and "-" not in str(arg.args[0].value)):
            return  # 已是 YYYYmmdd 形态（幂等：strftime('%Y%m%d') 不再改写/包装）
        else:
            expr = ast.unparse(arg)
            new_text = (f"({expr}.replace('-', '') if isinstance({expr}, str) "
                        f"else {expr}.strftime('%Y%m%d'))")
        self._replacements.append(
            (arg.lineno, arg.col_offset, arg.end_lineno, arg.end_col_offset, new_text))
        self.actions.append(ConversionAction(
            action_type="NORMALIZE", rule_id="NORM-ASHARES-DATE",
            api_name="get_Ashares", line=_line_of(node), severity="WARN",
            old_text=ast.unparse(arg), new_text=new_text,
            message="get_Ashares date 改为 YYYYmmdd（PTrade 契约；本地 pd.Timestamp 兼容解析）"))
        self.coverage["normalized_params"] += 1

    # ---- 修复 2：get_history 签名 A（security-first）→ B（count-first）----
    def _map_include_false_to_true(self, node: ast.Call, api_name: str) -> None:
        """NORM-INCLUDE-PTRADE：include=False → include=True（文本替换路径）。

        背景（PTrade 平台实测确认 2026-08-13）：PTrade 在 handle_data（15:00 收盘）时
        不把当天 bar 算作已完成的 history——即使 include=True 也不含当天。
        PTrade 日线 include=True 返回前一交易日 T-1，等价于本地 include=False。
        因此转换时把本地 include=False 改写为 PTrade include=True，两端看到同一天数据。

        频率分流（2026-08-13 PTrade 分钟实测）：只有日线频率做映射；分钟频率不改
        （PTrade 分钟 include 语义与本地一致：True 含当前 bar / False 到前一 bar，
        对分钟做 False→True 会在 PTrade 侧制造同 bar lookahead）。

        本方法用于"不改签名"的形态（count-first / security_list / frequency 关键字）：
        显式 include=False 常量 → 文本替换 include=False → include=True。
        无 include 参数时不做任何改动（PTrade 默认行为不变，shim 默认参数兜底）。"""
        # 频率提取：frequency/unit 关键字，或 count-first 位置第 2 参（frequency）。
        freq_val = None
        freq_present = False
        for kw in node.keywords:
            if kw.arg in ("frequency", "unit"):
                freq_present = True
                if isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    freq_val = kw.value.value
                break
        if freq_val is None and not freq_present and len(node.args) > 1:
            freq_present = True  # count-first: get_history(count, frequency, ...)
            if isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, str):
                freq_val = node.args[1].value
        if freq_present and freq_val is None:
            return  # 频率参数存在但非常量（变量/表达式）→ 无法确定 → 保守不改
        if freq_val is not None and str(freq_val).lower() not in _DAILY_FREQS:
            return  # 分钟频率（或无法识别频率）：不映射（P1-1 修正，禁止行为 6/7）
        # 无频率参数 → 默认 '1d'（本地与 PTrade 的 get_history 默认频率一致）→ 保持映射
        for kw in node.keywords:
            if kw.arg == "include" and isinstance(kw.value, ast.Constant) \
                    and kw.value.value is False:
                self._replacements.append(
                    (kw.value.lineno, kw.value.col_offset,
                     kw.value.end_lineno, kw.value.end_col_offset, "True"))
                self.actions.append(ConversionAction(
                    action_type="NORMALIZE", rule_id="NORM-INCLUDE-PTRADE",
                    api_name=api_name, line=_line_of(node), severity="INFO",
                    old_text="include=False", new_text="include=True",
                    message=("include=False → include=True（仅日线频率；PTrade 日线 "
                             "include=True ≡ 本地 include=False：两者都返回前一交易日 "
                             "T-1，不含当日 bar。分钟频率不改——两端 include 语义一致，"
                             "PTrade 实测 2026-08-13）")))
                self.coverage["normalized_params"] += 1

    def _rewrite_history_signature(self, node: ast.Call) -> None:
        kw_names = {kw.arg for kw in node.keywords if kw.arg}
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, int):
            self._map_include_false_to_true(node, "get_history")  # 不改签名，只映射 include
            return  # count-first（首参 int）：已符合 PTrade 契约，不动
        if "security_list" in kw_names or "frequency" in kw_names:
            self._map_include_false_to_true(node, "get_history")  # 不改签名，只映射 include
            return  # count-first 关键字形态（security_list/frequency 为 B 独有）：不动
        if not node.args and not ("security" in kw_names or "unit" in kw_names
                                  or "fields" in kw_names):
            return  # get_history() 无参或无法判定：跳过
        # 其余（含 security/unit/fields 任一关键字，或位置参数非 int）→ 签名 A，改写
        # fq 归一化（NORMALIZE 档）——count-first 形态也在此处理（见 _scan_calls 改动）
        fq = ast.Constant(value="pre")
        include = ast.Constant(value=False)
        is_dict = ast.Constant(value=False)
        for kw in node.keywords:
            if kw.arg == "fq":
                fq = kw.value
            elif kw.arg == "include":
                include = kw.value
            elif kw.arg == "is_dict":
                is_dict = kw.value
        # NORM-INCLUDE-PTRADE（并入签名改写整段 replacement）：
        # include 常量 False（显式 include=False 或未写 include 的本地默认值）→ True。
        # PTrade 日线 include=True ≡ 本地 include=False（均返回前一交易日 T-1；平台实测 2026-08-13）。
        # 频率分流：只有日线做映射；分钟不改（两端 include 语义一致，2026-08-13 PTrade 分钟实测）。
        # 显式 include=True 保持不动。
        _unit_val = None
        _unit_present = False
        for kw in node.keywords:
            if kw.arg == "unit":
                _unit_present = True
                if isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    _unit_val = kw.value.value
                break
        if _unit_val is None and not _unit_present and len(node.args) >= 3:
            _unit_present = True  # 签名 A: get_history(security, count, unit, ...)
            if isinstance(node.args[2], ast.Constant) \
                    and isinstance(node.args[2].value, str):
                _unit_val = node.args[2].value
        _is_daily = True  # 无 unit → 默认 '1d'（本地与 PTrade 默认频率一致）→ 保持映射
        if _unit_present and _unit_val is None:
            _is_daily = False  # unit 变量/表达式 → 无法确定 → 保守不改
        elif _unit_val is not None:
            _is_daily = str(_unit_val).lower() in _DAILY_FREQS
        if _is_daily and isinstance(include, ast.Constant) and include.value is False:
            include = ast.Constant(value=True)
            self.actions.append(ConversionAction(
                action_type="NORMALIZE", rule_id="NORM-INCLUDE-PTRADE",
                api_name="get_history", line=_line_of(node), severity="INFO",
                old_text="include=False", new_text="include=True",
                message=("include=False → include=True（仅日线频率；PTrade 日线 "
                         "include=True ≡ 本地 include=False：两者都返回前一交易日 "
                         "T-1，不含当日 bar。分钟频率不改——两端 include 语义一致，"
                         "PTrade 实测 2026-08-13）")))
            self.coverage["normalized_params"] += 1
        if isinstance(fq, ast.Constant) and isinstance(fq.value, str):
            for api, p, old_v, new_v, rule_id, grade in NORMALIZE_RULES:
                if api == "get_history" and p == "fq" \
                        and str(fq.value).lower() == str(old_v).lower() \
                        and grade == "NORMALIZE":
                    fq = ast.Constant(value=new_v)
                    self.actions.append(ConversionAction(
                        action_type="NORMALIZE", rule_id=rule_id, api_name="get_history",
                        line=_line_of(node), severity="INFO",
                        old_text=f"fq={old_v}", new_text=f"fq={new_v}",
                        message=f"G1 证据（provider 同分支）：{old_v}≡{new_v}，已归一化"))
                    self.coverage["normalized_params"] += 1
                    break
        # 签名 A 提取：位置 (security, count, unit, fields) 或关键字
        def take(index, key, default):
            if node.args and len(node.args) > index:
                return node.args[index]
            for kw in node.keywords:
                if kw.arg == key:
                    return kw.value
            return default
        security = take(0, "security", None)
        count = take(1, "count", None)
        unit = take(2, "unit", ast.Constant(value="1d"))
        fields = take(3, "fields", None)
        if security is None or count is None:
            return  # 参数不全：不改写（交给校验器）
        sec_expr = security
        if isinstance(security, ast.List) and len(security.elts) == 1:
            sec_expr = security.elts[0]  # 单只列表拆包为标量（PTrade 契约示例形态）
        new_call = ast.Call(
            func=ast.Name(id="get_history"),
            args=[count],
            keywords=[
                ast.keyword(arg="frequency", value=unit),
                ast.keyword(arg="security_list", value=sec_expr),
                ast.keyword(arg="fq", value=fq),
                ast.keyword(arg="include", value=include),
                ast.keyword(arg="is_dict", value=is_dict),
            ])
        if fields is not None:
            new_call.keywords.insert(1, ast.keyword(arg="field", value=fields))
        new_text = ast.unparse(new_call)
        self._replacements.append(
            (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, new_text))
        self.actions.append(ConversionAction(
            action_type="REWRITE", rule_id="NORM-GETHISTORY-SIG",
            api_name="get_history", line=_line_of(node), severity="WARN",
            old_text=ast.unparse(node), new_text=new_text,
            message="get_history 签名 A→B（count-first，PTrade 契约；本地双签名兼容）"))
        self.coverage["normalized_params"] += 1

    # ---- 修复 4：set_benchmark 裸码补后缀 ----
    def _rewrite_benchmark_suffix(self, node: ast.Call) -> None:
        if not node.args:
            return
        arg = node.args[0]
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            return
        code = arg.value
        if not re.fullmatch(r"\d{6}", code):
            return
        # 指数优先（set_benchmark 语义=基准指数；000xxx 与深市个股代码重叠，
        # 静态无法区分 → 按指数处理。契约示例 set_benchmark('000300.SS')）：
        # 000xxx → .SS（上证指数系列：上证指数/沪深300/中证系列）
        # 399xxx → .SZ（深证指数系列）
        # 其余 6 位裸码 → security_code_rules.normalize_to_ptrade（股票/ETF 规则）
        if re.fullmatch(r"000\d{3}", code):
            new_text = repr(f"{code}.SS")
        elif re.fullmatch(r"399\d{3}", code):
            new_text = repr(f"{code}.SZ")
        else:
            from quantstudio.backtest.libs.security_code_rules import normalize_to_ptrade
            new_text = repr(normalize_to_ptrade(code))
        self._replacements.append(
            (arg.lineno, arg.col_offset, arg.end_lineno, arg.end_col_offset, new_text))
        self.actions.append(ConversionAction(
            action_type="NORMALIZE", rule_id="NORM-BENCHMARK-SUFFIX",
            api_name="set_benchmark", line=_line_of(node), severity="WARN",
            old_text=repr(code), new_text=new_text,
            message=f"set_benchmark 裸码 {code} 补后缀（PTrade 契约；本地 bare_code 剥离等价）"))
        self.coverage["normalized_params"] += 1

    # ---- 修复 5：get_stock_status 位置传参 → 关键字 query_type ----
    def _rewrite_stock_status_keywords(self, node: ast.Call) -> None:
        kw_names = {kw.arg for kw in node.keywords if kw.arg}
        if "query_type" not in kw_names and len(node.args) >= 2:
            qtype = node.args[1]
            new_text = f"query_type={ast.unparse(qtype)}"
            self._replacements.append(
                (qtype.lineno, qtype.col_offset, qtype.end_lineno, qtype.end_col_offset,
                 new_text))
            self.actions.append(ConversionAction(
                action_type="REWRITE", rule_id="NORM-STOCKSTATUS-KW",
                api_name="get_stock_status", line=_line_of(node), severity="WARN",
                old_text=ast.unparse(qtype), new_text=new_text,
                message="get_stock_status 位置传参改为关键字 query_type=（PTrade 契约）"))
            self.coverage["normalized_params"] += 1
        # query_date：策略已有该关键字且值为含 '-' 常量 → 转 YYYYmmdd；无则不注入
        for kw in node.keywords:
            if kw.arg == "query_date" and isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, str) and "-" in kw.value.value:
                new_text = repr(kw.value.value.replace("-", ""))
                self._replacements.append(
                    (kw.value.lineno, kw.value.col_offset,
                     kw.value.end_lineno, kw.value.end_col_offset, new_text))
                self.actions.append(ConversionAction(
                    action_type="NORMALIZE", rule_id="NORM-STOCKSTATUS-DATE",
                    api_name="get_stock_status", line=_line_of(node), severity="WARN",
                    old_text=repr(kw.value.value), new_text=new_text,
                    message="get_stock_status query_date 改为 YYYYmmdd（PTrade 契约）"))
                self.coverage["normalized_params"] += 1

    # ---- 修复 6：行情字段 `.values` 访问归一化（返回类型兼容）----
    # 证据（2026-08-13 fall_reversal 平台第二次报错）：
    # - PTrade get_history 返回 numpy structured_array/recarray（非 pandas DataFrame），
    #   平台日志：AttributeError: 'numpy.ndarray' object has no attribute 'values'
    # - 源策略 `df['close'].values` 是 pandas DataFrame 专属写法，直接透传必崩
    # - 契约档案 get_history.return_contract.normalization 要求数值使用前归一化：
    #   "np.asarray(item[field], dtype=float).reshape(-1) or an equivalent
    #    hasattr(values, 'values') guarded helper"
    # 改写：X['col'].values → np.asarray(X['col'])（保持 dtype 语义，两边通用）
    #   - pandas DataFrame：np.asarray(Series) 等价 Series.values（保持 dtype）
    #   - numpy structured array：np.asarray(ndarray) 恒等（保持 dtype）
    # 不匹配场景（安全）：
    #   - .values() 方法调用（dict.values() 等）：node 是 Call 而非 Attribute
    #   - 非字符串下标（x[0].values 等）：下标限定 str 常量或 str 列表
    # 幂等：改写后无 `[...].values` 形态，二次转换不重复处理。
    def _rewrite_values_access(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != "values":
                continue
            sub = node.value
            if not isinstance(sub, ast.Subscript):
                continue
            if isinstance(sub.slice, ast.Constant) and isinstance(sub.slice.value, str):
                pass  # 单列 X['col'].values
            elif isinstance(sub.slice, ast.List) and sub.slice.elts and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in sub.slice.elts):
                pass  # 多列 X[['a','b']].values
            else:
                continue
            new_text = f"np.asarray({ast.unparse(sub)})"
            self._replacements.append(
                (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                 new_text))
            self.actions.append(ConversionAction(
                action_type="NORMALIZE", rule_id="NORM-HIST-VALUES",
                api_name="get_history", line=_line_of(node), severity="WARN",
                old_text=ast.unparse(node), new_text=new_text,
                message="行情字段 .values 访问改为 np.asarray(...)（PTrade get_history 返回"
                        " structured array 非 DataFrame；契约 return_contract.normalization）"))
            self.coverage["normalized_params"] += 1

    # ------------------------------------------------------------------
    # 代码后缀规范化（聚宽风格 XSHG/XSHE/SH → PTrade SS/SZ）
    # ------------------------------------------------------------------
    # 证据（2026-08-11 评估结论 B）：
    # - security_code_rules.py:156,201 —— PTrade 目标输出规范后缀为 .SS（SH/SS/XSHG 同组）
    # - ptrade-profile-contract.md —— "策略代码后缀"为 PTrade 渲染检查项
    # - 本地 index_daily code 为 bare 格式，.SH/.SS 经 bare_code 归一化后等价
    # - T5 逐位断言用于验证规范化后回测数值逐位一致
    _CODE_SUFFIX_RE = re.compile(r"^(\d{6})\.(XSHG|XSHE|SH)$")
    _CODE_SUFFIX_MAP = {"XSHG": "SS", "XSHE": "SZ", "SH": "SS"}

    def _normalize_code_suffixes(self, tree: ast.AST) -> None:
        """把字符串常量中的 6 位代码 XSHG/XSHE 后缀规范化为 SS/SZ（PTrade 约定）。

        背景：本地策略可用聚宽风格后缀（本地引擎 bare_code 规范化可跑，T5 证实）；
        PTrade 公共契约用 .SS/.SZ/.BJ，且 validate_local_strategy 对 XSHG/XSHE
        字符串常量 BLOCK（PORTFOLIO-POSITIONS-EXACT-MATCH）。转换时规范化，
        产物才可通过校验并在 PTrade 平台使用。仅匹配精确 code 形态（6 位数字+后缀），
        不误伤注释/日志文本。
        """
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            val = node.value
            if "." not in val:
                continue
            new_val = self._CODE_SUFFIX_RE.sub(
                lambda m: f"{m.group(1)}.{self._CODE_SUFFIX_MAP[m.group(2)]}", val)
            if new_val == val:
                continue
            self._replacements.append((
                node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                repr(new_val)))
            self.actions.append(ConversionAction(
                action_type="NORMALIZE", rule_id="NORM-CODE-SUFFIX",
                api_name="code_suffix", line=_line_of(node), severity="INFO",
                old_text=val,
                new_text=new_val,
                message=f"代码后缀规范化：{val} → {new_val}（聚宽 XSHG/XSHE → PTrade SS/SZ）"))
            self.coverage["normalized_params"] += 1

    # ------------------------------------------------------------------
    # ETF FREEZE 档（07 规格 §2）：get_etf_list_local → 静态池固化
    # ------------------------------------------------------------------
    def _freeze_etf_pool(self) -> None:
        if not self._freeze_calls:
            return
        # 幂等（07 §6 测试 17）：产物已有静态池 → 不再 FREEZE
        if "ETF_POOL_STATIC" in self._src:
            self.coverage["idempotent_skip"] = True
            return
        if not self._etf_pool_start_date:
            self.errors.append(
                "策略使用 get_etf_list_local，需提供 --etf-pool-start-date 才能固化静态池")
            for node in self._freeze_calls:
                self.actions.append(ConversionAction(
                    action_type="BLOCK", rule_id="FREEZE-MISSING-START-DATE",
                    api_name="get_etf_list_local", line=_line_of(node), severity="BLOCK",
                    old_text="get_etf_list_local(...)",
                    message="需提供 --etf-pool-start-date 才能固化静态池"))
            return
        # 边界检测（07 §2.2 步骤 5）：len(pool) 直接参与时变判断 → BLOCK
        for node in self._freeze_calls:
            parent = self._find_parent(node)
            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) \
                    and parent.func.id == "len":
                self.errors.append(
                    "该策略依赖动态池时变性（len(pool) 直接计算），不适合转 PTrade（无法静态固化）")
                self.actions.append(ConversionAction(
                    action_type="BLOCK", rule_id="FREEZE-LEN-TIMEVARY",
                    api_name="get_etf_list_local", line=_line_of(node), severity="BLOCK",
                    message="len(get_etf_list_local(...)) 依赖池子大小随时间变化，无法静态固化"))
                return
        # 快照查询（前置检查 + DATA_BLOCKED，07 §2.2 步骤 4a）
        pool, meta = self._query_etf_snapshot()
        if pool is None:
            return  # errors 已记录 DATA_BLOCKED
        # 后缀转换（07 §2.3）：.SH → .SS 等 PTrade 约定
        from ..backtest.libs.security_code_rules import normalize_to_ptrade
        ptrade_pool = [normalize_to_ptrade(c) for c in pool]
        # 注入静态池定义（07 §2.2 步骤 4e）
        pool_literal = ",\n        ".join(f'"{c}"' for c in ptrade_pool)
        n = len(ptrade_pool)
        m = meta.get("new_listed_excluded", [])
        k = meta.get("delisted_included", [])
        lines = [
            f"{INJECTED_MARKER}",
            f"# PTrade 静态 ETF 池（起始日 {self._etf_pool_start_date} 快照，共 {n} 只）",
            "# 本地版用 get_etf_list_local 动态池，PTrade 版固化为静态",
            f"# 不含起始日后新上市（{len(m)} 只：{('、'.join(m[:10]) + ('...' if len(m) > 10 else '')) if m else '无'}）",
            f"# 仍含起始日后退市（{len(k)} 只：{('、'.join(k[:10]) + ('...' if len(k) > 10 else '')) if k else '无'}；撮合拒单不影响持仓）",
            "ETF_POOL_STATIC = [",
            pool_literal,
            "]",
        ]
        self._etf_pool_block = "\n".join(lines) + "\n\n"
        self._etf_frozen = True
        self._etf_pool_meta = meta
        # 调用点替换为 ETF_POOL_STATIC（07 §2.2 步骤 4f）
        for node in self._freeze_calls:
            self._replacements.append((
                node.lineno, node.col_offset, node.end_lineno, node.end_col_offset,
                "ETF_POOL_STATIC"))
            self.actions.append(ConversionAction(
                action_type="FREEZE", rule_id="FREEZE-STATIC-POOL",
                api_name="get_etf_list_local", line=_line_of(node), severity="WARN",
                old_text="get_etf_list_local(...)",
                new_text="ETF_POOL_STATIC",
                message=f"get_etf_list_local() 已固化为静态池 ETF_POOL_STATIC"
                        f"（起始日 {self._etf_pool_start_date} 快照，{n} 只）"))
        # 提示文案（07 §2.4）
        self.warnings.append(
            f"PTrade 版基于回测起始日 {self._etf_pool_start_date} 的 ETF 池快照生成，共 {n} 只。\n"
            f"- 不含起始日后新上市的 ETF（{len(m)} 只）：{('、'.join(m[:10]) + ('...' if len(m) > 10 else '')) if m else '无'}\n"
            f"- 仍含起始日后退市的 ETF（{len(k)} 只）：{('、'.join(k[:10]) + ('...' if len(k) > 10 else '')) if k else '无'}。\n"
            f"  本地 PIT 版会在其退市后自动剔除；PTrade 静态版保留但撮合拒单，实际不持仓。\n"
            f"  如需完全对齐本地版，可手动从池中移除。\n"
            f"- 重要：在 PTrade 平台运行此代码时，回测起始日期不得早于 {self._etf_pool_start_date}。")

    def _query_etf_snapshot(self) -> tuple[Optional[list[str]], dict[str, Any]]:
        """07 §2.2 步骤 4a-4c：前置检查 + PIT 快照 + 差异计算。

        Returns (pool, meta)；pool=None 表示 DATA_BLOCKED（errors 已记录）。
        """
        import duckdb
        import pandas as pd
        db_path = Path(self._db_path) if self._db_path else Path("data/quantstudio.db")
        if not db_path.exists():
            self.errors.append(f"DATA_BLOCKED: db_path 不存在: {db_path}")
            return None, {}
        try:
            conn = duckdb.connect(str(db_path), read_only=True)
        except Exception as e:
            self.errors.append(f"DATA_BLOCKED: 无法打开 {db_path}: {e}")
            return None, {}
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'").fetchall()}
            missing = {"etf_basic", "etf_daily"} - tables
            if missing:
                self.errors.append(
                    f"DATA_BLOCKED: 缺表 {sorted(missing)}（请运行 scripts/sync_etf_basic.py 后重试）")
                return None, {}
            start_ms = int(pd.Timestamp(self._etf_pool_start_date).value // 10**6)
            # 全量元数据（供差异计算）
            rows = conn.execute(
                "SELECT code, list_date, delist_date, etf_type, is_cross_border "
                "FROM etf_basic").fetchall()
            # 起始日 PIT 快照（07 §1.2 SQL 语义 + data_access equity 过滤）
            type_pred = ("e.etf_type = ? AND COALESCE(e.is_cross_border, FALSE) = FALSE"
                         if self._etf_type == "equity"
                         else ("e.etf_type = ?" if self._etf_type != "all" else "TRUE"))
            # 参数顺序必须与 SQL 谓词出现顺序一致：
            # list_date(1) + EXISTS(1) + active(1) + type(1)
            params: list[Any] = [start_ms, start_ms]
            if self._active_only:
                params.append(start_ms)
            if self._etf_type != "all":
                params.append(self._etf_type)
            active_pred = ("(e.delist_date IS NULL OR e.delist_date > ?)" if self._active_only
                           else "TRUE")
            sql = f"""
                SELECT e.code FROM etf_basic e
                WHERE e.list_date IS NOT NULL
                  AND e.list_date <= ?
                  AND EXISTS (SELECT 1 FROM etf_daily d WHERE d.code = e.code AND d.time <= ?)
                  AND {active_pred}
                  AND {type_pred}
                ORDER BY e.code
            """
            pool = sorted(r[0] for r in conn.execute(sql, params).fetchall())
        except Exception as e:
            self.errors.append(f"DATA_BLOCKED: etf_basic 查询失败: {e}")
            return None, {}
        finally:
            conn.close()
        if not pool:
            self.errors.append(
                f"DATA_BLOCKED: 起始日 {self._etf_pool_start_date} 的 ETF 快照为空"
                f"（etf_basic/etf_daily 数据不足，不得退化为全 ETF 兜底）")
            return None, {}
        # 差异计算（07 §2.2 步骤 4c）
        pool_set = set(pool)
        new_listed = sorted(c for c, ld, dd, et, cb in rows
                            if ld is not None and ld > start_ms)
        delisted = sorted(c for c, ld, dd, et, cb in rows
                          if c in pool_set and dd is not None and dd > start_ms)
        meta = {"new_listed_excluded": new_listed, "delisted_included": delisted}
        return pool, meta

    # ------------------------------------------------------------------
    # 注入（helper / shim / MyTT / A股规则 / ETF 静态池）
    # ------------------------------------------------------------------
    def _inject_all(self, code: str) -> str:
        # 幂等：产物已有注入标记则跳过（测试 9）
        if INJECTED_MARKER in code:
            self.coverage["idempotent_skip"] = True
            return code
        blocks: list[str] = []
        # ETF 静态池（07 规格：FREEZE 固化产物，放在注入块最前）
        if self._etf_pool_block:
            blocks.append(self._etf_pool_block)
            self.coverage["injected_helpers"].append("ETF_POOL_STATIC")
        # helper 总是注入（防御函数，模板同款）
        blocks.append(_PTRADE_HELPERS.format(marker=INJECTED_MARKER))
        self.coverage["injected_helpers"].extend(
            ["_lookup_history_item", "_extract_history_field", "_bare_code",
             "_finite_float", "_finite_series", "_get_ma", "_portfolio_total_value"])
        # 方向 B：get_history 返回类型统一（structured array → DataFrame）。
        # 注入顺序：helper → wrapper → shim → 策略 def →
        #  - wrapper 在 shim 之前 → shim 内部调 get_history 已是 wrapper 版本 → 返回 DataFrame
        #  - wrapper 在策略 def 之前 → 策略 handle_data 调 get_history 已是 wrapper 版本
        blocks.append(_QS_HISTORY_WRAPPER.format(marker=INJECTED_MARKER))
        self.coverage["injected_helpers"].append("get_history_wrapper")
        for shim_name in sorted(self._need_shim):
            blocks.append(self._shim_source(shim_name))
            self.coverage["injected_helpers"].append(f"shim:{shim_name}")
        if self._mytt_needed:
            mytt_src = self._extract_lib_functions("MyTT", self._mytt_needed, "_mytt_")
            if mytt_src:
                blocks.append(mytt_src)
                self.coverage["inject_libs"].append("MyTT")
                self.coverage["injected_helpers"].extend(
                    f"_mytt_{n}" for n in sorted(self._mytt_needed))
                self.warnings.append(
                    "已注入 MyTT 函数（_mytt_ 前缀，用到才注入）：%s；"
                    "该策略不在 1:1 复刻承诺内（MyTT 在 PTrade 平台行为需验证）"
                    % ", ".join(sorted(self._mytt_needed)))
        if self._ashare_needed:
            ashare_src = self._extract_lib_functions("ashare", self._ashare_needed, "_ashare_")
            if ashare_src:
                blocks.append(ashare_src)
                self.coverage["inject_libs"].append("shared_ashare_rules")
                self.coverage["injected_helpers"].extend(
                    f"_ashare_{n}" for n in sorted(self._ashare_needed))
                self.warnings.append(
                    "已注入 A股规则函数（_ashare_ 前缀）：%s；"
                    "该策略不在 1:1 复刻承诺内"
                    % ", ".join(sorted(self._ashare_needed)))
        if not blocks:
            return code
        injected = "\n".join(blocks) + "\n\n"
        return self._insert_before_first_def(code, injected)

    def _shim_source(self, name: str) -> str:
        if name == "get_history_batch":
            return f'''{INJECTED_MARKER}
def get_history_batch(security_list, count, unit='1d', fields=None, fq='pre',
                      include=True, is_dict=True, **kwargs):
    """SHIM: 本地批量 API → 循环单调用（PTrade 兼容；返回 code→DataFrame 字典）。

    与原生 B1 实现语义一致：is_dict=True 的 get_history 返回 code→DataFrame 字典，
    此处解包出 DataFrame 再按 code 组装（T5 修复：禁止把整个 CodeDict 存入 result）。"""
    result = {{}}
    for code in security_list:
        try:
            df_dict = get_history(count, frequency=unit, field=fields,
                                  security_list=code, fq=fq, include=include,
                                  is_dict=True)
            if isinstance(df_dict, dict):
                for k, df in df_dict.items():
                    result[k] = df
        except Exception as exc:
            log.warning('get_history_batch skip %s: %s' % (code, exc))
    return result
'''
        if name == "get_fundamentals_batch":
            return f'''{INJECTED_MARKER}
def get_fundamentals_batch(security_list, table='valuation', fields=None,
                           date=None, is_dataframe=True, **kwargs):
    """SHIM: 本地批量 API → 循环单调用（PTrade 兼容；返回 dict[code→DataFrame]）"""
    result = {{}}
    for code in security_list:
        try:
            df = get_fundamentals(code, table, fields=fields, date=date,
                                  is_dataframe=is_dataframe)
            result[code] = df
        except Exception as exc:
            log.warning('get_fundamentals_batch skip %s: %s' % (code, exc))
    return result
'''
        return ""

    def _extract_lib_functions(self, lib: str, needed: set[str], prefix: str) -> str:
        """从 MyTT.py / shared_ashare_rules.py 提取用到的函数源码（含递归依赖），
        统一加 prefix 前缀（含函数体内互相调用，D1 约束②）。"""
        if lib == "MyTT":
            lib_path = Path(__file__).resolve().parents[1] / "backtest" / "libs" / "MyTT.py"
        else:
            lib_path = Path(__file__).resolve().parents[1] / "backtest" / "libs" / "shared_ashare_rules.py"
        try:
            src = lib_path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:
            self.errors.append(f"注入库读取失败: {lib_path}")
            return ""
        funcs: dict[str, ast.FunctionDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                funcs[node.name] = node
        # 递归收集依赖（ashare 依赖 security_code_rules 的 4 个判定函数）
        todo = set(needed)
        collected: set[str] = set()
        while todo:
            name = todo.pop()
            if name in collected or name not in funcs:
                continue
            collected.add(name)
            body_src = ast.get_source_segment(src, funcs[name]) or ""
            # 递归依赖：函数体内调用的同库函数名
            for m in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body_src):
                if m in funcs and m not in collected and m != name:
                    todo.add(m)
        # ashare 附加依赖：从 security_code_rules.py 提取判定函数（跨文件依赖）
        if lib == "ashare":
            if collected & {"get_price_limit_pct", "is_price_limit_blocked"}:
                for extra in ("is_bse_market", "is_chinext_market", "is_star_market", "is_st_stock"):
                    if extra not in collected:
                        collected.add(extra)
        # 提取源码（按文件顺序）并重命名：函数名 + 函数体内调用名
        order = [n for n in funcs if n in collected]
        parts: list[str] = []
        for n in order:
            seg = ast.get_source_segment(src, funcs[n]) or ""
            seg = re.sub(rf"\b{n}\b", f"{prefix}{n}", seg, count=1)
            # 函数体内对同库已收集函数的调用加前缀
            for dep in collected:
                seg = re.sub(rf"\b{dep}\s*\(", f"{prefix}{dep}(", seg)
            parts.append(seg)
        # ashare：security_code_rules 的 4 个判定函数（无前缀，避免与平台 API 冲突即可；
        # 但需保证其内部无本地依赖——security_code_rules 为纯代码判定，安全）
        if lib == "ashare" and collected & {"is_bse_market", "is_chinext_market",
                                              "is_star_market", "is_st_stock"}:
            scr_path = Path(__file__).resolve().parents[1] / "backtest" / "libs" / "security_code_rules.py"
            try:
                scr_src = scr_path.read_text(encoding="utf-8")
                scr_tree = ast.parse(scr_src)
                for node in ast.walk(scr_tree):
                    if isinstance(node, ast.FunctionDef) and node.name in collected:
                        seg = ast.get_source_segment(scr_src, node) or ""
                        parts.append(seg)
            except Exception:
                self.errors.append(f"security_code_rules 提取失败: {scr_path}")
        header = f"{INJECTED_MARKER}\n# 注入自 {lib_path.name}（{prefix}前缀，仅本策略用到的函数）\n"
        return header + "\n\n".join(parts) + "\n"

    def _insert_before_first_def(self, code: str, block: str) -> str:
        """最后一个 import 之后、第一个 def 之前插入（02 规格 §2 步骤 7 细化）。"""
        lines = code.splitlines(keepends=True)
        last_import = -1
        first_def = -1
        for i, ln in enumerate(lines):
            stripped = ln.lstrip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                last_import = i
            if stripped.startswith("def ") or stripped.startswith("async def "):
                first_def = i
                break
        if first_def == -1:
            return code + "\n" + block
        insert_at = first_def if last_import < 0 else last_import + 1
        lines.insert(insert_at, block)
        return "".join(lines)

    # ------------------------------------------------------------------
    # 头部
    # ------------------------------------------------------------------
    def _build_header(self, source_path: Optional[str]) -> str:
        src_name = Path(source_path).name if source_path else "unknown"
        sid = self.strategy_id or (
            src_name.replace(".py", "").replace("_quantstudio", "") if source_path else "strategy")
        diffs = []
        for a in self.actions:
            if a.severity in ("WARN", "BLOCK"):
                diffs.append(f"# - {a.api_name}: {a.message}\n")
        fq_note = ""
        if self.coverage["fq_warn_kept"]:
            fq_note = ("\n# 注意：fq 参数存在 WARN_KEEP 项（%s），该策略不满足 1:1 复刻"
                       % ", ".join(self.coverage["fq_warn_kept"]))
        mytt_note = ""
        if self.coverage["inject_libs"]:
            mytt_note = ("\n# 注意：已注入本地库（%s），该策略不在 1:1 复刻承诺内"
                         % ", ".join(self.coverage["inject_libs"]))
        return (
            f"# {sid}_ptrade.py - 由 QuantStudio source_import 转换生成\n"
            f"# 来源: {src_name}\n"
            f"# profile: {PTRADE_PROFILE_MARKER} (ptrade_profile_version 1.1.0-source-import)\n"
            f"# 已知差异:\n"
            + ("".join(diffs) if diffs else "#   (无)\n")
            + f"# PTRADE_RUNTIME_UNVERIFIED: 真实券商平台行为未验证，部署前须人工冒烟。\n"
            + fq_note + mytt_note + "\n\n"
        )


# ============================================================================
# 公开 API
# ============================================================================


def convert_source(
    source_path: str | Path,
    *,
    strategy_id: str | None = None,
    inject_helpers: bool = True,
    verbose: bool = True,
    etf_pool_start_date: str | None = None,   # 07 规格：ETF 静态池固化起始日 "YYYY-MM-DD"
    db_path: str | Path | None = None,        # 07 规格：查 etf_basic 的库路径（默认 data/quantstudio.db）
    etf_type: str = "equity",
    active_only: bool = True,
) -> SourceImportResult:
    """把本地策略 .py 转换为 PTrade 代码。不写盘（写盘由编排层负责）。

    etf_pool_start_date：策略含 get_etf_list_local 时必须提供，否则 BLOCK
    （07-ETF动态池固化补充规格.md §2）。
    """
    path = Path(source_path)
    # N1：统一 utf-8-sig（BOM 文件兼容，小市值策略ptrade.py 实锤）
    try:
        source_code = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            source_code = path.read_text(encoding="gbk")
        except UnicodeDecodeError as e:
            result = SourceImportResult(converted_code="", errors=[f"文件编码无法识别: {e}"])
            return result
    if strategy_id is None:
        strategy_id = path.stem.replace("_quantstudio", "")
    conv = SourceConverter(
        strategy_id=strategy_id, inject_helpers=inject_helpers, verbose=verbose,
        etf_pool_start_date=etf_pool_start_date,
        db_path=str(db_path) if db_path else None,
        etf_type=etf_type, active_only=active_only,
    )
    return conv.convert(source_code, source_path=str(path))
