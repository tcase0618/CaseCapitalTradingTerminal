# Frontend and Equity Research Promotion

Explicit user authorization: continue the work, then push to main and the VPS.

## Accepted

- Complete reviewed frontend display/navigation pass, shared bounded reads,
  cancellation, retention of last-good data, truthful unavailable states,
  responsive tables/calendars and pagination.
- Read-only saved strategy-evidence endpoint and research-only replay/experiment
  helpers. Research registrations have no live promotion or execution authority.
- PM observation metadata only: score version, original breakdown, price
  evidence, regime and operating mode. No change to scoring or allocations.
- Offline equity backtest scripts, tests and independently reviewed reports.
  Per-signal and account source exports remain outside Git and production.

## Deliberately Not Promoted

The complete audit-branch execution diff is NOT part of this release.
`cbb24d7` / `f9822e4` Public lifecycle, options-monitor and safety changes remain
subject to their separately documented concurrency/broker verification gaps.
Existing deployed operational/resource commits must be preserved when main is
merged into the production checkpoint; do not reset that branch to main.

No trading-policy, order-authority, allocation, broker credential or execution
environment change is authorized by the offline backtest result.

## Verification

- Frontend: 160 tests / 20 suites passed; optimized build passed.
- Browser fixtures: 136 checks passed, zero failures or mutation requests.
- Main promotion backend: 109 research/reporting tests and 90 PM/execution-helper
  regression tests passed; touched production Python compiled.
- Offline study: 47 tests plus 126 subtests passed before final data run.

These are offline/fixture checks, not a guarantee of investment performance or
complete broker-lifecycle safety. Deployment revision, static checksums and live
read-only health verification will be recorded separately.
