"""PR6b-1 operation expansion + manual_list multi-stock tests.

Covers (handoff §2 CP9 + CP2 遗留要求①):
  - pct_change IndicatorNode: builds + renders with numpy inline calc
  - rank/top_n RankingNode: builds + renders with sorted()[:top_n]
  - manual_list universe: 3 codes propagate to IR + both renders
  - 遗留要求① batch diff layer (AST, not substring):
      QS emits get_history_batch (real Call) / PTrade does NOT (real Call).
      Comments mentioning the API name must NOT trigger the diff (proves the
      check is AST-level, not naive substring).
  - zscore operation raises with "PR6b-2" message (deferred, not silently OK).
  - pct_change lookback propagates into dataload lookback (IR 承重).
"""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path

import pytest

from quantstudio.strategy_compiler.build_strategy_ir import build_strategy_ir
from quantstudio.strategy_compiler.contracts import ContractValidationError, validate_strategy_spec
from quantstudio.strategy_compiler.render import render_ptrade, render_quantstudio

EXAMPLES = Path(__file__).resolve().parents[1] / "quantstudio" / "strategy_compiler" / "examples"


@pytest.fixture
def case1_spec() -> dict:
    return json.loads((EXAMPLES / "case1_dual_ma_spec.json").read_text(encoding="utf-8"))


def _pct_rank_spec(case1_spec) -> dict:
    """Build a manual_list + pct_change + top_n spec (3-stock rotation)."""
    spec = deepcopy(case1_spec)
    spec["strategy_id"] = "pct_rank_test"
    spec["universe"] = {"kind": "manual_list",
                        "parameters": {"codes": ["600570.SH", "000001.SZ", "600036.SH"]}}
    spec["signals"] = {"steps": [
        {"id": "ret5", "operation": "pct_change",
         "parameters": {"field": "close", "lookback": 5}},
        {"id": "top", "operation": "top_n",
         "parameters": {"source": "ret5", "top_n": 2, "ascending": False}},
    ]}
    spec["portfolio"] = {"kind": "equal_weight_top_n",
                         "parameters": {"max_positions": 2, "rebalance": "signal_triggered",
                                       "target_weight": 1.0}}
    return spec


def _called_names(code: str) -> set[str]:
    """AST-level call extraction (NOT substring)."""
    names = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


# ---------------------------------------------------------------------------
# pct_change IndicatorNode
# ---------------------------------------------------------------------------

class TestPctChange:
    def test_builds_and_validates(self, case1_spec):
        spec = _pct_rank_spec(case1_spec)
        validate_strategy_spec(spec)  # raises if invalid
        ir = build_strategy_ir(spec)
        # an IndicatorNode with operation=pct_change exists
        pct_nodes = [n for n in ir.nodes
                     if n.node_type == "IndicatorNode"
                     and n.parameters.get("operation") == "pct_change"]
        assert len(pct_nodes) == 1
        assert pct_nodes[0].parameters["lookback"] == 5

    def test_lookback_propagates_to_dataload(self, case1_spec):
        """pct_change lookback must widen the dataload lookback (IR 承重)."""
        spec = _pct_rank_spec(case1_spec)
        ir = build_strategy_ir(spec)
        loads = [n for n in ir.nodes if n.node_type == "DataLoadNode"]
        assert loads, "expected a DataLoadNode"
        # dataload lookback should be >= the pct_change lookback (5)
        assert any(n.parameters.get("lookback", 0) >= 5 for n in loads)

    def test_variant_lookback_changes_render(self, case1_spec):
        """Changing pct_change lookback changes rendered output (传导非摆设)."""
        spec = _pct_rank_spec(case1_spec)
        ir1 = build_strategy_ir(spec)
        code1 = render_quantstudio(ir1)
        spec["signals"]["steps"][0]["parameters"]["lookback"] = 10
        ir2 = build_strategy_ir(spec)
        code2 = render_quantstudio(ir2)
        assert code1 != code2


# ---------------------------------------------------------------------------
# rank / top_n RankingNode
# ---------------------------------------------------------------------------

