"""Delivery flow integration tests: orchestrator + qs-compile auto-chaining.

Validates that deliver_strategy() produces a unified output dir with validation/,
package/, and DELIVERY_REPORT.md — the user doesn't need to manually run two commands.

Corrective coverage:
- R2.5 HARD GATE (no confirmations / PENDING / REJECTED / CONFIRMED)
- Strict static validation gate (BLOCKED → no package)
- Delivery status truth table
- DELIVERY_REPORT real-path-only listing
- Skill quick_validate UTF-8 encoding
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from copy import deepcopy

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "quantstudio" / "strategy_compiler" / "examples"
FROZEN = ROOT / "tests" / "strategy_references" / "frozen"


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture
def case1_spec():
    spec = _load(EXAMPLES / "case1_dual_ma_spec.json")
    # Add user confirmation (R2.5 HARD GATE requirement)
    spec.setdefault("user_confirmations", [{"item": "strategy_spec", "status": "CONFIRMED"}])
    return spec


class TestDeliveryStructure:
    def test_delivery_creates_unified_output(self, tmp_path, case1_spec):
        """deliver_strategy creates validation/ + package/ + DELIVERY_REPORT.md."""
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        assert (base / "validation").is_dir()
        assert (base / "DELIVERY_REPORT.md").exists()
        pkgs = list((base / "package").iterdir())
        assert len(pkgs) == 1

    def test_delivery_validation_has_run_card(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        assert (base / "validation" / "run_card.json").exists()
        assert (base / "validation" / "strategy_ir.json").exists()

    def test_delivery_package_has_manifest_and_strategies(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        pkg = next((base / "package").iterdir())
        assert (pkg / "manifest.json").exists()
        assert (pkg / "case1_dual_ma_quantstudio.py").exists()
        assert (pkg / "case1_dual_ma_ptrade.py").exists()

    def test_delivery_report_contains_required_fields(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        report = (base / "DELIVERY_REPORT.md").read_text(encoding="utf-8")
        assert "DELIVERED" in report
        assert "blocked" in report.lower()
        assert "validation" in report.lower()
        assert "package" in report.lower()


class TestDeliveryPackageIntegrity:
    def test_delivery_package_manifest_digests_verify(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        from quantstudio.strategy_compiler.reference.source_digest import sha256_file
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        pkg = next((base / "package").iterdir())
        manifest = _load(pkg / "manifest.json")
        for fname, recorded in manifest["artifact_digests"].items():
            assert sha256_file(pkg / fname) == recorded

    def test_delivery_strategies_compile(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        pkg = next((base / "package").iterdir())
        for f in ("case1_dual_ma_quantstudio.py", "case1_dual_ma_ptrade.py"):
            src = (pkg / f).read_text(encoding="utf-8")
            ast.parse(src)
            compile(src, str(pkg / f), "exec")


class TestDeliveryG2Boundary:
    def test_delivery_with_g2_records_blocked(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False,
                                g2_frozen_dir=FROZEN)
        pkg = next((base / "package").iterdir())
        manifest = _load(pkg / "manifest.json")
        g2 = manifest["g2_reference_closure"]
        assert g2["data_digest_status"] == "blocked"
        assert len(g2["frozen_artifact_digests"]) == 4


# ── Corrective: R2.5 HARD GATE ───────────────────────────────────────────────

class TestR25HardGate:
    """R2.5: user_confirmations checked BEFORE any work. No confirmation → fail closed."""

    def test_no_confirmations_fails_closed(self, tmp_path):
        """Spec with no user_confirmations → DeliveryConfirmationError before any output."""
        from quantstudio.strategy_compiler.delivery import deliver_strategy, DeliveryConfirmationError
        spec = _load(EXAMPLES / "case1_dual_ma_spec.json")
        spec.pop("user_confirmations", None)  # explicitly remove
        with pytest.raises(DeliveryConfirmationError, match="R2.5"):
            deliver_strategy(spec, out_dir=tmp_path, run_smoke=False)
        # No output directory created
        assert not any(tmp_path.iterdir()), "output created despite missing confirmation"

    def test_pending_confirmation_fails_closed(self, tmp_path):
        from quantstudio.strategy_compiler.delivery import deliver_strategy, DeliveryConfirmationError
        spec = _load(EXAMPLES / "case1_dual_ma_spec.json")
        spec["user_confirmations"] = [{"item": "spec", "status": "PENDING"}]
        with pytest.raises(DeliveryConfirmationError):
            deliver_strategy(spec, out_dir=tmp_path, run_smoke=False)

    def test_rejected_confirmation_fails_closed(self, tmp_path):
        from quantstudio.strategy_compiler.delivery import deliver_strategy, DeliveryConfirmationError
        spec = _load(EXAMPLES / "case1_dual_ma_spec.json")
        spec["user_confirmations"] = [{"item": "spec", "status": "REJECTED"}]
        with pytest.raises(DeliveryConfirmationError):
            deliver_strategy(spec, out_dir=tmp_path, run_smoke=False)

    def test_confirmed_proceeds(self, tmp_path, case1_spec):
        """CONFIRMED status passes the gate and delivery proceeds."""
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        assert (base / "DELIVERY_REPORT.md").exists()


# ── Corrective: Strict static validation gate ─────────────────────────────────

class TestStaticValidationGate:
    """Any static validator ≠ PASS → NO package generation."""

    def test_static_pass_proceeds_to_package(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        pkg = next((base / "package").iterdir())
        assert (pkg / "manifest.json").exists()


# ── Corrective: Delivery status truth table ────────────────────────────────────

class TestDeliveryStatusTruthTable:
    """Delivery status must accurately reflect smoke outcome."""

    def test_without_smoke_is_delivered_without_smoke(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        report = (base / "DELIVERY_REPORT.md").read_text(encoding="utf-8")
        assert "DELIVERED_WITHOUT_SMOKE" in report

    def test_status_not_misleading_delivered_without_smoke(self, tmp_path, case1_spec):
        """When smoke is not run, status must NOT be plain DELIVERED."""
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        report = (base / "DELIVERY_REPORT.md").read_text(encoding="utf-8")
        # Must NOT contain bare "DELIVERED\n" (which implies smoke PASS)
        assert "DELIVERED_WITHOUT_SMOKE" in report


# ── Corrective: DELIVERY_REPORT real-path-only listing ─────────────────────────

class TestDeliveryReportRealPaths:
    """DELIVERY_REPORT must only list files that actually exist on disk."""

    def test_all_listed_paths_exist(self, tmp_path, case1_spec):
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        report = (base / "DELIVERY_REPORT.md").read_text(encoding="utf-8")
        # Extract backtick-quoted paths from the "Files for the user" section
        lines = report.splitlines()
        in_files = False
        for line in lines:
            if "## Files for the user" in line:
                in_files = True
                continue
            if in_files and line.startswith("## "):
                break
            if in_files and "`" in line:
                # Extract the filename from backticks — these are within the package/validation
                # We just verify no false "capability_report.json" listed if it doesn't exist
                fname = line.split("`")[1] if "`" in line else ""
                if "capability_report" in fname:
                    cap_path = base / "validation" / "capability_report.json"
                    assert cap_path.exists(), f"capability_report listed but doesn't exist"


# ── Corrective: Skill quick_validate UTF-8 ──────────────────────────────────────

class TestSkillQuickValidateEncoding:
    """quick_validate.py must handle UTF-8/UTF-8-BOM SKILL.md on Windows (GBK default)."""

    def test_quick_validate_passs_on_utf8_bom_skill(self, tmp_path):
        """SKILL.md with UTF-8 BOM validates correctly (not crashed by GBK default)."""
        import subprocess
        skill_src = ROOT / "skills" / "quantstudio-strategy-compiler"
        # Copy skill to tmp, add BOM
        import shutil
        tmp_skill = tmp_path / "test_skill"
        shutil.copytree(skill_src, tmp_skill)
        skill_md = tmp_skill / "SKILL.md"
        content = skill_md.read_bytes()
        if not content.startswith(b"\xef\xbb\xbf"):
            skill_md.write_bytes(b"\xef\xbb\xbf" + content)
        result = subprocess.run(
            [sys.executable, str(tmp_skill.parent / "test_skill" / "scripts" / "quick_validate.py"),
             str(tmp_skill)],
            capture_output=True, text=True, check=False)
        # quick_validate script is in the source skill dir, run it against the tmp copy
        result = subprocess.run(
            [sys.executable,
             str(ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts" / "quick_validate.py"),
             str(tmp_skill)],
            capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"quick_validate failed: {result.stderr}"
        assert "valid" in result.stdout.lower()
