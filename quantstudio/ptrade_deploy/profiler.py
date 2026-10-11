# -*- coding: utf-8 -*-
"""P1 策略画像段（对应设计文档 docs/ptrade-deploy-pipeline-design.md §5.2 P1）。

职责：对回测策略源码做静态画像，输出 profile dict 供 P2 交叉校验 / P3 变换 /
P5 门禁消费：
- 频率档位（日线/分钟）识别：set_backtest/frequency 调用与 handle_data 形态推断
  （get_history/get_price 的 frequency 实参、模块级 FREQUENCY 常量）；
  无法判定 → unknown，要求 deploy.yaml 显式指定执行档位（P2 交叉校验兜底）；
- 生命周期函数清单（initialize/before_trading_start/handle_data/after_trading_end）；
- 股票池来源（静态字面量 / 动态变量·函数调用 / 无）；
- 订单 API 家族清单（order/order_target/order_value/order_target_value/order_market）；
- g.* 全局变量读写清单 + 可序列化性静态判定：
  字面量/容器/名称绑定 → 可序列化（ok）；
  显式类实例化 / open() 句柄 / IO 赋值 → 需豁免标记（exempt_needed，P3 改 g.__ 前缀）。
"""
from __future__ import annotations

import ast
from typing import Dict, List, Optional, Tuple

from .preflight import call_name, load_contract

#: 标准生命周期函数（PTrade 框架回调）
LIFECYCLE_FUNCS = ("initialize", "before_trading_start", "handle_data", "after_trading_end")

#: 交易专用回调形态（契约表交易专用族中的函数定义形态——出现即画像记录）
TRADING_CALLBACKS = ("after_trading_order", "after_trading_cancel_order",
                     "on_order_response", "on_trade_response", "tick_data", "run_interval")

#: 订单 API 家族
ORDER_API_FAMILY = frozenset({"order", "order_target", "order_value",
                              "order_target_value", "order_market"})

# 频率档位 token（大小写归一后前缀匹配）
_FREQ_DAILY_TOKENS = ("1d", "day", "daily", "days")
_FREQ_MINUTE_TOKENS = ("1m", "min", "minute", "minutes")

#: 安全构造/纯函数调用名（g 变量赋值出现时按可序列化对待）
SAFE_CALLS = frozenset({
    "dict", "list", "set", "tuple", "int", "float", "str", "bool",
    "len", "sorted", "min", "max", "sum", "round", "abs", "range",
    "date", "today", "datetime", "now",
})

#: g.state 为持久化注入保留容器（P3 在 initialize 头部注入恢复块并拥有该容器）
RESERVED_G_VARS = ("state",)

_SER_OK = "ok"
_SER_EXEMPT = "exempt_needed"


# ---------------------------------------------------------------- 频率识别 --

def _match_freq_token(value: str) -> Optional[str]:
    v = str(value).strip().lower()
    if any(v.startswith(t) for t in _FREQ_DAILY_TOKENS):
        return "daily"
    if any(v.startswith(t) for t in _FREQ_MINUTE_TOKENS):
        return "minute"
    return None


def _detect_frequency(tree: ast.Module) -> Tuple[str, List[str]]:
    """从 set_backtest/frequency 调用与 handle_data 形态推断频率档位。

    判定规则（按证据强度）：
    1. set_backtest(...) 的 frequency 关键字实参；
    2. 模块级 FREQUENCY/frequency 常量赋值；
    3. 取数调用（get_history/get_price 等）任意字符串实参中的频率 token
       （'1d'/'1m' 等唯一可匹配，'close'/'600000.SS' 类不会误命中）。
    多证据冲突 → unknown（保守，交由 deploy.yaml 显式指定）。
    """
    evidence: List[str] = []
    found: Optional[str] = None
    conflict = False

    def _consider(value: str, where: str) -> None:
        nonlocal found, conflict
        freq = _match_freq_token(value)
        if freq is None:
            return
        evidence.append(f"{where}: {value} → {freq}")
        if found is None:
            found = freq
        elif found != freq:
            conflict = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = call_name(node) or ""
            # ① set_backtest(frequency=...)
            for kw in node.keywords:
                if kw.arg in ("frequency", "unit") and isinstance(kw.value, ast.Constant):
                    _consider(str(kw.value.value), f"{name}({kw.arg}=...)")
            # ③ 取数调用字符串实参中的频率 token
            if name in ("get_history", "get_price", "set_backtest"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        _consider(arg.value, f"{name} 位置实参")
        elif isinstance(node, ast.Assign):
            # ② 模块级 FREQUENCY/frequency 常量
            for tgt in node.targets:
                if (isinstance(tgt, ast.Name) and tgt.id.lower() == "frequency"
                        and isinstance(node.value, ast.Constant)):
                    _consider(str(node.value.value), "模块级 FREQUENCY 常量")

    if conflict or found is None:
        return "unknown", evidence
    return found, evidence


# ---------------------------------------------------------------- 股票池来源 --

def _universe_source(tree: ast.Module) -> Dict[str, str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == "set_universe":
            arg = None
            if node.args:
                arg = node.args[0]
            else:
                for kw in node.keywords:
                    if kw.arg in ("security_list", "stock_pool"):
                        arg = kw.value
            if arg is None:
                return {"source": "none", "detail": "set_universe 无实参"}
            if isinstance(arg, (ast.List, ast.Tuple)):
                n = len(arg.elts)
                return {"source": "static", "detail": f"字面量股票池（{n} 项）"}
            try:
                detail = ast.unparse(arg)
            except Exception:
                detail = arg.__class__.__name__
            return {"source": "dynamic", "detail": f"动态股票池: {detail}"}
    return {"source": "none", "detail": "未发现 set_universe 调用"}


# ---------------------------------------------------------------- 可序列化性 --

def _strip_leading_underscores(name: str) -> str:
    return name.lstrip("_")


def _classify_value(node: ast.expr, table: Dict[str, dict]) -> str:
    """g 变量赋值右值的可序列化性静态判定（worst-of 递归）。"""
    if isinstance(node, ast.Constant):
        return _SER_OK
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        worst = _SER_OK
        for elt in node.elts:
            if _classify_value(elt, table) == _SER_EXEMPT:
                worst = _SER_EXEMPT
        return worst
    if isinstance(node, ast.Dict):
        worst = _SER_OK
        for k in node.keys:
            if k is not None and _classify_value(k, table) == _SER_EXEMPT:
                worst = _SER_EXEMPT
        for v in node.values:
            if _classify_value(v, table) == _SER_EXEMPT:
                worst = _SER_EXEMPT
        return worst
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _SER_OK  # 名称绑定/属性读取（含模块常量）按可序列化对待
    if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.IfExp,
                         ast.Compare, ast.Subscript, ast.Slice, ast.JoinedStr)):
        worst = _SER_OK
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr) and _classify_value(child, table) == _SER_EXEMPT:
                worst = _SER_EXEMPT
        return worst
    if isinstance(node, ast.Call):
        name = call_name(node)
        if name == "open":
            return _SER_EXEMPT  # IO 句柄
        if name in table:
            return _SER_OK  # 平台数据 API（DataFrame/dict 等容器族，pickle 可序列化）
        if name in SAFE_CALLS:
            return _SER_OK
        base = _strip_leading_underscores(name or "")
        if base and base[0].isupper():
            return _SER_EXEMPT  # 显式类实例化（自定义类实例一律改 g.__ 豁免，§6.1）
        return _SER_OK  # 其余调用按数据容器对待（P5 门禁层兜底复核）
    return _SER_OK


