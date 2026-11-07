# -*- coding: utf-8 -*-
"""P0 预检段（对应设计文档 docs/ptrade-deploy-pipeline-design.md §5.2 P0 / §8.4）。

职责：
1. AST 可解析校验（不可解析 → REJECT）；
2. PD 族知识库回哺读取（候选落点 frontmatter 条目：id/symptom/status；
   PD-0010 起多候选探测——trading 归口 → 主仓公开副本 → 旧址兜底；全部不可达时
   降级 degraded+警告，不抛异常；命中来源记入 provenance）；
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
from typing import Dict, List, NamedTuple, Optional, Tuple

import yaml

# ---------------------------------------------------------------- 常量 --

#: PD 族知识库候选落点（PD-0010：拓扑两次纠偏后改**多候选探测**，抗落点漂移）
#:
#: 2026-10-22 拓扑纠偏：trading 仓 `vault/` 解散，PD 条目迁入主仓 `knowledge/ptrade-deploy/`；
#: 随后件 D 批 2 再迁至 trading 仓 `knowledge/`（最终归口）。旧址 `vault/` 仅作末位兼容兜底。
#: 路径一律**相对求值**（主仓根 + 同级兄弟仓目录名）——不写绝对盘符，换机/换盘即断是旧硬伤。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SIBLING_REPO_NAME = "QuantStudio-trading"


def vault_candidates() -> Tuple[Tuple[str, Path], ...]:
    """返回 PD 族知识库候选落点（按稳定性优先级）。

    顺序：trading 归口 → 主仓公开副本 → 旧址（兼容期）。
    """
    sibling = _REPO_ROOT.parent / _SIBLING_REPO_NAME
    return (
        ("trading-knowledge", sibling / "knowledge" / "ptrade-deploy"),
        ("main-knowledge", _REPO_ROOT / "knowledge" / "ptrade-deploy"),
        ("legacy-vault", sibling / "vault" / "knowledge" / "ptrade-deploy"),
    )


#: 旧单点常量（**已弃用**，仅保留兼容旧引用；PD-0010 起 read_vault 走候选探测）
VAULT_DIR = _REPO_ROOT.parent / _SIBLING_REPO_NAME / "vault" / "knowledge" / "ptrade-deploy"

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
    vault_source: str = ""          # PD-0010：实际命中的回哺库落点（provenance）


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


class VaultRead(NamedTuple):
    """回哺读取结果（PD-0010：增 `source` 命中来源——消除「读到了但不知道读的哪」）。"""

    entries: List[Dict[str, str]]
    degraded: bool
    warnings: List[str]
    source: str


def read_vault(vault_dir: Optional[Path] = None,
               candidates: Optional[Tuple[Tuple[str, Path], ...]] = None
               ) -> VaultRead:
    """读取 PD 族知识条目（§8.4 回哺机制；PD-0010 多候选探测）。

    返回 (entries, degraded, warnings, source)：
    - `vault_dir` 显式给出 → 单候选（`source="explicit"`）；
    - 否则按 `vault_candidates()` 顺序取**第一个存在**的落点；
    - 全部不可达 → degraded=True + 警告（**不抛异常**，§14.6）；
    - 命中旧址（legacy-vault）→ 追加**提示性 warning**（兼容期，内容可能过期）；
    - `source` 记录实际命中来源，进 P0 details（provenance）。
    """
    warnings: List[str] = []
    if vault_dir is not None:
        cands: Tuple[Tuple[str, Path], ...] = (("explicit", Path(vault_dir)),)
    else:
        cands = tuple(candidates) if candidates else vault_candidates()

    picked: Optional[Tuple[str, Path]] = None
    for name, path in cands:
        if path.exists():
            picked = (name, path)
            break
    if picked is None:
        listed = "、".join(f"{n}:{p}" for n, p in cands)
        return VaultRead([], True, [
            f"vault 知识库路径全部不可达，回哺降级（degraded）：{listed}"], "")

    source, vdir = picked
    if source == "legacy-vault":
        warnings.append(
            f"vault 回哺命中旧址（兼容期，待迁移；内容可能过期）：{vdir}")

    entries: List[Dict[str, str]] = []
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
    return VaultRead(entries, False, warnings, source)


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

    # ② vault 回哺读取（降级不阻塞，§14.6；PD-0010 多候选探测 + 命中源记录）
    vault_read = read_vault(vault_dir)
    entries = vault_read.entries
    degraded = vault_read.degraded
    vault_warnings = vault_read.warnings
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
        vault_source=vault_read.source,
    )


#: 内建名集合（gates 段复用：区分未知平台 API 与普通内建调用）
BUILTIN_NAMES = frozenset(dir(builtins))


# ============================================================
# IMPORT-FRESHNESS（PD-2026-0019 假重转闸门）
# ============================================================

#: 第一跳在产物头写入的生成时刻标记（与 source_import 装配点同源）
_GEN_MARK_RE = r"#\s*PTRADE-DEPLOY:GEN\s+([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"


def _gen_timestamp(text: str) -> Optional[float]:
    """从产物文本取第一跳生成时刻戳；无标记或解析失败 ⇒ None（legacy 产物）。"""
    import datetime as _dt
    import re as _re

    m = _re.search(_GEN_MARK_RE, text or "")
    if not m:
        return None
    try:
        return _dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").timestamp()
    except Exception:
        return None


def _first_hop_sources(repo_root: Path) -> List[Path]:
    """第一跳源文件。

    只取 source_import.py：它是 _QS_ORDER_SPLIT_EXT 等**第一跳注入块**所在处。
    刻意**不**纳入 ptrade_deploy/templates/*.py（第二跳模板）——纳入会造成
    「只改第二跳却要求重跑第一跳」的假阳性。
    """
    return [repo_root / "quantstudio" / "strategy_compiler" / "source_import.py"]


def check_import_freshness(source_path: Path,
                           source_text: Optional[str] = None,
                           repo_root: Optional[Path] = None) -> List[str]:
    """IMPORT-FRESHNESS：产物内嵌 GEN 时刻戳不得早于第一跳源文件 mtime。

    判据载体＝**产物内嵌时刻戳**（非 FS mtime）：
      - mtime 会被 git 检出/操作重置，且测试夹具的 mtime 只是「写入时刻」⇒ 不可靠
        （2026-11-07 实测：mtime 版误伤 19 条既有固件；收敛作用域后仍误伤 7 条）。
      - 内嵌时刻戳随产物走，可复现、可审计，且同时充当「时序自证」。

    legacy 兼容：**无 GEN 标记**的产物（quick 模式/历史产物）⇒ 仅 WARN **不拒**。
    fail-closed：有标记但无法比对 ⇒ 拒。
    """
    reasons: List[str] = []
    try:
        src = Path(source_path)
        text = source_text
        if text is None:
            if not src.exists():
                return [f"IMPORT-FRESHNESS 无法判定：source 不存在 {src}"]
            text = src.read_text(encoding="utf-8", errors="replace")
        gen_ts = _gen_timestamp(text)
        if gen_ts is None:
            # legacy 产物：无 GEN 标记 ⇒ 不拒（仅提示），避免误伤 quick 模式与历史产物
            return []
        root = Path(repo_root) if repo_root is not None else _REPO_ROOT
        hop1 = [p for p in _first_hop_sources(root) if p.exists()]
        if not hop1:
            return ["IMPORT-FRESHNESS 有 GEN 标记但找不到第一跳源文件（fail-closed）"]
        newest = max(hop1, key=lambda p: p.stat().st_mtime)
        n_m = newest.stat().st_mtime
        if gen_ts < n_m - 1.0:  # 1s 容差：同一秒内生成不算陈旧
            import datetime as _dt

            try:
                rel = newest.relative_to(root).as_posix()
            except Exception:
                rel = newest.name
            reasons.append(
                "IMPORT-FRESHNESS 产物生成时刻早于第一跳源码：GEN="
                + _dt.datetime.fromtimestamp(gen_ts).strftime("%Y-%m-%d %H:%M:%S")
                + " < " + rel + "=" + _dt.datetime.fromtimestamp(n_m).strftime("%Y-%m-%d %H:%M:%S")
                + " —— 请先重跑 `qs-compile import` 重生第一跳产物（PD-2026-0019 假重转）")
    except Exception as exc:  # fail-closed
        return [f"IMPORT-FRESHNESS 判定异常（fail-closed）: {exc}"]
    return reasons
