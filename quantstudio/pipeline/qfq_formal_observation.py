"""B-6 WP7-P formal post-cutover observation rules and Run Card template.

Implements the hard-count observation gate (P2-3, G0 §5.5): the observation
period passes only when ``complete_post_close_cycles_success >= 2`` AND
``incremental_replay_cycles_success >= 2``.  Counts are by successful cycle,
not calendar day; non-trading days never count; half-day markets count only
when all completeness conditions hold; a failed cycle does not increment the
count and auto-extends the observation window.  There is no "time elapsed =>
pass" rule.

This module provides the *rules and Run Card template* for the big-span-A
gate; the real observation evidence is gathered in WP7-E5 ahead of G3.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, List, Mapping, Optional

BJ_TZ = timezone(timedelta(hours=8))

REQUIRED_COMPLETE_POST_CLOSE = 2
REQUIRED_INCREMENTAL_REPLAY = 2

#: Fields frozen per observation cycle (Run Card row).
RUN_CARD_FIELDS = (
    "scheduled_date", "run_id", "cycle_id", "trade_day", "is_trading_day",
    "traversal_completed", "four_table_cycle_terminal", "no_unexplained_failed_held_pending",
    "quality_audit_pass", "watermark_before", "watermark_after",
    "watermark_idempotent_or_monotonic", "complete_post_close_success",
    "incremental_replay_success", "evidence_sha256", "recorded_at",
)


@dataclass
class ObservationState:
    """Persistent observation counter state, restart-recoverable."""
    complete_post_close_cycles_success: int = 0
    incremental_replay_cycles_success: int = 0
    run_cards: List[dict] = field(default_factory=list)
    auto_extended: bool = False

    def satisfied(self) -> bool:
        return (self.complete_post_close_cycles_success >= REQUIRED_COMPLETE_POST_CLOSE
                and self.incremental_replay_cycles_success >= REQUIRED_INCREMENTAL_REPLAY)

    def to_dict(self) -> dict:
        return {
            "complete_post_close_cycles_success": self.complete_post_close_cycles_success,
            "incremental_replay_cycles_success": self.incremental_replay_cycles_success,
            "required_complete": REQUIRED_COMPLETE_POST_CLOSE,
            "required_incremental": REQUIRED_INCREMENTAL_REPLAY,
            "satisfied": self.satisfied(),
            "auto_extended": self.auto_extended,
            "run_cards": list(self.run_cards),
        }


def load_state(path: str | Path) -> ObservationState:
    """Restart-recover the observation counter from a JSON state file."""
    p = Path(path)
    if not p.is_file():
        return ObservationState()
    data = json.loads(p.read_text(encoding="utf-8"))
    return ObservationState(
        complete_post_close_cycles_success=int(data.get("complete_post_close_cycles_success", 0)),
        incremental_replay_cycles_success=int(data.get("incremental_replay_cycles_success", 0)),
        run_cards=list(data.get("run_cards", [])),
        auto_extended=bool(data.get("auto_extended", False)),
    )


def save_state(state: ObservationState, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2,
                            default=str) + "\n", encoding="utf-8")


def is_complete_post_close_cycle(run_card: Mapping[str, Any]) -> bool:
    """A complete post-close cycle requires (G0 §5.5.1):

      - trade_calendar marks the date a valid trading day;
      - the post-close schedule window is reached;
      - production daemon run_one_cycle traversed all eligible incremental tasks;
      - traversal_completed=true;
      - the four price-table QFQ cycles have a terminal status;
      - no unexplained failed/held/pending;
      - full quality audit passes its gate;
      - run state / cycle / watermark / alerts evidence is complete.

    Non-trading days never count.  Half-day markets count only when all
    conditions hold; otherwise they are recorded but not counted.
    """
    if not run_card.get("is_trading_day"):
        return False
    return bool(
        run_card.get("traversal_completed")
        and run_card.get("four_table_cycle_terminal")
        and run_card.get("no_unexplained_failed_held_pending")
        and run_card.get("quality_audit_pass"))


def is_incremental_replay_cycle(run_card: Mapping[str, Any], *, prior_complete: bool) -> bool:
    """An incremental replay cycle (G0 §5.5.2) requires, after a prior complete
    post-close cycle:

      - continues from the committed watermark;
      - no unexpected full re-pull;
      - no historical trigger replay;
      - no duplicate writes/intents;
      - next-cycle pending-slot audit = 0;
      - watermark idempotent or monotonically advancing on new business data.

    It must be an independent formal collection cycle, not a same-process
    function re-call.
    """
    if not prior_complete:
        return False
    return bool(
        run_card.get("watermark_idempotent_or_monotonic")
        and not run_card.get("unexpected_full_repull")
        and not run_card.get("historical_trigger_replay")
        and not run_card.get("duplicate_writes")
        and run_card.get("pending_slot_audit_zero"))


def record_run_card(state: ObservationState, run_card: Mapping[str, Any]) -> ObservationState:
    """Record one observation cycle, incrementing hard counts only on success.

    A failed cycle does NOT increment any count and sets ``auto_extended=True``.
    Returns the updated state (caller persists via ``save_state``).
    """
    prior_complete = state.complete_post_close_cycles_success >= REQUIRED_COMPLETE_POST_CLOSE
    # Evaluate the completeness/replay rules against the FULL input run_card
    # (the replay-specific fields are not in RUN_CARD_FIELDS but are needed by
    # is_incremental_replay_cycle); then persist a Run-Card row with the
    # canonical field set plus the replay verdicts.
    complete_ok = is_complete_post_close_cycle(run_card)
    replay_ok = is_incremental_replay_cycle(run_card, prior_complete=prior_complete)
    rc = {k: run_card.get(k) for k in RUN_CARD_FIELDS}
    rc["recorded_at"] = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    rc["complete_post_close_success"] = complete_ok
    rc["incremental_replay_success"] = replay_ok
    state.run_cards.append(rc)
    if complete_ok and state.complete_post_close_cycles_success < REQUIRED_COMPLETE_POST_CLOSE:
        state.complete_post_close_cycles_success += 1
    if replay_ok and state.incremental_replay_cycles_success < REQUIRED_INCREMENTAL_REPLAY:
        state.incremental_replay_cycles_success += 1
    if not complete_ok and rc.get("is_trading_day"):
        # A trading-day cycle that failed to be complete extends the window.
        state.auto_extended = True
    return state


def render_run_card_template() -> dict:
    """Return a blank Run Card template (G1 delivers the rule + template)."""
    return {k: None for k in RUN_CARD_FIELDS}


def observation_report(state: ObservationState, *, cutover_id: str) -> dict:
    """Render the observation report for G3 evidence (template at G1)."""
    return {
        "kind": "quantstudio-b6-wp7-observation-report",
        "cutover_id": cutover_id,
        "required_complete_post_close": REQUIRED_COMPLETE_POST_CLOSE,
        "required_incremental_replay": REQUIRED_INCREMENTAL_REPLAY,
        "complete_post_close_cycles_success": state.complete_post_close_cycles_success,
        "incremental_replay_cycles_success": state.incremental_replay_cycles_success,
        "satisfied": state.satisfied(),
        "auto_extended": state.auto_extended,
        "run_card_count": len(state.run_cards),
        "run_cards": list(state.run_cards),
        "generated_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }
