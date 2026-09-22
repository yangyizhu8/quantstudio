# -*- coding: utf-8 -*-
"""证券 code 形式契约（错误二 T1，2026-09-22）——纯函数、零依赖、单一真相源。

背景（ISS：MCP 测试码污染，方案 docs/qfq-test-code-pollution-fix-design.md）：
    上游数据集混入测试/占位记录（实测 TEST999.SH / FIXTEST / TEST.SH / GISISI_TEST*），
    框架内校验点行为不一致：落盘原样保留、aux 注入无校验（FIXTEST 入 adj_factor）、
    _normalize_code 抛错中断整轮 qfq_orch 周期（9-22 01:48 实录）。

设计（过审裁定）：
    **形式契约为主**——实测 8 处脏值全部「非 6 位纯数字」，纯形式即可全覆盖拦截，
    **不预设任何关键词拦截**（误杀面且无必要；deny 清单仅留 T3 演进位）。
    合法形态 = 裸 6 位数字，或 6 位数字 + .SH/.SZ/.BJ 后缀（归一为裸码）。
    股票/ETF/指数/北交所/科创板 6 位码全部满足，零误杀面（位数域不猜语义）。

用法：
    ok, norm, reason = validate_sec_code(raw)          # 单值
    kept, rejected = filter_sec_codes(iterable)        # 批量（rejected=[(raw, reason)]）
    调用方契约（T2/T3/T4）：拒绝 ⇒ 隔离计数 + 审计行，**不中断整批/整轮**。
"""
from __future__ import annotations

import re
from typing import Iterable, List, Tuple

__all__ = ["validate_sec_code", "filter_sec_codes", "is_valid_sec_code"]

# 交易所后缀（大写归一后比对）；裸码为唯一真相形态，后缀仅容忍上游书写格式
_VALID_SUFFIXES = frozenset({"SH", "SZ", "BJ"})

# 6 位裸码，或 6 位码 + 点 + 2~3 字母后缀。
# [0-9] 显式 ASCII 类（不用 \d——Python \d 匹配全角数字「００００００」，宽进必漏）
_CANONICAL_RE = re.compile(r"^([0-9]{6})(?:\.([A-Za-z]{2,3}))?$")


def validate_sec_code(value) -> Tuple[bool, str, str]:
    """校验并归一证券 code。

    Returns:
        (ok, normalized, reason)
        ok=True  → normalized=裸 6 位码，reason="ok"
        ok=False → normalized=""，reason ∈ {"none", "empty", "NONE", "non_canonical"}
    非抛错设计（fail-safe 友好）：任何异常输入一律 (False, "", reason)。
    """
    if value is None:
        return False, "", "none"
    s = str(value).strip()
    if s == "":
        return False, "", "empty"
    if s.upper() == "NONE":
        return False, "", "NONE"
    m = _CANONICAL_RE.fullmatch(s)
    if not m:
        return False, "", "non_canonical"
    suffix = m.group(2)
    if suffix is not None and suffix.upper() not in _VALID_SUFFIXES:
        return False, "", "non_canonical"
    return True, m.group(1), "ok"


def is_valid_sec_code(value) -> bool:
    """布尔便捷式。"""
    return validate_sec_code(value)[0]


def filter_sec_codes(values: Iterable) -> Tuple[List[str], List[Tuple[str, str]]]:
    """批量过滤：返回 (归一合法码列表, [(原始值, reason) 拒绝清单])。

    不去重（保持与输入行对齐，供调用方按行删置）；顺序稳定。
    """
    kept: List[str] = []
    rejected: List[Tuple[str, str]] = []
    for raw in values:
        ok, norm, reason = validate_sec_code(raw)
        if ok:
            kept.append(norm)
        else:
            rejected.append((str(raw).strip() if raw is not None else "None", reason))
    return kept, rejected
