"""PR6b-1 orchestrator tests: end-to-end spec → run_card.

Covers (handoff §2 CP9):
  - case1 end-to-end: run_card stage=STATIC_VALIDATED status=PARTIAL (no-smoke),
    all 5 validation checks PASS, run_card conforms to schema.
  - tick spec BLOCKED path: a minute→tick-style capability-gate block produces
    smoke BLOCKED + honest "能力门禁阻止" message (遗留要求②: 被阻断).
  - single-writer rule: variant_consistency_report.json written alongside
    run_card.json.
  - golden protection surfaced (not silent run_card).

The real READY→engine smoke is exercised in CP10 acceptance (DB-dependent);
here we use run_smoke=False to keep the tests deterministic and green
regardless of engine/DB state, plus a mocked-capability smoke block.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from quantstudio.strategy_compiler.orchestrator import orchestrate
from quantstudio.strategy_compiler.render import GoldenProtectionError

EXAMPLES = Path(__file__).resolve().parents[1] / "quantstudio" / "strategy_compiler" / "examples"
SCHEMA = Path(__file__).resolve().parents[1] / "quantstudio" / "strategy_compiler" / "schemas" / "run_card.schema.json"


@pytest.fixture
def case1_spec() -> dict:
    return json.loads((EXAMPLES / "case1_dual_ma_spec.json").read_text(encoding="utf-8"))


def _validate_run_card_schema(run_card: dict) -> None:
    import jsonschema
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema).validate(run_card)


# ---------------------------------------------------------------------------
# case1 end-to-end (no smoke — deterministic)
# ---------------------------------------------------------------------------

class TestCase1EndToEnd:
    def test_case1_static_validated(self, case1_spec, tmp_path):
        rc = orchestrate(case1_spec, run_smoke=False, out_dir=tmp_path)
        assert rc["stage"] == "STATIC_VALIDATED"
        # PARTIAL: static passed but smoke not run
        assert rc["status"] == "PARTIAL"
        assert all(v == "PASS" for v in rc["validation"].values()), rc["validation"]
        assert rc["smoke_backtest"] is None  # not run
        _validate_run_card_schema(rc)

    def test_case1_artifacts_written(self, case1_spec, tmp_path):
        rc = orchestrate(case1_spec, run_smoke=False, out_dir=tmp_path)
        names = {a["name"] for a in rc["artifacts"]}
        # spec + IR + both renders + variant report
        assert "strategy_spec.json" in names
        assert "strategy_ir.json" in names
        assert any(n.endswith("_quantstudio.py") for n in names)
        assert any(n.endswith("_ptrade.py") for n in names)
        assert "variant_consistency_report.json" in names
        # single-writer: the report file exists on disk
        assert (tmp_path / "variant_consistency_report.json").exists()
        assert (tmp_path / "run_card.json").exists()
        # sha256 populated for all artifacts
        assert all(len(a["sha256"]) == 64 for a in rc["artifacts"])

    def test_case1_rendered_code_compiles(self, case1_spec, tmp_path):
        rc = orchestrate(case1_spec, run_smoke=False, out_dir=tmp_path)
        for a in rc["artifacts"]:
            if a["name"].endswith(".py"):
                compile(Path(a["path"]).read_text(encoding="utf-8"), a["name"], "exec")

    def test_run_card_contract_versions(self, case1_spec, tmp_path):
        rc = orchestrate(case1_spec, run_smoke=False, out_dir=tmp_path)
        cv = rc["contract_versions"]
        # all 7 keys present with semver strings
        for key in ("strategy_spec_version", "engine_semantics_version",
                    "provider_contract_version", "security_code_rules_version",
                    "ptrade_profile_version", "renderer_version", "skill_version"):
            assert key in cv
            assert cv[key]  # non-empty


# ---------------------------------------------------------------------------
# capability-gate BLOCKED smoke path (mocked capability, real run_smoke_backtest)
# ---------------------------------------------------------------------------

class TestSmokeBlockedPath:
    def test_smoke_blocked_when_capability_not_ready(self, case1_spec, tmp_path, monkeypatch):
        """Force capability_report.overall_execution_status=PLANNED → smoke BLOCKED.

        This simulates a tick-profile strategy (invariant 4: tick never READY).
        We monkeypatch the orchestrator's capability inspector so the test is
        deterministic (no real tick data dependency). 遗留要求②: assert 被阻断.
        """
        import quantstudio.strategy_compiler.orchestrator as orch_mod

        def fake_inspect(strategy_id, profile_id):
            return {
                "report_version": "1.0",
                "generated_at": "2026-07-23T00:00:00+08:00",
                "strategy_id": strategy_id,
                "requested_profile": profile_id,
                "capabilities": [],
                "overall_execution_status": "PLANNED",
                "blockers": ["tick_backtest: PLANNED — PR9 scope"],
                "repair_actions": [],
            }
        monkeypatch.setattr(orch_mod, "_inspect_capabilities", fake_inspect)

        rc = orchestrate(case1_spec, run_smoke=True, out_dir=tmp_path)
        assert rc["stage"] == "SMOKE_EXECUTED"  # smoke step RAN (honestly blocked)
        assert rc["profile"]["execution_status"] == "PLANNED"
        assert rc["smoke_backtest"]["status"] == "BLOCKED"
        assert "能力门禁阻止" in rc["smoke_backtest"]["summary"]
        # overall status reflects the block
        assert rc["status"] == "BLOCKED"
        _validate_run_card_schema(rc)


# ---------------------------------------------------------------------------
# golden protection surfaced (not silent run_card)
# ---------------------------------------------------------------------------

class TestGoldenProtection:
    def test_protected_id_raises(self, case1_spec, tmp_path):
        spec = deepcopy(case1_spec)
        spec["strategy_id"] = "etf_momentum"
        with pytest.raises(GoldenProtectionError, match="etf_momentum"):
            orchestrate(spec, run_smoke=False, out_dir=tmp_path)


# ---------------------------------------------------------------------------
# static-BLOCK path: stage stays STATIC_VALIDATED, smoke NOT invoked
# ---------------------------------------------------------------------------

class TestStaticBlockPath:
    def test_static_block_stage_static_validated_smoke_skipped(self, case1_spec, tmp_path, monkeypatch):
        """A static validator BLOCK must leave stage=STATIC_VALIDATED (the static
        step ran), record the block in validation.*, set status=BLOCKED, and NOT
        invoke the smoke engine (a static-failing strategy is not engine-worthy).

        We force a static BLOCK by monkeypatching check_hard_filters to fail, and
        assert the smoke inspector was never called (via a spy that would raise).
        """
        import quantstudio.strategy_compiler.orchestrator as orch_mod

        smoke_called = {"yes": False}

        def spy_inspect(strategy_id, profile_id):
            smoke_called["yes"] = True  # if reached, smoke was attempted — bug
            raise AssertionError("smoke inspector must NOT run on static BLOCK")

        monkeypatch.setattr(orch_mod, "_inspect_capabilities", spy_inspect)

        # Force check_hard_filters to BLOCK by stripping execution-stage nodes
        def fake_check(ir, spec=None):
            from quantstudio.strategy_compiler.validators.scan_lookahead import Violation
            return False, [Violation("HARDFILTER-EXECUTION-STAGE", "BLOCK", "forced")], []

        monkeypatch.setattr(orch_mod, "check_hard_filters", fake_check)

        rc = orchestrate(case1_spec, run_smoke=True, out_dir=tmp_path)

        # stage reflects the step reached (static ran), NOT rolled back to SPEC_ONLY
        assert rc["stage"] == "STATIC_VALIDATED", (
            f"static BLOCK must stay STATIC_VALIDATED (step ran), got {rc['stage']}"
        )
        assert rc["validation"]["hard_filters"] == "BLOCKED"
        assert rc["status"] == "BLOCKED"
        # smoke NOT attempted
        assert rc["smoke_backtest"] is None
        assert not smoke_called["yes"], "smoke engine must NOT be invoked on static BLOCK"
        # the IR/render artifacts WERE written (proving we got past SPEC_ONLY)
        artifact_names = {a["name"] for a in rc["artifacts"]}
        assert "strategy_ir.json" in artifact_names
        _validate_run_card_schema(rc)

    def test_schema_fail_stage_spec_only(self, case1_spec, tmp_path):
        """Schema failure → stage SPEC_ONLY (IR never built), no artifacts."""
        spec = deepcopy(case1_spec)
        spec["time_model"]["market_data_frequency"] = "1m"  # violates bar==mdf rule
        rc = orchestrate(spec, run_smoke=False, out_dir=tmp_path)
        assert rc["stage"] == "SPEC_ONLY"
        assert rc["validation"]["schema"] == "BLOCKED"
        assert rc["artifacts"] == []  # nothing built
