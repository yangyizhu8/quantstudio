# -*- coding: utf-8 -*-
"""P3 AST 变换段（对应设计文档 docs/ptrade-deploy-pipeline-design.md §5.2 P3 / §6）。

变换五步（全部结构化 AST 操作，非字符串拼接；定制逐语句 unparse 发射器输出）：
a) 回测专用设置函数调用 → is_trade() 门控包裹（GATE 标记；回测 is_trade()=False，
   行为与原直呼完全一致）；
b) 持久化层注入：initialize 头部 try 恢复/except 默认块、after_trading_end 尾部落盘块
   （缺失的生命周期函数则新建，新建函数整体带 PERSIST 标记）；
c) 执行时点挂载：日线档把 handle_data 逻辑包装为 _deploy_daily_exec(context, data=None)
   并在 initialize 注入 run_daily(..., time='14:50'/'09:31')（MOUNT 标记）；
   分钟档（minute_keep）保持原生命周期；
d) 护栏注入：下单调用前插停牌/涨跌停/限价参考/幂等检查块（GUARD 标记，
   原下单语句原样内联在护栏 if 体内——策略原语句零改动）；
e) 画像标记需豁免的 g 变量改 g.__ 前缀（F1：__ 前缀不参与框架自动持久化）。

标记规范（§5.4 白名单六类，机器可识别）：
- 持久化块   # PTRADE-DEPLOY:PERSIST BEGIN / END
- 护栏块     # PTRADE-DEPLOY:GUARD BEGIN / END
- 挂载包装   # PTRADE-DEPLOY:MOUNT BEGIN / END
- 门控       # PTRADE-DEPLOY:GATE BEGIN / END
- import 追加 行尾 # PTRADE-DEPLOY:IMPORT
- g.__ 前缀改写由 P4 按前缀文本还原（无独立标记行）

确定性：同输入同配置两次转换输出逐位一致（无时间戳/绝对路径/随机数），
为双端 parity SHA256 门禁（§7.3）与 P4 等价 diff（§5.4）提供基础。
"""
from __future__ import annotations

import ast
import copy
import importlib.util
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .preflight import call_name

# ---------------------------------------------------------------- 标记常量 --

MARKER_PERSIST = "PERSIST"
MARKER_GUARD = "GUARD"
MARKER_MOUNT = "MOUNT"
MARKER_GATE = "GATE"
#: PD-0012：「恒真分支还原」块标记（P4 归一化时把条件还原回 `not False`）
MARKER_GATE_RESTORE = "GATE_RESTORE"
MARKER_IMPORT = "IMPORT"

#: import 追加行的行尾标记（§5.4 白名单第 6 类）
IMPORT_TAG = "  # PTRADE-DEPLOY:IMPORT"

#: g.state 持久化 schema 版本（§6.1：版本不匹配回退默认并告警，防旧 pickle 炸新代码）
STATE_VERSION = 1

#: 订单 API 家族（护栏注入对象）
ORDER_API_FAMILY = frozenset({"order", "order_target", "order_value",
                              "order_target_value", "order_market"})

#: 执行档位 → run_daily 注册时间（§6.2 映射表；minute_keep 不挂载）
RUN_DAILY_TIME = {"daily_eod_1450": "14:50", "daily_open_0931": "09:31"}

#: 挂载包装函数名（P4 按此前缀识别挂载块并还原为 handle_data）
MOUNT_WRAPPER_PREFIX = "_deploy_daily"


class TransformError(Exception):
    """P3 硬失败（如日线档无 handle_data 可挂载）。"""


