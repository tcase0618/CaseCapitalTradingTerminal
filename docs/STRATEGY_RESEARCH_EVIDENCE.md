# Strategy research evidence

This implementation measures evidence. It does not authorize orders, change Core
discovery, reset active weights, or promise improved returns.

## Connected surfaces

- Learning tab: saved, version- and mode-separated strategy scorecards.
- GET `/api/research/strategy-evidence`: saved report only, no provider calls.
- POST `/api/research/strategy-evidence/refresh`: bounded research refresh.
- Weekdays 21:15 America/New_York: refresh the research report independently of
  scan and position-monitor cadences.
- POST `/api/research/path-replay`: caller-supplied OHLC replay, up to 10,000 bars.
  Source, entry, period, and explicit costs are required for net comparisons.
- GET/POST `/api/research/experiments`: immutable preregistration ledger. Future
  holdout dates are required. Registration is not evidence of a completed test.
- Public fill analytics: side-signed arrival and limit-relative costs, separate
  actual and estimated fees, explicit missing quantities/side/arrival marks.
- Options chain: observed-only historical ATM call IV series; provider and feed
  histories are isolated. Unknown rank is not silently replaced by 50.

## Measurement contract

PM sightings collapse into nonoverlapping ticker/strategy/horizon episodes.
Horizons are the next 1, 5, or 20 exchange-session closes after the sighting.
Resolved facts persist immutably; provider outages cannot erase them. Missing
exact closes remain missing. Historic unverified entry marks remain labelled.

Gross return uses the signal mark, not a broker fill. SPY excess return currently
uses its prior completed close as entry, not the same intraday instant. Therefore
it is not execution-matched or risk-adjusted alpha. Net episode expectancy remains
unavailable without attributable round-trip costs. Daily rank IC is descriptive;
dependent cohorts do not receive a fabricated t-statistic or confidence claim.

Replay evaluates both OHLC intrabar orderings, flags ambiguity, and chooses the
conservative outcome. Gap-through stops fill at the opening price, not at an
unavailable floor. Arming is strictly above a milestone; touching an armed floor
exits. Floors continue beyond 20% with the existing uncapped milestone schedule.
Open positions are not silently liquidated at the last bar. Caller-supplied data
and costs are labelled as assumptions, not independently verified observations.

Historical IV rank requires 200 earlier daily observations spanning 330 days
within a 365-day window, plus freshness and variability checks. This deployment
does not manufacture a year of historical IV. IV minus realized volatility is
descriptive, not an options profitability test or automatic gate.

## Learning boundary

Signal, Trade Floor, and Lottery scheduled learning cycles append SHADOW
proposals. Existing active weights, stop coefficients, entry offsets, phase
parameters, and Lottery configuration remain unchanged. Sample thresholds do
not promote proposals. Manual reset/admin capabilities remain explicit actions.

## Still requires evidence

This is not a completed causal experiment platform. Purged model fitting,
trial-adjusted inference (including deflated Sharpe/PBO), executable historical
option-chain selection, licensed long-horizon IV backfill, and automatic
experiment resolution/promotion are not claimed. Preregister the hypothesis and
frozen config, collect the holdout, review costs and coverage, and evaluate it
before any strategy promotion. No automatic promotion route exists here.

## Verification

Offline tests cover exchange holidays and early closes, episode deduplication,
missing exact closes, persistent outcome idempotency and provider failure,
immutable experiment registration, replay gaps and floor touches, missing-side
costs, insufficient IV history, and active-config equality during shadow learning.
External integrations and trading-order tests are excluded from the default run.
