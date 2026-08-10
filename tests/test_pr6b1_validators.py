"""PR6b-1 validator tests: positive + negative for the 5 validators.

Covers (handoff §2 CP9):
  - validate_strategy_spec (converged to contracts.py 5 timing rules)
  - check_hard_filters (positive PASS + missing execution-stage BLOCK)
  - validate_ptrade_portability (positive PASS + batch/file BLOCK)
  - compare_strategy_variants (positive DIFF_OK + PTrade forbidden BLOCK)
  - run_smoke_backtest (BLOCKED capability-gate path + missing-file FAILED path)

遗留要求②: failure cases assert the strategy is "被阻断" (BLOCKED) — never "通过".
The READY→engine subprocess path is exercised in test_pr6b1_orchestrator (CP10
real smoke); here we cover the deterministic non-engine paths so this file is
green regardless of DB/engine availability.
"""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path

import pytest

from quantstudio.strategy_compiler.build_strategy_ir import build_strategy_ir
from quantstudio.strategy_compiler.contracts import validate_strategy_spec
from quantstudio.strategy_compiler.render import render_ptrade, render_quantstudio
from quantstudio.strategy_compiler.validators.check_hard_filters import check_hard_filters
from quantstudio.strategy_compiler.validators.compare_strategy_variants import compare_strategy_variants
from quantstudio.strategy_compiler.validators.run_smoke_backtest import run_smoke_backtest
from quantstudio.strategy_compiler.validators.scan_lookahead import Violation
from quantstudio.strategy_compiler.validators.validate_ptrade_portability import validate_ptrade_portability

EXAMPLES = Path(__file__).resolve().parents[1] / "quantstudio" / "strategy_compiler" / "examples"


@pytest.fixture
def case1_spec() -> dict:
    return json.loads((EXAMPLES / "case1_dual_ma_spec.json").read_text(encoding="utf-8"))


@pytest.fixture
def case1_ir(case1_spec):
    return build_strategy_ir(case1_spec)


def _has_block(violations: list[Violation], rule_id: str) -> bool:
    return any(v.severity == "BLOCK" and v.rule_id == rule_id for v in violations)


# ---------------------------------------------------------------------------
# validate_strategy_spec (converged to contracts.py — 5 timing rules)
# ---------------------------------------------------------------------------

class TestValidateStrategySpec:
    def test_case1_passes(self, case1_spec):
        validate_strategy_spec(case1_spec)  # raises on invalid

    def test_timing_violation_caught(self, case1_spec):
        """market_data_frequency must equal bar_frequency (contracts.py rule).

        Before PR6b-1 convergence, the Skill script skipped this. Now contracts
        catches it.
        """
        spec = deepcopy(case1_spec)
        spec["time_model"]["market_data_frequency"] = "1m"  # mismatch bar_frequency 1d
        with pytest.raises(Exception):  # ContractValidationError
            validate_strategy_spec(spec)


# ---------------------------------------------------------------------------
# check_hard_filters
# ---------------------------------------------------------------------------

class TestCheckHardFilters:
    def test_case1_passes(self, case1_ir, case1_spec):
        ok, violations, _ = check_hard_filters(case1_ir, case1_spec)
        assert ok, f"case1 hard filters should PASS: {violations}"

    def test_missing_execution_stage_blocked(self, case1_spec):
        """Remove the execution-stage HardFilterNode → HARDFILTER-EXECUTION-STAGE BLOCK."""
        ir = build_strategy_ir(case1_spec)
        # Strip execution-stage hard filter nodes
        ir.nodes = [n for n in ir.nodes
                    if not (n.node_type == "HardFilterNode"
                            and n.parameters.get("stage") == "execution")]
        ok, violations, _ = check_hard_filters(ir, case1_spec)
        assert not ok, "missing execution-stage must BLOCK (遗留要求②: 被阻断 not 通过)"
        assert _has_block(violations, "HARDFILTER-EXECUTION-STAGE")


# ---------------------------------------------------------------------------
# validate_ptrade_portability
# ---------------------------------------------------------------------------

