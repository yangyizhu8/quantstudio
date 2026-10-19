# -*- coding: utf-8 -*-
"""P0 预检段（对应设计文档 docs/ptrade-deploy-pipeline-design.md §5.2 P0 / §8.4）。

职责：
1. AST 可解析校验（不可解析 → REJECT）；
2. vault 知识库回哺读取（trading 仓 vault/knowledge/ptrade-deploy/ 的 frontmatter
   条目：id/symptom/status；路径不可达时降级 degraded+警告，不抛异常）；
3. 危险 import 扫描（F10：交易环境封闭，不能 import 自有包/连外网——
   白名单外模块即视为自有包/危险依赖 → REJECT）；
4. 按契约表统计回测专用/交易专用 API 调用并判定可转换性
   （依赖 get_trades_file / convert_position_from_csv 等回测专用核心逻辑 → REJECT）。

契约表由本模块统一加载（load_contract，带缓存），供 P1/P3/P5 复用——
契约表是唯一 API 事实源，禁止各段代码内散落硬编码 API 判断（设计文档 §5.1）。
"""
from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

# ---------------------------------------------------------------- 常量 --

#: vault 知识库目录（批复修订②：vault 落 QuantStudio-trading 侧，主仓仅存指针）
VAULT_DIR = Path(r"D:\miniQMT策略实盘\QuantStudio-trading\vault\knowledge\ptrade-deploy")

#: import 白名单（F10 交易环境封闭：stdlib 常见 + 项目既有三方 + 内置 tushare；
#: 模块顶部常量便于扩展——新增白名单条目属契约层变更，走框架层六步流水线）
IMPORT_WHITELIST = frozenset({
    # stdlib 常见（文件/序列化/基础数据结构/时间/正则等）
    "os", "sys", "re", "json", "pickle", "math", "time", "datetime",
    "collections", "copy", "functools", "itertools", "enum", "string",
    "csv", "sqlite3", "typing", "traceback", "random", "decimal",
    "statistics", "heapq", "bisect", "array", "struct", "uuid",
    # 项目既有依赖（pyproject.toml dependencies）
    "numpy", "pandas", "dateutil", "pytz",
    # PTrade 内置数据源（F10：tushare 内置除外）
    "tushare",
})
# 白名单明确排除：requests/urllib/socket/http/subprocess/threading 等
# 网络/进程/线程模块（交易环境禁止外联，F10）。


# ---------------------------------------------------------------- 数据结构 --

@dataclass
class PreflightResult:
    """P0 预检报告（PASS/REJECT + 结构化理由，机读友好）。"""

    passed: bool
    ast_ok: bool
    reject_reasons: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    backtest_only_calls: Dict[str, int] = field(default_factory=dict)
    trading_only_calls: Dict[str, int] = field(default_factory=dict)
    vault_entries: List[Dict[str, str]] = field(default_factory=list)
    vault_degraded: bool = False
    vault_warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- 契约表加载 --