# ---------------------------------------------------------------- g 变量画像 --

def _profile_g_vars(tree: ast.Module, table: Dict[str, dict],
                    warnings: List[str]) -> Tuple[Dict[str, dict], List[str]]:
    g_vars: Dict[str, dict] = {}

    def _entry(name: str) -> dict:
        return g_vars.setdefault(name, {"writes": 0, "reads": 0,
                                        "serializable": _SER_OK, "reason": ""})

    def _is_g(node: ast.expr) -> Optional[str]:
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "g":
            return node.attr
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                name = _is_g(tgt)
                if name is not None:
                    e = _entry(name)
                    e["writes"] += 1
                    verdict = _classify_value(node.value, table)
                    if verdict == _SER_EXEMPT or e["serializable"] == _SER_OK:
                        e["serializable"] = verdict
                        if verdict == _SER_EXEMPT and not e["reason"]:
                            try:
                                e["reason"] = f"赋值右值含类实例/IO: {ast.unparse(node.value)}"
                            except Exception:
                                e["reason"] = "赋值右值含类实例/IO"
                    if name in RESERVED_G_VARS:
                        warnings.append(
                            f"g.{name} 为持久化注入保留容器，策略直接赋值会覆盖 P3 恢复逻辑"
                            f"（建议改用 g.{name}[...] = ...）")
                elif isinstance(tgt, ast.Subscript) and _is_g(tgt.value) is not None:
                    # g.state['k'] = v 形态：容器内写，不改绑定
                    _entry(_is_g(tgt.value))["writes"] += 1
        elif isinstance(node, ast.AugAssign):
            name = _is_g(node.target)
            if name is not None:
                _entry(name)["writes"] += 1
        elif isinstance(node, ast.Delete):
            for tgt in node.targets:
                name = _is_g(tgt)
                if name is not None:
                    _entry(name)["writes"] += 1
        elif isinstance(node, ast.Attribute) and _is_g(node) is not None:
            # 其余 Attribute 出现处（表达式上下文）计为读
            if not (isinstance(getattr(node, "ctx", None), (ast.Store, ast.Del))):
                _entry(_is_g(node))["reads"] += 1

    exempt = sorted(n for n, v in g_vars.items() if v["serializable"] == _SER_EXEMPT)
    return g_vars, exempt


# ---------------------------------------------------------------- 主入口 --

def build_profile(source_text: str,
                  contract: Optional[Dict[str, dict]] = None) -> Dict[str, object]:
    """执行 P1 画像，输出 profile dict（结构见返回值各键）。"""
    table = (contract or load_contract())["api"]
    tree = ast.parse(source_text)

    warnings: List[str] = []
    frequency, freq_evidence = _detect_frequency(tree)
    if frequency == "unknown":
        warnings.append("频率档位无法静态判定（unknown），要求 deploy.yaml 显式指定执行档位")

    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    func_names = [f.name for f in functions]
    lifecycle = [n for n in LIFECYCLE_FUNCS if n in func_names]
    callbacks = [n for n in TRADING_CALLBACKS if n in func_names]
    if callbacks:
        warnings.append(f"回测源码定义了交易专用回调函数（转换保留）: {', '.join(callbacks)}")

    universe = _universe_source(tree)

    order_apis = sorted({
        name for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (name := call_name(node)) is not None
        and name in ORDER_API_FAMILY
    })

    g_vars, g_exempt = _profile_g_vars(tree, table, warnings)
    if g_exempt:
        warnings.append(
            f"g 变量需豁免持久化（类实例/IO，P3 改 g.__ 前缀）: {', '.join(g_exempt)}")

    return {
        "frequency": frequency,
        "frequency_evidence": freq_evidence,
        "lifecycle": lifecycle,
        "trading_callbacks": callbacks,
        "universe": universe,
        "order_apis": order_apis,
        "g_vars": g_vars,
        "g_exempt": g_exempt,
        "warnings": warnings,
    }