@dataclass
class TransformResult:
    """P3 输出：产物源码 + 改写清单（P6 报告逐条引用）+ 警告。"""

    product_text: str
    edits: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- 模板加载 --

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@lru_cache(maxsize=4)
def _load_template(name: str):
    """按文件路径加载注入模板模块（templates/ 无 __init__.py，路径加载即可；
    模板仅含字符串常量，exec 无副作用）。"""
    path = _TEMPLATES_DIR / f"{name}.py"
    if not path.exists():
        raise TransformError(f"注入模板不存在: {path}")
    spec = importlib.util.spec_from_file_location(
        f"quantstudio.ptrade_deploy.templates.{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 环境异常兜底
        raise TransformError(f"注入模板加载失败: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 标记辅助 --
# 注入节点通过 _pd_marker 属性携带标记：(类别, 位置)，
# 位置 ∈ wrap(BEGIN+语句+END) | begin(仅 BEGIN) | end(仅 END) | line(行尾 IMPORT)。


def _mark(node, kind: str, pos: str):
    node._pd_marker = (kind, pos)  # noqa: SLF001 - AST 节点属性注入（CPython 稳定行为）
    return node


def _is_marked(node) -> bool:
    return getattr(node, "_pd_marker", None) is not None


def _contains_marker_inside(stmt) -> bool:
    """stmt 内部（不含自身）是否含标记节点——决定复合语句是否需结构化发射。"""
    for child in ast.iter_child_nodes(stmt):
        for sub in ast.walk(child):
            if _is_marked(sub):
                return True
    return False


def _is_compound(stmt) -> bool:
    return isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While,
                             ast.With, ast.AsyncWith, ast.Try))


# ---------------------------------------------------------------- 定制发射器 --

def _emit_module(tree: ast.Module) -> str:
    lines: List[str] = []
    for stmt in tree.body:
        _emit_stmt(stmt, "", lines)
    return "\n".join(lines) + "\n"


def _emit_stmt(stmt, indent: str, lines: List[str]) -> None:
    marker = getattr(stmt, "_pd_marker", None)
    if marker is not None and marker[1] in ("wrap", "begin"):
        lines.append(f"{indent}# PTRADE-DEPLOY:{marker[0]} BEGIN")
    _emit_stmt_inner(stmt, indent, lines)
    if marker is not None:
        kind, pos = marker
        if pos == "line":
            lines[-1] = lines[-1] + IMPORT_TAG
        elif pos in ("wrap", "end"):
            lines.append(f"{indent}# PTRADE-DEPLOY:{kind} END")


def _emit_stmt_inner(stmt, indent: str, lines: List[str]) -> None:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for dec in stmt.decorator_list:
            lines.append(f"{indent}@{ast.unparse(dec)}")
        prefix = "async def" if isinstance(stmt, ast.AsyncFunctionDef) else "def"
        ret = f" -> {ast.unparse(stmt.returns)}" if stmt.returns is not None else ""
        lines.append(f"{indent}{prefix} {stmt.name}({ast.unparse(stmt.args)}){ret}:")
        _emit_body(stmt.body, indent + "    ", lines)
    elif isinstance(stmt, ast.ClassDef):
        # 类体不注入标记，整体 unparse（round-trip 稳定）
        lines.extend(indent + ln for ln in ast.unparse(stmt).split("\n"))
    elif _is_compound(stmt) and _contains_marker_inside(stmt):
        _emit_compound(stmt, indent, lines)
    else:
        lines.extend(indent + ln for ln in ast.unparse(stmt).split("\n"))


def _emit_body(body: List[ast.stmt], indent: str, lines: List[str]) -> None:
    if not body:
        lines.append(f"{indent}pass")
        return
    for stmt in body:
        _emit_stmt(stmt, indent, lines)


def _emit_compound(stmt, indent: str, lines: List[str]) -> None:
    """含标记注入的复合语句结构化发射（If/elif 链、For/While、With、Try）。"""
    pad = indent + "    "
    if isinstance(stmt, ast.If):
        lines.append(f"{indent}if {ast.unparse(stmt.test)}:")
        _emit_body(stmt.body, pad, lines)
        _emit_if_tail(stmt.orelse, indent, lines)
    elif isinstance(stmt, (ast.For, ast.AsyncFor)):
        prefix = "async for" if isinstance(stmt, ast.AsyncFor) else "for"
        lines.append(f"{indent}{prefix} {ast.unparse(stmt.target)} in {ast.unparse(stmt.iter)}:")
        _emit_body(stmt.body, pad, lines)
        if stmt.orelse:
            lines.append(f"{indent}else:")
            _emit_body(stmt.orelse, pad, lines)
    elif isinstance(stmt, ast.While):
        lines.append(f"{indent}while {ast.unparse(stmt.test)}:")
        _emit_body(stmt.body, pad, lines)
        if stmt.orelse:
            lines.append(f"{indent}else:")
            _emit_body(stmt.orelse, pad, lines)
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        prefix = "async with" if isinstance(stmt, ast.AsyncWith) else "with"
        items = ", ".join(_withitem_text(it) for it in stmt.items)
        lines.append(f"{indent}{prefix} {items}:")
        _emit_body(stmt.body, pad, lines)
    elif isinstance(stmt, ast.Try):
        lines.append(f"{indent}try:")
        _emit_body(stmt.body, pad, lines)
        for h in stmt.handlers:
            head = "except"
            if h.type is not None:
                head += f" {ast.unparse(h.type)}"
            if h.name:
                head += f" as {h.name}"
            lines.append(f"{indent}{head}:")
            _emit_body(h.body, pad, lines)
        if stmt.orelse:
            lines.append(f"{indent}else:")
            _emit_body(stmt.orelse, pad, lines)
        if stmt.finalbody:
            lines.append(f"{indent}finally:")
            _emit_body(stmt.finalbody, pad, lines)
    else:  # pragma: no cover - 复合类型兜底
        lines.extend(indent + ln for ln in ast.unparse(stmt).split("\n"))