@lru_cache(maxsize=4)
def load_contract(path: Optional[str] = None) -> Dict[str, dict]:
    """加载契约表（唯一 API 事实源；带缓存，重复调用零开销）。"""
    p = Path(path) if path else Path(__file__).resolve().parent / "contract_table.yaml"
    if not p.exists():
        raise FileNotFoundError(f"契约表不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "api" not in data:
        raise ValueError(f"契约表 schema 非法（缺 api 键）: {p}")
    return data


def call_name(node: ast.Call) -> Optional[str]:
    """提取调用点函数名（仅支持 Name 直呼与 log.info 这类单层属性调用）。"""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        # 如 log.info —— 以根对象名 log 入表（契约表 log 条目即此口径）
        return func.value.id
    return None


# ---------------------------------------------------------------- vault 回哺 --

def _parse_frontmatter(text: str) -> Dict[str, str]:
    """极简 frontmatter 解析（只取 id/symptom/status 等顶层键，避免引入依赖）。"""
    fm: Dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fm
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" in stripped and not stripped.startswith(("-", " ", "#")):
            key, _, val = stripped.partition(":")
            fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm


def read_vault(vault_dir: Optional[Path] = None) -> Tuple[List[Dict[str, str]], bool, List[str]]:
    """读取 vault 知识条目（§8.4 回哺机制）。

    返回 (entries, degraded, warnings)；路径不可达 → degraded=True + 警告，
    绝不抛异常（设计文档 §14.6：trading 仓不可达不阻塞转换）。
    """
    vdir = Path(vault_dir) if vault_dir else VAULT_DIR
    entries: List[Dict[str, str]] = []
    warnings: List[str] = []
    if not vdir.exists():
        return entries, True, [f"vault 知识库路径不可达，回哺降级（degraded）: {vdir}"]
    # sorted 保证确定性（同输入同输出，交付段 SHA256 门禁前提）
    for md in sorted(vdir.glob("*.md")):
        try:
            # PD-2026-0009：用 utf-8-sig 读——vault 条目可能被带 BOM 的编辑器写出，
            # 而 `_parse_frontmatter` 以 `lines[0].strip() == "---"` 起判，BOM 会让
            # 首行变成 "\ufeff---"（str.strip 不去 BOM）→ 条目被**静默跳过**，
            # 回哺链面上"存在"实则未生效（实测 8 条中 5 条如此）。
            text = md.read_text(encoding="utf-8-sig")
        except OSError as exc:  # 单文件损坏不拖垮整段
            warnings.append(f"vault 条目读取失败（跳过）: {md.name} ({exc})")
            continue
        fm = _parse_frontmatter(text)
        if not fm:
            # 断链必须可见：条目文件名是 PD-* 但 frontmatter 解析不出来 → 告警
            if md.stem.upper().startswith("PD-"):
                warnings.append(
                    "vault 条目 frontmatter 解析失败（回哺未生效）："
                    f"{md.name}——检查首行是否 `---`（BOM/空白）或 YAML 形态")
            continue  # 非 frontmatter 文件（README 等）不计入条目
        entries.append({
            "file": md.name,
            "id": fm.get("id", ""),
            "symptom": fm.get("symptom", ""),
            "status": fm.get("status", ""),
        })
    return entries, False, warnings


# ---------------------------------------------------------------- 危险 import 扫描 --

def _import_roots(tree: ast.Module) -> List[str]:
    roots: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # 相对导入（level>0）即自有包形态，单列
                roots.append(node.module.split(".")[0])
            elif node.level > 0:
                roots.append(f"<relative>{node.module or ''}")
    return roots


# ---------------------------------------------------------------- 主入口 --

def run_preflight(source_text: str,
                  contract: Optional[Dict[str, dict]] = None,
                  vault_dir: Optional[Path] = None) -> PreflightResult:
    """执行 P0 预检四项（AST / vault / import / API 可转换性）。"""
    table = (contract or load_contract())["api"]

    # ① AST 可解析
    try:
        tree = ast.parse(source_text)
    except SyntaxError as exc:
        return PreflightResult(
            passed=False,
            ast_ok=False,
            reject_reasons=[f"AST 解析失败（{exc.msg}，行 {exc.lineno}）"],
        )

    findings: List[str] = []
    reject_reasons: List[str] = []

    # ② vault 回哺读取（降级不阻塞，§14.6）
    entries, degraded, vault_warnings = read_vault(vault_dir)
    findings.extend(vault_warnings)
    open_entries = [e for e in entries if e.get("status") == "open"]
    for e in open_entries:  # §8.4：open 状态条目在报告中显式警告
        findings.append(f"vault 开放条目（已知坑未闭环）: {e['id']} {e['symptom']}")

    # ③ 危险 import 扫描（F10：白名单外 = 自有包/危险依赖 → REJECT）
    for root in _import_roots(tree):
        if root.startswith("<relative>") or root.split(".")[0] not in IMPORT_WHITELIST:
            reject_reasons.append(f"危险 import（F10 交易环境封闭，白名单外）: {root}")

    # ④ 按契约表统计 API 调用并判定可转换性
    backtest_only: Dict[str, int] = {}
    trading_only: Dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if name is None or name not in table:
            continue
        entry = table[name]
        if entry["backtest"] and not entry["trading"]:
            backtest_only[name] = backtest_only.get(name, 0) + 1
            if entry.get("action") == "reject":
                reject_reasons.append(
                    f"依赖回测专用核心逻辑（契约表 action=reject）: {name}——交易环境不可转换")
        elif entry["trading"] and not entry["backtest"]:
            trading_only[name] = trading_only.get(name, 0) + 1
            findings.append(
                f"回测源码出现交易专用 API（回测不可用，转换保留）: {name}")

    passed = not reject_reasons
    return PreflightResult(
        passed=passed,
        ast_ok=True,
        reject_reasons=reject_reasons,
        findings=findings,
        backtest_only_calls=backtest_only,
        trading_only_calls=trading_only,
        vault_entries=entries,
        vault_degraded=degraded,
        vault_warnings=vault_warnings,
    )


#: 内建名集合（gates 段复用：区分未知平台 API 与普通内建调用）
BUILTIN_NAMES = frozenset(dir(builtins))
