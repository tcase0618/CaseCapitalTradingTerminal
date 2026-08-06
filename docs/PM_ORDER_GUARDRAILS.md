# PM Order Guardrails

This note exists because the LDOS paper order on 2026-07-19 exposed a bad limit-price risk: Alpaca IEX returned an ask of 116.91 while the PM/scanner price was about 106.48 and the PM entry-high was 108.61.

## Rule

For every PM-managed buy order, Trade Floor must not blindly chase the raw Alpaca ask.

The order limit must be capped by the PM entry band:

- If `raw_alpaca_ask > pm_entry_high`, use `pm_entry_high`.
- Else if `raw_alpaca_ask > scanner_price * 1.03`, use `scanner_price * 1.01`.
- Else use the raw Alpaca ask.

Every PM-managed trade record must store:

- `raw_alpaca_ask`
- `limit_price`
- `limit_price_guard.entry_high`
- `limit_price_guard.scanner_price`
- `limit_price_guard.capped`
- `stop_price`
- `current_stop`
- `pm_active_stop`
- `pm_active_target`

## Pre-Order Checklist

Before submitting a PM-managed position:

1. Confirm the ticker has no open Alpaca position.
2. Confirm the ticker has no pending Alpaca order.
3. Compare raw Alpaca ask against PM scanner price.
4. Compare raw Alpaca ask against PM entry high.
5. Refuse or cap any order where the raw ask is materially above the PM entry band.
6. Record the PM stop before or at order submission.
7. Keep `fill_status=PENDING` until Alpaca confirms a fill.

## Stop Handling

The PM stop is recorded immediately, but it is only enforceable after a buy order fills.

For unfilled orders:

- `status=OPEN`
- `fill_status=PENDING`
- no sell stop can protect the position because no position exists yet

For filled orders:

- sync must set `fill_status=FILLED`
- sync must set `filled_avg_price`
- sync must set `qty_total` and `qty_remaining`
- position monitor must enforce `current_stop`
- PM ratchet may only move the stop favorably

## Sync Rule

Never mark a pending buy order as closed just because there is no live Alpaca position yet.

If `fill_status` is `PENDING`, sync should leave the trade open and pending until Alpaca reports a fill, cancellation, expiration, or rejection.

## Current LDOS Fix

The bad LDOS order was canceled and replaced:

- Bad limit: 116.91
- Corrected limit: 108.61
- Scanner price: 106.48
- PM entry high: 108.61
- PM stop: 93.70
- PM active target: 133.10
- Guard applied: `capped=true`

## Engineering Requirement

Any future order-entry changes must preserve this guardrail. Do not submit PM-managed buy limits directly from `get_latest_ask()` without applying the PM entry-band cap.

## Options Desk V2 Guardrails

Options Desk uses a separate Alpaca options account and must never call the equity Trade Floor execution path.

Entry eligibility is intentionally stricter than scanner discovery:

- Only PM routes `OPTION` or `BOTH` can become executable.
- Equity-only routes must not spend Alpaca option-chain refresh budget.
- Option entries require Alpaca execution-grade data.
- Provider-reported delta is required; synthetic/estimated delta is research-only.
- Single-leg grind candidates must stay inside the execution delta band: `0.45` to `0.70` absolute delta.
- Minimum ask/premium is `1.00`.
- Minimum open interest is `500`, unless volume is at least `200`.
- Maximum spread is the stricter of `0.75` absolute or `8%` of ask.
- Multi-leg spread execution remains blocked until the desk has a true spread order path.

Open option risk is measured on an honest mark basis:

- Entry truth records actual `fill_price`, `mid_at_fill`, bid/ask spread, and spread cost paid.
- Open-position stop logic uses snapshot mid when available.
- Alpaca position `unrealized_plpc` is retained only as broker diagnostic context.
- Closed trades write to the expectancy ledger only after actual sell fills sync back from Alpaca.

The V2 exit priority is:

1. Hard stop at `-25%` mid-to-mid P/L.
2. Event stop one day before earnings unless the lane is explicitly event-defined risk.
3. Time stop when `40%` of entry DTE has elapsed and the trade has not reached `+10%`.
4. Theta stop when daily theta is worse than `3%` of entry premium and the position is not green.
5. Ratchet floor using the no-TP ratchet tiers.

Sizing stays in grind mode until the expectancy ledger proves a lane:

- Default option risk budget is flat, based on `85%` grind sleeve equity times `1.5%`.
- Automatic order count is capped per scan and per day.
- Lane throttle can reduce sizing after enough closed-trade evidence shows negative expectancy.
- Tail Hunter and spread execution should remain research-only until the expectancy ledger has real closed-trade samples.

## Code Owners' Map

Current enforcement points:

- PM-managed order entry: `backend/services/trade_floor.py`, inside `evaluate_and_execute()`.
- One-ticker PM execution endpoint: `backend/server.py`, route `POST /api/trade_floor/execute_pm_ticker`.
- Pending-order sync protection: `backend/services/trade_floor.py`, inside `sync_positions_and_close_settled()`.
- Options entry and exit enforcement: `backend/services/options_desk.py`.
- Options contract recommendation and execution-grade filtering: `backend/services/options_engine.py`.
- Options closed-trade truth ledger: `backend/services/expectancy_ledger.py`.

Any future route that submits PM-managed buys must call the guarded PM execution path or duplicate this exact cap and audit fields.
