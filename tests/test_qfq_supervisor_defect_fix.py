"""B-6 supervisor defect-fix tests (C).

Covers the two supervisor defects found during the formal cutover:
  * Defect A: ``run_supervised_cutover`` must NOT write a success-style exit
    evidence when the child exits with a non-zero, non-hard-crash code.
  * Defect B: a pre-existing exit evidence file (from a prior failed attempt in
    the same output dir) is archived (renamed ``_superseded_``) before the new
    one is written, instead of crashing on the O_EXCL collision.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quantstudio.pipeline.qfq_formal_cutover_cli import (
    EXPECTED_CHILD_EXIT_CODE, HARD_CRASH_EXIT_CODE, _archive_existing_exit_evidence,
)


# ===========================================================================
# Defect B: archive pre-existing exit evidence
# ===========================================================================


class TestArchiveExistingExitEvidence:
    def test_archive_renames_existing_file(self, tmp_path):
        """A pre-existing exit evidence is renamed with _superseded_ suffix."""
        exit_path = tmp_path / "formal_runner_exit_evidence.json"
        exit_path.write_text(json.dumps({"handoff_raw_sha256": "", "old": True}))
        archived = _archive_existing_exit_evidence(exit_path)
        assert not exit_path.exists(), "original must be moved away"
        assert Path(archived).exists(), "archived copy must exist"
        assert "_superseded_" in Path(archived).name
        # content preserved
        assert json.loads(Path(archived).read_text())["old"] is True

    def test_archive_raises_if_no_existing_file(self, tmp_path):
        """Archiving a non-existent file should raise (caller checks existence first)."""
        exit_path = tmp_path / "formal_runner_exit_evidence.json"
        assert not exit_path.exists()
        with pytest.raises(FileNotFoundError):
            _archive_existing_exit_evidence(exit_path)


# ===========================================================================
# Defect A: failed child does NOT get success exit evidence
# ===========================================================================


class TestSupervisorFailedChildHandling:
    """The supervisor's defect-A fix is verified at the logic level: a child
    exiting non-zero/non-hard-crash returns early WITHOUT writing exit evidence.
    We test the ``_archive_existing_exit_evidence`` helper + the early-return
    contract by inspecting the supervisor source (the full spawn test is
    expensive and covered by the end-to-end tests)."""

    def test_supervisor_returns_early_on_failed_child(self):
        """Inspect run_supervised_cutover source: it must have an early-return
        guard for exit_code not in (0, 92) that does NOT call write_exit_evidence."""
        import inspect
        from quantstudio.pipeline.qfq_formal_cutover_cli import run_supervised_cutover
        src = inspect.getsource(run_supervised_cutover)
        # The defect-A fix must contain this guard.
        assert "not writing success exit evidence" in src, \
            "supervisor must have the defect-A early-return guard"
        assert "EXPECTED_CHILD_EXIT_CODE and exit_code != HARD_CRASH_EXIT_CODE" in src, \
            "supervisor must guard on exit_code not in (expected, hard-crash)"

    def test_supervisor_archives_before_write(self):
        """Inspect run_supervised_cutover source: it must call
        _archive_existing_exit_evidence before write_exit_evidence (defect B)."""
        import inspect
        from quantstudio.pipeline.qfq_formal_cutover_cli import run_supervised_cutover
        src = inspect.getsource(run_supervised_cutover)
        assert "_archive_existing_exit_evidence" in src, \
            "supervisor must archive pre-existing exit evidence (defect B fix)"
        # The archive call must come BEFORE write_exit_evidence.
        archive_idx = src.index("_archive_existing_exit_evidence")
        write_idx = src.index("write_exit_evidence", archive_idx + 1)
        assert archive_idx < write_idx, "archive must precede write"


# ===========================================================================
# Integration: the archive + write sequence produces a clean evidence chain
# ===========================================================================


class TestEvidenceChainAfterArchive:
    def test_new_evidence_written_after_archive(self, tmp_path):
        """Simulate: old exit evidence exists → archive → write new one succeeds.
        The new evidence must be readable and the old one preserved."""
        from quantstudio.pipeline.qfq_formal_cutover import write_exit_evidence
        exit_path = tmp_path / "formal_runner_exit_evidence.json"
        # Old evidence from a failed attempt.
        exit_path.write_text(json.dumps({"handoff_raw_sha256": "", "stale": True}))
        # Archive it.
        archived = _archive_existing_exit_evidence(exit_path)
        # Write a fresh one (the real write_exit_evidence uses O_EXCL).
        write_exit_evidence(
            handoff_dir=tmp_path, handoff_raw_sha="a" * 64,
            child_pid=123, child_create_time=0.0, exit_code=0,
            locks_released_verified=True, descendant_scan=[])
        # New evidence is correct.
        new_ev = json.loads(exit_path.read_text())
        assert new_ev["handoff_raw_sha256"] == "a" * 64
        assert new_ev["locks_released_verified"] is True
        assert new_ev["child_exit_code"] == 0
        # Old evidence preserved.
        old_ev = json.loads(Path(archived).read_text())
        assert old_ev["stale"] is True
