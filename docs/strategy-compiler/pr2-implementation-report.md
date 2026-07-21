# PR2 Implementation Report

Stage: PR2  
Status: PASS (waiting for user confirmation)

## Goal

Implement a real pending-order queue for `match_price_mode == "next_open"`, eliminating the cross-day look-ahead and same-day accounting leak in the legacy implementation. T-day signals create pending orders; the T+1 open event drains the queue and fills (or rejects) using T+1 price and state.

The change is strictly isolated to `next_open`. `close`/`open` execution paths, the `context.portfolio.positions` plain-dict membership, security-code rules, providers, the Skill skeleton, renderers, and GUI are not touched.

## Completed

1. Added a `PendingOrder` dataclass with lifecycle `created → pending → filled/rejected/expired/cancelled` and seven instruction enums (`target_value` / `target_shares` / `buy_shares` / `sell_shares` / `buy_value` / `sell_value` / `sell_all`).
2. Added `Account.locked_cash` for pending-buy fund pre-deduction (0 in close/open) and `Position.pending_sell_shares` for pending-sell share pre-deduction (0 in close/open). Both symmetric pre-deductions refund exactly on reject/expire/cancel.
3. Added `Order.filled_dt` to record the T+1 fill date (empty in close/open).
4. Rewrote `_build_match_prices` next_open branch: it no longer prefetches T+1 data. It returns same-day close as the "strategy-visible price" so `_get_current_price` / `order_value` pre-conversion logic still works. Real matching duty moved to the drain step.
5. Added `_create_pending_order`: T-day creates a pending order, locks `locked_cash` (buy) or `pending_sell_shares` (sell) using T-day close as the estimate. T-day `volume`, `trade_records`, and `can_sell` are unchanged.
6. Added `_drain_pending_orders`: the T+1 open event processes `scheduled_dt == T+1` orders using a separately-built T+1 open price dictionary plus T+1 state (halt via `suspendFlag==1 OR volume==0`, limit via `is_price_limit_blocked`, cash/lot via `_execute_buy`/`_execute_sell`). Fills record T+1 date; rejections refund pre-deductions.
7. Inserted drain into the main loop after the T+1 unlock and before `_run_ptrade_strategy`, so newly filled buys keep `can_sell=0` and are not re-unlocked the same day.
8. Branched all four PtradeAPI trade methods (`order_target_value` / `order` / `order_value` / `order_target`) to `_create_pending_order` before any value→shares or delta conversion. `order_value`/`order_target` re-resolve at T+1 in `_resolve_at_t1`, preserving `min_rebalance_pct` / `below_rebalance_threshold` semantics.
9. Populated `get_open_orders` / `get_order` / `cancel_order` in next_open mode. `cancel_order` is status=`cancelled` (a distinct terminal state, not folded into `rejected`), and refunds exactly by `est_cost` / `est_shares` snapshot so accumulated multi-day orders do not drift.
10. Added `_expire_remaining_pending`: still-pending orders at end of backtest are marked `expired` with pre-deduction refund.
11. Added `engine_semantics_version` property and recorded it in the Run Card config.csv: `0.1.0-legacy` for close/open, `0.2.0-next_open_pending` for next_open.
12. Did not touch security-code rules, providers, the Skill skeleton, renderers, or GUI.

## Critical design decisions (from plan review)

- **Drain order**: unlock → drain → refresh_portfolio → strategy. Placing drain after the unlock loop prevents the new-buy `can_sell=0` from being overwritten, which would have re-introduced a more隐蔽 T+1 violation.
- **Drain fill price**: a separately-built T+1 open dictionary, not `match_prices`. After the next_open rewrite, `match_prices` is T-day close; reusing it for drain would fill pending orders at the close price — the exact leak PR2 removes.
- **Symmetric pre-deduction**: buy locks `locked_cash`, sell locks `pending_sell_shares`. Both are reflected in `total_asset_at_price` so T-day NAV stays invariant; both refund exactly on reject/expire/cancel using the original snapshot to prevent drift.
- **T+1 gap rejection**: when the recomputed share demand exceeds available cash (e.g. a gap-up on a fixed-share buy), the whole order is rejected with `insufficient_cash_or_rounding` and the pre-deduction is refunded — no缩单, matching the existing PTrade semantics in `_execute_buy`.
- **End-of-backtest / no-next-day**: a T-day order with no next trading day is rejected at creation (`no_next_trade_day`, no pre-deduction); a still-pending order at backtest end is marked `expired` with refund. This realizes the master plan item "末日订单标记 expired".

