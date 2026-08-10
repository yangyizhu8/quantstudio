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

# 档 2 表达式内嵌的等价字面量（H2）：本地函数 → PTrade 语义等价字面量
_REWRITE_LITERALS: dict[str, str] = {
    "set_backtest": "None",
    "is_trade": "False",
}

# DENY_REMOVE 中允许档 2 改写的函数；其余 DENY_REMOVE 档 2 → BLOCK
_REMOVE_ALLOW_INLINE: frozenset[str] = frozenset(_REWRITE_LITERALS.keys())

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
                 verbose: bool = True):
        self.strategy_id = strategy_id
        self.inject_helpers = inject_helpers
        self.verbose = verbose
        self.actions: list[ConversionAction] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
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
                    start = self._line_start_abs(sl)
                    end = self._line_end_abs(el)
                    self._replacements.append((sl, 0, el, len(self._lines[el - 1].rstrip("\r\n")),
                                               ""))
                    self.actions.append(ConversionAction(
                        action_type="REMOVE", rule_id="DENY-SET_BACKTEST",
                        api_name="set_backtest", line=sl, severity="INFO",
                        old_text=f"def set_backtest() 定义（行 {sl}-{el}）",
                        message="本地自创 API 定义已整体删除（真实 PTrade 无此函数）"))
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
                self._normalize_call(node, name)

    # ------------------------------------------------------------------
    # DENY_REMOVE 分档（H2）
    # ------------------------------------------------------------------
    def _remove_call(self, node: ast.Call, name: str) -> None:
        line = _line_of(node)
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
    # 注入（helper / shim / MyTT / A股规则）
    # ------------------------------------------------------------------
    def _inject_all(self, code: str) -> str:
        # 幂等：产物已有注入标记则跳过（测试 9）
        if INJECTED_MARKER in code:
            self.coverage["idempotent_skip"] = True
            return code
        blocks: list[str] = []
        # helper 总是注入（防御函数，模板同款）
        blocks.append(_PTRADE_HELPERS.format(marker=INJECTED_MARKER))
        self.coverage["injected_helpers"].extend(
            ["_lookup_history_item", "_extract_history_field", "_bare_code",
             "_finite_float", "_finite_series", "_get_ma", "_portfolio_total_value"])
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
                      include=False, is_dict=True, **kwargs):
    """SHIM: 本地批量 API → 循环单调用（PTrade 兼容；返回 dict[code→DataFrame]）"""
    result = {{}}
    for code in security_list:
        try:
            df = get_history(count, unit, field=fields, security_list=code,
                             fq=fq, include=include, is_dict=True)
            if df is not None:
                result[code] = df
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
) -> SourceImportResult:
    """把本地策略 .py 转换为 PTrade 代码。不写盘（写盘由编排层负责）。"""
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
    conv = SourceConverter(strategy_id=strategy_id, inject_helpers=inject_helpers, verbose=verbose)
    return conv.convert(source_code, source_path=str(path))
