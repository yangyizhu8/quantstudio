"""Delivery flow integration tests: orchestrator + qs-compile auto-chaining.

Validates that deliver_strategy() produces a unified output dir with validation/,
package/, and DELIVERY_REPORT.md — the user doesn't need to manually run two commands.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "quantstudio" / "strategy_compiler" / "examples"
FROZEN = ROOT / "tests" / "strategy_references" / "frozen"


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture
def case1_spec():
    return _load(EXAMPLES / "case1_dual_ma_spec.json")


class TestDeliveryStructure:
    def test_delivery_creates_unified_output(self, tmp_path, case1_spec):
        """deliver_strategy creates validation/ + package/ + DELIVERY_REPORT.md."""
        from quantstudio.strategy_compiler.delivery import deliver_strategy
        base = deliver_strategy(case1_spec, out_dir=tmp_path, run_smoke=False)
        assert (base / "validation").is_dir()
        assert (base / "DELIVERY_REPORT.md").exists()
        # package/ contains a package dir
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
        assert "data_digest_status" in report.lower() or "blocked" in report.lower()
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
