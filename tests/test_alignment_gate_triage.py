# -*- coding: utf-8 -*-
"""件 A+B 契约测试（2026-10-07）。

A · ALIGNMENT-GATE 三态（达标 PASS 无 issue / 缺失 WARN / 未达标 BLOCK + legacy 豁免）
B · S3 规则库回放（四象限三轮历史 + POS/FEE 规则 + 无命中立新案）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest

from validate_agent_strategy import _validate_alignment_gate
from align_triage_rules import triage, RULES


# ---------- 件 A：ALIGNMENT-GATE 三态 ----------

def _mk(tmp_path, report_text=None):
    """strategies_dir=output/strategies 形态：产物在 ../output/generated_strategies/<stem>/。"""
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    if report_text is not None:
        rdir = tmp_path / "output" / "generated_strategies" / "测试策略" / "alignment"
        rdir.mkdir(parents=True)
        (rdir / "report.md").write_text(report_text, encoding="utf-8")
    return strategies


def test_gate_missing_report_warns(tmp_path):
    issues = []
    _validate_alignment_gate("测试策略.py", _mk(tmp_path, None), issues)
    assert len(issues) == 1
    assert issues[0]["rule_id"] == "ALIGNMENT-GATE"
    assert issues[0]["severity"] == "WARN"          # 缺失=柔性 WARN（legacy 豁免）


def test_gate_aligned_report_passes_silently(tmp_path):
    report = "# S1\n...\n- **判定：已对齐**\n"
    issues = []
    _validate_alignment_gate("测试策略.py", _mk(tmp_path, report), issues)
    assert issues == []                              # PASS 态无 issue


def test_gate_unaligned_report_blocks(tmp_path):
    report = "# S1\n...\n- **判定：归因候选（委托分歧在先 ⇒ 触发链回溯：订单语义/API/风控状态（POS/ENG/API 类））**\n"
    issues = []
    _validate_alignment_gate("测试策略.py", _mk(tmp_path, report), issues)
    assert len(issues) == 1
    assert issues[0]["severity"] == "BLOCK"          # 跑了没过=BLOCK


def test_gate_residual_ok_passes_despite_mixed_verdict(tmp_path):
    """四象限终态形态：§5=混合形态（尾差级非零）但 §6 残差达标 → 放行（防误 BLOCK 已达标策略）。"""
    report = ("# S1\n...\n- **判定：混合形态（需人工仲裁）**\n\n"
              "## 6. 案件路由（S3 判别特征规则库 · 机判人核，非终判）\n"
              "- **残差达标 ✅**：末值 -20.25 元 / 总资产 152737.85 = 0.0133% < 0.5%"
              "（对齐门 aligned 判据满足）\n")
    issues = []
    _validate_alignment_gate("测试策略.py", _mk(tmp_path, report), issues)
    assert issues == []


def test_gate_no_strategies_dir_noop():
    issues = []
    _validate_alignment_gate("x.py", None, issues)
    assert issues == []                              # 无法定位=不判（豁免）


# ---------- 件 B：S3 规则库回放（四象限三轮历史） ----------

def test_triage_round1_corp01_merge_band():
    """回放 CORP-01 修复前轮：nav 首偏+委托现金全一致 → merge-band 命中（人工归因复现）。"""
    J = {"nav_diff": {"first_div_date": "2025-10-09", "first_div": 3880.0, "nonzero_days": 200},
         "order_div_count": 0, "order_first_div": None, "cash_diff_first": None,
         "order_divs": [], "verdict": "不可归因→立新案（净值偏差但委托/现金全一致 ⇒ 估值口径类（DAT）候选）"}
    out = triage(J)
    ids = [c["id"] for c in out["candidates"]]
    assert "CORP-01-merge-band" in ids
    assert not out["new_case"]


def test_triage_round2_corp02_odd_lot():
    """回放 CORP-02 轮：分歧 18 组含整手截断（3200 vs 3296 等）→ odd-lot-tail 命中。"""
    J = {"nav_diff": {"first_div_date": "2026-01-29", "first_div": 37.28, "nonzero_days": 100},
         "order_div_count": 18, "order_first_div": "2026-01-29", "cash_diff_first": "2026-01-29",
         "order_divs": [[["2026-01-29", "159934", "sell"], 3296, 3200],
                        [["2026-01-29", "159629", "buy"], 34800, 34700]],
         "verdict": "归因候选（委托分歧在先 ⇒ 触发链回溯）"}
    out = triage(J)
    ids = [c["id"] for c in out["candidates"]]
    assert "CORP-02-odd-lot-tail" in ids             # 3296-3200=96 非整百 + 本地 3200 整百平台非整百
    assert "POS-ENG-API-trigger-chain" in ids          # 委托分歧在先同真（多候选合法）


def test_triage_round3_aligned_no_routing():
    """回放终轮：已对齐 → 无需路由。"""
    J = {"nav_diff": {"first_div_date": "2024-02-01", "first_div": 0.2, "nonzero_days": 200},
         "order_div_count": 3, "order_first_div": "2026-01-29", "cash_diff_first": "2025-12-26",
         "order_divs": [[["2026-01-29", "159934", "sell"], 3296, 3293],
                        [["2026-01-29", "159629", "buy"], 34800, 34700],
                        [["2026-02-02", "159629", "buy"], 500, 600]],
         "verdict": "已对齐"}
    out = triage(J)
    assert out["candidates"] == [] and not out["new_case"]


def test_triage_fee_cash_first():
    J = {"nav_diff": {"first_div_date": "2026-02-01", "first_div": 5.0, "nonzero_days": 10},
         "order_div_count": 0, "order_first_div": None,
         "cash_diff_first": "2026-01-15", "order_divs": [],
         "verdict": "混合形态（需人工仲裁）"}
    out = triage(J)
    assert any(c["id"] == "FEE-cash-first" for c in out["candidates"])


def test_triage_no_hit_new_case():
    """委托分歧晚于 nav 首偏 + 现金不先偏 + 无尾差 → 无命中立新案。"""
    J = {"nav_diff": {"first_div_date": "2025-01-05", "first_div": 10.0, "nonzero_days": 30},
         "order_div_count": 2, "order_first_div": "2025-06-01",
         "cash_diff_first": "2025-07-01",
         "order_divs": [[["2025-06-01", "510300", "buy"], 10000, 9900]],
         "verdict": "混合形态（需人工仲裁）"}
    out = triage(J)
    assert out["new_case"] is True


def test_rules_registry_ids_unique():
    ids = [r.id for r in RULES]
    assert len(ids) == len(set(ids))