def _emit_if_tail(orelse: List[ast.stmt], indent: str, lines: List[str]) -> None:
    if not orelse:
        return
    if len(orelse) == 1 and isinstance(orelse[0], ast.If):  # elif 链
        elf = orelse[0]
        lines.append(f"{indent}elif {ast.unparse(elf.test)}:")
        _emit_body(elf.body, indent + "    ", lines)
        _emit_if_tail(elf.orelse, indent, lines)
    else:
        lines.append(f"{indent}else:")
        _emit_body(orelse, indent + "    ", lines)


def _withitem_text(item: ast.withitem) -> str:
    text = ast.unparse(item.context_expr)
    if item.optional_vars is not None:
        text += f" as {ast.unparse(item.optional_vars)}"
    return text


# ---------------------------------------------------------------- 生命周期函数辅助 --

def _after_header_index(tree: ast.Module) -> int:
    """文档串 + 顶部连续 import 段之后的插入位置（确定性）。"""
    idx = 0
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        idx = 1
    while idx < len(body) and isinstance(body[idx], (ast.Import, ast.ImportFrom)):
        idx += 1
    return idx


def _find_function(tree: ast.Module, name: str) -> Optional[ast.FunctionDef]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _get_or_create_function(tree: ast.Module, name: str,
                            create_marker_kind: str) -> Tuple[ast.FunctionDef, bool]:
    """定位模块级生命周期函数；缺失则新建（整体带块级标记，P4 整体还原）。"""
    fn = _find_function(tree, name)
    if fn is not None:
        return fn, False
    fn = ast.FunctionDef(
        name=name,
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="context")],
                           vararg=None, kwonlyargs=[], kw_defaults=[],
                           kwarg=None, defaults=[]),
        body=[], decorator_list=[], returns=None, type_comment=None)
    _mark(fn, create_marker_kind, "wrap")
    tree.body.insert(_after_header_index(tree), fn)
    return fn, True


# ---------------------------------------------------------------- ① GATE 门控 --

def _is_not_false_literal(node) -> bool:
    """是否**正是** `not False` 字面量形态（PD-0012 还原的唯一识别面）。

    背景：`qs-compile import` 把 `is_trade()` 固化为字面量 `False`（source_import.py:3087），
    源策略的 `if not is_trade():` 于是变成 `if not False:`（**恒真**）⇒ 交易侧门控失效。

    **只认这一形态**（保守）：equiv 的还原处理器需在文本层把条件重建回源码原样，
    而源码原样由固化规则唯一确定为 `not False`；`if True:` 等其它恒真形态一律不动
    （source_import 不会产出，动了反而破坏 P4 重建）。
    """
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
            and isinstance(node.operand, ast.Constant)
            and node.operand.value is False)


def _branch_only_gate_calls(body: List[ast.stmt], gate_apis: set) -> bool:
    """分支体是否**只含**回测专用 API 调用（允许 pass / 纯注释常量）。

    允许集式收紧（沿用 PD-2026-0007 教训）：体内只要混有一条业务语句即判否，
    从而保证还原动作不会误伤真实业务逻辑。
    """
    if not body:
        return False
    for s in body:
        if isinstance(s, ast.Pass):
            continue
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) \
                and isinstance(s.value.value, str):
            continue  # 纯注释/文档字符串
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) \
                and call_name(s.value) in gate_apis:
            continue
        return False
    return True


