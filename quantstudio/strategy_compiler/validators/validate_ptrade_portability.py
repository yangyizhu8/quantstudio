"""validate_ptrade_portability validator (PR6b-1).

Derived from `docs/strategy-compiler/ptrade-profile-contract.md` §3 + IR contract §5.

PTrade-specific portability checks that go BEYOND validate_local_strategy:
the validate_local_strategy API whitelist reads ALL of ptrade_import.py (which
INCLUDES the batch/local-extension APIs). PTrade's public profile forbids those,
so this validator applies a stricter DENYLIST on top.

DENYLIST (ptrade-profile-contract.md §2 + IR contract §5): APIs that are
local-only optimizations or local file/DB access, forbidden in PTrade output:
  - get_fundamentals_batch, get_history_batch  (B1 batch perf APIs)
  - create_dir, get_trades_file, convert_position_from_csv  (local file-system access)

A-1/A-2/A-3 (2026-11-11, user-approved): is_trade / set_commission /
get_research_path are PTrade PUBLIC APIs (verbatim doc evidence in
portability_rules.py next to DENY_REMOVE / PTRADE_REGISTERED_WARN) — they are
no longer denied by name. File/DB access is instead detected at REAL file
access call sites (AST, no regex): open()/os.path.*/os.makedirs/os.remove/
os.rename/os.rmdir/json.dump|load/pickle.dump|load/csv.reader|writer/
shutil.*/subprocess.*/pathlib read_text|write_text|open|read_bytes|
write_bytes/pd.read_*/.to_csv|.to_parquet|.to_pickle — gated by path
conditions (see portability_rules.FILE_DB_* tables, single source).

Returns (ok, violations, warnings) — same shape as PR6a validators.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from ..ir_nodes import StrategyIR
from ..portability_rules import (
    DENY_SHIM,
    FILE_DB_BARE_FUNCS,
    FILE_DB_IO_METHODS,
    FILE_DB_IO_MODULE_ANY_ATTR,
    FILE_DB_IO_MODULE_FUNCS,
    FILE_DB_OS_PATH_PREFIX,
    FILE_DB_PD_MODULES,
    FILE_DB_PD_READ_PREFIX,
    FILE_DB_PATH_SEPARATORS,
    FILE_DB_RESEARCH_PATH_APIS,
    FILE_DB_RESEARCH_ROOT_NAMES,
    INJECTED_WRAPPER_NAMES,
    SHIM_CONTRACT_REGISTRY,
    denylist,
)
from .scan_lookahead import Violation

# T3 修订：DENYLIST 单一来源——并集引用 portability_rules.denylist()，
# 转换器（source_import）与校验器共用同一清单，杜绝双边漂移。
# 消息分类集合仅用于生成 rule_id（不改变判定本身）：
_BATCH_APIS: frozenset[str] = frozenset(DENY_SHIM)  # get_fundamentals_batch / get_history_batch
# A-2（2026-11-11）：get_research_path 移出——PTrade 公有 API（依据见
# portability_rules.py DENY_REMOVE 处逐字引文）；其文件访问风险由下方
# _scan_file_db_io_access（A-3 真实文件访问调用点检测）承接。
_FILE_DB_APIS: frozenset[str] = frozenset({
    "create_dir", "get_trades_file", "convert_position_from_csv",
})
# 其余 DENYLIST 项（本地自创 API / 外部数据源 / 成本模型）→ PORTABILITY-LOCAL-API


# ============================================================================
# A-3（2026-11-11 用户批准·二审 A 项）：真实文件访问调用点检测（AST 遍历，禁用正则）
#   + B6-b 动态取函数判定（getattr 非字面量 / 别名赋值后调用 → WARN，不得静默放行）
#   + B6-c 负向不放松（别名/getattr 可解析到 denylist 名 → 与直接调用同分类 BLOCK）
# 常量表单一来源：portability_rules.FILE_DB_*（B6-a 完整清单，不得只做 open）。
# ============================================================================

_KNOWN_IO_MODULES: frozenset[str] = frozenset(
    {"os", "json", "pickle", "csv", "shutil", "subprocess"} | set(FILE_DB_PD_MODULES)
)


def _collect_import_bindings(
    tree: ast.AST,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """import 绑定归一表（A-3）：把 import 别名/成员导入还原为 (模块, 函数) 配对。

    返回 (mod_alias, from_binding)：
      mod_alias：``import X [as Y]`` → 本地名 → 模块点路径
                 （``import pickle as pk`` → pk→"pickle"；``import os.path as osp``
                   → osp→"os.path"；无 as 时仅整模块名）；
      from_binding：``from M import n [as a]`` → 本地裸名 → (M, n)。
    使 ``pk.dump(...)`` / ``from pickle import dump; dump(...)`` 都能与
    FILE_DB_IO_MODULE_FUNCS 的 (pickle, dump) 精确配对，堵住按别名绕过。
    """
    mod_alias: dict[str, str] = {}
    from_binding: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                local = al.asname or al.name.split(".")[0]
                mod_alias[local] = al.name if al.asname else al.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for al in node.names:
                from_binding[al.asname or al.name] = (node.module, al.name)
    return mod_alias, from_binding


def _resolve_module(local: str | None, mod_alias: dict[str, str]) -> str | None:
    """本地名 → IO 相关模块点路径（非模块名/未知名 → None）。"""
    if not local:
        return None
    dotted = mod_alias.get(local)
    if dotted is not None:
        return dotted
    return local if local in _KNOWN_IO_MODULES else None


def _io_surface_label(
    call: ast.Call,
    mod_alias: dict[str, str],
    from_binding: dict[str, tuple[str, str]],
    defined_names: set[str],
    name_alias_modules: dict[str, str],
) -> str | None:
    """该调用是否属 A-3 IO 调用面；命中返回可读标签（如 "pickle.dump"），否则 None。"""
    f = call.func
    if isinstance(f, ast.Name):
        n = f.id
        fb = from_binding.get(n)
        if fb is not None:
            mod, member = fb
            if mod in FILE_DB_IO_MODULE_ANY_ATTR:
                return f"{mod}.{member}"
            if (mod, member) in FILE_DB_IO_MODULE_FUNCS:
                return f"{mod}.{member}"
            if mod.startswith(FILE_DB_OS_PATH_PREFIX):
                return f"os.path.{member}"
            if mod in FILE_DB_PD_MODULES and member.startswith(FILE_DB_PD_READ_PREFIX):
                return f"{mod}.{member}"
            return None
        if n in FILE_DB_BARE_FUNCS and n not in defined_names:
            return n  # 内建 open（未被本文件 def 遮蔽）
        return None
    if isinstance(f, ast.Attribute):
        attr = f.attr
        # os.path.<anything>(...) 整链（import 别名归一后判定）
        base = f.value
        if isinstance(base, ast.Attribute) and base.attr == "path":
            root = base.value
            if isinstance(root, ast.Name) and \
                    _resolve_module(root.id, mod_alias) == "os":
                return f"os.path.{attr}"
        root_name = base.id if isinstance(base, ast.Name) else None
        mod = _resolve_module(root_name, mod_alias)
        if mod is None and root_name is not None:
            mod = name_alias_modules.get(root_name)  # m = os; m.remove(...) 形态
        if mod is not None:
            if mod in FILE_DB_IO_MODULE_ANY_ATTR:
                return f"{mod}.{attr}"
            if (mod, attr) in FILE_DB_IO_MODULE_FUNCS:
                return f"{mod}.{attr}"
            if mod.startswith(FILE_DB_OS_PATH_PREFIX):
                return f"os.path.{attr}"
            if mod in FILE_DB_PD_MODULES and attr.startswith(FILE_DB_PD_READ_PREFIX):
                return f"{mod}.{attr}"
        if attr in FILE_DB_IO_METHODS:
            return attr  # pathlib 五件套 / .to_csv|.to_parquet|.to_pickle（按方法名收）
    return None


def _path_access_reason(call: ast.Call) -> str | None:
    """A-3 路径条件（三选一命中即返回描述，否则 None）。

    扫描面 = 位置参数 + 关键字值 +（方法调用时的）**接收者表达式**——pathlib 形态
    的路径在接收者（``Path(_PD_NOTEBOOK + 'x').write_text(...)``），不在参数表。
    """
    exprs: list[ast.expr] = list(call.args) + [kw.value for kw in call.keywords]
    if isinstance(call.func, ast.Attribute):
        exprs.append(call.func.value)
    for e in exprs:
        for sub in ast.walk(e):
            if isinstance(sub, ast.Name) and sub.id in FILE_DB_RESEARCH_ROOT_NAMES:
                return f"operand references research-root variable {sub.id!r}"
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and sub.func.id in FILE_DB_RESEARCH_PATH_APIS:
                return "operand uses get_research_path() return value"
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                    and any(sep in sub.value for sep in FILE_DB_PATH_SEPARATORS):
                return f"literal path {sub.value!r} contains a path separator"
    return None


def _build_alias_map(
    tree: ast.AST, mod_alias: dict[str, str],
) -> dict[str, tuple[str, str]]:
    """B6-b/B6-c：函数引用别名表（变量名 → 解析终点）。

    收单目标 Name 的 Assign，右值三形态：Name（``f = api``）/ Attribute
    （``f = mod.api`` / ``f = obj.api``）/ getattr(...)（字面量属性名可解析，
    非字面量 → dynamic）。链式别名（``a = f; b = a``）传递解析；环 → dynamic。
    终点：("name", X) 普通名字｜("attr", A) 属性/字面量 getattr 名｜
    ("attrmod", "mod.attr") 模块可解析的属性别名｜("dynamic", "") 不可解析。
    """
    raw: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        t = node.targets[0]
        if not isinstance(t, ast.Name):
            continue
        v = node.value
        if isinstance(v, ast.Name):
            raw[t.id] = ("name", v.id)
        elif isinstance(v, ast.Attribute):
            mod = None
            if isinstance(v.value, ast.Name):
                mod = _resolve_module(v.value.id, mod_alias)
            if mod is not None:
                raw[t.id] = ("attrmod", f"{mod}.{v.attr}")
            else:
                raw[t.id] = ("attr", v.attr)
        elif isinstance(v, ast.Call) and isinstance(v.func, ast.Name) \
                and v.func.id == "getattr":
            attr_arg = None
            if len(v.args) >= 2:
                attr_arg = v.args[1]
            else:
                for kw in v.keywords:
                    if kw.arg in ("name", "attribute"):
                        attr_arg = kw.value
                        break
            if isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str):
                raw[t.id] = ("attr", attr_arg.value)
            else:
                raw[t.id] = ("dynamic", "")

    def _resolve(n: str, seen: tuple[str, ...] = ()) -> tuple[str, str]:
        if n in seen:
            return ("dynamic", "")
        if n not in raw:
            return ("name", n)
        k, val = raw[n]
        if k == "name":
            return _resolve(val, seen + (n,))
        return (k, val)

    return {n: _resolve(n) for n in raw}


def _scan_file_db_and_dynamic(
    tree: ast.AST,
    violations: list[Violation],
    defined_names: set[str],
) -> None:
    """A-3 IO 调用面×路径条件 + B6-b 动态取函数扫描（结果写入 violations）。

    BLOCK（与直接调用同分类，B6-c 负向不放松）：
      - IO 调用面 × 路径条件 → PORTABILITY-FILE-DB-ACCESS（真实文件访问调用点）；
      - 别名/getattr 可解析到 denylist() 名 → EXTENSION-BAN / FILE-DB-ACCESS /
        LOCAL-API（与既有名字分支同分类，消息标注经由形态）。
    WARN（B6-b，不得静默放行；不影响 ok）：
      - 别名调用且终点不可证实安全 → PORTABILITY-DYNAMIC-CALL；
      - getattr 非字面量属性参数 → PORTABILITY-DYNAMIC-CALL。
    """
    mod_alias, from_binding = _collect_import_bindings(tree)
    alias_map = _build_alias_map(tree, mod_alias)
    # 变量持模块引用形态（m = os; m.remove(...)）：Name 终点可解析为模块的子集
    name_alias_modules: dict[str, str] = {}
    for n, (k, val) in alias_map.items():
        if k == "name":
            dotted = _resolve_module(val, mod_alias)
            if dotted:
                name_alias_modules[n] = dotted

    import builtins as _py_builtins
    provably_safe = (frozenset(dir(_py_builtins)) | defined_names
                     | set(mod_alias) | set(from_binding))

    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    # getattr 节点 id → 以其为 func 的外层调用（getattr(mod,'dump')(args…) 形态）
    outer_by_func_id = {id(c.func): c for c in calls}
    seen_warn: set[tuple[str, int]] = set()

    def _warn_dynamic(line: int, detail: str) -> None:
        key = ("PORTABILITY-DYNAMIC-CALL", line)
        if key in seen_warn:
            return
        seen_warn.add(key)
        violations.append(Violation(
            rule_id="PORTABILITY-DYNAMIC-CALL",
            severity="WARN",
            message=(f"可疑动态调用面：{detail}——静态无法证实为登记平台 API"
                     f"（B6-b：不得静默放行）；若确为安全请人工复核确认，"
                     f"禁止借此绕过 portability 名单。"),
            location=f"line {line}",
        ))

    def _block_file_io(line: int, label: str, reason: str, via: str = "") -> None:
        violations.append(Violation(
            rule_id="PORTABILITY-FILE-DB-ACCESS",
            severity="BLOCK",
            message=(f"PTrade code performs file access at real IO call site "
                     f"{label}(...){via} — {reason} — local file/DB/position "
                     f"access forbidden in PTrade public Profile "
                     f"(ptrade-profile-contract.md §2: 不得访问 DuckDB/Provider/本地文件)."),
            location=f"line {line}",
        ))

    def _classify_deny_hit(name: str, line: int, via: str) -> None:
        """别名/getattr 解析到 denylist 名 → 与直接调用同分类（B6-c）。"""
        if name in _BATCH_APIS:
            violations.append(Violation(
                rule_id="PORTABILITY-LOCAL-EXTENSION-BAN",
                severity="BLOCK",
                message=(f"PTrade code calls {name}() {via} — batch perf API is "
                         f"local-only, forbidden in PTrade public Profile "
                         f"(ptrade-profile-contract.md §2). Must degrade to "
                         f"per-stock loop get_history/get_fundamentals."),
                location=f"line {line}",
            ))
        elif name in _FILE_DB_APIS:
            violations.append(Violation(
                rule_id="PORTABILITY-FILE-DB-ACCESS",
                severity="BLOCK",
                message=(f"PTrade code calls {name}() {via} — local file/DB/position "
                         f"access forbidden in PTrade public Profile "
                         f"(ptrade-profile-contract.md §2: 不得访问 DuckDB/Provider/本地文件)."),
                location=f"line {line}",
            ))
        else:
            violations.append(Violation(
                rule_id="PORTABILITY-LOCAL-API",
                severity="BLOCK",
                message=(f"PTrade code calls {name}() {via} — local-only API "
                         f"(portability_rules DENY_REMOVE/DENY_BLOCK), forbidden "
                         f"in PTrade public Profile."),
                location=f"line {line}",
            ))

    def _module_func_io(mod: str, member: str) -> bool:
        return ((mod, member) in FILE_DB_IO_MODULE_FUNCS
                or mod in FILE_DB_IO_MODULE_ANY_ATTR
                or (mod in FILE_DB_PD_MODULES
                    and member.startswith(FILE_DB_PD_READ_PREFIX))
                or mod.startswith(FILE_DB_OS_PATH_PREFIX))

    for node in calls:
        f = node.func

        # ---- getattr 动态取函数（B6-b / B6-c(2)）----
        if isinstance(f, ast.Name) and f.id == "getattr":
            attr_arg = node.args[1] if len(node.args) >= 2 else next(
                (kw.value for kw in node.keywords
                 if kw.arg in ("name", "attribute")), None)
            if isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str):
                s = attr_arg.value
                if s in denylist():
                    _classify_deny_hit(s, node.lineno, f"via getattr(…, {s!r})")
                else:
                    # getattr(mod, '<io 函数>')(…)：字面量+模块可解析 → 按 IO 调用面判
                    outer = outer_by_func_id.get(id(node))
                    if outer is not None:
                        mod = None
                        a0 = node.args[0] if node.args else None
                        if isinstance(a0, ast.Name):
                            mod = (_resolve_module(a0.id, mod_alias)
                                   or name_alias_modules.get(a0.id))
                        if mod is not None and _module_func_io(mod, s):
                            reason = _path_access_reason(outer)
                            if reason:
                                _block_file_io(outer.lineno, f"getattr→{mod}.{s}",
                                               reason)
            else:
                _warn_dynamic(node.lineno,
                              f"getattr(…, 非字面量属性) @ line {node.lineno}")
            continue  # getattr 节点不按普通 Name 调用重复处理

        # ---- A-3：IO 调用面 × 路径条件（真实文件访问调用点，B6-a 全清单）----
        label = _io_surface_label(node, mod_alias, from_binding,
                                  defined_names, name_alias_modules)
        if label is not None:
            reason = _path_access_reason(node)
            if reason:
                _block_file_io(node.lineno, label, reason)

        # ---- B6-b/B6-c(1)：别名调用（函数名被赋值给变量后再调用）----
        if isinstance(f, ast.Name) and f.id in alias_map and f.id not in defined_names:
            kind, val = alias_map[f.id]
            if kind in ("name", "attr") and val in denylist():
                _classify_deny_hit(val, node.lineno, f"via alias {f.id!r}")
            elif kind == "attrmod":
                mod, _, member = val.partition(".")
                if _module_func_io(mod, member):
                    reason = _path_access_reason(node)
                    if reason:
                        _block_file_io(node.lineno, val, reason, via=" (alias)")
                    else:
                        _warn_dynamic(node.lineno,
                                      f"别名 {f.id!r} → {val}(...) 调用")
                else:
                    _warn_dynamic(node.lineno, f"别名 {f.id!r} → {val} 调用")
            elif kind == "dynamic":
                _warn_dynamic(node.lineno, f"别名 {f.id!r}（源自 getattr 非字面量）调用")
            elif kind == "name" and val in provably_safe:
                pass  # 内建/本文件 def/import 绑定：静态可证实非 denylist 绕过
            elif kind == "name":
                fb = from_binding.get(val)
                if fb is not None and _module_func_io(fb[0], fb[1]):
                    reason = _path_access_reason(node)
                    if reason:
                        _block_file_io(node.lineno, f"{fb[0]}.{fb[1]}", reason,
                                       via=f" (alias {f.id!r})")
                else:
                    _warn_dynamic(node.lineno, f"别名 {f.id!r} → {val}(...) 调用")
            elif kind == "attr" and val in FILE_DB_IO_METHODS:
                reason = _path_access_reason(node)
                if reason:
                    _block_file_io(node.lineno, f".{val}", reason,
                                   via=f" (alias {f.id!r})")
                else:
                    _warn_dynamic(node.lineno, f"别名 {f.id!r} → .{val}(...) 调用")
            else:
                _warn_dynamic(node.lineno, f"别名 {f.id!r} → {val}(...) 调用")


def validate_ptrade_portability(
    code: str,
    ir: StrategyIR | None = None,
    spec: dict[str, Any] | None = None,
) -> tuple[bool, list[Violation], list[str]]:
    """Validate that rendered PTrade .py contains no forbidden local-extension APIs.

    Returns (ok, violations, warnings). ok=False iff any BLOCK violation.
    """
    violations: list[Violation] = []
    warnings: list[str] = []

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        violations.append(Violation(
            rule_id="PORTABILITY-SYNTAX",
            severity="BLOCK",
            message=f"PTrade code has SyntaxError: {e}",
            location=f"line {e.lineno}",
        ))
        return False, violations, warnings

    # 收集产物中定义的函数名（含注入的 shim：def get_history_batch 等）。
    # 若 DENY_SHIM 类 API 在产物中有同名 def 定义，说明转换器已注入 shim
    # （Python 模块级 LEGB 查找，调用绑定到注入的 shim 而非本地扩展）→ 放行。
    # 修复（2026-08-12，zcode）：etf_theme_rotation 转换产物含注入 shim 仍被误 BLOCK。
    _defined_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    _defined_locs: dict[str, int] = {}
    for _node in ast.walk(tree):
        if isinstance(_node, ast.FunctionDef):
            _defined_locs.setdefault(_node.name, _node.lineno)

    # P-D10 三道防线①（机器门禁）：注入 shim/wrapper def 必须登记于
    # SHIM_CONTRACT_REGISTRY（本地契约四要素唯一真相），防第四例漏形状。
    _registered_injected = frozenset(SHIM_CONTRACT_REGISTRY)
    _injectable_names = frozenset(INJECTED_WRAPPER_NAMES) | frozenset(DENY_SHIM)
    for _n in sorted(_defined_names):
        if _n in _injectable_names and _n not in _registered_injected:
            violations.append(Violation(
                rule_id="PORTABILITY-UNREGISTERED-SHIM",
                severity="BLOCK",
                message=(f"PTrade code defines injected shim/wrapper {_n}() not registered in "
                         f"SHIM_CONTRACT_REGISTRY (portability_rules.py). P-D10: every injected "
                         f"shim/wrapper must register its local-contract four elements "
                         f"(type/index/columns/empty) before injection."),
                location=f"line {_defined_locs.get(_n, '?')}",
            ))

    # AST scan for DENYLIST calls
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        f = node.func
        if isinstance(f, ast.Name):
            name = f.id
        elif isinstance(f, ast.Attribute):
            name = f.attr
        if name in denylist():
            # 例外：DENY_SHIM 类 API（get_history_batch / get_fundamentals_batch）
            # 若产物中已有同名 def（注入的 shim），调用绑定到 shim 函数（shim 内部
            # 循环调 get_history/get_fundamentals 公共 API），语义已降级 → 放行。
            # DENY_REMOVE / DENY_BLOCK 类不适用此例外（产物中不会有其 def 定义）。
            if name in _BATCH_APIS and name in _defined_names:
                continue
            # 分类：batch API vs 本地文件/DB 访问 vs 本地自创 API
            if name in _BATCH_APIS:
                rule_id = "PORTABILITY-LOCAL-EXTENSION-BAN"
                msg = (f"PTrade code calls {name}() — batch perf API is local-only, "
                       f"forbidden in PTrade public Profile (ptrade-profile-contract.md §2). "
                       f"Must degrade to per-stock loop get_history/get_fundamentals.")
            elif name in _FILE_DB_APIS:
                rule_id = "PORTABILITY-FILE-DB-ACCESS"
                msg = (f"PTrade code calls {name}() — local file/DB/position access "
                       f"forbidden in PTrade public Profile (ptrade-profile-contract.md §2: "
                       f"不得访问 DuckDB/Provider/本地文件).")
            else:
                rule_id = "PORTABILITY-LOCAL-API"
                msg = (f"PTrade code calls {name}() — local-only API (portability_rules "
                       f"DENY_REMOVE/DENY_BLOCK), forbidden in PTrade public Profile.")
            violations.append(Violation(
                rule_id=rule_id,
                severity="BLOCK",
                message=msg,
                location=f"line {node.lineno}",
            ))

    # A-3 + B6-b（2026-11-11 二审 A 项）：真实文件访问调用点检测（get_research_path
    # 公有化后 FILE-DB 风险在 open()/pickle/os.path 等真实 IO 调用点拦截）
    # + 动态取函数判定（getattr 非字面量 / 别名赋值后调用 → WARN；可解析到
    # denylist 名 → 同分类 BLOCK，B6-c 负向不放松）。
    _scan_file_db_and_dynamic(tree, violations, _defined_names)

    # Header check: PTrade output should declare the profile
    if "ptrade-default" not in code[:500]:
        warnings.append("PTrade code header does not declare 'ptrade-default' profile")

    # 2026-09-03 平台吸收配套（docs/strategy-compiler/ptrade-platform-absorptions-design.md §B）：
    # 转换产物已被吸收（正常产物不命中）；以下规则防 PTrade 直产/未吸收产物再犯（审计 P2-1/P2-4）：
    #   PORTABILITY-COMMISSION-MIN-ZERO      — set_commission 常量 ≤0（平台 IQInvalidArgument 实证）
    #   PORTABILITY-COMMISSION-POSITIONAL    — set_commission 位置参数（注册表 max_positional=0）
    #   PORTABILITY-ASHARES-EXCLUDE_BSE      — get_Ashares 携带本地扩展参数 exclude_bse
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
        if name == "set_commission":
            if node.args:
                violations.append(Violation(
                    rule_id="PORTABILITY-COMMISSION-POSITIONAL",
                    severity="BLOCK",
                    message="set_commission 平台契约为关键字调用（注册表 max_positional=0）；"
                            "位置参数（如 set_commission('ETF', 0.0001, 0)）平台解析错位",
                    location=f"line {node.lineno}"))
            for kw in node.keywords:
                if kw.arg not in ("min_commission", "commission_ratio"):
                    continue
                neg = None
                if isinstance(kw.value, ast.Constant):
                    try:
                        neg = float(kw.value.value)
                    except (TypeError, ValueError):
                        neg = None
                elif (isinstance(kw.value, ast.UnaryOp) and isinstance(kw.value.op, ast.USub)
                        and isinstance(kw.value.operand, ast.Constant)):
                    try:
                        neg = -float(kw.value.operand.value)
                    except (TypeError, ValueError):
                        neg = None
                if neg is not None and neg <= 0:
                    violations.append(Violation(
                        rule_id="PORTABILITY-COMMISSION-MIN-ZERO",
                        severity="BLOCK",
                        message=(f"set_commission {kw.arg}={neg} 平台 arg_checker 拒绝 ≤0"
                                 "（IQInvalidArgument 实证 2026-09-03）；转换管线将吸收为可表达下限"
                                 "（min_commission 0.01 / commission_ratio 1e-6）；PTrade 直产源码请直接写平台契约值"),
                        location=f"line {node.lineno}"))
        elif name == "get_Ashares":
            for kw in node.keywords:
                if kw.arg == "exclude_bse":
                    violations.append(Violation(
                        rule_id="PORTABILITY-ASHARES-EXCLUDE_BSE",
                        severity="BLOCK",
                        message="get_Ashares 的 exclude_bse 为本地扩展参数（平台仅接受 date）；"
                                "转换管线将剥离并烘焙 _QS_EXCLUDE_BSE；PTrade 直产源码请勿携带",
                        location=f"line {node.lineno}"))

    # B2 (2026-08-28/31)：RSRS 量纲防再发——分位 pct（0-100）与分数阈值（0.65/0.75）
    # 直接比较 = 量纲错误（fscore_rsrs 平台 0% 成交根因之一）；产物 p_frac=pct/100 归一后
    # 不命中该模式（RHS 变 0.0x）。WARN 级（产物侧防线，策略编写期由 skill validator 兜底）。
    for _m in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]*pct[A-Za-z0-9_]*)\s*(>=|<=|>|<)\s*(0\.[6-8]\d*)\b",
            code):
        warnings.append(
            f"B2-RSRS-DIMENSION: {_m.group(1)} 与分数阈值 {_m.group(3)} 直接比较"
            "（RSRS 分位 0-100 vs 0-1 量纲可疑）；应先 p_frac=pct/100 归一（rule B2）")

    ok = not any(v.severity == "BLOCK" for v in violations)
    return ok, violations, warnings


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m quantstudio.strategy_compiler.validators.validate_ptrade_portability <ptrade.py>"""
    import sys
    from pathlib import Path
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) < 1:
        print("Usage: validate_ptrade_portability <ptrade.py>", file=sys.stderr)
        return 2
    code = Path(argv[0]).read_text(encoding="utf-8")
    ok, violations, warnings = validate_ptrade_portability(code)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    if ok:
        print(f"VALID: PTrade code passes portability checks ({len(violations)} non-block)")
        return 0
    print(f"INVALID: {len(violations)} portability violation(s):", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
