# -*- coding: utf-8 -*-
"""run_deploy 内核入口（对应设计文档 docs/ptrade-deploy-pipeline-design.md §4/§5.2）。

串联七段流水线 P0→P6：预检 → 画像 → 配置校验 → AST 变换 → 等价验证 → 平台门禁 → 交付。
任一段 FAIL 即停并保留已完成段结果；失败信息结构化可机读（DeployReport.to_dict）。

CLI（qs-compile deploy，M4）与 PyQt tab（M5）均调用本入口——单内核 + 双薄壳（§4），
对等判据为产物 SHA256 逐位一致（§7.3）。

说明：deploy.yaml 的**原始加载**（找 source 路径）发生在 P0 之前（预载 I/O，
非流水线段）；schema 校验仍在 P2 段执行——yaml 本身不可解析时流水线以
P2_config FAIL 终止（此时无从定位源码，属设计文档流水线顺序的唯一实现偏差，
在返回 notes 中显式声明）。
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from . import config as config_mod
from . import deliver as deliver_mod
from . import equiv as equiv_mod
from . import gates as gates_mod
from . import preflight as preflight_mod
from . import profiler as profiler_mod
from . import transformer as transformer_mod

#: 流水线段顺序（P0→P6）
STAGE_ORDER = ("P0_preflight", "P1_profile", "P2_config", "P3_transform",
               "P4_equiv", "P5_gates", "P6_deliver")


@dataclass
class StageResult:
    """单段结果（PASS / FAIL / SKIPPED；details 结构化可机读）。"""

    stage: str
    status: str
    details: Dict[str, object] = field(default_factory=dict)
    reason: Optional[str] = None


@dataclass
class DeployReport:
    """run_deploy 汇总报告：各段结果 / 总状态 / 工件 / 附注。"""

    stage_results: Dict[str, StageResult] = field(default_factory=dict)
    status: str = "FAIL"
    artifacts: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "stages": {k: {"status": v.status, "reason": v.reason,
                           "details": v.details}
                       for k, v in self.stage_results.items()},
            "artifacts": self.artifacts,
            "notes": self.notes,
        }


def _mark_skipped(stages: Dict[str, StageResult], failed: str) -> None:
    seen_fail = False
    for name in STAGE_ORDER:
        if name == failed:
            seen_fail = True
            continue
        if seen_fail and name not in stages:
            stages[name] = StageResult(name, "SKIPPED",
                                       reason=f"前置段 {failed} FAIL，本段跳过")


def _resolve_source(raw: dict, config_path: Path) -> Path:
    """source 路径解析：先 cwd 相对，再配置文件所在目录相对。"""
    src = str(raw.get("source", ""))
    cand = Path(src)
    if cand.exists():
        return cand
    alt = config_path.resolve().parent / src
    if alt.exists():
        return alt
    return cand  # 返回原路径由后续报错


def run_deploy(config_path: str) -> DeployReport:
    """执行 P0→P6 七段流水线；返回 DeployReport（绝不抛异常，失败结构化返回）。"""
    stages: Dict[str, StageResult] = {}
    notes: List[str] = []
    cfg_path = Path(config_path)

    # ---------- 预载（I/O，非流水线段） ----------
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        stages["P2_config"] = StageResult(
            "P2_config", "FAIL", reason=f"deploy.yaml 加载失败: {exc}")
        _mark_skipped(stages, "P2_config")
        return DeployReport(stages, "FAIL", notes=[
            "deploy.yaml 预载失败（yaml 不可解析）——流水线在 P2_config 终止；"
            "此为设计文档流水线顺序（P0 先行）的唯一实现偏差：无配置无法定位源码"])
    if not isinstance(raw, dict):
        stages["P2_config"] = StageResult(
            "P2_config", "FAIL", reason="deploy.yaml 根节点应为映射（dict）")
        _mark_skipped(stages, "P2_config")
        return DeployReport(stages, "FAIL", notes=["deploy.yaml 根节点非法"])

    source_path = _resolve_source(raw, cfg_path)
    try:
        source_text = source_path.read_text(encoding="utf-8")
    except Exception as exc:
        stages["P2_config"] = StageResult(
            "P2_config", "FAIL", reason=f"源策略读取失败: {source_path} ({exc})")
        _mark_skipped(stages, "P2_config")
        return DeployReport(stages, "FAIL", notes=[f"源策略不可读: {source_path}"])

    contract = preflight_mod.load_contract()

    def _stop(failed: str, reason: str) -> DeployReport:
        _mark_skipped(stages, failed)
        return DeployReport(stages, "FAIL", notes=notes)

    # ---------- P0 预检 ----------
    try:
        pre = preflight_mod.run_preflight(source_text, contract)
        stages["P0_preflight"] = StageResult(
            "P0_preflight", "PASS" if pre.passed else "FAIL",
            details={"ast_ok": pre.ast_ok,
                     "reject_reasons": pre.reject_reasons,
                     "findings": pre.findings,
                     "backtest_only_calls": pre.backtest_only_calls,
                     "trading_only_calls": pre.trading_only_calls,
                     "vault_entries": len(pre.vault_entries),
                     "vault_degraded": pre.vault_degraded})
        if pre.vault_degraded:
            notes.append("P0 vault 回哺降级（trading 仓不可达，不阻塞转换，§14.6）")
        if not pre.passed:
            stages["P0_preflight"].reason = "；".join(pre.reject_reasons)
            return _stop("P0_preflight", stages["P0_preflight"].reason)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        stages["P0_preflight"] = StageResult("P0_preflight", "FAIL",
                                             reason=f"内部异常: {exc}")
        return _stop("P0_preflight", str(exc))

    # ---------- P1 画像 ----------
    try:
        profile = profiler_mod.build_profile(source_text, contract)
        stages["P1_profile"] = StageResult(
            "P1_profile", "PASS",
            details={"frequency": profile["frequency"],
                     "lifecycle": profile["lifecycle"],
                     "universe": profile["universe"],
                     "order_apis": profile["order_apis"],
                     "g_exempt": profile["g_exempt"],
                     "warnings": profile["warnings"]})
        notes.extend(f"P1 警告: {w}" for w in profile["warnings"])
    except Exception as exc:
        stages["P1_profile"] = StageResult("P1_profile", "FAIL",
                                           reason=f"画像失败: {exc}")
        return _stop("P1_profile", str(exc))

    # ---------- P2 配置校验（schema + 档位交叉） ----------
    try:
        cfg = config_mod.load_config(raw)
        cross_errors = config_mod.cross_validate(cfg, profile)
        stages["P2_config"] = StageResult(
            "P2_config", "PASS" if not cross_errors else "FAIL",
            details={"strategy_id": cfg.strategy_id,
                     "profile": cfg.execution.profile,
                     "persistence_enabled": cfg.persistence.enabled,
                     "save_points": list(cfg.persistence.save_points),
                     "cross_errors": cross_errors})
        if cross_errors:
            stages["P2_config"].reason = "；".join(cross_errors)
            return _stop("P2_config", stages["P2_config"].reason)
    except config_mod.ConfigError as exc:
        stages["P2_config"] = StageResult("P2_config", "FAIL",
                                          details={"errors": exc.errors},
                                          reason="；".join(exc.errors))
        return _stop("P2_config", stages["P2_config"].reason)
    except Exception as exc:  # pragma: no cover
        stages["P2_config"] = StageResult("P2_config", "FAIL",
                                          reason=f"内部异常: {exc}")
        return _stop("P2_config", str(exc))

    # ---------- P3 AST 变换 ----------
    try:
        tr = transformer_mod.transform(source_text, profile, cfg, contract)
        stages["P3_transform"] = StageResult(
            "P3_transform", "PASS",
            details={"edit_count": len(tr.edits),
                     "edits": tr.edits, "warnings": tr.warnings})
        notes.extend(f"P3 警告: {w}" for w in tr.warnings)
    except transformer_mod.TransformError as exc:
        stages["P3_transform"] = StageResult("P3_transform", "FAIL",
                                             reason=str(exc))
        return _stop("P3_transform", str(exc))
    except Exception as exc:
        stages["P3_transform"] = StageResult(
            "P3_transform", "FAIL",
            reason=f"内部异常: {exc}\n{traceback.format_exc(limit=3)}")
        return _stop("P3_transform", str(exc))

    # ---------- P4 等价验证 ----------
    try:
        ev = equiv_mod.compare_sources(source_text, tr.product_text,
                                       g_exempt=profile.get("g_exempt") or ())
        stages["P4_equiv"] = StageResult(
            "P4_equiv", "PASS" if ev.passed else "FAIL",
            details=ev.to_dict())
        if not ev.passed:
            stages["P4_equiv"].reason = (
                f"白名单外 diff {len(ev.diffs)} 处 + 标记块异常 {len(ev.anomalies)} 处")
            return _stop("P4_equiv", stages["P4_equiv"].reason)
    except Exception as exc:
        stages["P4_equiv"] = StageResult(
            "P4_equiv", "FAIL", reason=f"内部异常: {exc}\n{traceback.format_exc(limit=3)}")
        return _stop("P4_equiv", str(exc))

    # ---------- P5 平台门禁 ----------
    try:
        gr = gates_mod.run_gates(tr.product_text, contract, cfg)
        stages["P5_gates"] = StageResult(
            "P5_gates", "PASS" if gr.passed else "FAIL",
            details=gr.to_dict())
        notes.extend(f"P5 版本注记: {v}" for v in gr.version_notes)
        notes.extend(f"P5 未知 API（需人工确认）: {u}" for u in gr.unknown_apis)
        if not gr.passed:
            failed = [f"{c.name}: {c.findings}" for c in gr.checks if c.status == "FAIL"]
            stages["P5_gates"].reason = "；".join(failed)
            return _stop("P5_gates", stages["P5_gates"].reason)
    except Exception as exc:
        stages["P5_gates"] = StageResult(
            "P5_gates", "FAIL", reason=f"内部异常: {exc}\n{traceback.format_exc(limit=3)}")
        return _stop("P5_gates", str(exc))

    # ---------- P6 交付 ----------
    try:
        dv = deliver_mod.deliver(tr.product_text, tr.edits, ev, gr, cfg, profile)
        stages["P6_deliver"] = StageResult(
            "P6_deliver", "PASS",
            details={"out_dir": dv.out_dir,
                     "artifacts": dv.artifacts,
                     "sensitive_hits": dv.sensitive_hits})
        for h in dv.sensitive_hits:
            notes.append(f"P6 敏感词命中（{h}）——相关材料路由 trading 侧处置")
        return DeployReport(stages, "PASS", dv.artifacts, notes)
    except Exception as exc:
        stages["P6_deliver"] = StageResult(
            "P6_deliver", "FAIL", reason=f"交付失败: {exc}\n{traceback.format_exc(limit=3)}")
        return _stop("P6_deliver", str(exc))