class TestValidatePtradePortability:
    def test_case1_ptrade_passes(self, case1_ir):
        code = render_ptrade(case1_ir)
        ok, violations, _ = validate_ptrade_portability(code, case1_ir)
        assert ok, f"case1 PTrade portability should PASS: {violations}"

    def test_batch_api_blocked(self, case1_ir):
        """PTrade code calling get_history_batch → PORTABILITY-LOCAL-EXTENSION-BAN."""
        code = render_ptrade(case1_ir)
        # Inject a forbidden batch call into handle_data
        code = code.replace(
            "def handle_data",
            "def _poison():\n    get_history_batch(g.stock_list, 5)\n\ndef handle_data",
            1,
        )
        ok, violations, _ = validate_ptrade_portability(code, case1_ir)
        assert not ok, "batch API in PTrade must BLOCK (遗留要求②)"
        assert _has_block(violations, "PORTABILITY-LOCAL-EXTENSION-BAN")

    def test_file_db_access_blocked(self, case1_ir):
        """PTrade code calling create_dir → PORTABILITY-FILE-DB-ACCESS."""
        code = render_ptrade(case1_ir)
        code = code.replace(
            "def handle_data",
            "def _poison():\n    create_dir('/local/db')\n\ndef handle_data",
            1,
        )
        ok, violations, _ = validate_ptrade_portability(code, case1_ir)
        assert not ok
        assert _has_block(violations, "PORTABILITY-FILE-DB-ACCESS")

    def test_set_backtest_blocked(self, case1_ir):
        """T3：本地自创 set_backtest 必须 BLOCK（上次真实平台 NameError 根因）。

        旧 6 项 DENYLIST 之外的本地扩展 API 现由 portability_rules.denylist()
        并集覆盖（T1 盘点 REMOVE 分类）。"""
        code = render_ptrade(case1_ir)
        code = code.replace(
            "def handle_data",
            "def _poison():\n    set_backtest(True)\n\ndef handle_data",
            1,
        )
        ok, violations, _ = validate_ptrade_portability(code, case1_ir)
        assert not ok, "set_backtest in PTrade must BLOCK (T3)"
        assert _has_block(violations, "PORTABILITY-LOCAL-API")

    def test_load_research_signals_blocked(self, case1_ir):
        """T3：外部数据源 load_research_signals 必须 BLOCK（不在 1:1 承诺内）。"""
        code = render_ptrade(case1_ir)
        code = code.replace(
            "def handle_data",
            "def _poison():\n    rows = load_research_signals('x.csv')\n\ndef handle_data",
            1,
        )
        ok, violations, _ = validate_ptrade_portability(code, case1_ir)
        assert not ok
        assert _has_block(violations, "PORTABILITY-LOCAL-API")


# ---------------------------------------------------------------------------
# compare_strategy_variants
# ---------------------------------------------------------------------------

class TestCompareStrategyVariants:
    def test_case1_dual_products_pass(self, case1_spec, case1_ir):
        qs = render_quantstudio(case1_ir)
        pt = render_ptrade(case1_ir)
        ok, violations, warnings, report = compare_strategy_variants(case1_spec, case1_ir, qs, pt)
        assert ok, f"case1 variant consistency should PASS: {violations}"
        # Cost (dim13) and stop-loss (dim10) are honestly marked GAP/EMPTY
        assert report["dimensions"]["13_costs_slippage"]["status"] == "GAP"
        assert report["dimensions"]["10_stop_loss_take_profit"]["status"] == "EMPTY"
        # No forbidden APIs in PTrade output
        assert report["dimensions"]["14_api_capability_diff"]["status"] == "DIFF_OK"

    def test_ptrade_forbidden_api_blocked(self, case1_spec, case1_ir):
        """If PTrade output contained a forbidden API, variant diff BLOCKS."""
        qs = render_quantstudio(case1_ir)
        pt = render_ptrade(case1_ir).replace(
            "def handle_data",
            "def _p():\n    get_history_batch([], 5)\n\ndef handle_data",
            1,
        )
        ok, violations, _, report = compare_strategy_variants(case1_spec, case1_ir, qs, pt)
        assert not ok
        assert _has_block(violations, "VARIANT-API-DIFF-VIOLATION")
        assert report["dimensions"]["14_api_capability_diff"]["status"] == "DIFF_VIOLATION"


# ---------------------------------------------------------------------------
# run_smoke_backtest (deterministic non-engine paths)
# ---------------------------------------------------------------------------

class TestRunSmokeBacktest:
    def test_blocked_capability_path(self, tmp_path):
        """overall_execution_status != READY → BLOCKED, engine NOT invoked (R6)."""
        # tick case: inspect returns PLANNED (invariant 4)
        cap = {"overall_execution_status": "PLANNED",
               "blockers": ["tick_backtest: PLANNED — PR9 scope"]}
        status, result, warns = run_smoke_backtest("dummy.py", cap)
        assert status == "BLOCKED"
        assert result["status"] == "BLOCKED"
        assert result["command"] == ""  # engine not invoked
        # R6 honest message (master plan line 583)
        assert "能力门禁阻止" in result["summary"]
        assert "R6" in result["summary"]
        assert warns  # a warning was recorded

    def test_blocked_path_also_for_blocked_status(self, tmp_path):
        """overall_execution_status=BLOCKED → BLOCKED (not READY)."""
        cap = {"overall_execution_status": "BLOCKED", "blockers": ["data missing"]}
        status, result, _ = run_smoke_backtest("dummy.py", cap)
        assert status == "BLOCKED"
        assert "能力门禁阻止" in result["summary"]

    def test_ready_missing_file_failed(self, tmp_path):
        """READY but strategy file missing → FAILED (not a crash)."""
        cap = {"overall_execution_status": "READY", "blockers": []}
        status, result, _ = run_smoke_backtest(tmp_path / "nope.py", cap)
        assert status == "FAILED"
        assert result["status"] == "FAILED"
        assert "not found" in result["summary"]

    def test_smoke_result_schema_conformant(self):
        """smokeResult dict matches run_card.schema.json {status, command, summary}."""
        cap = {"overall_execution_status": "PLANNED", "blockers": []}
        _, result, _ = run_smoke_backtest("x.py", cap)
        assert set(result.keys()) == {"status", "command", "summary"}
        assert result["status"] in ("PASS", "BLOCKED", "FAILED")
