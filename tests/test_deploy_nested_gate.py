# -*- coding: utf-8 -*-
"""PD-0012：恒真分支还原（`if not False:` → `if not is_trade():`）测试。

背景：`qs-compile import` 把 `is_trade()` 固化为字面量 `False`（source_import.py:3087），
源策略的 `if not is_trade(): set_limit_mode(...)` 变成 `if not False:`（**恒真**）⇒ 交易侧门控失效。
M3 只门控首层 ⇒ 嵌套逃逸 ⇒ P5 `contract_full` 拦截（保护生效）。

修复：静态恒真 **且** 分支体只含回测专用 API ⇒ 条件还原为 `not is_trade()`（分支体不动），
并在**转换审计行**留痕「恒真分支还原@行号」（②审 2026-10-30 新增要求）。

覆盖：
- 正向：`if not False:` / `if True:` + 仅 gate API → 还原 + 审计行
- 负向①：分支体混业务语句 → **不得还原**（保持 P5 拦截，防误伤）
- 负向②：非恒真条件（`if some_flag:`）→ 不还原
- 回归：首层 gate 调用 → 仍走 `is_trade()` 包裹（行为不变）
- 告警：未还原的嵌套调用仍产生「嵌套未门控」警告；已还原的不再告警
"""
import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from quantstudio.ptrade_deploy.transformer import (  # noqa: E402
    _apply_gates, _is_not_false_literal, _branch_only_gate_calls)

#: 最小契约表：set_limit_mode = 回测专用 + strip_or_gate（与真实契约表同形）
TABLE = {
    "set_limit_mode": {"signature": "set_limit_mode(m)", "backtest": True,
                       "trading": False, "action": "strip_or_gate", "replacement": None},
    "log": {"signature": "log.info(m)", "backtest": True,
            "trading": True, "action": "keep", "replacement": None},
}


def _run(src: str):
    tree = ast.parse(src)
    edits, warns = [], []
    _apply_gates(tree, TABLE, edits, warns)
    return tree, edits, warns


def _conds(tree) -> list:
    return [ast.dump(n.test) for n in ast.walk(tree) if isinstance(n, ast.If)]


def _static_ifs(tree) -> list:
    """所有「条件为 `not False` 字面量」的 If 节点（结构的，不依赖 ast.dump 文本）。"""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.If) and _is_not_false_literal(n.test)]


def test_static_true_detection():
    """还原识别面**只认** `not False`；`True`/`1`/变量/函数调用一律不认（保守）。"""
    for src, expect in [("not False", True), ("True", False), ("1", False),
                        ("some_flag", False), ("is_trade()", False)]:
        assert _is_not_false_literal(ast.parse(src).body[0].value) is expect, src


def test_branch_only_gate_calls_allows_pass_and_docstring():
    body = ast.parse("def f():\n if x:\n  set_limit_mode('U')\n  pass\n").body[0].body[0].body
    assert _branch_only_gate_calls(body, {"set_limit_mode"}) is True
    body2 = ast.parse("def f():\n if x:\n  set_limit_mode('U')\n  y = 1\n").body[0].body[0].body
    assert _branch_only_gate_calls(body2, {"set_limit_mode"}) is False


def _static_ifs(tree) -> list:
    """所有「条件为 `not False` 字面量」的 If 节点（结构的，不依赖 ast.dump 文本）。"""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.If) and _is_not_false_literal(n.test)]


def test_restore_always_true_branch_and_audit():
    """正向：`if not False:` + 仅 gate 调用 ⇒ 还原 + 审计行含行号。"""
    src = (
        "def initialize(context):\n"
        "    if not False:\n"
        "        set_limit_mode('UNLIMITED')\n"
    )
    tree, edits, warns = _run(src)
    assert any("is_trade" in c for c in _conds(tree)), _conds(tree)
    assert _static_ifs(tree) == [], "恒真条件应已还原"
    restore_edits = [e for e in edits if e["kind"] == "GATE_RESTORE"]
    assert restore_edits, edits
    assert "恒真分支还原@" in restore_edits[0]["detail"]
    assert warns == [], warns                      # 已还原 ⇒ 不再报嵌套告警


def test_no_restore_for_if_true_form():
    """`if True:` 不还原（source_import 不产出该形态；还原会破坏 P4 重建）。"""
    src = ("def initialize(context):\n    if True:\n        set_limit_mode('U')\n")
    tree, edits, warns = _run(src)
    assert not [e for e in edits if e["kind"] == "GATE_RESTORE"], edits
    assert any("嵌套复合语句内" in w for w in warns)


def test_no_restore_when_branch_has_business_stmt():
    """负向①：分支体混业务语句 ⇒ 不还原（仍由 P5 拦截，防误伤）。"""
    src = (
        "def initialize(context):\n"
        "    if not False:\n"
        "        set_limit_mode('U')\n"
        "        g.x = 1\n"
    )
    tree, edits, warns = _run(src)
    assert not [e for e in edits if e["kind"] == "GATE_RESTORE"], edits
    assert _static_ifs(tree), "条件应保持静态恒真（未还原）"
    assert any("嵌套复合语句内" in w for w in warns), warns      # 仍告警


def test_no_restore_when_condition_not_static():
    """负向②：非恒真条件不还原。"""
    src = ("def initialize(context):\n    if some_flag:\n        set_limit_mode('U')\n")
    tree, edits, warns = _run(src)
    assert not [e for e in edits if e["kind"] == "GATE_RESTORE"]
    assert any("嵌套复合语句内" in w for w in warns)


def test_first_level_gate_unchanged():
    """回归：首层 gate 调用仍走 is_trade() 包裹（行为不变）。"""
    src = "def initialize(context):\n    set_limit_mode('U')\n"
    tree, edits, _ = _run(src)
    kinds = [e["kind"] for e in edits]
    assert "GATE" in kinds and "GATE_RESTORE" not in kinds
    assert any("is_trade" in c for c in _conds(tree))


def test_deep_nesting_restored():
    """任意嵌套深度（for → if not False → gate）均应还原。"""
    src = (
        "def initialize(context):\n"
        "    for s in ['a']:\n"
        "        if not False:\n"
        "            set_limit_mode('U')\n"
    )
    tree, edits, warns = _run(src)
    assert any(e["kind"] == "GATE_RESTORE" for e in edits), edits
    assert warns == []


# ------------------------------------------------- 真实产物端到端（可选） --

@pytest.mark.skipif(not (REPO / "output/ptrade_export/全球资产轮动").exists(),
                    reason="真实产物不存在（不同 checkout）")
def test_real_product_p5_passes_after_restore(tmp_path):
    """真实产物 `全球资产轮动`：还原后 P5 不再报 set_limit_mode 未门控。"""
    from quantstudio.ptrade_deploy import quickmode, run_deploy
    prod = REPO / "output/ptrade_export/全球资产轮动/全球资产轮动_ptrade.py"
    cfg = quickmode.write_quick_config("全球资产轮动", str(prod), config_dir=str(tmp_path))
    d = run_deploy(str(cfg)).to_dict()
    p5 = d["stages"]["P5_gates"]
    assert "set_limit_mode" not in (p5.get("reason") or ""), p5
