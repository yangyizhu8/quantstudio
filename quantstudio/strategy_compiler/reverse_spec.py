# -*- coding: utf-8 -*-
"""二期 T8：build_reverse_spec —— 从策略源码 AST 逆向推断 strategy_spec。

规格：02-source_import模块规格.md §6（逆向 spec 推断规则）。
铁律：所有推断显式记录 inference notes（field / source / inferred: true）；
凡"猜测"字段一律 inferred: true；validate_strategy_spec 不通过时由调用方按
PARTIAL 处理（本函数不硬凑）。
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any


def _infer_universe(tree: ast.AST) -> tuple[dict, list[dict]]:
    """从 g.security/g.stock_list 赋值推断 universe（single_stock / explicit_list）。"""
    notes: list[dict] = []
    single_code: str | None = None
    codes: list[str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Attribute)
                       and isinstance(t.value, ast.Name) and t.value.id == "g"]
            if not targets:
                continue
            attr = targets[0].attr
            if attr not in ("security", "stock_list", "security_list"):
                continue
            val = node.value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                single_code = val.value
            elif isinstance(val, (ast.List, ast.Tuple)):
                codes = [e.value for e in val.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if single_code:
        notes.append({"field": "universe", "source": f"g.security 赋值 {single_code!r}",
                      "inferred": True})
        return {"kind": "single_stock", "parameters": {"code": single_code}}, notes
    if codes:
        notes.append({"field": "universe",
                      "source": f"g.stock_list 赋值 {len(codes)} 只", "inferred": True})
        return {"kind": "explicit_list", "parameters": {"codes": codes}}, notes
    notes.append({"field": "universe",
                  "source": "未发现 g.security/g.stock_list 赋值，示例保守默认 single_stock 占位",
                  "inferred": True})
    return {"kind": "single_stock", "parameters": {"code": "600000.SS"}}, notes


def _infer_frequency(tree: ast.AST) -> tuple[str, list[dict]]:
    """从 get_history 的 unit/frequency 参数推断 market_data_frequency（默认 '1d'）。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Name) and f.id == "get_history"):
            continue
        unit: str | None = None
        for kw in node.keywords:
            if kw.arg in ("unit", "frequency") and isinstance(kw.value, ast.Constant):
                unit = str(kw.value.value)
        if unit is None and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            unit = str(node.args[1].value)  # count-first 签名 (count, unit, ...)
        if unit is not None:
            return unit, [{"field": "time_model.market_data_frequency",
                           "source": f"get_history unit={unit!r}", "inferred": True}]
    return "1d", [{"field": "time_model.market_data_frequency",
                   "source": "未发现 get_history 调用，默认 1d", "inferred": True}]