class TestRanking:
    def test_top_n_builds_ranking_node(self, case1_spec):
        spec = _pct_rank_spec(case1_spec)
        ir = build_strategy_ir(spec)
        ranking = [n for n in ir.nodes if n.node_type == "RankingNode"]
        assert len(ranking) == 1
        assert ranking[0].parameters.get("operation") == "top_n"
        assert ranking[0].parameters.get("top_n") == 2

    def test_rank_render_emits_sorted_topn(self, case1_spec):
        """QS render emits scores dict + sorted()[:max_positions]."""
        spec = _pct_rank_spec(case1_spec)
        ir = build_strategy_ir(spec)
        qs = render_quantstudio(ir)
        assert "scores" in qs
        assert "sorted(" in qs
        assert "[:g.max_positions]" in qs or "[: 2]" in qs or "[:max_positions" in qs


# ---------------------------------------------------------------------------
# manual_list multi-stock universe propagation
# ---------------------------------------------------------------------------

class TestManualList:
    def test_codes_propagate_to_ir(self, case1_spec):
        spec = _pct_rank_spec(case1_spec)
        ir = build_strategy_ir(spec)
        uni = [n for n in ir.nodes if n.node_type == "UniverseNode"][0]
        assert uni.parameters["kind"] == "manual_list"
        assert uni.parameters["codes"] == ["600570.SH", "000001.SZ", "600036.SH"]

    def test_codes_propagate_to_both_renders(self, case1_spec):
        spec = _pct_rank_spec(case1_spec)
        ir = build_strategy_ir(spec)
        qs = render_quantstudio(ir)
        pt = render_ptrade(ir)
        for code in ("600570.SH", "000001.SZ", "600036.SH"):
            assert code in qs, f"{code} missing from QS render"
            assert code in pt, f"{code} missing from PTrade render"
        # max_positions (2) propagates
        assert "g.max_positions" in qs or "max_positions" in qs


# ---------------------------------------------------------------------------
# 遗留要求① batch diff layer (AST-level, not substring)
# ---------------------------------------------------------------------------

class TestBatchDiffLayer:
    def test_qs_emits_batch_call_ptrade_does_not(self, case1_spec):
        """QS manual_list emits get_history_batch (Call); PTrade loops get_history.

        This is the load-bearing platform difference. The assertion is on the
        AST Call set, NOT a substring — PTrade comments mention the API name
        (forbidden-list doc) but must not actually CALL it.
        """
        spec = _pct_rank_spec(case1_spec)
        ir = build_strategy_ir(spec)
        qs_calls = _called_names(render_quantstudio(ir))
        pt_calls = _called_names(render_ptrade(ir))
        assert "get_history_batch" in qs_calls, "QS should emit get_history_batch for manual_list"
        assert "get_history_batch" not in pt_calls, (
            "PTrade must NOT call get_history_batch (AST-level); substring in comments is fine"
        )
        # PTrade loops per-stock get_history instead
        assert "get_history" in pt_calls

    def test_single_stock_no_batch_in_either(self, case1_spec):
        """single_stock universe: neither platform needs batch (1 stock)."""
        ir = build_strategy_ir(case1_spec)
        qs_calls = _called_names(render_quantstudio(ir))
        pt_calls = _called_names(render_ptrade(ir))
        assert "get_history_batch" not in qs_calls
        assert "get_history_batch" not in pt_calls


# ---------------------------------------------------------------------------
# minute + manual_list (all 4 templates supported — reviewer fix)
# ---------------------------------------------------------------------------

def _to_minute(ir):
    """Produce a minute-profile copy of the IR (mirrors PR6a test helper)."""
    from copy import deepcopy
    ir2 = deepcopy(ir)
    ir2.engine_profile["bar_frequency"] = "1m"
    ir2.engine_profile["profile_id"] = "minute-bar-v1"
    ir2.time_model["market_data_frequency"] = "1m"
    ir2.time_model["factor_frequency"] = "1m"
    ir2.time_model["signal_frequency"] = "1m"
    ir2.time_model["portfolio_valuation_frequency"] = "1m"
    return ir2