def _restore_always_true_gates(stmt: ast.stmt, gate_apis: set,
                               edits: List[Dict[str, str]], fn_name: str) -> set:
    """把「静态恒真 且 分支体只含回测专用 API」的条件还原为 `not is_trade()`（PD-0012）。

    返回被还原节点的 `id()` 集合（供告警扫描跳过，避免重复报「嵌套未门控」）。
    **只改条件、不动分支体**——回测语境 `is_trade()` 为 False ⇒ `not is_trade()` 为真，
    执行行为与改前 `not False` 一致（P4 等价性红线依赖此性质）。
    """
    restored: set = set()
    for node in ast.walk(stmt):
        if not isinstance(node, (ast.If, ast.While)):
            continue
        if not _is_not_false_literal(getattr(node, "test", None)):
            continue
        if not _branch_only_gate_calls(getattr(node, "body", []), gate_apis):
            continue
        node.test = ast.UnaryOp(
            op=ast.Not(),
            operand=ast.Call(func=ast.Name(id="is_trade", ctx=ast.Load()),
                             args=[], keywords=[]))
        ast.copy_location(node.test, node)
        _mark(node, MARKER_GATE_RESTORE, "wrap")   # P4 归一化据此还原条件
        restored.add(id(node))
        # ②审 2026-10-30 新增：还原动作须在**转换审计行**留痕（可追溯性）
        edits.append({"kind": "GATE_RESTORE", "location": fn_name,
                      "detail": f"恒真分支还原@{getattr(node, 'lineno', '?')}: "
                                "静态恒真条件 → not is_trade()（分支体仅含回测专用 API；"
                                "回测行为不变，交易侧恢复跳过）"})
    return restored


def _scan_ungated_calls(node: ast.AST, gate_apis: set, restored: set,
                        sink: List[str]) -> None:
    """递归扫描未被还原分支内的回测专用调用（还原过的分支**整体**跳过）。"""
    if id(node) in restored:      # 首层语句本身即被还原的分支时，直接整体跳过
        return
    for child in ast.iter_child_nodes(node):
        if id(child) in restored:
            continue
        if isinstance(child, ast.Call) and call_name(child) in gate_apis:
            sink.append(call_name(child))
        _scan_ungated_calls(child, gate_apis, restored, sink)


def _apply_gates(tree: ast.Module, table: Dict[str, dict],
                 edits: List[Dict[str, str]], warnings: List[str]) -> None:
    gate_apis = {n for n, e in table.items()
                 if e["backtest"] and not e["trading"]
                 and e.get("action") in ("strip_or_gate", "gate")}
    strip_apis = {n for n, e in table.items()
                  if e["backtest"] and not e["trading"] and e.get("action") == "strip"}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        new_body: List[ast.stmt] = []
        for stmt in fn.body:
            call = None
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call = stmt.value
            name = call_name(call) if call is not None else None
            if name in gate_apis:
                gate_if = ast.If(
                    test=ast.UnaryOp(
                        op=ast.Not(),
                        operand=ast.Call(
                            func=ast.Name(id="is_trade", ctx=ast.Load()),
                            args=[], keywords=[])),
                    body=[stmt], orelse=[])
                _mark(gate_if, MARKER_GATE, "wrap")
                new_body.append(gate_if)
                edits.append({"kind": "GATE", "location": fn.name,
                              "detail": f"{name}(...) → is_trade() 门控包裹（回测行为不变）"})
            elif name in strip_apis:  # 种子集无 strip 条目；保守实现为剔除
                edits.append({"kind": "GATE", "location": fn.name,
                              "detail": f"{name}(...) 按契约表 action=strip 剔除"})
            else:
                # PD-0012：先做「恒真分支还原」，再对**未还原**的嵌套调用告警（P5 将拦截）
                restored = _restore_always_true_gates(stmt, gate_apis, edits, fn.name)
                new_body.append(stmt)
                sinks: List[str] = []
                _scan_ungated_calls(stmt, gate_apis, restored, sinks)
                if sinks:
                    warnings.append(
                        f"{fn.name}: 回测专用 API {sinks[0]} 位于嵌套复合语句内，"
                        "M3 仅门控函数体首层语句（P5 将按未门控拦截）")
        fn.body = new_body


