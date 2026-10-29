# -*- coding: utf-8 -*-
"""P5 平台门禁段（对应设计文档 docs/ptrade-deploy-pipeline-design.md §5.2 P5 / §6.3）。

五项门禁：
a) contract_full —— 契约表全量校验：产物每个 API 调用按 trading 环境查表，
   环境不可用且未被 GATE 门控 → FAIL；表外未知 API（非内建/非本文件定义）→ 警告清单；
b) scheduler_count —— run_daily + run_interval 累计注册数 ≤ 5（F4，超限任务不触发）；
c) pickle_serializable —— g 状态变量 pickle 可序列化静态检查：
   豁免名单（g.__ 前缀）外出现类实例化 / open() 句柄 → FAIL（§6.1）；
d) import_whitelist —— 产物 import 白名单校验（F10 交易环境封闭）；
e) check_strategy —— 官方预检 hook（F7）：接口函数接收产物源码返回 dict；
   默认实现返回 skipped+说明（研究环境运行期调用，M6 接线）。

契约表为唯一 API 事实源（禁止代码内散落硬编码 API 判断）；PBOX 版本依赖
（如 check_limit ≥ PBOXQT1.0V202202.01.038）记入 version_notes 供报告标注（§14.3）。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from .preflight import BUILTIN_NAMES, IMPORT_WHITELIST, call_name

#: 定时任务累计上限（F4：run_daily + run_interval 共享线程池，超限不触发）
SCHEDULER_LIMIT = 5

_MARK_BEGIN = re.compile(
    r"^(\s*)#\s*PTRADE-DEPLOY:(GATE_RESTORE|PERSIST|GUARD|MOUNT|GATE)\s+BEGIN\s*$")


@dataclass
class GateCheck:
    """单项门禁结果（PASS / FAIL / SKIPPED）。"""

    name: str
    status: str
    detail: str = ""
    findings: List[str] = field(default_factory=list)


@dataclass
class GateResult:
    """P5 输出：门禁清单 + 汇总结论 + 版本注记/未知 API 警告。"""

    checks: List[GateCheck] = field(default_factory=list)
    passed: bool = True
    version_notes: List[str] = field(default_factory=list)
    unknown_apis: List[str] = field(default_factory=list)

    def check(self, name: str) -> GateCheck:
        for c in self.checks:
            if c.name == name:
                return c
        raise KeyError(name)

    def to_dict(self) -> dict:
        return {"passed": self.passed,
                "checks": [{"name": c.name, "status": c.status,
                            "detail": c.detail, "findings": c.findings}
                           for c in self.checks],
                "version_notes": self.version_notes,
                "unknown_apis": self.unknown_apis}


# ---------------------------------------------------------------- check_strategy hook --

_check_strategy_impl: Optional[Callable[[str], dict]] = None


def register_check_strategy(fn: Callable[[str], dict]) -> None:
    """注册官方 check_strategy 预检实现（M6 研究环境运行期接线）。"""
    global _check_strategy_impl
    _check_strategy_impl = fn


def run_check_strategy(product_source: str) -> dict:
    """F7 官方预检接口：接收产物源码返回 dict（api_change_list / package_change_list）。

    默认实现返回 skipped + 说明（研究环境不可达时降级为自建门禁，§14.5）。
    """
    if _check_strategy_impl is not None:
        return _check_strategy_impl(product_source)
    return {
        "status": "skipped",
        "reason": "check_strategy 官方预检需研究环境运行期调用（F7，M6 接线）；"
                  "当前降级为自建门禁 ①-④（契约表全量/定时任务/pickle/import）",
    }


# ---------------------------------------------------------------- 辅助 --

#: 视为「已门控」的标记块种类（PD-0012 起含 GATE_RESTORE：嵌套恒真分支还原）
_GATE_BLOCK_KINDS = ("GATE", "GATE_RESTORE")


def _gated_linenos(product_text: str) -> Set[int]:
    """门控标记块覆盖的行号集合（块内调用视为已门控）。

    注：这里曾把种类**硬编码**为 `"GATE"`，PD-0012 引入 `GATE_RESTORE` 时被自测/实跑抓出
    （`GATE_RESTORE` 块被忽略 ⇒ 门控后仍报未门控）。现按 `_GATE_BLOCK_KINDS` 统一判定。
    """
    lines = product_text.split("\n")
    gated: Set[int] = set()
    open_depth = 0
    for idx, ln in enumerate(lines, start=1):
        m = _MARK_BEGIN.match(ln)
        if open_depth == 0:
            if m and m.group(2) in _GATE_BLOCK_KINDS:
                open_depth = 1
        else:
            stripped = ln.strip()
            if stripped.endswith(tuple(f"# PTRADE-DEPLOY:{k} END" for k in _GATE_BLOCK_KINDS)):
                open_depth = 0
            else:
                gated.add(idx)
    return gated


#: 未知 API 门禁豁免的模块名（import 引入的模块名不在 dir(builtins)，
#: call_name 对 `pickle.dump(...)`/`np.array(...)` 返回基名——M3 实测误报后补；
#: numpy/pandas 为 PTrade 官方可 import 第三方（别名 np/pd 常见）；
#: args/kwargs/kwds 为参数解包调用形态（f(**kwargs)），非 API——轮 2 横验证实测补）
MODULE_NAME_EXEMPTIONS = frozenset({
    "pickle", "json", "os", "sys", "math", "time", "datetime", "collections",
    "sqlite3", "copy", "functools", "itertools", "re", "random", "decimal",
    "threading", "queue", "pathlib", "typing", "dataclasses", "enum",
    "np", "pd", "args", "kwargs", "kwds",
})
# 兼容旧名（M3 期名称，避免外部引用断裂）
STDLIB_MODULE_NAMES = MODULE_NAME_EXEMPTIONS


def _add_target_names(target, names: Set[str]) -> None:
    """解包目标展开（`a, b = ...` / `a, *rest = ...` / `(a, b)`）。"""
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _add_target_names(elt, names)
    elif isinstance(target, ast.Starred):
        _add_target_names(target.value, names)


def _local_names(tree: ast.Module) -> Set[str]:
    """全量收集本文件绑定名（含嵌套函数/类 + import 绑定名 + 7 类作用域绑定）。

    嵌套函数（M3 固件命中）与 import 别名（M7 横验证命中：`import pandas as
    _qs_pd`、`from datetime import date`、wrapper 的 `from x import history`）
    都不是平台 API，不应被未知 API 门禁误报。

    PD-0008 补全（P5 误报修复）：`call_name` 对 `X.method()` 返回**根对象名 X**
    （为兼容 `log.info` 归入契约表 `log` 条目），故 X 只要不是本地绑定就会被报
    「契约表外」。旧实现只收 函数/类名 + 赋值目标 + import 绑定，漏掉 7 类作用域
    绑定 —— `history.keys()`（history 为参数）、`ch.isdigit()`（ch 为 for 目标）
    因而误报。此处补全：函数参数 / for 目标 / with as / except 名 / walrus 目标 /
    推导式目标 / 解包赋值目标。
    """
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            args = node.args
            for arg in (list(args.posonlyargs) + list(args.args)
                        + list(args.kwonlyargs)
                        + ([args.vararg] if args.vararg else [])
                        + ([args.kwarg] if args.kwarg else [])):
                names.add(arg.arg)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                _add_target_names(tgt, names)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _add_target_names(node.target, names)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _add_target_names(node.target, names)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                _add_target_names(node.optional_vars, names)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
        elif isinstance(node, ast.NamedExpr):
            _add_target_names(node.target, names)
        elif isinstance(node, ast.comprehension):
            _add_target_names(node.target, names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
    return names


#: 引擎注入全局（PTrade 运行时提供；其属性/方法调用不是平台 API —— PD-0008）
ENGINE_GLOBALS = frozenset({"g"})


def _is_class_instantiation(call: ast.Call) -> bool:
    """类实例化启发式：直呼名去下划线后首字母大写（PEP8 类命名约定）。"""
    if isinstance(call.func, ast.Name):
        base = call.func.id.lstrip("_")
        return bool(base) and base[0].isupper()
    return False


def _g_assign_targets(tree: ast.Module) -> List[ast.Assign]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) \
                        and tgt.value.id == "g":
                    out.append(node)
    return out


# ---------------------------------------------------------------- 主入口 --

def run_gates(product_text: str, contract: Dict[str, dict],
              config=None) -> GateResult:
    """执行 P5 五项门禁（a-e），任一 FAIL → passed=False。"""
    result = GateResult()
    tree = ast.parse(product_text)
    table = contract["api"]
    gated = _gated_linenos(product_text)
    local_names = _local_names(tree)

    # a) 契约表全量校验（trading 环境口径）
    findings: List[str] = []
    unknown: List[str] = []
    version_notes: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if name is None:
            continue
        if name in table:
            entry = table[name]
            if not entry["trading"] and getattr(node, "lineno", None) not in gated:
                findings.append(f"行 {node.lineno}: {name} 交易环境不可用且未门控（契约表 trading:false）")
            pbox = entry.get("pbox_version", "all")
            if pbox and pbox != "all":
                version_notes.append(f"{name} 依赖 PBOX ≥ {pbox}（{entry.get('source', '')}）")
        elif name not in BUILTIN_NAMES and name not in STDLIB_MODULE_NAMES \
                and name not in local_names and name not in ENGINE_GLOBALS:
            unknown.append(f"行 {getattr(node, 'lineno', '?')}: {name}（契约表外，需人工确认）")
    result.unknown_apis = unknown
    result.version_notes = sorted(set(version_notes))
    result.checks.append(GateCheck(
        "contract_full", "FAIL" if findings else "PASS",
        "产物 API 调用按 trading 环境查契约表" + ("；未知 API 见 unknown_apis" if unknown else ""),
        findings))

    # b) 定时任务累计 ≤ 5（F4）
    sched = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and call_name(n) in ("run_daily", "run_interval")]
    sched_findings = []
    if len(sched) > SCHEDULER_LIMIT:
        sched_findings = [f"run_daily+run_interval 累计注册 {len(sched)} 次 > 上限 {SCHEDULER_LIMIT}（F4 超限任务不触发）"]
    result.checks.append(GateCheck(
        "scheduler_count", "FAIL" if sched_findings else "PASS",
        f"定时任务注册数 {len(sched)}/{SCHEDULER_LIMIT}（F4）", sched_findings))

    # c) g 状态变量 pickle 可序列化静态检查（豁免名单 = g.__ 前缀，F1/§6.1）
    pickle_findings: List[str] = []
    for assign in _g_assign_targets(tree):
        for tgt in assign.targets:
            if not (isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "g"):
                continue
            if tgt.attr.startswith("__"):
                continue  # g.__ 前缀 = 框架自动持久化豁免名单
            value = assign.value
            if isinstance(value, ast.Call):
                if call_name(value) == "open":
                    pickle_findings.append(
                        f"行 {assign.lineno}: g.{tgt.attr} = open(...)（IO 句柄不可 pickle，应改 g.__{tgt.attr} 豁免）")
                elif _is_class_instantiation(value):
                    pickle_findings.append(
                        f"行 {assign.lineno}: g.{tgt.attr} = {ast.unparse(value)[:60]}"
                        f"（类实例应改 g.__{tgt.attr} 豁免，§6.1）")
    result.checks.append(GateCheck(
        "pickle_serializable", "FAIL" if pickle_findings else "PASS",
        "g 状态变量可序列化静态检查（豁免名单外类实例/IO → FAIL）", pickle_findings))

    # d) 产物 import 白名单（F10 交易环境封闭）
    import_findings: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in IMPORT_WHITELIST:
                    import_findings.append(f"行 {node.lineno}: import {alias.name}（白名单外）")
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                import_findings.append(f"行 {node.lineno}: 相对导入（自有包形态，F10 禁止）")
            elif node.module and node.module.split(".")[0] not in IMPORT_WHITELIST:
                import_findings.append(f"行 {node.lineno}: from {node.module} import ...（白名单外）")
    result.checks.append(GateCheck(
        "import_whitelist", "FAIL" if import_findings else "PASS",
        "产物 import 白名单校验（stdlib 常见 + 项目既有依赖 + 内置 tushare）",
        import_findings))

    # e) check_strategy 官方预检 hook（F7；默认 skipped，M6 接线）
    cs = run_check_strategy(product_text)
    result.checks.append(GateCheck(
        "check_strategy", cs.get("status", "skipped").upper(),
        str(cs.get("reason", cs.get("detail", "官方预检结果"))),
        [str(cs.get(k)) for k in ("api_change_list", "package_change_list") if cs.get(k)]))

    result.passed = all(c.status != "FAIL" for c in result.checks)
    return result