## Changed files

- `quantstudio/backtest/backtest_engine.py`
  - `Position` (added `pending_sell_shares`)
  - `Order` (added `filled_dt`)
  - `PendingOrder` (new dataclass)
  - `Account` (added `locked_cash`; `total_asset`/`total_asset_at_price` include it)
  - `BacktestEngine.__init__` (pending queue state)
  - `engine_semantics_version` (new property)
  - `_build_match_prices` (next_open returns same-day close)
  - `_next_trade_day_str` / `_estimate_pending` / `_create_pending_order` / `_reject_pending` / `_cancel_pending_order` / `_expire_remaining_pending` / `_is_halted_at` / `_resolve_at_t1` / `_drain_pending_orders` / `_po_to_order` (new)
  - `run()` main loop (inject T-day state; drain after unlock; expire at end)
- `quantstudio/backtest/ptrade_api.py`
  - `order_target_value` / `order` / `order_value` / `order_target` (next_open branch before conversion)
  - `get_open_orders` / `get_order` / `cancel_order` (populated in next_open mode)
- `quantstudio/backtest/result_exporter.py`
  - config.csv records `engine_semantics_version`
- `docs/strategy-compiler/implementation-status.md`

## New tests

```text
tests/test_next_open_pending_orders.py
tests/test_next_open_nav_timing.py
tests/test_next_open_limit_and_halt.py
tests/test_pending_order_end_of_backtest.py
```

### Test coverage

- `test_next_open_pending_orders.py`: T-day buy locks cash / sell locks `pending_sell_shares`; T+1 drain fills at T+1 open (≠ close), records T+1 date, `filled_dt=T+1`; T-day total asset invariant; close/open `locked_cash==0` zero-touch; `engine_semantics_version` mode mapping.
- `test_next_open_nav_timing.py`: `Account.total_asset`/`total_asset_at_price` include `locked_cash`; T-day NAV unchanged by pending order; cash+locked_cash conserved; T-day volume unchanged; T+1 NAV reflects fill.
- `test_next_open_limit_and_halt.py`: T+1 limit-up rejects buy; limit-down rejects sell; halt via `suspendFlag==1` or `volume==0`; T+1 gap cash-insufficient whole-order rejection (no partial).
- `test_pending_order_end_of_backtest.py`: end-of-backtest `expired` with refund; `scheduled_dt=None` create-time rejection (no pre-deduction); `cancel_order` distinct `cancelled` status; cancel only removes the target order.

## Verification (fixed order)

```text
1. New pending-order tests:        26 passed
2. Core regression (zero-touch):   66 passed
3. Full test suite:                281 passed (255 prior + 26 new)
4. Real Fidelity gates:
   ETF momentum:    PASS  final_asset 87752.561780  3 fills  exact sequence
   Small-cap guard: CLOSE final_asset 118551.211880 57 fits  within frozen envelope
```

ETF golden sequence and small-cap CLOSE envelope are byte-level identical to the pre-PR2 baseline, confirming the close-mode zero-touch property.

## Isolation contract

The close/open zero-touch property is protected by dedicated assertions:

- `test_close_mode_account_has_no_locked_cash` / `test_open_mode_account_has_no_locked_cash`
- `test_close_mode_immediate_execute_does_not_touch_locked_cash` — the immediate path never touches `locked_cash` / `pending_sell_shares`.
- `test_drain_pending_buy_fills_at_t1_open_not_close` — drain fill price uses T+1 open, not close.

## What was not mixed in

- No new security-code rules (PR1 frozen).
- No minute provider or minute event engine (PR3/PR4).
- No Skill skeleton (PR5).
- No renderer / IR / static validator (PR6).
- No change to close/open matching口径.

## Next gate

Waiting for user confirmation of PR2 before starting PR3 (multi-frequency Provider). PR3 must not mix in next_open pending-order changes.