# ---------------------------------------------------------------- ② g.__ 豁免改名 --

class _GExemptRenamer(ast.NodeTransformer):

    def __init__(self, names: set, edits: List[Dict[str, str]]):
        self._names = names
        self._edits = edits
        self._done: set = set()

    def visit_Attribute(self, node: ast.Attribute):
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "g" \
                and node.attr in self._names:
            new = ast.Attribute(value=node.value, attr="__" + node.attr, ctx=node.ctx)
            ast.copy_location(new, node)
            if node.attr not in self._done:
                self._done.add(node.attr)
                self._edits.append({
                    "kind": "G_EXEMPT", "location": "<module>",
                    "detail": f"g.{node.attr} → g.__{node.attr}"
                              f"（类实例/IO 豁免框架自动持久化，F1）"})
            return new
        return node


def _apply_g_exempt(tree: ast.Module, exempt: List[str],
                    edits: List[Dict[str, str]]) -> None:
    if not exempt:
        return
    _GExemptRenamer(set(exempt), edits).visit(tree)


# ---------------------------------------------------------------- ③ 持久化注入 --

def _ensure_pickle_import(tree: ast.Module, edits: List[Dict[str, str]]) -> None:
    for node in tree.body:
        if isinstance(node, ast.Import) and any(a.name == "pickle" for a in node.names):
            return
        if isinstance(node, ast.ImportFrom) and node.module == "pickle":
            return
    imp = ast.Import(names=[ast.alias(name="pickle", asname=None)])
    _mark(imp, MARKER_IMPORT, "line")
    tree.body.insert(_after_header_index(tree), imp)
    edits.append({"kind": "IMPORT", "location": "<module>",
                  "detail": "追加 import pickle（持久化层依赖，F2）"})


def _ensure_notebook_path(tree: ast.Module, tpl, edits: List[Dict[str, str]]) -> None:
    """注入模块级研究根目录解析（PD-0015）。

    为什么不能写字面量：部分券商禁止产物代码中出现研究目录路径
    （官方 knowledge/platform/ptrade/01-API文档主页/001-page.md:2573：
    「会限制使用路径 /home/fly/，如果代码里面存在该路径字符，会提示无法保存，
     需要替换成 get_research_path()，效果一样」）。
    故改为运行时 `get_research_path()`，状态文件只拼纯文件名。

    标记：整块带 PERSIST 标记（首行 begin / 末行 end）——P4 等价性据此整体剥离，
    剥离后与源策略逐节点相等（该块只被 PERSIST 块自身引用，剥离后无悬挂引用）。
    """
    nb_stmts = ast.parse(tpl.PERSIST_NOTEBOOK_TEMPLATE).body
    # 模板解析为**单个 ast.Try** ⇒ 用 "wrap" 标记（同时发射 BEGIN 与 END）
    for s in nb_stmts:
        _mark(s, MARKER_PERSIST, "wrap")
    idx = _after_header_index(tree)
    for k, s in enumerate(nb_stmts):
        tree.body.insert(idx + k, s)
    edits.append({"kind": "PERSIST", "location": "<module>",
                  "detail": "注入 _PD_NOTEBOOK = get_research_path()（研究根目录运行时解析，"
                            "PD-0015：规避部分券商禁用路径字面量）"})


