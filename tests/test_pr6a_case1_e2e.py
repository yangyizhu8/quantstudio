"""PR6a end-to-end tests: case1 dual-MA spec -> IR -> render -> validate.

Covers (per PR6a plan §4 acceptance + Checkpoint 6 前置要求):
  - Strong consistency: build_strategy_ir(case1_spec).to_dict() == 固化 example
  - render -> compile() + StrategyIsolationGuard PASS (daily + minute, QS + PTrade)
  - IR 承重断言: 渲染产物含 Spec 具体值 (ma 5/10, 600570, close)
  - 变体测试: Spec 窗口改 10/20 后渲染产物相应变化
  - 差异层断言: PTrade 禁 batch API (AST 调用检查, 非朴素子串)
  - 黄金保护双源: etf_momentum/smallcap_guard (动态) + dual_ma_sample (硬编码兜底)
  - scan_lookahead + validate_local_strategy 正向 PASS (daily + minute 产物)
  - 分钟产物正向扫描 (防止模板改动引入误报)
"""

from __future__ import annotations

import ast
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest

from quantstudio.strategy_compiler.build_strategy_ir import build_strategy_ir
from quantstudio.strategy_compiler.contracts import (
    validate_strategy_spec,
    validate_strategy_ir,
)
from quantstudio.strategy_compiler.render import (
    GoldenProtectionError,
    render_ptrade,
    render_quantstudio,
)
from quantstudio.strategy_compiler.validators.scan_lookahead import scan_lookahead
from quantstudio.strategy_compiler.validators.validate_local_strategy import (
    validate_local_strategy,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "quantstudio" / "strategy_compiler" / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def case1_spec() -> dict:
    return _load("case1_dual_ma_spec.json")


@pytest.fixture
def case1_ir_dict() -> dict:
    return _load("strategy_ir.example.json")


@pytest.fixture
def case1_ir(case1_spec):
    return build_strategy_ir(case1_spec)


# ---------------------------------------------------------------------------
# Strong consistency: builder output == 固化 example (field-by-field)
# ---------------------------------------------------------------------------

class TestStrongConsistency:
    def test_built_ir_equals固化_example(self, case1_spec, case1_ir_dict):
        """build_strategy_ir(case1_spec).to_dict() == 固化 example 逐字段相等。

        example is the builder's standard product (not hand-written); this test
        detects any drift between the mapping rules in code vs the固化 snapshot.
        """
        ir = build_strategy_ir(case1_spec)
        built = ir.to_dict()
        assert built == case1_ir_dict, (
            "STRONG CONSISTENCY FAIL: built IR != 固化 example. Either re-固化 "
            "example after intentional builder changes, or fix the builder."
        )

    def test_built_ir_passes_validate_strategy_ir(self, case1_spec):
        ir = build_strategy_ir(case1_spec)
        validate_strategy_ir(ir.to_dict())

    def test_case1_spec_passes_validate_strategy_spec(self, case1_spec):
        validate_strategy_spec(case1_spec)


# ---------------------------------------------------------------------------
# render -> compile() + StrategyIsolationGuard (daily + minute, QS + PTrade)
# ---------------------------------------------------------------------------

class TestRenderCompilesAndPassesGuard:
    def _compile_and_guard(self, code: str, label: str) -> None:
        compile(code, f"<{label}>", "exec")
        from quantstudio.backtest.strategy_runner import StrategyIsolationGuard
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp = f.name
        try:
            StrategyIsolationGuard.validate_path(tmp)
        finally:
            os.unlink(tmp)

    def test_daily_quantstudio_compiles_and_guarded(self, case1_ir):
        self._compile_and_guard(render_quantstudio(case1_ir), "qs_daily")

    def test_daily_ptrade_compiles_and_guarded(self, case1_ir):
        self._compile_and_guard(render_ptrade(case1_ir), "pt_daily")

    def test_minute_quantstudio_compiles_and_guarded(self, case1_ir):
        ir = self._to_minute(case1_ir)
        self._compile_and_guard(render_quantstudio(ir), "qs_minute")

    def test_minute_ptrade_compiles_and_guarded(self, case1_ir):
        ir = self._to_minute(case1_ir)
        self._compile_and_guard(render_ptrade(ir), "pt_minute")

    @staticmethod
    def _to_minute(ir):
        """Produce a minute-profile copy of the IR for minute-template coverage."""
        from copy import deepcopy
        ir2 = deepcopy(ir)
        ir2.engine_profile["bar_frequency"] = "1m"
        ir2.engine_profile["profile_id"] = "minute-bar-v1"
        ir2.time_model["market_data_frequency"] = "1m"
        ir2.time_model["factor_frequency"] = "1m"
        ir2.time_model["signal_frequency"] = "1m"
        ir2.time_model["portfolio_valuation_frequency"] = "1m"
        return ir2


# ---------------------------------------------------------------------------
# IR 承重断言 + 变体测试 (Spec -> IR -> render 真实传导)
# ---------------------------------------------------------------------------

class TestIRLoadBearing:
    def test_render_carries_spec_concrete_values(self, case1_ir):
        """渲染产物含 Spec 具体值 (ma 5/10, 600570, close) - IR 承重为真."""
        code = render_quantstudio(case1_ir)
        assert "_get_ma(close_data, 5)" in code, "ma 窗口 5 未出现在调用参数"
        assert "_get_ma(close_data, 10)" in code, "ma 窗口 10 未出现在调用参数"
        assert "600570" in code, "股票代码 600570 未出现"

    def test_variant_spec_changes_rendered_output(self, case1_spec, case1_ir):
        """Spec 窗口改 10/20 后渲染产物相应变化 - 证明传导非摆设."""
        spec2 = deepcopy(case1_spec)
        spec2["signals"]["steps"][0]["parameters"]["lookback"] = 12
        spec2["signals"]["steps"][1]["parameters"]["lookback"] = 26
        ir2 = build_strategy_ir(spec2)
        code1 = render_quantstudio(case1_ir)
        code2 = render_quantstudio(ir2)
        assert code1 != code2, "变体后产物未变化 (IR 未承重)"
        assert "_get_ma(close_data, 12)" in code2, "新窗口 12 未透传"
        assert "_get_ma(close_data, 26)" in code2, "新窗口 26 未透传"


# ---------------------------------------------------------------------------
# Profile 差异层断言 (AST 调用检查, 非朴素子串)
# ---------------------------------------------------------------------------

class TestProfileDiffLayer:
    @staticmethod
    def _called_names(code: str) -> set[str]:
        tree = ast.parse(code)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
        return names

    def test_ptrade_has_no_batch_api_calls(self, case1_ir):
        """PTrade 产物不得含 get_fundamentals_batch / get_history_batch 调用."""
        code = render_ptrade(case1_ir)
        calls = self._called_names(code)
        assert "get_fundamentals_batch" not in calls
        assert "get_history_batch" not in calls

    def test_qs_ptrade_use_explicit_profile_specific_calls(self, case1_ir):
        """PTrade uses scheduled, history-only runtime compatibility semantics."""
        qs_calls = self._called_names(render_quantstudio(case1_ir))
        pt_calls = self._called_names(render_ptrade(case1_ir))
        assert "run_daily" in pt_calls
        assert "get_history_batch" not in pt_calls
        assert "get_history_batch" not in qs_calls  # single-stock case
        assert "order_target_value" in pt_calls

    def test_ptrade_header_declares_profile(self, case1_ir):
        code = render_ptrade(case1_ir)
        assert "ptrade-default" in code


# ---------------------------------------------------------------------------
# 黄金保护双源负向
# ---------------------------------------------------------------------------

class TestGoldenProtection:
    def test_etf_momentum_blocked_dynamic_source(self, case1_spec):
        """config gates 动态源命中 etf_momentum."""
        spec = deepcopy(case1_spec)
        spec["strategy_id"] = "etf_momentum"
        ir = build_strategy_ir(spec)
        with pytest.raises(GoldenProtectionError, match="etf_momentum"):
            render_quantstudio(ir)

    def test_smallcap_guard_blocked_dynamic_source(self, case1_spec):
        spec = deepcopy(case1_spec)
        spec["strategy_id"] = "smallcap_guard"
        ir = build_strategy_ir(spec)
        with pytest.raises(GoldenProtectionError, match="smallcap_guard"):
            render_ptrade(ir)

    def test_dual_ma_sample_blocked_hardcoded_fallback(self, case1_spec):
        """config 不可用时硬编码兜底仍 raise (dual_ma_sample 仅在硬编码)."""
        spec = deepcopy(case1_spec)
        spec["strategy_id"] = "dual_ma_sample"
        ir = build_strategy_ir(spec)
        with pytest.raises(GoldenProtectionError, match="dual_ma_sample"):
            render_quantstudio(ir, config_path="/nonexistent/path.json")

    def test_case1_not_protected_renders_normally(self, case1_ir):
        """非保护 ID 正常渲染 (黄金保护不过度拦截)."""
        # render_quantstudio in fixture already succeeded; explicit assert here
        code = render_quantstudio(case1_ir)
        assert "case1_dual_ma" in code


# ---------------------------------------------------------------------------
# scan_lookahead + validate_local_strategy 正向 (daily + minute 产物)
# ---------------------------------------------------------------------------

class TestValidatorsPositive:
    def test_daily_qs_scan_lookahead_pass(self, case1_ir):
        ok, violations, _ = scan_lookahead(case1_ir, render_quantstudio(case1_ir))
        assert ok, f"scan_lookahead daily QS 误报: {violations}"

    def test_daily_qs_validate_local_pass(self, case1_spec, case1_ir):
        ok, violations, _ = validate_local_strategy(
            case1_spec, case1_ir, render_quantstudio(case1_ir), "quantstudio"
        )
        assert ok, f"validate_local_strategy daily QS 误报: {violations}"

    def test_daily_pt_validate_local_pass(self, case1_spec, case1_ir):
        ok, violations, _ = validate_local_strategy(
            case1_spec, case1_ir, render_ptrade(case1_ir), "ptrade-default"
        )
        assert ok, f"validate_local_strategy daily PTrade 误报: {violations}"

    def test_minute_qs_scan_lookahead_no_false_positive(self, case1_ir):
        """分钟渲染产物当前形态 (信号在 run_daily) scan = 0 violations - 防止模板改动引入误报."""
        from copy import deepcopy
        ir = deepcopy(case1_ir)
        ir.engine_profile["bar_frequency"] = "1m"
        ir.engine_profile["profile_id"] = "minute-bar-v1"
        ir.time_model["market_data_frequency"] = "1m"
        ir.time_model["factor_frequency"] = "1m"
        ir.time_model["signal_frequency"] = "1m"
        ir.time_model["portfolio_valuation_frequency"] = "1m"
        ok, violations, _ = scan_lookahead(ir, render_quantstudio(ir))
        assert ok, f"scan_lookahead minute QS 误报 (模板改动可能引入): {violations}"
