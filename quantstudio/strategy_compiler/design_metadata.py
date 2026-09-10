# -*- coding: utf-8 -*-
"""设计元数据可信解析层（2026-09-09，docs/design-metadata-auto-profile-design.md v4）。

转换管线感知 agent_strategy_design.json 的元数据。当前消费者：engine_profile
（get_index_day_bar 重写门禁输入）；未来任何需设计元数据的转换/校验逻辑复用本层。

权威解析链（审计+复审+终审钉死）：
- 只扫 agent_workspace/*/workspace_state.json 一层（不递归 output/pytest_tmp/历史）；
- 候选归属链：先按源码静态 STRATEGY_ID 定位 agent_workspace/<STRATEGY_ID>/，再按 ledger
  quantstudio_output 精确路径匹配；
- ledger 生命周期/路径安全校验：stage==PUBLISHED 且 publish_status==PASS 且
  quantstudio_output_status==GENERATED 且 formal_publish_allowed==True；路径 resolve(strict=True)
  仍在项目根（防符号链接逃逸/.. /盘符跳转）；
- ledger canonical_sha256 == 当前源文件 SHA；design 与 ledger 三字段一致；design 过 2.3 schema；
- 返回结构化 DesignMetadataResolution（杜绝 None 混淆故障与正常 legacy）。

设计 schema 固定源：仓库跟踪 skills/quantstudio-strategy-compiler/schemas/agent_strategy_design.schema.json
（不读用户级 .agents 副本——防客户机/项目内分叉）。
本模块只读、无副作用，不写回 design/ledger。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

from quantstudio._paths import _ROOT  # 项目根（quantstudio/..）

# 仓库跟踪的 design schema（运行时唯一事实源）
DESIGN_SCHEMA_PATH = (_ROOT / "skills" / "quantstudio-strategy-compiler"
                      / "schemas" / "agent_strategy_design.schema.json")
SUPPORTED_DESIGN_VERSION = "2.3"

VALID_PROFILES = ("daily-bar-v1", "minute-bar-v1", "daily-open-close-proxy-v1")
_MINUTE_FREQS = ("1m", "5m", "15m", "30m", "60m")


@dataclass
class DesignMetadataResolution:
    """结构化解析结果（终审钉死：None 会把故障与正常 legacy 混为一类，禁止）。

    status 枚举：
    - RESOLVED                可信 design 命中，engine_profile 可用
    - NOT_FOUND_LEGACY        无 design 候选（legacy 策略，可人工指定 profile）
    - AMBIGUOUS               多个 ledger 精确指向同一策略文件
    - INVALID_JSON            ledger/design JSON 损坏
    - HASH_MISMATCH           ledger canonical_sha256 != 源文件 SHA
    - LEDGER_MISMATCH         ledger/design 三字段不一致 或 生命周期组合不一致
    - INVALID_PROFILE         profile 枚举外 或 组合一致性失败
    - SCHEMA_UNAVAILABLE      design schema 文件缺失/不可读
    - UNSUPPORTED_DESIGN_VERSION  design 版本非 2.3
    """
    status: str
    design_path: Optional[str] = None
    ledger_path: Optional[str] = None
    strategy_id: Optional[str] = None
    engine_profile: Optional[str] = None
    source_sha256: Optional[str] = None
    design_sha256: Optional[str] = None
    schema_sha256: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "design_path": self.design_path,
            "ledger_path": self.ledger_path,
            "strategy_id": self.strategy_id,
            "engine_profile": self.engine_profile,
            "source_sha256": self.source_sha256,
            "design_sha256": self.design_sha256,
            "schema_sha256": self.schema_sha256,
            "reason": self.reason,
        }


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rel_path(p: Path, root: Path) -> str:
    """项目根相对路径（防客户机绝对路径入可复现产物）。"""
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(p)


def _extract_static_strategy_id(source_text: str) -> Optional[str]:
    """AST 提取源码静态 STRATEGY_ID="<literal>"（候选归属链定位用，不构成可信证据）。"""
    import ast
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return None
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1                 and isinstance(node.targets[0], ast.Name)                 and node.targets[0].id == "STRATEGY_ID":
            v = node.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                found.append(v.value)
    if len(found) == 1:
        return found[0]
    return None  # 0 个或重复赋值 → 不可信


def _profile_combo_valid(profile_id: str, design: dict) -> bool:
    """profile 组合一致性校验（终审阻断 4：不只校验名称）。"""
    ep = design.get("engine_profile") or {}
    bf = ep.get("bar_frequency")
    mp = ep.get("match_price_mode")
    if profile_id == "daily-bar-v1":
        return bf == "1d" and mp == "close"
    if profile_id == "minute-bar-v1":
        return bf in _MINUTE_FREQS
    if profile_id == "daily-open-close-proxy-v1":
        return bf == "1d"
    return False


def _schema_available() -> tuple[bool, Optional[str]]:
    """schema 可用性 + SHA（固定仓库源）。返回 (ok, sha_or_reason)。"""
    if jsonschema is None:
        return False, "jsonschema not available"
    if not DESIGN_SCHEMA_PATH.exists():
        return False, "SCHEMA_UNAVAILABLE"
    try:
        schema = json.loads(DESIGN_SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False, "SCHEMA_UNAVAILABLE"
    ver = (schema.get("properties", {}).get("design_version", {})
           .get("enum", []))
    if SUPPORTED_DESIGN_VERSION not in ver:
        return False, "UNSUPPORTED_DESIGN_VERSION"
    return True, _sha256(DESIGN_SCHEMA_PATH)


def find_design_for_strategy(
    strategy_path: str | Path,
    project_root: str | Path | None = None,
) -> DesignMetadataResolution:
    """权威解析链：返回结构化 DesignMetadataResolution。"""
    sp = Path(strategy_path)
    root = Path(project_root) if project_root else _ROOT
    if not sp.is_absolute():
        sp = root / sp
    sp = sp.resolve(strict=True) if sp.exists() else sp.resolve()
    if not sp.is_file():
        return DesignMetadataResolution(status="INVALID_JSON", reason="source file not found")
    source_sha = _sha256(sp)

    # ---- 候选归属链 1：源码静态 STRATEGY_ID ----
    source_text = sp.read_text(encoding="utf-8", errors="ignore")
    sid = _extract_static_strategy_id(source_text)
    candidate_ledgers: list[Path] = []
    if sid:
        wd = (root / "agent_workspace" / sid).resolve(strict=False)
        lp = wd / "workspace_state.json"
        if lp.exists():
            candidate_ledgers.append(lp)

    # ---- 候选归属链 2：可解析 ledger 的 quantstudio_output 精确路径 ----
    if not candidate_ledgers:
        ws_root = (root / "agent_workspace").resolve(strict=False)
        if ws_root.is_dir():
            for ledger in sorted(ws_root.glob("*/workspace_state.json")):
                try:
                    st = json.loads(ledger.read_text(encoding="utf-8"))
                except Exception:
                    continue  # 无关损坏 ledger 不阻断
                qo = st.get("quantstudio_output")
                if qo:
                    qp = (root / qo).resolve(strict=False)
                    if qp == sp:
                        candidate_ledgers.append(ledger)

    if not candidate_ledgers:
        # 本策略 STRATEGY_ID 路径存在但损坏 → 不得降级 legacy
        if sid:
            wd = (root / "agent_workspace" / sid).resolve(strict=False)
            lp = wd / "workspace_state.json"
            if lp.exists():
                try:
                    json.loads(lp.read_text(encoding="utf-8"))
                except Exception:
                    return DesignMetadataResolution(
                        status="INVALID_JSON", source_sha256=source_sha,
                        reason=f"ledger for STRATEGY_ID {sid!r} is corrupted JSON")
        return DesignMetadataResolution(
            status="NOT_FOUND_LEGACY", source_sha256=source_sha,
            reason="no trusted design/ledger found")

    if len(candidate_ledgers) > 1:
        return DesignMetadataResolution(
            status="AMBIGUOUS", source_sha256=source_sha,
            reason=f"multiple ledgers point to same strategy: {sorted(str(p) for p in candidate_ledgers)[:3]}...")

    ledger = candidate_ledgers[0]
    # ---- 路径安全 ----
    try:
        resolved = ledger.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
        if root_resolved not in resolved.parents:
            return DesignMetadataResolution(
                status="LEDGER_MISMATCH", source_sha256=source_sha,
                reason="ledger escapes project root (symlink/../drive escape)")
    except OSError:
        return DesignMetadataResolution(
            status="INVALID_JSON", source_sha256=source_sha,
            reason="ledger path unreadable")

    # ---- ledger 读取 + 生命周期 ----
    try:
        st = json.loads(ledger.read_text(encoding="utf-8"))
    except Exception as e:
        return DesignMetadataResolution(
            status="INVALID_JSON", source_sha256=source_sha,
            ledger_path=_rel_path(ledger, root),
            reason=(f"ledger invalid JSON (STRATEGY_ID={sid!r} 候选归属链命中): {e}"))
    lc_stage = st.get("stage")
    lc_pub = st.get("publish_status")
    lc_qout = st.get("quantstudio_output_status")
    lc_fpa = st.get("formal_publish_allowed")
    if not (lc_stage == "PUBLISHED" and lc_pub == "PASS"
            and lc_qout == "GENERATED" and lc_fpa is True):
        return DesignMetadataResolution(
            status="LEDGER_MISMATCH", source_sha256=source_sha,
            ledger_path=_rel_path(ledger, root),
            reason=(f"ledger lifecycle inconsistent: stage={lc_stage} "
                    f"publish={lc_pub} qout={lc_qout} fpa={lc_fpa}"))

    # ---- canonical SHA 校验 ----
    canon = st.get("canonical_sha256")
    if canon != source_sha:
        return DesignMetadataResolution(
            status="HASH_MISMATCH", source_sha256=source_sha,
            ledger_path=_rel_path(ledger, root),
            reason="ledger canonical_sha256 != source SHA")

    # ---- 同目录 design ----
    design_path = ledger.parent / "agent_strategy_design.json"
    if not design_path.exists():
        return DesignMetadataResolution(
            status="INVALID_JSON", source_sha256=source_sha,
            ledger_path=_rel_path(ledger, root),
            reason="design missing alongside ledger")
    try:
        design = json.loads(design_path.read_text(encoding="utf-8"))
    except Exception as e:
        return DesignMetadataResolution(
            status="INVALID_JSON", source_sha256=source_sha,
            design_path=_rel_path(design_path, root),
            ledger_path=_rel_path(ledger, root),
            reason=f"design invalid JSON: {e}")
    design_sha = _sha256(design_path)

    # ---- 三字段一致性（ledger vs design vs 源）----
    d_sid = design.get("strategy_id")
    d_sname = design.get("strategy_name")
    d_qout = (design.get("output", {}) or {}).get("quantstudio_path")
    l_sid = st.get("strategy_id")
    l_sname = st.get("strategy_name")
    l_qout = st.get("quantstudio_output")
    src_name = sp.stem
    if not (d_sid == l_sid and d_sname == l_sname and d_qout == l_qout
            and l_qout == str(Path("quantstudio/backtest/strategies") / src_name)
            or (d_sid == l_sid and d_sname == l_sname and d_qout == l_qout
                and src_name == d_sname)):
        return DesignMetadataResolution(
            status="LEDGER_MISMATCH", source_sha256=source_sha,
            design_path=_rel_path(design_path, root),
            ledger_path=_rel_path(ledger, root),
            reason="strategy_id/name/output inconsistent across ledger/design/source")

    # ---- schema 校验 ----
    ok, schema_sha = _schema_available()
    if not ok:
        return DesignMetadataResolution(
            status=(schema_sha if schema_sha in ("SCHEMA_UNAVAILABLE",
                                                 "UNSUPPORTED_DESIGN_VERSION")
                    else "INVALID_PROFILE"),
            source_sha256=source_sha,
            design_path=_rel_path(design_path, root),
            ledger_path=_rel_path(ledger, root),
            reason=f"design schema unavailable: {schema_sha}")
    try:
        from jsonschema import Draft7Validator
        v = Draft7Validator(json.loads(DESIGN_SCHEMA_PATH.read_text(encoding="utf-8")))
        errs = list(v.iter_errors(design))
        if errs:
            return DesignMetadataResolution(
                status="INVALID_PROFILE", source_sha256=source_sha,
                design_path=_rel_path(design_path, root),
                ledger_path=_rel_path(ledger, root),
                reason=f"design fails schema: {errs[0].message[:120]}")
    except Exception as e:
        return DesignMetadataResolution(
            status="INVALID_PROFILE", source_sha256=source_sha,
            reason=f"schema validation error: {e}")

    # ---- engine_profile 提取 + 组合校验 ----
    ep = design.get("engine_profile") or {}
    pid = ep.get("profile_id")
    if pid not in VALID_PROFILES or not _profile_combo_valid(pid, design):
        return DesignMetadataResolution(
            status="INVALID_PROFILE", source_sha256=source_sha,
            design_path=_rel_path(design_path, root),
            ledger_path=_rel_path(ledger, root),
            design_sha256=design_sha, schema_sha256=schema_sha,
            reason=f"invalid/conflicting engine_profile: {pid!r}")

    return DesignMetadataResolution(
        status="RESOLVED", design_path=_rel_path(design_path, root),
        ledger_path=_rel_path(ledger, root), strategy_id=d_sid,
        engine_profile=pid, source_sha256=source_sha,
        design_sha256=design_sha, schema_sha256=schema_sha,
        reason="trusted design/ledger chain verified")


def resolve_engine_profile(
    strategy_path: str | Path,
    project_root: str | Path | None = None,
) -> DesignMetadataResolution:
    """从关联 design 解析 engine_profile（find_design_for_strategy + profile 组合校验）。"""
    return find_design_for_strategy(strategy_path, project_root)


__all__ = [
    "DesignMetadataResolution", "find_design_for_strategy",
    "resolve_engine_profile", "DESIGN_SCHEMA_PATH", "VALID_PROFILES",
]
