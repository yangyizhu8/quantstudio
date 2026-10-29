# -*- coding: utf-8 -*-
"""P4 等价验证段（对应设计文档 docs/ptrade-deploy-pipeline-design.md §5.2 P4 / §5.4）。

等价判据：策略逻辑函数体（initialize/before_trading_start/handle_data/after_trading_end
及策略自定义函数）与输入源码在**剥离六类白名单改写后**，其余语句 AST dump 逐节点相等：
1. 回测专用设置函数行的门控/剔除（GATE 标记块 → 还原为原语句）；
2. 持久化注入块（PERSIST 标记块 → 整体剔除）；
3. 护栏注入块（GUARD 标记块 → 还原为被包裹的原语句）；
4. 执行时点挂载包装（MOUNT 标记块 → 包装函数还原为 handle_data；注册语句剔除）；
5. g.__ 前缀改写行（文本还原 g.__ → g.）；
6. 头部 import 追加行（行尾 IMPORT 标记 → 剔除）。

白名单外 diff 非零或出现非法标记块形态 → FAIL（防模型/脚本夹带未声明改写）。

实现说明：ast.parse 后注释不存活，故白名单剥离在**文本行层**按标记注释扫描完成，
随后 ast.parse 双侧做结构化对比；标记块内部结构不合法（如 GATE 块内非
`if not is_trade(): <原语句>` 形态）一律记为异常并 FAIL。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .preflight import call_name
from .transformer import MOUNT_WRAPPER_PREFIX

_MARK_BEGIN = re.compile(
    r"^(\s*)#\s*PTRADE-DEPLOY:(GATE_RESTORE|PERSIST|GUARD|MOUNT|GATE)\s+BEGIN\s*$")
_IMPORT_TAG = "# PTRADE-DEPLOY:IMPORT"

_DUMP_EXCERPT = 160  # diff 报告中 AST dump 摘录上限（保持报告可读）


@dataclass
class EquivResult:
    """P4 输出：等价判定 + 白名单外差异清单 + 标记块异常清单。"""

    passed: bool
    diffs: List[dict] = field(default_factory=list)
    anomalies: List[str] = field(default_factory=list)
    normalized_product: str = ""

    def to_dict(self) -> dict:
        return {"passed": self.passed, "diff_count": len(self.diffs),
                "diffs": self.diffs, "anomalies": self.anomalies}


# ---------------------------------------------------------------- 标记块剥离 --

def _dedent_lines(lines: List[str], indent: str) -> str:
    out = []
    for ln in lines:
        out.append(ln[len(indent):] if ln.startswith(indent) else ln)
    return "\n".join(out)


def _emit_at_indent(text: str, indent: str) -> List[str]:
    return [(indent + ln) if ln else ln for ln in text.split("\n")]


def _is_not_is_trade(test: ast.expr) -> bool:
    return (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Call)
            and isinstance(test.operand.func, ast.Name)
            and test.operand.func.id == "is_trade")


def _is_guard_call(test: ast.expr) -> bool:
    return (isinstance(test, ast.Call) and isinstance(test.func, ast.Name)
            and test.func.id == "_deploy_guard_pass")


def _handle_gate(indent: str, block: List[str]) -> List[str]:
    tree = ast.parse(_dedent_lines(block, indent))
    if len(tree.body) == 1 and isinstance(tree.body[0], ast.If):
        ifn = tree.body[0]
        if _is_not_is_trade(ifn.test) and len(ifn.body) == 1 and not ifn.orelse:
            return _emit_at_indent(ast.unparse(ifn.body[0]), indent)
    raise ValueError("GATE 块非 `if not is_trade(): <原语句>` 形态")


def _is_guard_warn(stmt: ast.stmt) -> bool:
    """GUARD 拦截分支只允许 log.warning/log.info 告警调用（防借 GUARD 标记夹带逻辑）。"""
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and stmt.value.func.attr in ("warning", "info"))


def _handle_guard(indent: str, block: List[str]) -> List[str]:
    tree = ast.parse(_dedent_lines(block, indent))
    if len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef) \
            and tree.body[0].name == "_deploy_guard_pass":
        return []  # 护栏 helper 函数（§5.4 白名单第 3 类注入物）整体剔除
    if len(tree.body) == 1 and isinstance(tree.body[0], ast.If):
        ifn = tree.body[0]
        if _is_guard_call(ifn.test) and len(ifn.body) == 1:
            for stmt in (ifn.orelse or []):
                if not _is_guard_warn(stmt):
                    raise ValueError("GUARD orelse 含非告警语句，疑似夹带改写")
            return _emit_at_indent(ast.unparse(ifn.body[0]), indent)
    raise ValueError("GUARD 块非 `if _deploy_guard_pass(...): <原语句>` 形态")


def _handle_persist(indent: str, block: List[str]) -> List[str]:
    return []  # 持久化注入整体剔除（含新建生命周期函数场景）


def _handle_mount(indent: str, block: List[str],
                  restore_name: str = "handle_data") -> List[str]:
    tree = ast.parse(_dedent_lines(block, indent))
    # 形态一：挂载包装函数 → 还原（签名还原，函数体已由前序剥离还原）：
    # PD-0006 纯 run_daily 源还原为 handle_data；PD-0007 混合形态（源已有
    # handle_data 诊断型 stub）还原为**源挂载目标名**，避免与保留的 stub 撞名（D5′）
    if len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef) \
            and tree.body[0].name.startswith(MOUNT_WRAPPER_PREFIX):
        fn = tree.body[0]
        fn.name = restore_name
        fn.args = ast.arguments(
            posonlyargs=[], args=[ast.arg(arg="context"), ast.arg(arg="data")],
            vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[])
        return _emit_at_indent(ast.unparse(fn), indent)
    # 形态二：run_daily 注册语句 / 仅为承载注册而新建的函数 → 剔除
    def _droppable(stmt) -> bool:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) \
                and call_name(stmt.value) == "run_daily":
            return True
        if isinstance(stmt, ast.FunctionDef):
            return bool(stmt.body) and all(_droppable(s) for s in stmt.body)
        return False
    if tree.body and all(_droppable(s) for s in tree.body):
        return []
    raise ValueError("MOUNT 块非包装函数/注册语句形态")


def _handle_gate_restore(indent: str, block: List[str]) -> List[str]:
    """PD-0012：「恒真分支还原」块 → 还原为源码原形态 `if not False:` + 原分支体。

    产物侧是 `if not is_trade(): <分支体>`（交易侧跳过、回测侧执行）；
    源码侧是 `if not False: <分支体>`（恒真）。二者在**回测语境**等价，
    故归一化时把产物条件重建回 `not False`，使 AST 逐节点相等（P4 不误报）。
    """
    tree = ast.parse(_dedent_lines(block, indent))
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.If):
        raise ValueError("GATE_RESTORE 块应为单一 if 语句")
    ifn = tree.body[0]
    if not _is_not_is_trade(ifn.test) or ifn.orelse:
        raise ValueError("GATE_RESTORE 块非 `if not is_trade(): <分支体>` 形态")
    out: List[str] = [f"{indent}if not False:"]
    for stmt in ifn.body:
        out.extend(_emit_at_indent(ast.unparse(stmt), indent + "    "))
    return out


_HANDLERS = {"GATE": _handle_gate, "GATE_RESTORE": _handle_gate_restore,
             "GUARD": _handle_guard,
             "PERSIST": _handle_persist, "MOUNT": _handle_mount}


def _strip_pass(text: str, kinds: Tuple[str, ...],
                anomalies: List[str],
                mount_restore_name: str = "handle_data") -> str:
    """单类标记块剥离一遍（GATE → GUARD → PERSIST → MOUNT 逐类多遍，避免嵌套问题）。"""
    lines = text.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        m = _MARK_BEGIN.match(lines[i])
        if m and m.group(2) in kinds:
            indent, kind = m.group(1), m.group(2)
            end_line = f"{indent}# PTRADE-DEPLOY:{kind} END"
            j = i + 1
            while j < len(lines):
                if lines[j] == end_line:
                    break
                nested = _MARK_BEGIN.match(lines[j])
                if nested and nested.group(1) == indent and nested.group(2) == kind:
                    anomalies.append(f"{kind} 块内出现同缩进同类别嵌套 BEGIN（行 {j + 1}）")
                    break
                j += 1
            else:
                anomalies.append(f"{kind} BEGIN 未找到匹配 END（行 {i + 1}）")
                out.append(lines[i])
                i += 1
                continue
            if j >= len(lines) or lines[j] != end_line:
                out.append(lines[i])
                i += 1
                continue
            block = lines[i + 1:j]
            try:
                if kind == "MOUNT":
                    out.extend(_handle_mount(indent, block, mount_restore_name))
                else:
                    out.extend(_HANDLERS[kind](indent, block))
            except (ValueError, SyntaxError) as exc:
                anomalies.append(f"{kind} 块剥离失败（行 {i + 1}）: {exc}")
                out.extend(lines[i:j + 1])  # 保留原文 → 必然产生 diff → FAIL
            i = j + 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


class _GExemptRestore(ast.NodeTransformer):
    """第 5 类白名单（g.__ 前缀还原）的 AST 级实现。

    仅还原「豁免名单内」的 g 属性节点（transformer._GExemptRenamer 的逆变换），
    不触碰字符串字面量/注释——文本级 replace 会误伤 docstring 中描述变量名的
    原文与源策略自生的 g.__ 私有变量（M3 实测固件踩中，修复落点=框架层）。
    """

    def __init__(self, g_exempt):
        self._names = {n.lstrip("_") for n in (g_exempt or ())}

    def visit_Attribute(self, node: ast.Attribute):
        self.generic_visit(node)
        if (isinstance(node.value, ast.Name) and node.value.id == "g"
                and node.attr.startswith("__")
                and node.attr.lstrip("_") in self._names):
            node.attr = node.attr.lstrip("_")
        return node


def normalize_product(product_text: str,
                      g_exempt=(),
                      mount_restore_name: str = "handle_data"
                      ) -> Tuple[str, List[str]]:
    """按白名单六类剥离产物标记改写，返回 (规范化文本, 异常清单)。

    ``mount_restore_name``：MOUNT 包装体还原名（PD-0007 D5′——纯 run_daily 源用
    'handle_data'，混合形态源用其挂载目标名，避免与保留的 stub 撞名）。
    """
    anomalies: List[str] = []
    text = product_text
    # 顺序：叶子块先行（GATE/GUARD 可嵌于 MOUNT 包装函数内、MOUNT 可嵌于新建 PERSIST 函数内）
    for kinds in (("GATE_RESTORE",), ("GATE",), ("GUARD",), ("PERSIST",), ("MOUNT",)):
        text = _strip_pass(text, kinds, anomalies, mount_restore_name)
    # 第 6 类：import 追加行剔除
    text = "\n".join(ln for ln in text.split("\n")
                     if not ln.rstrip().endswith(_IMPORT_TAG))
    # 第 5 类：g.__ 前缀还原（AST 级，限豁免名单；unparse 往返对 AST dump 无损）
    tree = ast.parse(text)
    _GExemptRestore(g_exempt).visit(tree)
    text = ast.unparse(tree)
    return text, anomalies


# ---------------------------------------------------------------- AST 对比 --

def _func_nodes(tree: ast.Module) -> dict:
    return {f.name: f for f in tree.body
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _dump(node) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _excerpt(dump_text: str) -> str:
    return dump_text[:_DUMP_EXCERPT] + ("…" if len(dump_text) > _DUMP_EXCERPT else "")


def _diff_stmt_lists(location: str, src_body: List[ast.stmt],
                     prod_body: List[ast.stmt], diffs: List[dict]) -> None:
    n = max(len(src_body), len(prod_body))
    for k in range(n):
        if k >= len(src_body):
            diffs.append({"location": f"{location}[{k}]", "kind": "extra_stmt",
                          "product": _excerpt(_dump(prod_body[k]))})
        elif k >= len(prod_body):
            diffs.append({"location": f"{location}[{k}]", "kind": "missing_stmt",
                          "source": _excerpt(_dump(src_body[k]))})
        elif _dump(src_body[k]) != _dump(prod_body[k]):
            diffs.append({"location": f"{location}[{k}]", "kind": "stmt_mismatch",
                          "source": _excerpt(_dump(src_body[k])),
                          "product": _excerpt(_dump(prod_body[k]))})


def _source_run_daily_targets(tree: ast.Module) -> set:
    """PD-0006：源侧 run_daily 挂载目标识别（initialize 直接语句级
    run_daily(ctx, <Name>, ...)）。用于等价比较的源侧对称规范化。"""
    targets = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "initialize":
            for stmt in node.body:
                if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                        and call_name(stmt.value) == "run_daily"
                        and len(stmt.value.args) >= 2
                        and isinstance(stmt.value.args[1], ast.Name)):
                    targets.add(stmt.value.args[1].id)
    return targets


def _strip_source_registrations(tree: ast.Module) -> None:
    """源侧对称规范化（第 5 类前半）：剔除源 initialize 内 run_daily 注册语句——
    产物侧注册已被 MOUNT 剥离剔除，源侧对称剔除后方为可比形态（挂载行为差异
    归白名单，函数体逻辑仍逐句比对）。"""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "initialize":
            node.body[:] = [
                s for s in node.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
                        and call_name(s.value) == "run_daily")]


def _compare_asts(source_text: str, normalized_product: str) -> List[dict]:
    src_tree = ast.parse(source_text)
    prod_tree = ast.parse(normalized_product)
    diffs: List[dict] = []

    # PD-0006 第 5 类（run_daily 源形态）：源挂载目标 → 还原产物 handle_data
    rd_targets = _source_run_daily_targets(src_tree)
    if rd_targets:
        _strip_source_registrations(src_tree)  # 源侧对称剔除注册（与产物对称）

    src_funcs = _func_nodes(src_tree)
    prod_funcs = _func_nodes(prod_tree)
    mount = ("handle_data" not in prod_funcs
             and any(n.startswith(MOUNT_WRAPPER_PREFIX) for n in prod_funcs))

    for name, fn in src_funcs.items():
        tgt_name = (f"{MOUNT_WRAPPER_PREFIX}_exec"
                    if mount and name == "handle_data" else name)
        # 第 5 类：run_daily 源挂载目标（如 weekly_rebalance）经 P3 包装、
        # P4 还原后名/签名变化——名字映射 + 签名白名单（挂载函数仅
        # (context,) 单参，包装还原为 (context, data=None) 双参，行为等价）
        # PD-0007 D5′：混合形态源（已有 handle_data stub）下还原名 = 挂载目标名，
        # 故不做 handle_data 映射，按同名比对 + 签名白名单。
        is_mount_target = name in rd_targets
        rd_mounted = (is_mount_target and "handle_data" in prod_funcs
                      and "handle_data" not in src_funcs)
        if rd_mounted:
            tgt_name = "handle_data"
        tgt = prod_funcs.get(tgt_name)
        if tgt is None:
            diffs.append({"location": name, "kind": "missing_function"})
            continue
        if not (mount and name == "handle_data") and not rd_mounted \
                and not is_mount_target:
            # 挂载签名行为白名单（第 4 类既有 + 第 5 类 run_daily 源形态）
            if _dump(fn.args) != _dump(tgt.args):
                diffs.append({"location": name, "kind": "args_mismatch",
                              "source": _excerpt(_dump(fn.args)),
                              "product": _excerpt(_dump(tgt.args))})
        _diff_stmt_lists(name, fn.body, tgt.body, diffs)

    for name in prod_funcs:
        if name not in src_funcs and not (mount and name.startswith(MOUNT_WRAPPER_PREFIX)) \
                and not (name == "handle_data" and rd_targets):
            # 第 5 类尾段：run_daily 源形态还原产物的 handle_data 为合法还原名
            # （源挂载目标函数经 P3 包装、P4 恒名还原——rd_targets 非空时豁免）
            diffs.append({"location": name, "kind": "extra_function"})

    # 模块级非函数语句（import/常量/类定义等）
    src_mod = [s for s in src_tree.body
               if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    prod_mod = [s for s in prod_tree.body
                if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    _diff_stmt_lists("<module>", src_mod, prod_mod, diffs)
    return diffs


# ---------------------------------------------------------------- 主入口 --

def _mount_restore_name(source_text: str) -> str:
    """PD-0007 D5′：MOUNT 包装体归一化目标名选择。

    纯 run_daily 源（无 handle_data）→ 'handle_data'（PD-0006 既有契约）；
    混合形态源（已有 handle_data 诊断型 stub + 恰 1 挂载）→ 源挂载目标名，
    避免与保留的 stub 撞名（否则归一化后两函数同名，dict 覆盖产生伪 diff）。
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return "handle_data"
    targets = _source_run_daily_targets(tree)
    names = {f.name for f in tree.body
             if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if "handle_data" in names and len(targets) == 1:
        return next(iter(targets))
    return "handle_data"


def compare_sources(source_text: str, product_text: str,
                    g_exempt=()) -> EquivResult:
    """P4 等价 diff：白名单外 diff=0 且无标记块异常 → passed=True。"""
    normalized, anomalies = normalize_product(
        product_text, g_exempt, _mount_restore_name(source_text))
    try:
        diffs = _compare_asts(source_text, normalized)
    except SyntaxError as exc:
        return EquivResult(passed=False, diffs=[], anomalies=anomalies + [
            f"规范化产物 AST 解析失败: {exc}"], normalized_product=normalized)
    return EquivResult(passed=not diffs and not anomalies, diffs=diffs,
                       anomalies=anomalies, normalized_product=normalized)
