"""B-6 WP7-P formal observation hard-count rule tests (P2-3).

Verifies the observation gate (2 complete post-close + 2 incremental replay),
non-trading-day exclusion, half-day-market conditional counting, and failure
auto-extension.  These test the *rules*; real observation evidence is gathered
in WP7-E5 ahead of G3.
"""
from __future__ import annotations

from quantstudio.pipeline.qfq_formal_observation import (
    ObservationState, REQUIRED_COMPLETE_POST_CLOSE, REQUIRED_INCREMENTAL_REPLAY,
    is_complete_post_close_cycle, is_incremental_replay_cycle,
    load_state, observation_report, record_run_card, render_run_card_template,
    save_state,
)


def _complete_cycle(date="2026-08-06"):
    return {
        "scheduled_date": date, "run_id": "r1", "cycle_id": "cyc1",
        "trade_day": date, "is_trading_day": True, "traversal_completed": True,
        "four_table_cycle_terminal": True, "no_unexplained_failed_held_pending": True,
        "quality_audit_pass": True, "watermark_before": 100, "watermark_after": 110,
        "watermark_idempotent_or_monotonic": True,
    }


def _replay_cycle(date="2026-08-07"):
    base = _complete_cycle(date)
    base.update({
        "unexpected_full_repull": False, "historical_trigger_replay": False,
        "duplicate_writes": False, "pending_slot_audit_zero": True,
    })
    return base


class TestObservationRules:
    def test_required_counts_are_two_plus_two(self):
        assert REQUIRED_COMPLETE_POST_CLOSE == 2
        assert REQUIRED_INCREMENTAL_REPLAY == 2

    def test_non_trading_day_never_counts_as_complete(self):
        rc = _complete_cycle()
        rc["is_trading_day"] = False
        assert is_complete_post_close_cycle(rc) is False

    def test_complete_cycle_requires_all_conditions(self):
        for field in ("traversal_completed", "four_table_cycle_terminal",
                      "no_unexplained_failed_held_pending", "quality_audit_pass"):
            rc = _complete_cycle()
            rc[field] = False
            assert is_complete_post_close_cycle(rc) is False

    def test_incremental_replay_requires_prior_complete(self):
        rc = _replay_cycle()
        # No prior complete cycle -> not counted as replay.
        assert is_incremental_replay_cycle(rc, prior_complete=False) is False
        assert is_incremental_replay_cycle(rc, prior_complete=True) is True

    def test_incremental_replay_rejects_anomalies(self):
        rc = _replay_cycle()
        rc["duplicate_writes"] = True
        assert is_incremental_replay_cycle(rc, prior_complete=True) is False

    def test_two_plus_two_satisfies_gate(self, tmp_path):
        state = ObservationState()
        # Two complete cycles.
        for d in ("2026-08-06", "2026-08-07"):
            record_run_card(state, _complete_cycle(d))
        assert state.complete_post_close_cycles_success == 2
        # Now prior_complete is True; two replay cycles.
        for d in ("2026-08-08", "2026-08-09"):
            record_run_card(state, _replay_cycle(d))
        assert state.incremental_replay_cycles_success == 2
        assert state.satisfied() is True

    def test_failed_cycle_does_not_increment_and_extends(self, tmp_path):
        state = ObservationState()
        record_run_card(state, _complete_cycle("2026-08-06"))
        failed = _complete_cycle("2026-08-07")
        failed["quality_audit_pass"] = False  # trading day but not complete
        record_run_card(state, failed)
        assert state.complete_post_close_cycles_success == 1
        assert state.auto_extended is True
        assert state.satisfied() is False

    def test_half_day_market_counts_only_when_complete(self):
        # A half-day market is recorded with is_trading_day=True; it counts
        # only if ALL completeness conditions hold (the rule does not special-case
        # half-day beyond the completeness gate).
        half = _complete_cycle()
        half["four_table_cycle_terminal"] = False  # incomplete half-day
        assert is_complete_post_close_cycle(half) is False
        half["four_table_cycle_terminal"] = True  # complete half-day
        assert is_complete_post_close_cycle(half) is True

    def test_state_persists_and_recovers(self, tmp_path):
        state = ObservationState()
        record_run_card(state, _complete_cycle("2026-08-06"))
        path = tmp_path / "obs_state.json"
        save_state(state, path)
        recovered = load_state(path)
        assert recovered.complete_post_close_cycles_success == 1
        assert len(recovered.run_cards) == 1

    def test_run_card_template_has_all_fields(self):
        tpl = render_run_card_template()
        for field in ("scheduled_date", "run_id", "cycle_id", "is_trading_day",
                      "traversal_completed", "watermark_before", "watermark_after",
                      "evidence_sha256"):
            assert field in tpl

    def test_observation_report_completeness(self):
        state = ObservationState()
        report = observation_report(state, cutover_id="cut1")
        for field in ("required_complete_post_close", "required_incremental_replay",
                      "complete_post_close_cycles_success", "incremental_replay_cycles_success",
                      "satisfied", "auto_extended", "run_cards"):
            assert field in report
        assert report["cutover_id"] == "cut1"