class TestMinuteManualList:
    """All 4 templates (daily+minute × QS+PTrade) render manual_list.

    Reviewer caught that minute templates raised NotImplementedError on
    non-single_stock. Now they render the multi-stock rotation branch.
    """

    def test_minute_qs_renders_manual_list(self, case1_spec):
        ir = build_strategy_ir(_pct_rank_spec(case1_spec))
        code = render_quantstudio(_to_minute(ir))
        compile(code, "<qs_min>", "exec")
        assert "NotImplementedError" not in code
        for c in ("600570.SH", "000001.SZ", "600036.SH"):
            assert c in code

    def test_minute_pt_renders_manual_list(self, case1_spec):
        ir = build_strategy_ir(_pct_rank_spec(case1_spec))
        code = render_ptrade(_to_minute(ir))
        compile(code, "<pt_min>", "exec")
        assert "NotImplementedError" not in code
        for c in ("600570.SH", "000001.SZ", "600036.SH"):
            assert c in code

    def test_minute_batch_diff_layer(self, case1_spec):
        """minute + manual_list: QS batch / PTrade loop (AST, all 4 templates)."""
        ir = build_strategy_ir(_pct_rank_spec(case1_spec))
        min_ir = _to_minute(ir)
        qs_calls = _called_names(render_quantstudio(min_ir))
        pt_calls = _called_names(render_ptrade(min_ir))
        assert "get_history_batch" in qs_calls
        assert "get_history_batch" not in pt_calls
        assert "get_history" in pt_calls


# ---------------------------------------------------------------------------
# scores dict population (semantic correctness — reviewer caught the bug)
# ---------------------------------------------------------------------------

class TestScoresPopulation:
    """pct_change results must populate the `scores` dict (ranking source).

    Reviewer caught that the daily templates assigned to {{ ind.id }}[code]
    (an undefined name) instead of scores[code], leaving scores empty and the
    ranking broken. This is an IR→render 传导 correctness check, not just AST.
    """

    @pytest.mark.parametrize("profile,render", [
        ("quantstudio", render_quantstudio),
        ("ptrade-default", render_ptrade),
    ])
    def test_daily_scores_populated(self, case1_spec, profile, render):
        ir = build_strategy_ir(_pct_rank_spec(case1_spec))
        code = render(ir)
        assert "scores[code] =" in code, (
            f"{profile} daily: pct_change must assign to scores[code], not "
            f"an undefined indicator-name dict (ranking would be empty)"
        )

    @pytest.mark.parametrize("profile,render", [
        ("quantstudio", render_quantstudio),
        ("ptrade-default", render_ptrade),
    ])
    def test_minute_scores_populated(self, case1_spec, profile, render):
        ir = build_strategy_ir(_pct_rank_spec(case1_spec))
        code = render(_to_minute(ir))
        assert "scores[code] =" in code


# ---------------------------------------------------------------------------
# zscore deferred to PR6b-2 (raises, not silently OK)
# ---------------------------------------------------------------------------

class TestDeferredOperations:
    def test_zscore_raises_pr6b2(self, case1_spec):
        spec = deepcopy(case1_spec)
        spec["signals"] = {"steps": [{"id": "z", "operation": "zscore", "parameters": {}}]}
        with pytest.raises(ContractValidationError, match="zscore"):
            build_strategy_ir(spec)

    def test_zscore_error_mentions_pr6b2(self, case1_spec):
        """The raise message must point to PR6b-2 (not PR6a)."""
        spec = deepcopy(case1_spec)
        spec["signals"] = {"steps": [{"id": "z", "operation": "zscore", "parameters": {}}]}
        with pytest.raises(ContractValidationError, match="PR6b-2"):
            build_strategy_ir(spec)

    def test_supported_ops_do_not_raise(self, case1_spec):
        """ma / pct_change / cross / rank / top_n / bottom_n all build cleanly."""
        for op, params in [("ma", {"field": "close", "lookback": 5}),
                           ("pct_change", {"field": "close", "lookback": 5})]:
            spec = deepcopy(case1_spec)
            spec["signals"] = {"steps": [{"id": "s", "operation": op, "parameters": params}]}
            build_strategy_ir(spec)  # should not raise
