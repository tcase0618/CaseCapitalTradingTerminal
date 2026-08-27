# Options Filter Overhaul Build List

## Implemented

- One shared policy now drives contract selection and Options Desk gates.
- `standard` preserves the stricter profile for live/non-paper use.
- `paper_scout` broadens only the Alpaca paper/indicative path: OI 100, low-OI
  volume 50, unknown-OI volume 50, max spread $1.50/15%, indicative 30%,
  delta 0.25-0.80, and target delta 0.50.
- The premium floor remains $0.05 as requested.
- Environment overrides remain supported and are applied consistently.
- Contract-learning stays shadow until 100 resolved observations, advisory at
  100, and only live-eligible after 150 with measured outperformance. Automatic
  live promotion remains disabled.

## Deliberately unchanged

- No risk-budget increase was applied. Wider acceptance must not silently add
  dollars at risk.
- No live/non-paper account can use `paper_scout`.
- No order is submitted by deployment or verification.

## Verification

1. Run the options unit suite.
2. Verify backend and options status endpoints.
3. Run a read-only candidate refresh and inspect blockers.
4. Confirm policy mode and data quality are visible in diagnostics.
