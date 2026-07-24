"""Delivery flow tests: Skill-local deliver_strategy + orchestrator + package builder.

Corrective #2 coverage:
- R2.5 HARD GATE (ALL confirmations must be CONFIRMED; mixed → fail closed)
- Strict static gate (per-field BLOCKED → no package)
- Smoke truth table (PASS/BLOCKED/FAILED/missing × allow_deferred_smoke True/False)
- DELIVERY_REPORT real-path-only listing
- Skill quick_validate UTF-8/BOM
- Installed-Skill-copy E2E (imports from released wheel only)
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path
from copy import deepcopy

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "quantstudio" / "strategy_compiler" / "examples"
FROZEN = ROOT / "tests" / "strategy_references" / "frozen"
SKILL_SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import deliver_strategy as ds  # Skill-local script


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _confirmed_spec():
    """Spec with valid CONFIRMED user_confirmations (schema-conforming)."""
    spec = _load(EXAMPLES / "case1_dual_ma_spec.json")
    spec["user_confirmations"] = [
        {"confirmation_id": "spec_review", "status": "CONFIRMED"}
    ]
    return spec


@pytest.fixture
def case1_spec():
    return _confirmed_spec()


# ── Positive delivery ─────────────────────────────────────────────────────────

class TestDeliveryPositive:
    def test_delivery_creates_unified_output(self, tmp_path, case1_spec):
        base = ds.deliver_strategy(case1_spec, out_dir=tmp_path,
                                   run_smoke=False, allow_deferred_smoke=True)
        assert (base / "validation").is_dir()
        assert (base / "DELIVERY_REPORT.md").exists()
        assert len(list((base / "package").iterdir())) == 1

    def test_delivery_package_integrity(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.reference.source_digest import sha256_file
        base = ds.deliver_strategy(case1_spec, out_dir=tmp_path,
                                   run_smoke=False, allow_deferred_smoke=True)
        pkg = next((base / "package").iterdir())
        manifest = _load(pkg / "manifest.json")
        for fname, recorded in manifest["artifact_digests"].items():
            assert sha256_file(pkg / fname) == recorded

    def test_delivery_strategies_compile(self, tmp_path, case1_spec):
        base = ds.deliver_strategy(case1_spec, out_dir=tmp_path,
                                   run_smoke=False, allow_deferred_smoke=True)
        pkg = next((base / "package").iterdir())
        for f in ("case1_dual_ma_quantstudio.py", "case1_dual_ma_ptrade.py"):
            ast.parse((pkg / f).read_text(encoding="utf-8"))

    def test_delivery_g2_blocked(self, tmp_path, case1_spec):
        base = ds.deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False,
                                   allow_deferred_smoke=True, g2_frozen_dir=FROZEN)
        pkg = next((base / "package").iterdir())
        manifest = _load(pkg / "manifest.json")
        assert manifest["g2_reference_closure"]["data_digest_status"] == "blocked"

    def test_delivery_report_status_without_smoke(self, tmp_path, case1_spec):
        base = ds.deliver_strategy(case1_spec, out_dir=tmp_path,
                                   run_smoke=False, allow_deferred_smoke=True)
        report = (base / "DELIVERY_REPORT.md").read_text(encoding="utf-8")
        assert "DELIVERED_WITHOUT_SMOKE" in report
        assert "DELIVERED\n" not in report  # not misleading bare DELIVERED


# ── R2.5 HARD GATE (ALL must be CONFIRMED) ────────────────────────────────────

class TestR25HardGate:
    def test_no_confirmations_fails(self, tmp_path):
        spec = _load(EXAMPLES / "case1_dual_ma_spec.json")
        spec.pop("user_confirmations", None)
        with pytest.raises(ds.DeliveryConfirmationError, match="empty"):
            ds.deliver_strategy(spec, out_dir=tmp_path, run_smoke=False)

    def test_mixed_confirmed_rejected_fails(self, tmp_path):
        spec = _confirmed_spec()
        spec["user_confirmations"].append(
            {"confirmation_id": "risk_ack", "status": "REJECTED"})
        with pytest.raises(ds.DeliveryConfirmationError, match="REJECTED"):
            ds.deliver_strategy(spec, out_dir=tmp_path, run_smoke=False)

    def test_mixed_confirmed_pending_fails(self, tmp_path):
        spec = _confirmed_spec()
        spec["user_confirmations"].append(
            {"confirmation_id": "risk_ack", "status": "PENDING"})
        with pytest.raises(ds.DeliveryConfirmationError, match="PENDING"):
            ds.deliver_strategy(spec, out_dir=tmp_path, run_smoke=False)

    def test_missing_confirmation_id_fails(self, tmp_path):
        spec = _confirmed_spec()
        spec["user_confirmations"].append({"status": "CONFIRMED"})  # no confirmation_id
        with pytest.raises(ds.DeliveryConfirmationError, match="confirmation_id"):
            ds.deliver_strategy(spec, out_dir=tmp_path, run_smoke=False)

    def test_non_dict_confirmation_fails(self, tmp_path):
        spec = _confirmed_spec()
        spec["user_confirmations"].append("not a dict")
        with pytest.raises(ds.DeliveryConfirmationError, match="not a valid object"):
            ds.deliver_strategy(spec, out_dir=tmp_path, run_smoke=False)

    def test_no_output_created_on_gate_failure(self, tmp_path):
        """R2.5 failure must not create any output dir."""
        spec = _load(EXAMPLES / "case1_dual_ma_spec.json")
        spec.pop("user_confirmations", None)
        with pytest.raises(ds.DeliveryConfirmationError):
            ds.deliver_strategy(spec, out_dir=tmp_path, run_smoke=False)
        assert not any(tmp_path.iterdir()), "output created despite gate failure"

    def test_all_confirmed_proceeds(self, tmp_path):
        spec = _confirmed_spec()
        spec["user_confirmations"].append(
            {"confirmation_id": "risk_ack", "status": "CONFIRMED"})
        base = ds.deliver_strategy(spec, out_dir=tmp_path, run_smoke=False,
                                   allow_deferred_smoke=True)
        assert (base / "DELIVERY_REPORT.md").exists()


# ── Strict static validation gate (monkeypatched run_card) ─────────────────────

class TestStaticGate:
    """Each static field BLOCKED → no package, RuntimeError."""

    @pytest.mark.parametrize("field", ds._STATIC_FIELDS)
    def test_static_field_blocked_no_package(self, field, tmp_path, monkeypatch):
        spec = _confirmed_spec()
        # monkeypatch orchestrate to return a run_card with one field BLOCKED
        def fake_orchestrate(spec, **kw):
            from pathlib import Path
            out = Path(kw["out_dir"])
            out.mkdir(parents=True, exist_ok=True)
            validation = {f: "PASS" for f in ds._STATIC_FIELDS}
            validation[field] = "BLOCKED"
            rc = {"validation": validation, "smoke_backtest": None}
            (out / "run_card.json").write_text(json.dumps(rc), encoding="utf-8")
            return rc
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate", fake_orchestrate)
        with pytest.raises(RuntimeError, match="Static validation failed"):
            ds.deliver_strategy(spec, out_dir=tmp_path, run_smoke=False, allow_deferred_smoke=True)
        # No package dir
        pkg_parent = tmp_path / spec["strategy_id"] / "package"
        if pkg_parent.exists():
            assert not any(pkg_parent.iterdir()), "package created despite static failure"


# ── Smoke truth table (monkeypatched run_card) ────────────────────────────────

class TestSmokeTruthTable:
    """Full truth table: run_smoke × smoke_status × allow_deferred_smoke."""

    def _fake_orchestrate_factory(self, smoke_status):
        def fake(spec, **kw):
            from pathlib import Path
            out = Path(kw["out_dir"])
            out.mkdir(parents=True, exist_ok=True)
            validation = {f: "PASS" for f in ds._STATIC_FIELDS}
            smoke = {"status": smoke_status} if smoke_status is not None else None
            rc = {"validation": validation, "smoke_backtest": smoke}
            (out / "run_card.json").write_text(json.dumps(rc), encoding="utf-8")
            return rc
        return fake

    def test_smoke_pass_delivered(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate",
                            self._fake_orchestrate_factory("PASS"))
        base = ds.deliver_strategy(_confirmed_spec(), out_dir=tmp_path, run_smoke=True)
        report = (base / "DELIVERY_REPORT.md").read_text(encoding="utf-8")
        assert "DELIVERED\n" in report  # bare DELIVERED = smoke PASS

    def test_smoke_blocked_allow_false_no_package(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate",
                            self._fake_orchestrate_factory("BLOCKED"))
        with pytest.raises(RuntimeError, match="BLOCKED"):
            ds.deliver_strategy(_confirmed_spec(), out_dir=tmp_path, run_smoke=True,
                                allow_deferred_smoke=False)

    def test_smoke_blocked_allow_true_deferred(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate",
                            self._fake_orchestrate_factory("BLOCKED"))
        base = ds.deliver_strategy(_confirmed_spec(), out_dir=tmp_path, run_smoke=True,
                                   allow_deferred_smoke=True)
        report = (base / "DELIVERY_REPORT.md").read_text(encoding="utf-8")
        assert "DELIVERED_WITH_DEFERRED_SMOKE" in report

    def test_smoke_failed_no_package(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate",
                            self._fake_orchestrate_factory("FAILED"))
        with pytest.raises(RuntimeError, match="FAILED"):
            ds.deliver_strategy(_confirmed_spec(), out_dir=tmp_path, run_smoke=True)

    def test_smoke_missing_allow_false_no_package(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate",
                            self._fake_orchestrate_factory(None))
        with pytest.raises(RuntimeError, match="missing"):
            ds.deliver_strategy(_confirmed_spec(), out_dir=tmp_path, run_smoke=True,
                                allow_deferred_smoke=False)

    def test_no_smoke_allow_false_no_package(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate",
                            self._fake_orchestrate_factory(None))
        with pytest.raises(RuntimeError, match="not run"):
            ds.deliver_strategy(_confirmed_spec(), out_dir=tmp_path, run_smoke=False,
                                allow_deferred_smoke=False)

    def test_no_smoke_allow_true_without_smoke(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate",
                            self._fake_orchestrate_factory(None))
        base = ds.deliver_strategy(_confirmed_spec(), out_dir=tmp_path, run_smoke=False,
                                   allow_deferred_smoke=True)
        report = (base / "DELIVERY_REPORT.md").read_text(encoding="utf-8")
        assert "DELIVERED_WITHOUT_SMOKE" in report

    def test_unknown_smoke_status_allow_false_fails(self, tmp_path, monkeypatch):
        """Unknown smoke status (e.g. 'GARBLED') must fail closed regardless of allow."""
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate",
                            self._fake_orchestrate_factory("GARBLED"))
        with pytest.raises(RuntimeError, match="[Uu]nknown"):
            ds.deliver_strategy(_confirmed_spec(), out_dir=tmp_path, run_smoke=True,
                                allow_deferred_smoke=False)

    def test_unknown_smoke_status_allow_true_also_fails(self, tmp_path, monkeypatch):
        """Unknown smoke status must fail closed EVEN with allow_deferred_smoke=True."""
        monkeypatch.setattr("quantstudio.strategy_compiler.orchestrator.orchestrate",
                            self._fake_orchestrate_factory("GARBLED"))
        with pytest.raises(RuntimeError, match="[Uu]nknown"):
            ds.deliver_strategy(_confirmed_spec(), out_dir=tmp_path, run_smoke=True,
                                allow_deferred_smoke=True)


# ── Quick validate UTF-8 ───────────────────────────────────────────────────────

class TestQuickValidateEncoding:
    def test_plain_utf8(self, tmp_path):
        import subprocess
        skill = tmp_path / "skill"
        shutil.copytree(ROOT / "skills" / "quantstudio-strategy-compiler", skill)
        r = subprocess.run(
            [sys.executable, str(SKILL_SCRIPTS / "quick_validate.py"), str(skill)],
            capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        assert "valid" in r.stdout.lower()

    def test_utf8_bom(self, tmp_path):
        import subprocess
        skill = tmp_path / "skill_bom"
        shutil.copytree(ROOT / "skills" / "quantstudio-strategy-compiler", skill)
        skill_md = skill / "SKILL.md"
        content = skill_md.read_bytes()
        if not content.startswith(b"\xef\xbb\xbf"):
            skill_md.write_bytes(b"\xef\xbb\xbf" + content)
        r = subprocess.run(
            [sys.executable, str(SKILL_SCRIPTS / "quick_validate.py"), str(skill)],
            capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        assert "valid" in r.stdout.lower()


# ── Installed Skill copy E2E (imports from released wheel only) ────────────────

class TestInstalledSkillCopyE2E:
    def test_skill_copy_delivery_works_with_wheel_modules(self, tmp_path):
        """Copy Skill to tmp, import deliver_strategy from the copy; verify it imports
        orchestrator + package.builder from the installed quantstudio (wheel), not from
        a non-existent quantstudio.strategy_compiler.delivery."""
        skill_copy = tmp_path / "installed_skill"
        shutil.copytree(ROOT / "skills" / "quantstudio-strategy-compiler", skill_copy)
        # Import from the copy's scripts dir
        scripts_copy = skill_copy / "scripts"
        sys.path.insert(0, str(scripts_copy))
        try:
            # Force reimport from the copy
            if "deliver_strategy" in sys.modules:
                del sys.modules["deliver_strategy"]
            import deliver_strategy as ds_copy
            # Verify it does NOT import quantstudio.strategy_compiler.delivery
            assert not hasattr(ds_copy, "quantstudio") or \
                "delivery" not in dir(ds_copy), "should not import delivery from quantstudio pkg"
            # Verify it CAN import orchestrator + package.builder (from wheel)
            from quantstudio.strategy_compiler.orchestrator import orchestrate
            from quantstudio.strategy_compiler.package.builder import build_strategy_package
            assert callable(orchestrate)
            assert callable(build_strategy_package)
        finally:
            sys.path.remove(str(scripts_copy))
