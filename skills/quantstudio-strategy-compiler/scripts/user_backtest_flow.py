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


def candidate_path(project_root: str | Path, strategy_id: str) -> Path:
    return (Path(project_root) / "quantstudio" / "backtest" / "strategies"
            / f"{strategy_id}{CANDIDATE_SUFFIX}")


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
                                  strategy_id: str) -> Path:
    selected = Path(path).resolve()
    expected = candidate_path(project_root, strategy_id).resolve()
    if selected != expected:
        raise ValueError(f"candidate path mismatch: expected {expected}, got {selected}")
    return selected


def write_state(path: str | Path, state: dict) -> None:
    write_json(path, state)
