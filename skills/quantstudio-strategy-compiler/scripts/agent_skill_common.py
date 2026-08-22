#!/usr/bin/env python3
"""Shared helpers for the agent-first strategy Skill."""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

CURRENT_DESIGN_VERSION = "2.2"
CONFIRMATION_EVIDENCE_REQUIRED_KEYS = [
    "generation_target",
    "strategy_semantics",
    "portfolio_contract",
    "rebalance_funding_contract",
    "r5_deployment_invariants",
]

# Execution funding matrix (references/execution-funding-matrix.md). These
# values describe the *current* local engine semantics; they are validation
# inputs, never a request to change engine behavior.
IMMEDIATE_MATCH_MODES = {"close", "open", "current_bar"}
LEGACY_PENDING_MATCH_MODES = {"next_open"}


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_catalog() -> dict[str, Any]:
    return load_json(skill_root() / "references" / "component-catalog.json")


def validate_design(design: dict[str, Any]) -> list[str]:
    schema_path = skill_root() / "schemas" / "agent_strategy_design.schema.json"
    schema = load_json(schema_path)
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is required to validate agent_strategy_design.json"]
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(design), key=lambda e: list(e.absolute_path)):
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        errors.append(f"{location}: {error.message}")
    return errors


def confirmation_errors(design: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    confirmations = design.get("user_confirmations", {})
    required_confirmations = ["strategy_semantics", "execution_approximations", "component_plan"]
    if design.get("design_version") in {"2.1", "2.2"}:
        required_confirmations.extend(["generation_target", "backtest_validation_mode"])
        if "ptrade" in design.get("targets", []) and design.get("asset_class") == "etf":
            required_confirmations.append("static_etf_whitelist")
    for key in required_confirmations:
        if confirmations.get(key) is not True:
            errors.append(f"user_confirmations.{key} must be true before code generation")
    open_questions = design.get("open_questions", [])
    if open_questions:
        errors.append(f"open_questions must be empty before code generation: {open_questions}")
    unconfirmed = [item.get("id", "<unnamed>") for item in design.get("approximations", [])
                   if item.get("confirmed") is not True]
    if unconfirmed:
        errors.append(f"all approximations must be confirmed before code generation: {unconfirmed}")
    return errors


def _is_tz_aware_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def confirmation_evidence_errors(design: dict[str, Any]) -> list[dict[str, str]]:
    """Design 2.2 confirmations must bind verbatim customer text and time.

    A bare ``"strategy_semantics": true`` boolean is not evidence; every
    required item needs confirmed=true, non-empty customer_text, a timezone
    aware ISO confirmed_at and source='customer_reply'.
    """
    issues: list[dict[str, str]] = []
    if design.get("design_version") != CURRENT_DESIGN_VERSION:
        return issues
    evidence = design.get("confirmation_evidence", {})
    for key in CONFIRMATION_EVIDENCE_REQUIRED_KEYS:
        entry = evidence.get(key)
        if not isinstance(entry, dict):
            issues.append({
                "rule_id": "DESIGN-CONFIRMATION-EVIDENCE",
                "message": f"confirmation_evidence.{key} is required for design 2.2 and must "
                           "record the verbatim customer confirmation",
            })
            continue
        if entry.get("confirmed") is not True:
            issues.append({
                "rule_id": "DESIGN-CONFIRMATION-EVIDENCE",
                "message": f"confirmation_evidence.{key}.confirmed must be true",
            })
        if not isinstance(entry.get("customer_text"), str) or not entry.get("customer_text", "").strip():
            issues.append({
                "rule_id": "DESIGN-CONFIRMATION-EVIDENCE",
                "message": f"confirmation_evidence.{key}.customer_text must contain the verbatim "
                           "customer reply; a bare boolean is not evidence",
            })
        if not _is_tz_aware_iso(entry.get("confirmed_at")):
            issues.append({
                "rule_id": "DESIGN-CONFIRMATION-EVIDENCE",
                "message": f"confirmation_evidence.{key}.confirmed_at must be a timezone-aware "
                           "ISO 8601 timestamp",
            })
        if entry.get("source") != "customer_reply":
            issues.append({
                "rule_id": "DESIGN-CONFIRMATION-EVIDENCE",
                "message": f"confirmation_evidence.{key}.source must be 'customer_reply'",
            })
    return issues


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def portfolio_contract_errors(design: dict[str, Any]) -> list[dict[str, str]]:
    """Cross-validate portfolio_contract so capital math cannot contradict itself."""
    issues: list[dict[str, str]] = []
    contract = design.get("portfolio_contract")
    if not isinstance(contract, dict):
        return issues

    def add(rule: str, message: str) -> None:
        issues.append({"rule_id": rule, "message": message})

    gross = _num(contract.get("gross_exposure_target"))
    cash_buffer = _num(contract.get("cash_buffer_ratio"))
    per_weight = _num(contract.get("per_position_target_weight"))
    max_single = _num(contract.get("max_single_weight"))
    target_holdings = _num(contract.get("target_holdings"))

    if gross is not None and cash_buffer is not None and gross + cash_buffer > 1.0 + 1e-9:
        add("PORTFOLIO-EXPOSURE-CONTRADICTION",
            f"gross_exposure_target ({gross}) + cash_buffer_ratio ({cash_buffer}) exceeds 1.0")
    if per_weight is not None and max_single is not None and per_weight > max_single + 1e-12:
        add("PORTFOLIO-WEIGHT-INCONSISTENT",
            f"per_position_target_weight ({per_weight}) exceeds max_single_weight ({max_single})")
    if target_holdings and per_weight is not None and gross is not None \
            and target_holdings * per_weight > gross + 1e-9:
        add("PORTFOLIO-WEIGHT-INCONSISTENT",
            f"target_holdings x per_position_target_weight "
            f"({target_holdings} x {per_weight} = {target_holdings * per_weight}) exceeds "
            f"gross_exposure_target ({gross})")
    if target_holdings and per_weight is not None and cash_buffer is not None \
            and target_holdings * per_weight + cash_buffer > 1.0 + 1e-9:
        add("PORTFOLIO-CASH-BUFFER-CONTRADICTION",
            f"target_holdings x per_position_target_weight + cash_buffer_ratio "
            f"({target_holdings} x {per_weight} + {cash_buffer}) exceeds 1.0; the design "
            "cannot fully deploy and keep the claimed cash buffer at the same time")
    if contract.get("allow_leverage") is False and gross is not None and gross > 1.0 + 1e-9:
        add("PORTFOLIO-EXPOSURE-CONTRADICTION",
            "allow_leverage=false but gross_exposure_target exceeds 1.0")

    sizing_mode = contract.get("sizing_mode")
    required_cash = _num(contract.get("required_initial_cash"))
    fixed_target = _num(contract.get("fixed_target_value"))
    if sizing_mode == "fixed_notional":
        if required_cash is None or fixed_target is None:
            add("PORTFOLIO-FIXED-CAPITAL-MISSING",
                "fixed_notional sizing requires both required_initial_cash and fixed_target_value")
    elif sizing_mode == "runtime_total_value":
        if fixed_target is not None:
            add("PORTFOLIO-SIZING-MODE-MISMATCH",
                "runtime_total_value sizing must not declare fixed_target_value; per-position "
                "targets derive from the runtime portfolio value")
        if contract.get("required_initial_cash") is not None:
            add("PORTFOLIO-SIZING-MODE-MISMATCH",
                "runtime_total_value sizing must set required_initial_cash to null; absolute "
                "cash requirements belong to fixed_notional mode")
    elif sizing_mode is not None:
        add("PORTFOLIO-SIZING-MODE-MISMATCH",
            f"unknown sizing_mode {sizing_mode!r}; expected runtime_total_value or fixed_notional")
    return issues


def execution_funding_errors(design: dict[str, Any]) -> list[dict[str, str]]:
    """Cross-check rebalance funding assumptions against the real engine matrix."""
    issues: list[dict[str, str]] = []
    contract = design.get("rebalance_funding_contract")
    if not isinstance(contract, dict):
        return issues

    def add(rule: str, message: str) -> None:
        issues.append({"rule_id": rule, "message": message})

    match_mode = design.get("engine_profile", {}).get("match_price_mode")
    lifecycles = {event.get("lifecycle")
                  for event in design.get("timing", {}).get("decision_events", [])}
    implementation = contract.get("implementation_mode")
    needs_proceeds = contract.get("requires_same_cycle_sell_proceeds") is True
    cash_only = contract.get("cash_only_for_new_buys") is True

    if match_mode not in IMMEDIATE_MATCH_MODES | LEGACY_PENDING_MATCH_MODES:
        add("EXECUTION-FUNDING-INCOMPATIBLE",
            f"unknown match_price_mode {match_mode!r}; the execution funding matrix only "
            f"covers {sorted(IMMEDIATE_MATCH_MODES | LEGACY_PENDING_MATCH_MODES)}")
        return issues

    if cash_only and needs_proceeds:
        add("EXECUTION-FUNDING-INCOMPATIBLE",
            "cash_only_for_new_buys contradicts requires_same_cycle_sell_proceeds; new buys "
            "cannot simultaneously ignore and depend on same-cycle sell proceeds")

    if implementation == "sell_then_buy_immediate":
        if match_mode in LEGACY_PENDING_MATCH_MODES and needs_proceeds:
            add("EXECUTION-SELL-PROCEEDS-UNAVAILABLE",
                f"match_price_mode={match_mode} uses legacy pending semantics: same-batch sell "
                "proceeds are not available for buys in the same cycle. Use "
                "basket_atomic_sell_first (handle_data + basket) or staged_two_phase, or "
                "size new buys from cash only.")
    elif implementation == "basket_atomic_sell_first":
        profile_id = design.get("engine_profile", {}).get("profile_id")
        rebalance_mode = design.get("engine_profile", {}).get("rebalance_mode")
        expected_semantics = design.get("engine_profile", {}).get(
            "expected_engine_semantics_version")
        if match_mode not in LEGACY_PENDING_MATCH_MODES or "handle_data" not in lifecycles:
            add("EXECUTION-BASKET-REQUIRED",
                "basket_atomic_sell_first is only verified for match_price_mode=next_open "
                "with a handle_data decision event and basket_active=true; this combination "
                "is outside the execution funding matrix")
        elif profile_id != "daily-bar-v1" or rebalance_mode != "callback_basket":
            add("EXECUTION-BASKET-REQUIRED",
                "basket_atomic_sell_first requires engine_profile with profile_id='daily-bar-v1', "
                "match_price_mode='next_open' and rebalance_mode='callback_basket' "
                "(engine semantics 0.4.0-next_open_basket); the real engine activates the "
                "basket only when all three hold.")
        if expected_semantics != "0.4.0-next_open_basket":
            add("EXECUTION-BASKET-REQUIRED",
                "basket_atomic_sell_first requires engine_profile."
                "expected_engine_semantics_version='0.4.0-next_open_basket' so R5 can "
                "prove from config.csv that the basket was actually active; omitting it "
                f"(got {expected_semantics!r}) disables the R5 semantics check.")
    elif implementation == "staged_two_phase":
        pass  # two-phase rebalancing is compatible with every profiled match mode
    elif implementation == "cash_only_new_buys":
        if match_mode in LEGACY_PENDING_MATCH_MODES and not cash_only:
            add("EXECUTION-STAGED-REBALANCE-REQUIRED",
                "cash_only_new_buys requires cash_only_for_new_buys=true under legacy "
                "pending semantics")
    else:
        add("EXECUTION-FUNDING-INCOMPATIBLE",
            f"unknown implementation_mode {implementation!r}; expected one of "
            "sell_then_buy_immediate, basket_atomic_sell_first, staged_two_phase, "
            "cash_only_new_buys")
    return issues


ETF_T0_ENFORCEMENTS = ("engine_per_code", "all_t1")
STOP_DEFERRAL_SEMANTICS = ("trigger_lock_defer_next_sellable_day",)


# ---------------------------------------------------------------------------
# Chinese naming contract (2026-08-22): every skill-generated local strategy
# publishes as quantstudio/backtest/strategies/<strategy_name>.py with a
# Chinese name; strategy_id stays the ASCII machine identifier.
# ---------------------------------------------------------------------------
STRATEGY_NAME_MAX_LEN = 50
STRATEGY_NAME_PATTERN = re.compile(
    r"^(?![_\s])(?!.*[.\s]$)(?=.*[\u4e00-\u9fa5])[^\\/:*?\"<>|]{1,50}$"
)


def strategy_naming_errors(design: dict[str, Any]) -> list[dict[str, str]]:
    """The local strategy name must be a Chinese, filename-safe string.

    The name doubles as the published filename
    ``quantstudio/backtest/strategies/<strategy_name>.py`` shown in the PyQt
    strategy selector, so Windows filename traps apply: no leading ``_`` or
    whitespace (the PyQt selector hides ``_``-prefixed files; leading spaces
    are stripped inconsistently across Windows layers), no trailing ``.`` or
    whitespace (Windows silently strips them, breaking hash-bound path
    identity), no ``\\/:*?"<>|`` characters, at most 50 characters, and at
    least one CJK character. Mirrors the schema pattern as a second,
    better-diagnosed gate.
    """
    issues: list[dict[str, str]] = []
    name = design.get("strategy_name")

    def add(message: str) -> None:
        issues.append({"rule_id": "STRATEGY-NAME-CONTRACT", "message": message})

    if not isinstance(name, str) or not name:
        add("strategy_name is required and must be a non-empty string")
        return issues
    if name != name.strip():
        add(f"strategy_name {name!r} must not start or end with whitespace")
    if len(name) > STRATEGY_NAME_MAX_LEN:
        add(
            f"strategy_name must be at most {STRATEGY_NAME_MAX_LEN} characters, "
            f"got {len(name)}"
        )
    if STRATEGY_NAME_PATTERN.fullmatch(name) is None:
        add(
            f"strategy_name {name!r} 必须为中文策略名（至少一个汉字）且文件名安全："
            "不得以 _ 或空白开头、不得以 . 或空白结尾（Windows 文件名限制）、"
            '不得包含 \\ / : * ? " < > | 等非法字符、'
            f"长度 ≤ {STRATEGY_NAME_MAX_LEN}；该名称即发布文件名 "
            "quantstudio/backtest/strategies/<strategy_name>.py"
        )
    return issues


def published_quantstudio_filename(design: dict[str, Any]) -> str:
    """Chinese published filename: ``<strategy_name>.py`` (no ASCII suffix).

    Single derivation point shared by the workspace ledger, the user-PyQt
    candidate path and the R6 formal publish target so the three can never
    drift apart.
    """
    return f"{design['strategy_name'].strip()}.py"


def strategy_name_conflict_errors(
    design: dict[str, Any],
    strategies_dir: str | Path | None,
) -> list[dict[str, str]]:
    """Block names colliding with any existing strategy file stem.

    The Chinese name space is far smaller than ASCII; generic names such as
    双均线策略 collide with hand-written or legacy ASCII strategies easily.
    Front-load the detection at R4/candidate/publish instead of failing only
    at publish time. ``design.output.overwrite=true`` is the explicit consent
    aligned with the existing publish overwrite semantics. A missing or
    unlocatable strategies directory simply means there is nothing to collide
    with and never blocks.
    """
    issues: list[dict[str, str]] = []
    name = design.get("strategy_name")
    if not isinstance(name, str) or not name.strip():
        return issues
    if design.get("output", {}).get("overwrite") is True:
        return issues
    if strategies_dir is None:
        return issues
    directory = Path(strategies_dir)
    if not directory.is_dir():
        return issues
    target = name.strip()
    for existing in sorted(directory.glob("*.py")):
        if existing.stem == target:
            issues.append({
                "rule_id": "STRATEGY-NAME-CONFLICT",
                "message": (
                    f"strategy_name {target!r} 与现存策略文件 {existing.name!r} 同名"
                    "（stem 冲突）；请更换中文名，或经客户确认后在 design.output "
                    "设置 overwrite=true 显式覆盖"
                ),
            })
            break
    return issues


def etf_t0_contract_errors(design: dict[str, Any]) -> list[dict[str, str]]:
    """Cross-check the per-code ETF T+0 contract（docs/etf-t0-per-code-design.md §6）。

    - etf_t0_enforcement 取值必须在枚举内；
    - engine_per_code（按 fund_type 逐码分类）必须同时声明 stop_deferral_semantics
      （止损顺延语义），否则 BLOCK——分钟级止损策略不得默认"当日必卖"。
    """
    issues: list[dict[str, str]] = []
    contract = design.get("market_data_contract")
    if not isinstance(contract, dict):
        return issues

    def add(rule: str, message: str) -> None:
        issues.append({"rule_id": rule, "message": message})

    enforcement = contract.get("etf_t0_enforcement")
    deferral = contract.get("stop_deferral_semantics")
    if enforcement is not None and enforcement not in ETF_T0_ENFORCEMENTS:
        add("ETF-T0-ENFORCEMENT-ENUM",
            f"etf_t0_enforcement={enforcement!r} 不在枚举 {ETF_T0_ENFORCEMENTS} 内")
    if enforcement == "engine_per_code" and deferral != STOP_DEFERRAL_SEMANTICS[0]:
        add("STOP-DEFERRAL-SEMANTICS-MISSING",
            "etf_t0_enforcement='engine_per_code' 必须同时声明 "
            f"stop_deferral_semantics={STOP_DEFERRAL_SEMANTICS[0]!r}（止损触发即锁、"
            "T+1 当日买入顺延次日首个可卖窗口成交，见 references/etf-t0-rules.md）")
    if deferral is not None and deferral not in STOP_DEFERRAL_SEMANTICS:
        add("STOP-DEFERRAL-SEMANTICS-ENUM",
            f"stop_deferral_semantics={deferral!r} 不在枚举 {STOP_DEFERRAL_SEMANTICS} 内")
    return issues


def function_map(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in getattr(tree, "body", [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def constant_value(node: ast.AST) -> Any:
    return node.value if isinstance(node, ast.Constant) else None


def is_placeholder_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return True
    if len(body) == 1 and isinstance(body[0], ast.Pass):
        return True
    if len(body) == 1 and isinstance(body[0], ast.Raise):
        exc = body[0].exc
        if isinstance(exc, ast.Call) and call_name(exc) == "NotImplementedError":
            return True
        if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
            return True
    return False
