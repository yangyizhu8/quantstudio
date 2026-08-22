#!/usr/bin/env python3
"""Shared state and hashing helpers for user-managed PyQt backtest flow."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from agent_skill_common import load_json, write_json

CANDIDATE_SUFFIX = "__candidate_quantstudio.py"
USER_MODE = "user_pyqt"
AGENT_MODE = "agent_managed"
R4_PASS_STAGES = {"R4_PASS", "STATIC_VALIDATION_PASS", "DUAL_VALIDATION_PASS"}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def atomic_write(path: str | Path, payload: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
    temp.write_bytes(payload)
    os.replace(temp, target)


def workflow_state_path(strategy_path: str | Path) -> Path:
    return Path(strategy_path).parent / "workspace_state.json"


def load_workflow_state(strategy_path: str | Path) -> tuple[Path, dict]:
    path = workflow_state_path(strategy_path)
    if not path.exists():
        raise ValueError("workspace_state.json is required for candidate/backtest workflow")
    return path, load_json(path)


def validation_mode(design: dict) -> str:
    if design.get("design_version") == "2.0":
        return AGENT_MODE
    return design.get("validation_execution", {}).get("mode", "")


def candidate_path(project_root: str | Path, strategy_id: str,
                   strategy_name: str | None = None) -> Path:
    """Candidate file path under quantstudio/backtest/strategies/.

    Chinese naming contract (2026-08-22): the base name is the Chinese
    ``strategy_name`` when available (``<strategy_name>__candidate_quantstudio.py``);
    the ASCII ``strategy_id`` remains as the legacy fallback for callers that
    have not been migrated yet.
    """
    base = strategy_name.strip() if strategy_name else strategy_id
    return (Path(project_root) / "quantstudio" / "backtest" / "strategies"
            / f"{base}{CANDIDATE_SUFFIX}")


def candidate_payload(canonical_payload: bytes, strategy_id: str,
                      canonical_sha256: str) -> bytes:
    source = canonical_payload.decode("utf-8-sig")
    header = (
        "# QUANTSTUDIO USER-PYQT BACKTEST CANDIDATE\n"
        f"# strategy_id={strategy_id}\n"
        f"# canonical_sha256={canonical_sha256}\n"
        "# STATUS=UNVALIDATED_BY_BACKTEST\n"
        "# NOT_FOR_PTRADE_UPLOAD=true\n"
        "# Formal publication requires hash-bound R5 evidence PASS.\n\n"
    )
    return (header + source).encode("utf-8")


def ensure_candidate_path_is_safe(path: str | Path, project_root: str | Path,
                                  strategy_id: str,
                                  strategy_name: str | None = None) -> Path:
    selected = Path(path).resolve()
    expected = candidate_path(
        project_root, strategy_id, strategy_name).resolve()
    if selected != expected:
        raise ValueError(f"candidate path mismatch: expected {expected}, got {selected}")
    return selected


def write_state(path: str | Path, state: dict) -> None:
    write_json(path, state)


RETIRED_SUFFIX = ".RETIRED_DO_NOT_UPLOAD"


def retire_artifact(path: str | Path, *, must_be_under: str | Path | None = None) -> Path | None:
    """Retire a stale upload/candidate artifact while keeping it auditable.

    The file is renamed with a RETIRED_DO_NOT_UPLOAD suffix plus a SHA-256
    prefix of its content so retired evidence is never overwritten. When
    ``must_be_under`` is given, paths outside that directory are refused so a
    polluted workspace state cannot move arbitrary files.
    """
    source = Path(path).resolve()
    if must_be_under is not None:
        root = Path(must_be_under).resolve()
        if root != source.parent and root not in source.parents:
            raise ValueError(f"refuse to retire artifact outside {root}: {source}")
    if not source.exists():
        return None
    digest = sha256_path(source)[:12]
    retired = source.with_name(f"{source.name}{RETIRED_SUFFIX}.{digest}")
    counter = 1
    while retired.exists():
        retired = source.with_name(f"{source.name}{RETIRED_SUFFIX}.{digest}.{counter}")
        counter += 1
    source.rename(retired)
    return retired


def mark_ptrade_runtime_failure(state: dict, reason: str,
                                retired_artifacts: list[str] | None = None) -> dict:
    """Real PTrade broker runtime failure invalidates every prior PASS.

    Old R4 profile PASS, candidate, local backtest evidence and formal publish
    permission all become STALE; regeneration under a repaired profile must
    produce fresh hashes. Old artifacts are retired, never silently reused.
    """
    state.update({
        "stage": "PTRADE_RUNTIME_FAIL_RETURN_R1_R4",
        "ptrade_profile_validation_status": "STALE",
        "ptrade_runtime_status": "FAIL",
        "local_backtest_status": "STALE",
        "backtest_status": "STALE",
        "backtest_evidence_status": "STALE",
        "formal_publish_allowed": False,
        "candidate_status": "STALE",
        "candidate_sha256": None,
        "canonical_sha256_status": "STALE",
        "old_ptrade_artifact_status": "RETIRED",
        "ptrade_runtime_failure_reason": reason,
        "retired_artifacts": retired_artifacts or [],
    })
    return state