def _apply_persistence(tree: ast.Module, cfg, edits: List[Dict[str, str]]) -> None:
    tpl = _load_template("persistence_template")
    _ensure_pickle_import(tree, edits)
    _ensure_notebook_path(tree, tpl, edits)

    restore_src = (tpl.PERSIST_RESTORE_TEMPLATE
                   .replace("__STATE_FILE__", repr(cfg.persistence.state_file))
                   .replace("__STATE_VERSION__", str(STATE_VERSION)))
    restore_stmts = ast.parse(restore_src).body
    init, created = _get_or_create_function(tree, "initialize", MARKER_PERSIST)
    if created:
        edits.append({"kind": "PERSIST", "location": "<module>",
                      "detail": "新建 initialize(context) 承载持久化层"})
    else:  # 既有函数：块级标记加在注入语句首尾（避免与函数级标记嵌套）
        _mark(restore_stmts[0], MARKER_PERSIST, "begin")
        _mark(restore_stmts[-1], MARKER_PERSIST, "end")
    init.body[0:0] = restore_stmts  # 头部插入（try 恢复 / except 默认）
    edits.append({"kind": "PERSIST", "location": "initialize",
                  "detail": f"头部注入 try 恢复/except 默认块"
                            f"（state_file={cfg.persistence.state_file}，version={STATE_VERSION}）"})

    if "after_trading_end" in cfg.persistence.save_points:
        save_src = tpl.PERSIST_SAVE_TEMPLATE.replace(
            "__STATE_FILE__", repr(cfg.persistence.state_file))
        save_stmts = ast.parse(save_src).body
        ate, created_ate = _get_or_create_function(tree, "after_trading_end", MARKER_PERSIST)
        if created_ate:
            edits.append({"kind": "PERSIST", "location": "<module>",
                          "detail": "新建 after_trading_end(context) 承载落盘"})
        else:
            for s in save_stmts:
                _mark(s, MARKER_PERSIST, "wrap")
        ate.body.extend(save_stmts)  # 尾部落盘
        edits.append({"kind": "PERSIST", "location": "after_trading_end",
                      "detail": "尾部注入 g.state 落盘块（pickle 协议 -1）"})


# ---------------------------------------------------------------- ④ 执行时点挂载 --

def _apply_mount(tree: ast.Module, cfg, edits: List[Dict[str, str]],
                 profile: Optional[Dict[str, object]] = None) -> None:
    time_str = RUN_DAILY_TIME[cfg.execution.profile]
    idx = None
    handle_data = None
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.FunctionDef) and node.name == "handle_data":
            handle_data, idx = node, i
            break

    # PD-0006 第二输入形态：run_daily 源（无 handle_data，恰 1 个执行挂载）——
    # 包装源挂载函数（如 weekly_rebalance）并替换源注册语句为部署档位挂载。
    # PD-0007：混合形态（handle_data 为诊断型 stub + 恰 1 挂载）走同一路径——
    # 以挂载目标为部署执行体，stub 函数体保持原样（诊断日志不丢、语义零变化）。
    mounts = list((profile or {}).get("run_daily_mounts") or [])
    stub_mixed = bool((profile or {}).get("handle_data_stub"))
    src_fn_name = None
    src_time = None
    if (handle_data is None or stub_mixed) and len(mounts) == 1 and mounts[0].get("func"):
        src_fn_name = mounts[0]["func"]
        src_time = mounts[0].get("time")
        for i, node in enumerate(tree.body):
            if isinstance(node, ast.FunctionDef) and node.name == src_fn_name:
                handle_data, idx = node, i
                break
        if handle_data is None:
            raise TransformError(
                f"run_daily 挂载目标函数 {src_fn_name} 未在模块级定义（PD-0006 D1 "
                "识别与定义不一致）")

    if handle_data is None or idx is None:
        raise TransformError(
            "日线执行档要求源码存在模块级 handle_data（挂载包装输入，§6.2），"
            "或恰 1 个 initialize 内 run_daily 挂载（PD-0006 源形态）")

    ctx_name = handle_data.args.args[0].arg if handle_data.args.args else "context"
    data_name = (handle_data.args.args[1].arg if len(handle_data.args.args) > 1
                 else "data")
    wrapper = ast.FunctionDef(
        name=f"{MOUNT_WRAPPER_PREFIX}_exec",
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=ctx_name), ast.arg(arg=data_name)],
            vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None,
            defaults=[ast.Constant(value=None)]),
        body=handle_data.body, decorator_list=[], returns=None, type_comment=None)
    _mark(wrapper, MARKER_MOUNT, "wrap")
    tree.body[idx] = wrapper
    if src_fn_name:
        edits.append({"kind": "MOUNT", "location": src_fn_name,
                      "detail": f"包装为 {MOUNT_WRAPPER_PREFIX}_exec({ctx_name}, {data_name}=None)"
                                f"（函数体原样内联；原 {src_fn_name} 移除，run_daily 源形态"
                                f" PD-0006：源挂载 time='{src_time}' 重挂到档位时点）"})
    else:
        edits.append({"kind": "MOUNT", "location": "handle_data",
                      "detail": f"包装为 {MOUNT_WRAPPER_PREFIX}_exec({ctx_name}, {data_name}=None)"
                                f"（函数体原样内联；原 handle_data 移除，交易端由 run_daily 驱动）"})

    init, created = _get_or_create_function(tree, "initialize", MARKER_MOUNT)
    init_ctx = init.args.args[0].arg if init.args.args else "context"

    # run_daily 源形态：定点移除源注册语句 run_daily(init_ctx, src_fn_name, ...)
    if src_fn_name:
        kept = []
        removed = 0
        for stmt in init.body:
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                    and getattr(stmt.value.func, "id", "") == "run_daily"
                    and len(stmt.value.args) >= 2
                    and isinstance(stmt.value.args[1], ast.Name)
                    and stmt.value.args[1].id == src_fn_name):
                removed += 1
                continue
            kept.append(stmt)
        if removed == 0:
            raise TransformError(
                f"源 run_daily({init_ctx}, {src_fn_name}, ...) 注册语句未找到"
                "（与 P1 识别不一致）")
        init.body[:] = kept
        edits.append({"kind": "MOUNT", "location": "initialize",
                      "detail": f"移除源注册 run_daily({init_ctx}, {src_fn_name}, "
                                f"time='{src_time}')（{removed} 处）"})

    reg = ast.Expr(value=ast.Call(
        func=ast.Name(id="run_daily", ctx=ast.Load()),
        args=[ast.Name(id=init_ctx, ctx=ast.Load()),
              ast.Name(id=f"{MOUNT_WRAPPER_PREFIX}_exec", ctx=ast.Load())],
        keywords=[ast.keyword(arg="time", value=ast.Constant(value=time_str))]))
    if not created:
        _mark(reg, MARKER_MOUNT, "wrap")
    init.body.append(reg)
    edits.append({"kind": "MOUNT", "location": "initialize",
                  "detail": f"注入 run_daily({init_ctx}, {MOUNT_WRAPPER_PREFIX}_exec, "
                            f"time='{time_str}')（档位 {cfg.execution.profile}，F4"
                            + (f"；源时点 {src_time} 重挂，provenance 记录" if src_fn_name
                               else "") + "）"})