def _infer_benchmark(tree: ast.AST) -> tuple[str | None, list[dict]]:
    """从 set_benchmark 参数推断 benchmark（无调用 → None）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "set_benchmark":
            if node.args and isinstance(node.args[0], ast.Constant):
                return str(node.args[0].value), \
                    [{"field": "benchmark", "source": f"set_benchmark({node.args[0].value!r})",
                      "inferred": True}]
    return None, []


def _infer_order_patterns(tree: ast.AST) -> list[str]:
    """粗推断交易调用模式：order_value / order_target_value / order_target / order。"""
    seen: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("order_value", "order_target_value", "order_target", "order"):
            if node.func.id not in seen:
                seen.append(node.func.id)
    return seen


def build_reverse_spec(source_path: str | Path) -> tuple[dict, list[dict]]:
    """【二期 T8】从策略源码 AST 逆向推断 strategy_spec.json + inference notes。

    返回 (spec, notes)：spec 为符合 strategy_spec.schema.json 的完整骨架
    （未推断字段使用示例保守默认值）；notes 为逐字段推断记录（field/source/inferred）。
    铁律：凡"猜测"字段 inferred: true；validate_strategy_spec 不通过时由调用方
    按 PARTIAL 处理（本函数不硬凑）。
    """
    src = Path(source_path)
    source_code = src.read_text(encoding="utf-8")
    tree = ast.parse(source_code)
    notes: list[dict] = []

    strategy_id = src.stem
    if strategy_id.endswith("_quantstudio"):  # T1 命名规范归一化
        strategy_id = strategy_id[:-len("_quantstudio")]
    # schema 契约：strategy_id 必须匹配 ^[a-z][a-z0-9_]{2,63}$（小写 ASCII）。
    # 中文/非 ASCII 文件名不满足 → 确定性 fallback（md5 前 8 位，可复现）。
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", strategy_id):
        digest = hashlib.md5(strategy_id.encode("utf-8")).hexdigest()[:8]
        notes.append({"field": "strategy_id",
                      "source": f"文件名 {src.stem!r} 非 ASCII，schema 契约要求 "
                                f"^[a-z][a-z0-9_]{{2,63}}$，fallback 为 rev_{digest}",
                      "inferred": True})
        strategy_id = f"rev_{digest}"

    universe, un_notes = _infer_universe(tree)
    notes += un_notes
    freq, freq_notes = _infer_frequency(tree)
    notes += freq_notes
    benchmark, bm_notes = _infer_benchmark(tree)
    notes += bm_notes
    order_patterns = _infer_order_patterns(tree)
    notes.append({"field": "portfolio",
                  "source": f"交易调用模式 {order_patterns or '无'}（粗推断，需人工复核）",
                  "inferred": True})
    notes.append({"field": "signals.steps",
                  "source": "无法从任意代码推断信号计算逻辑；使用受支持 operation 的结构占位 "
                            "(ma/close/5)，不代表真实信号，见 approximations",
                  "inferred": True})
    notes.append({"field": "hard_filters",
                  "source": "无法从源码推断，默认全开（示例保守值）", "inferred": True})
    notes.append({"field": "execution",
                  "source": "无法从源码推断撮合参数，默认 native/close/market",
                  "inferred": True})
    notes.append({"field": "time_model（除频率外）",
                  "source": "无法从源码推断，使用示例保守默认值", "inferred": True})

    spec: dict[str, Any] = {
        "spec_version": "1.0",
        "strategy_id": strategy_id,
        "strategy_name": strategy_id,
        "strategy_type": "stock_selection",
        "asset_class": "stock",
        "target_platforms": ["quantstudio", "ptrade-default"],
        "contract_versions": {
            "strategy_spec_version": "1.0.0",
            "engine_semantics_version": "0.1.0-legacy",
            "provider_contract_version": "0.1.0-daily",
            "security_code_rules_version": "1.0.0",
            "ptrade_profile_version": "1.0.0-default",
            "renderer_version": "0.0.0-planned",
            "skill_version": "0.0.0-planned",
        },
        "time_model": {
            "market_data_frequency": freq,
            "factor_frequency": freq,
            "signal_frequency": freq,
            "decision_clock": "before_trading_start",
            "execution_clock": "next_open",
            "portfolio_valuation_frequency": "1d",
            "holding_period_unit": "trading_day",
            "signal_data_cutoff": "T-1-close",
        },
        "engine_profile": {
            "profile_id": "daily-bar-v1",
            "event_type": "bar",
            "bar_frequency": "1d" if freq == "1d" else freq,
            "market_depth": "L1",
            "order_book_levels": 0,
            "schema_supported": True,
            "execution_status": "READY",
        },
        "universe": universe,
        "hard_filters": {
            "exclude_st": True, "exclude_suspended": True, "exclude_delisted": True,
            "exclude_delisting_sorting": True, "exclude_star_market": True,
            "exclude_bse": True, "min_listing_trade_days": 252,
            "exclude_invalid_price": True, "exclude_zero_volume": True,
            "block_limit_up_buy": True, "block_limit_down_sell": True,
            "enforce_t1": True, "round_lot": 100,
        },
        "signals": {
            # 结构占位：renderer 对 single_stock 要求 dual-MA cross 信号形状
            # （render.py _build_template_context）。占位不代表真实信号逻辑
            # （note + inference_notes 声明），仅使路径 B 可渲染。
            "steps": [
                {"id": "ma5", "operation": "ma",
                 "parameters": {"field": "close", "lookback": 5,
                                "note": "逆向 spec 结构占位，不代表真实信号逻辑"}},
                {"id": "ma10", "operation": "ma",
                 "parameters": {"field": "close", "lookback": 10}},
                {"id": "cross_signal", "operation": "cross",
                 "parameters": {"sources": ["ma5", "ma10"], "direction": "golden"}},
            ],
        },
        "portfolio": {
            "kind": "single_position",
            "parameters": {"max_positions": 1, "rebalance": "signal_triggered",
                           "target_weight": 1.0},
        },
        "execution": {
            "mode": "native", "match_price_mode": "close",
            "order_type": "market", "allow_partial_fill": False,
        },
        "risk": {"kind": "position_limits",
                 "parameters": {"max_single_weight": 1.0, "cash_buffer": 0.0}},
        "costs": {"commission_rate": 0.0003, "minimum_commission": 5.0,
                  "stamp_tax_rate": 0.0005, "transfer_fee_rate": 1e-05,
                  "slippage_bps": 5.0},
        "data_requirements": {
            "datasets": [
                {"dataset": "stock_daily", "frequency": freq,
                 "fields": ["open", "high", "low", "close", "volume"],
                 "required": True, "pit_required": False},
                {"dataset": "stock_status", "frequency": freq,
                 "fields": ["is_st", "is_suspended", "is_delisted"],
                 "required": True, "pit_required": True},
            ],
        },
        "capability_requirements": ["stock_daily_backtest", "stock_status_filter"],
        # approximations：spec 的执行近似记录（const 契约要求 user_confirmed=true）。
        # 逆向 spec 的推断信息由 inference notes 承载（T8 铁律），此处不误用 →
        # 保持空数组（同 case1 示例）。
        "approximations": [],
        "user_confirmations": [{
            "confirmation_id": "reverse_spec_inferred",
            "status": "PENDING",
            "confirmed_at": None,
            "note": "逆向 spec 未经用户确认（推断生成）",
        }],
        "validation_policy": {"no_lookahead": "BLOCK", "hard_filters": "BLOCK",
                              "strict_public_api": True, "smoke_backtest": "WHEN_READY"},
        "output": {"root": "output/generated_strategies", "overwrite": False},
    }
    # benchmark / initial_capital 契约：string，且非 required（schema 不允许 null）。
    # 无 set_benchmark 推断时省略键（case1 示例的 null 实际不通过契约校验）。
    if benchmark is not None:
        spec["benchmark"] = benchmark
    return spec, notes
