# -*- coding: utf-8 -*-
"""S3 案例路由器 · 判别特征规则库（件 B，2026-10-07 总调度批准）。

消费 S1 对账报告的结构化字段（align_diff_report.py --as-json 产物 J），输出候选
案件类型供 diff-triage 仲裁——**L1 半自动：机判人核，非终判**（铁律：根因未证实
不得修，S3 命中只提供路由建议与证据链接）。

回灌纪律：每案六步闭环时，registry 结案行追加 triage_rule 字段并回灌一条 Rule
（判别特征 → 案件类型 → 证据链接）——闭环第⑥环的制度化入口。

设计文档：docs/alignment-triage-convergence-design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Rule:
    id: str
    case_type: str
    pattern: str                # 人类可读的判别特征描述
    evidence: str               # 案件证据/契约档案链接
    match: Callable[[dict[str, Any]], bool]
    reason: Callable[[dict[str, Any]], str] = field(default=lambda J: "")


def _order_divs(J: dict[str, Any]) -> list:
    """S1 §6 挂钩后 J 携带 order_divs 明细 [(key=(date,code,side), platform, local)]。"""
    return J.get("order_divs") or []


def _nav_first(J: dict[str, Any]) -> Optional[str]:
    return ((J.get("nav_diff") or {}).get("first_div_date"))


RULES: list[Rule] = [
    Rule(
        id="CORP-01-merge-band",
        case_type="CORP（公司行为：份额合并/折算）",
        pattern="nav 单日突跳 + 委托/现金无分歧（preClose 缺口 ratio 0.5~0.99 由人核）",
        evidence="knowledge/contracts/etf-share-merge.md；"
                 "docs/evidence/corp-action-etf-merge-fix-acceptance-20261007.md",
        match=lambda J: (_nav_first(J) is not None
                         and J.get("order_div_count") == 0
                         and J.get("cash_diff_first") is None),
        reason=lambda J: f"nav 首偏 {(_nav_first(J))} 而委托/现金全一致 ⇒ 估值口径类；"
                         f"优先核实该日标的 preClose 缺口（合并带 ratio 0.5~0.99）",
    ),
    Rule(
        id="CORP-02-odd-lot-tail",
        case_type="CORP（零股卖出/整手截断）",
        pattern="委托分歧组含非整百尾差（平台全量 vs 本地 round_to_lot 截断）",
        evidence="knowledge/contracts/etf-share-merge.md §CORP-02；"
                 "docs/evidence/corp02-odd-lot-sell-acceptance-20261007.md",
        match=lambda J: any(
            (p - l) % 100 != 0 or (l > 0 and l % 100 != 0) or (l == 0 and p > 0)
            for (_, p, l) in _order_divs(J)),
        reason=lambda J: "委托分歧含非整百尾差/本地整手截断 ⇒ 零股语义候选"
                         f"（{sum(1 for _, p, l in _order_divs(J) if (p - l) % 100 != 0 or l % 100 != 0)} 组命中）",
    ),
    Rule(
        id="POS-ENG-API-trigger-chain",
        case_type="POS/ENG/API（触发链：成本口径/引擎语义/接口行为）",
        pattern="委托分歧在先（首委托分歧日 ≤ nav 首偏日）⇒ 交易触发链回溯",
        evidence="knowledge/registry.md（POS-01 摊薄先例）",
        match=lambda J: (J.get("order_div_count", 0) > 0
                         and _nav_first(J) is not None
                         and (J.get("order_first_div") or "") <= _nav_first(J)),
        reason=lambda J: f"首委托分歧 {J.get('order_first_div')} 早于/等于 nav 首偏 "
                         f"{_nav_first(J)} ⇒ 回溯订单语义/成本口径/风控级联",
    ),
    Rule(
        id="FEE-cash-first",
        case_type="FEE（费用/利息口径）",
        pattern="现金先偏（cash 首偏日早于 nav 首偏日）⇒ 费用类候选",
        evidence="knowledge/registry.md（FEE-05 先例）",
        match=lambda J: (J.get("cash_diff_first") is not None
                         and _nav_first(J) is not None
                         and J["cash_diff_first"] < _nav_first(J)),
        reason=lambda J: f"现金首偏 {J['cash_diff_first']} 早于 nav 首偏 {_nav_first(J)} "
                         f"⇒ 成交金额/费用口径候选",
    ),
]


def triage(J: dict[str, Any]) -> dict[str, Any]:
    """路由主入口：返回 {verdict, candidates, new_case}。

    - S1 verdict 已对齐 → 直接放行（不进路由）；
    - 规则命中 → candidates 列表（id/case_type/reason/evidence）；
    - 未对齐且无命中 → new_case=True（立新案进根因证实，铁律 L2 人审）。
    """
    verdict = str(J.get("verdict") or "")
    if verdict.startswith("已对齐"):
        return {"verdict": verdict, "candidates": [], "new_case": False}
    hits = []
    for rule in RULES:
        try:
            if rule.match(J):
                hits.append({"id": rule.id, "case_type": rule.case_type,
                             "reason": rule.reason(J), "evidence": rule.evidence})
        except Exception:            # 单规则失败不拖垮整轮路由
            continue
    return {"verdict": verdict, "candidates": hits, "new_case": not hits}


__all__ = ["RULES", "Rule", "triage"]