# ---------------------------------------------------------------- ⑤ 护栏注入 --

def _build_guard_helper(guards) -> str:
    tpl = _load_template("guard_template")
    segments = []
    if guards.suspension_block:
        segments.append(tpl.GUARD_SUSPENSION_BLOCK)
    if guards.limit_up_down_block:
        segments.append(tpl.GUARD_LIMIT_BLOCK)
    if guards.limit_price_explicit:
        segments.append(tpl.GUARD_SNAPSHOT_BLOCK)
    if guards.order_idempotency:
        segments.append(tpl.GUARD_IDEMPOTENCY_BLOCK)
    body = "\n".join(segments) if segments else "    pass"
    return tpl.GUARD_HELPER_TEMPLATE.replace("__SEGMENTS__", body.rstrip("\n"))


def _order_side(call: ast.Call) -> str:
    """静态推断买卖方向（数量/目标数量/金额字面量；无法判定 → auto 仅基础检查）。"""
    amount = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) \
            and isinstance(call.args[1].value, (int, float)):
        amount = call.args[1].value
    else:
        for kw in call.keywords:
            if kw.arg in ("amount", "value") and isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, (int, float)):
                amount = kw.value.value
                break
    if amount is None:
        return "auto"
    return "buy" if amount > 0 else "sell"  # 目标数量 0 = 清仓 → sell


def _order_call(stmt: ast.stmt):
    """识别下单语句（Expr/Assign 且值为订单家族调用）；返回 (call, 标的表达式, 方向)。"""
    call = None
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
    elif isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
        call = stmt.value
    if call is None:
        return None
    name = call_name(call)
    if name not in ORDER_API_FAMILY:
        return None
    sec = copy.deepcopy(call.args[0]) if call.args else ast.Constant(value=None)
    return call, sec, _order_side(call)


