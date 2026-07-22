"""run_smoke_backtest validator (PR6b-1).

Derived from `docs/strategy-compiler/master-implementation-plan-v1.0.md` §R6
(line 579-583) + capability-model.md §2 + run_card.schema.json (smokeResult).

R6 rule (master plan line 583): the smoke backtest runs ONLY when the requested
Profile's `overall_execution_status == READY`. Otherwise the validator must
output "代码已生成 / 静态检查已通过 / 执行验证被能力门禁阻止" and MUST NOT
invoke the backtest engine — it must not claim a backtest passed when the
capability gate blocks execution.

smokeResult schema (run_card.schema.json line 50):
    {status: PASS|BLOCKED|FAILED, command: str, summary: str}

Design: this is a PURE function returning a tuple (status, smoke_result,
warnings). It does NOT write run_card.json — the orchestrator is the single
writer of run_card (contract: validators stay pure functions, orchestrator
collects tuples and writes the card in one place).

Attention point ② (handoff §1 note 2): the subprocess invocation uses
encoding='utf-8', errors='replace' and injects PYTHONIOENCODING=utf-8 into the
child env, so Windows console codepage (GBK) cannot corrupt the engine's stdout
before we parse the result.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Status words (run_card.schema.json smokeResult.status enum).
_SMOKE_PASS = "PASS"
_SMOKE_BLOCKED = "BLOCKED"
_SMOKE_FAILED = "FAILED"

# R6 master plan line 583 honest message (capability gate blocks execution).
_R6_BLOCKED_SUMMARY = (
    "代码已生成 / 静态检查已通过 / 执行验证被能力门禁阻止 — "
    "requested Profile overall_execution_status != READY (R6: 不得在能力门禁阻断时宣称回测通过)"
)

# Default smoke backtest window (short — this is a smoke, not a full run).
# Overridable via start/end args; orchestrator passes Spec-derived window.
_DEFAULT_START = "2026-01-01"
_DEFAULT_END = "2026-04-29"
_DEFAULT_TIMEOUT = 600  # seconds; engine on real DB may take minutes


def run_smoke_backtest(
    qs_strategy_path: str | Path,
    capability_report: dict[str, Any],
    start: str | None = None,
    end: str | None = None,
    profile_id: str = "daily-bar-v1",
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> tuple[str, dict[str, Any], list[str]]:
    """Run (or gate) the smoke backtest per R6.

    Args:
        qs_strategy_path: path to the rendered QuantStudio .py strategy file.
        capability_report: parsed capability_report.json dict. Only
            ``overall_execution_status`` is read (the gate signal).
        start, end: backtest window (YYYY-MM-DD). Default to the smoke window.
        profile_id: engine profile passed as ``--profile`` to the engine CLI.
        timeout: subprocess timeout in seconds.

    Returns:
        (status, smoke_result, warnings) where:
          - status: "PASS" | "BLOCKED" | "FAILED"
          - smoke_result: dict conforming to run_card.schema.json smokeResult
            ({status, command, summary}); None-ish → orchestrator may store null
            for BLOCKED if it prefers, but we return the dict for honesty.
          - warnings: list of soft notices (e.g. timeout, non-zero exit reasons)

    The function never raises on capability-gate BLOCKED or engine non-zero
    exit — those are legitimate smoke outcomes encoded in the result. It raises
    only on programmer error (missing strategy file) or subprocess.TimeoutExpired
    which is caught and mapped to FAILED.
    """
    warnings: list[str] = []

    overall = capability_report.get("overall_execution_status")
    qs_path = Path(qs_strategy_path)

    # --- Gate: R6 line 583 — only execute when capability == READY ---
    if overall != "READY":
        blockers = capability_report.get("blockers", [])
        summary = _R6_BLOCKED_SUMMARY
        if blockers:
            summary += f" | blockers: {blockers}"
        smoke_result = {
            "status": _SMOKE_BLOCKED,
            "command": "",
            "summary": summary,
        }
        warnings.append(
            f"run_smoke_backtest BLOCKED: overall_execution_status={overall!r} "
            f"(expected 'READY'). Engine not invoked per R6."
        )
        return _SMOKE_BLOCKED, smoke_result, warnings

    # --- READY path: invoke the backtest engine via subprocess ---
    if not qs_path.exists():
        smoke_result = {
            "status": _SMOKE_FAILED,
            "command": "",
            "summary": f"rendered strategy file not found: {qs_path}",
        }
        warnings.append(f"run_smoke_backtest FAILED: strategy file missing: {qs_path}")
        return _SMOKE_FAILED, smoke_result, warnings

    start = start or _DEFAULT_START
    end = end or _DEFAULT_END
    command_list = [
        sys.executable, "-m", "quantstudio.backtest.run_ptrade_strategy",
        str(qs_path), start, end, "--profile", profile_id,
    ]
    # Attention point ②: force UTF-8 so Windows GBK console cannot corrupt stdout.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.run(
            command_list,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        smoke_result = {
            "status": _SMOKE_FAILED,
            "command": " ".join(command_list),
            "summary": f"smoke backtest timed out after {timeout}s: {e}",
        }
        warnings.append(f"run_smoke_backtest FAILED: subprocess timed out after {timeout}s")
        return _SMOKE_FAILED, smoke_result, warnings

    # The engine prints "结果导出: <dir>" on success (run_ptrade_strategy.py:189)
    # and exits 0. Non-zero exit (e.g. data not ready → exit 3) is FAILED.
    stdout_tail = (proc.stdout or "")[-800:]
    ok = (proc.returncode == 0)
    summary = (
        f"smoke backtest {'succeeded' if ok else 'failed'} "
        f"(exit={proc.returncode}); last stdout: {stdout_tail!r}"
    )
    if proc.stderr:
        stderr_tail = (proc.stderr or "")[-400:]
        summary += f"; last stderr: {stderr_tail!r}"
    if not ok:
        warnings.append(
            f"run_smoke_backtest FAILED: engine exited {proc.returncode}"
        )

    status = _SMOKE_PASS if ok else _SMOKE_FAILED
    smoke_result = {
        "status": status,
        "command": " ".join(command_list),
        "summary": summary,
    }
    return status, smoke_result, warnings


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m quantstudio.strategy_compiler.validators.run_smoke_backtest
            <qs_strategy.py> <capability_report.json> [start] [end] [--profile <id>]
    """
    import json
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) < 2:
        print(
            "Usage: run_smoke_backtest <qs_strategy.py> <capability_report.json> "
            "[start] [end] [--profile <id>]",
            file=sys.stderr,
        )
        return 2
    qs_path = argv[0]
    cap_path = argv[1]
    positional = []
    profile_id = "daily-bar-v1"
    i = 2
    while i < len(argv):
        a = argv[i]
        if a == "--profile" and i + 1 < len(argv):
            profile_id = argv[i + 1]
            i += 2
        elif a.startswith("--profile="):
            profile_id = a.split("=", 1)[1]
            i += 1
        else:
            positional.append(a)
            i += 1
    start = positional[0] if len(positional) > 0 else None
    end = positional[1] if len(positional) > 1 else None

    capability_report = json.loads(Path(cap_path).read_text(encoding="utf-8"))
    status, smoke_result, warnings = run_smoke_backtest(
        qs_path, capability_report, start, end, profile_id
    )
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    print(f"SMOKE {status}: {smoke_result['summary']}")
    return 0 if status == _SMOKE_PASS else (1 if status == _SMOKE_FAILED else 2)


if __name__ == "__main__":
    raise SystemExit(main())
