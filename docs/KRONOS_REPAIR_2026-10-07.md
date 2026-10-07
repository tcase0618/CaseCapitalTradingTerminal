# Kronos Research Repair

## Scope

No changes to trading gates, PM allocation, Public equity orders, Alpaca option
orders, ratchets, or protective exits. Forecasts remain research-only.

## Replaced Behavior

- Oldest-history requests replaced with descending latest-window retrieval,
  chronological normalization, completed-bar filtering, and XNYS session-aware
  freshness checks. Daily bars complete at session close, not midnight.
- Daily close-only arrays no longer masquerade as intraday OHLCV.
- Public equities replace the wrong Alpaca equity book. The saved terminal PM
  plan replaces the separate Core-only PM replay.
- Fixed PM-action return assumptions are removed. Each held underlying uses a
  versioned OHLCV baseline, or explicitly reports unavailable. Option forecasts
  describe the underlying, never pretend to predict option premium returns.
- Scheduler refreshes persist immutable, content-addressed predictions. Outcome
  resolution requires the exact target bar to complete; resolved evidence is
  retained through provider outages.
- Accuracy is isolated by model version and reports a no-change MAE benchmark,
  skill relative to that benchmark, and interval width alongside coverage.
- Untested feedback cannot change baseline parameters without an explicitly
  validated advisory promotion record. Scores and scenario weights are marked
  uncalibrated, not presented as proven probabilities.
- PostgreSQL natural keys stop snapshot/disagreement duplication. Migration
  002 archives every affected original row before transactional deduplication.
- Calendar counts predictions and resolved outcomes by Eastern issue date;
  five-minute estimates are no longer graded against full-day SPY returns.
- Forecast/accuracy/learning GETs read persisted evidence without writing or
  rebuilding the portfolio. Explicit refresh is serialized.
- Frontend updates selected data when the snapshot changes, requests latest
  chart windows, sorts candles, preserves percent units, and removes the
  fabricated local forecast fallback and sinusoidal future paths.

## Honest Limitations

This is `ohlcv_baseline_v2`, not the shiyu-coder/Kronos neural foundation model.
The previous implementation never loaded that model. No neural model is silently
substituted, and no predictive edge or calibrated win rate is claimed. A model
challenger requires separate walk-forward evaluation against the versioned
baseline, with matched horizons, costs, interval-width checks, and held-out
calibration before promotion. Legacy snapshots cannot be repaired into valid
ex-ante predictions; they are retained as historical audit evidence, excluded
from new-model proof.

## Verification

Offline tests cover stale historical data, incomplete daily/intraday bars,
holidays and early closes, exact resolution horizons, immutable persistence,
Public/PM contracts, provider outages, percent units, calendar counts, and
PostgreSQL natural keys. Production verification is read-only except for the
Kronos research refresh and scoped evidence migration; no orders are tested.