def _make_guard_if(ctx_name: str, sec_expr: ast.expr, side: str,
                   original: ast.stmt) -> ast.If:
    test = ast.Call(
        func=ast.Name(id="_deploy_guard_pass", ctx=ast.Load()),
        args=[ast.Name(id=ctx_name, ctx=ast.Load()), sec_expr,
              ast.Constant(value=side)],
        keywords=[])
    warn = ast.Expr(value=ast.Call(
        func=ast.Attribute(value=ast.Name(id="log", ctx=ast.Load()),
                           attr="warning", ctx=ast.Load()),
        args=[ast.BinOp(
            left=ast.Constant(value="PTRADE-DEPLOY 护栏拦截下单: %s (%s)"),
            op=ast.Mod(),
            right=ast.Tuple(elts=[copy.deepcopy(sec_expr), ast.Constant(value=side)],
                            ctx=ast.Load()))],
        keywords=[]))
    guard_if = ast.If(test=test, body=[original], orelse=[warn])
    return _mark(guard_if, MARKER_GUARD, "wrap")


def _sub_bodies(stmt: ast.stmt) -> List[List[ast.stmt]]:
    """复合语句的直接子语句列表（护栏递归下降范围；Match 等不注入）。"""
    out: List[List[ast.stmt]] = []
    if isinstance(stmt, ast.If):
        out.append(stmt.body)
        if stmt.orelse:
            out.append(stmt.orelse)
    elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
        out.append(stmt.body)
        if stmt.orelse:
            out.append(stmt.orelse)
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        out.append(stmt.body)
    elif isinstance(stmt, ast.Try):
        out.append(stmt.body)
        out.extend(h.body for h in stmt.handlers)
        if stmt.orelse:
            out.append(stmt.orelse)
        if stmt.finalbody:
            out.append(stmt.finalbody)
    return out


def _guard_body(body: List[ast.stmt], loc: str, ctx_name: str,
                edits: List[Dict[str, str]]) -> None:
    for i, stmt in enumerate(body):
        info = _order_call(stmt)
        if info is not None:
            call, sec, side = info
            body[i] = _make_guard_if(ctx_name, sec, side, stmt)
            try:
                detail = ast.unparse(call)
            except Exception:  # pragma: no cover
                detail = "<order call>"
            edits.append({"kind": "GUARD", "location": loc,
                          "detail": f"下单调用前插护栏检查块（side={side}）: {detail}"})
        else:
            for sub in _sub_bodies(stmt):
                _guard_body(sub, loc, ctx_name, edits)


def _apply_guards(tree: ast.Module, cfg, edits: List[Dict[str, str]]) -> None:
    g = cfg.guards
    if not (g.limit_price_explicit or g.limit_up_down_block
            or g.suspension_block or g.order_idempotency):
        return
    helper = ast.parse(_build_guard_helper(g)).body[0]
    _mark(helper, MARKER_GUARD, "wrap")
    tree.body.insert(_after_header_index(tree), helper)
    edits.append({"kind": "GUARD", "location": "<module>",
                  "detail": "注入 _deploy_guard_pass 护栏函数（停牌/涨跌停/限价参考/幂等"
                            "按 guards 配置启停，§6.3）"})
    for fn in [n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name != "_deploy_guard_pass"]:
        ctx_name = fn.args.args[0].arg if fn.args.args else "context"
        _guard_body(fn.body, fn.name, ctx_name, edits)


# ---------------------------------------------------------------- 主入口 --

def transform(source_text: str, profile: Dict[str, object], cfg,
              contract: Dict[str, dict]) -> TransformResult:
    """执行 P3 五步变换（a→e），输出带标记注释的部署产物源码。"""
    tree = ast.parse(source_text)
    table = contract["api"]
    edits: List[Dict[str, str]] = []
    warnings: List[str] = []

    _apply_gates(tree, table, edits, warnings)            # a) GATE 门控
    _apply_g_exempt(tree, list(profile.get("g_exempt") or []), edits)  # e) g.__ 豁免
    if cfg.persistence.enabled:
        _apply_persistence(tree, cfg, edits)              # b) 持久化层
    if cfg.execution.profile in RUN_DAILY_TIME:
        _apply_mount(tree, cfg, edits, profile)          # c) 执行时点挂载
    _apply_guards(tree, cfg, edits)                       # d) 护栏

    return TransformResult(product_text=_emit_module(tree), edits=edits,
                           warnings=warnings)
