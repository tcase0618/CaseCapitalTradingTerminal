# Case Capital Equity Signal Study

## Verdict

Research-only retrospective study, not a trading-policy release. No optimizer was used. All tested variants and missing observations are retained. A highest historical mean is not a validated future edge.

### Decision From the Results

- Day-2 Continuation is not a demonstrated money-maker in this sample. Its
  five-session specialist cohort has 98 comparable windows: -7.56% without a
  stop versus -1.49% with current floors, against +0.31% matched SPY. The floors
  improve the modeled loss; they do not establish profitable selection.
- In the retrospective holdout, Day-2's current-floor mean is -0.84% on 81
  windows, with 24.7% winners and -1.20% mean excess versus SPY. Its ticker-cluster
  excess interval is approximately [-5.57%, +4.19%], not evidence of positive alpha.
- Core's holdout mean is -0.14% versus +0.38% matched SPY. Its excess interval
  spans zero. Leave Core discovery unchanged as requested; this study does not
  establish that adding execution gates or loosening entries repairs the edge.
- A tighter -8% stop does not universally improve results. Across all comparable
  Day-2 windows, current floors/-8% average -1.88%, worse than -1.49% at -10%.
  Delaying the first floor arm to +7% produces -1.39%, only a small descriptive
  difference. Neither should be promoted from this retrospective comparison.
- Max Conviction and Narrative Lock are prospective-shadow candidates, not
  certified winners: their holdout counts are 28 and 7 respectively. Max
  Conviction's excess interval includes zero; Narrative Lock is too sparse for
  the declared interval threshold. Do not increase capital on these means alone.
- Market matching is material, but does not excuse every loss. Core and Day-2
  five-session holdout entries all fall in the declared **flat** prior-20-session
  SPY regime. This is not validation across multiple adverse regimes.

The next defensible improvement is to capture contemporaneous security identity,
price adjustment basis, spread and fill economics, then prospectively shadow the
fixed alternatives per lane. No live strategy was retuned by this study.

## Scope and Evidence

- Signal period: 2026-07-22T08:51:02.817011+00:00 through 2026-10-08T19:00:50.495524+00:00; completed historical closes end 2026-10-07.
- 79,469 valid source sightings; 62,175 canonical sightings; 4,056 unique tickers; 55 SPY-derived sessions.
- Public history responses: 4056; request errors: 0; empty bar payloads: 377. Empty/missing data are not zero returns.
- Retrospective holdout starts 2026-09-15; common 20-session purge applies to all horizons. This is deliberately conservative, not horizon-specific purging.
- Four independent agents reviewed rules, data, methodology, and policy reconstruction. Their reports are in this directory. Later agent capacity was exhausted; the main reviewer completed integration and result checks.
- Options-named rows below measure their EQUITY UNDERLYINGS, never option contract performance. Earnings and SEC remain research-only. No broker orders, strategy settings or execution gates were changed for the study.
- LOTTERY_BOARD_* identifies broad persisted discovery-board fits, not the separately scored lottery_* specialist proposals. UNCLASSIFIED rows are prescreen discoveries, not approved signals. FIRST_SEEN_UNATTRIBUTED is ticker-level historical discovery without a recoverable lane.

## Five-Session Matched Counterfactuals

All columns use the same next-eligible-RTH-open entry and common fifth-session endpoint. Stops can exit earlier; proceeds then stay flat. These are modeled gross returns, not actual fills. The no-stop column is a comparator, not a faithful reconstruction of every historical live policy.

| Strategy | N | No Stop | Current Floors / -10% | Current Floors / -8% | Delay First Arm to 7% | Matched SPY | Current Win Rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| CORE | 653 | +0.85% | +0.57% | +0.58% | +0.81% | +0.50% | 57.3% |
| DARK_HORSE | 28 | +0.58% | +0.44% | +0.45% | +0.55% | +0.98% | 50.0% |
| FIRST_SEEN_UNATTRIBUTED | 981 | -1.98% | -1.39% | -1.31% | -1.39% | +0.14% | 40.4% |
| LOTTERY_BOARD_CATALYST_RUNNER | 2 | -7.42% | -7.86% | -8.00% | -7.86% | -0.88% | 0.0% |
| LOTTERY_BOARD_DAY2_CONTINUATION | 588 | -4.64% | -1.62% | -1.61% | -1.58% | +0.44% | 35.2% |
| LOTTERY_BOARD_SIGNAL_CONFLUENCE | 610 | -1.43% | -0.40% | -0.34% | -0.32% | +0.36% | 44.3% |
| LOTTERY_BOARD_UNCLASSIFIED | 2737 | -0.62% | -0.54% | -0.45% | -0.52% | +0.32% | 42.7% |
| MAX_CONVICTION | 38 | +1.67% | +1.83% | +2.02% | +1.01% | +0.39% | 65.8% |
| NARRATIVE_LOCK | 9 | +7.66% | +7.18% | +7.18% | +6.92% | +1.22% | 88.9% |
| earnings_calendar | 165 | -0.91% | -0.93% | -0.96% | -0.90% | +0.26% | 44.8% |
| earnings_core_overlap | 154 | -0.69% | -0.77% | -0.94% | -0.48% | +0.40% | 50.0% |
| lottery_catalyst_runner | 2 | -7.42% | -7.86% | -8.00% | -7.86% | -0.88% | 0.0% |
| lottery_day2_continuation | 98 | -7.56% | -1.49% | -1.88% | -1.39% | +0.31% | 23.5% |
| lottery_dilution_read | 75 | +6.06% | -1.33% | -1.35% | -1.75% | +0.46% | 44.0% |
| options_breakout_call | 597 | -0.28% | +0.10% | +0.11% | -0.08% | +0.37% | 49.7% |
| options_event_defined_risk | 4 | -1.89% | -2.33% | -2.33% | -2.86% | +0.23% | 25.0% |
| options_leaps_trend | 57 | +1.40% | +1.19% | +1.11% | +1.08% | +0.49% | 64.9% |
| options_tactical_momentum_call | 848 | -1.08% | -0.38% | -0.33% | -0.47% | +0.33% | 45.0% |
| pharma_calendar | 27 | -4.18% | -4.28% | -3.54% | -4.16% | -0.48% | 25.9% |

## Retrospective Holdout: Five Sessions

| Strategy | N | No Stop | Current Floors / -10% | Delta vs No Stop | Excess vs SPY |
|---|---:|---:|---:|---:|---:|
| CORE | 140 | -0.71% | -0.14% | +0.57% | -0.51% |
| DARK_HORSE | 25 | +0.79% | +0.52% | -0.27% | -0.64% |
| FIRST_SEEN_UNATTRIBUTED | 220 | -0.50% | -0.20% | +0.30% | -1.32% |
| LOTTERY_BOARD_CATALYST_RUNNER | 0 | -- | -- | -- | -- |
| LOTTERY_BOARD_DAY2_CONTINUATION | 446 | -5.55% | -1.61% | +3.94% | -2.11% |
| LOTTERY_BOARD_SIGNAL_CONFLUENCE | 459 | -2.11% | -0.36% | +1.75% | -0.86% |
| LOTTERY_BOARD_UNCLASSIFIED | 1945 | -0.94% | -0.28% | +0.66% | -0.80% |
| MAX_CONVICTION | 28 | +2.55% | +2.51% | -0.04% | +1.95% |
| NARRATIVE_LOCK | 7 | +8.62% | +8.20% | -0.41% | +6.85% |
| earnings_calendar | 114 | -1.55% | -1.17% | +0.38% | -1.61% |
| earnings_core_overlap | 123 | -0.08% | -0.05% | +0.04% | -0.72% |
| lottery_catalyst_runner | 0 | -- | -- | -- | -- |
| lottery_day2_continuation | 81 | -9.17% | -0.84% | +8.33% | -1.20% |
| lottery_dilution_read | 53 | +10.36% | -1.64% | -12.00% | -2.16% |
| options_breakout_call | 455 | +0.05% | +0.38% | +0.34% | -0.15% |
| options_event_defined_risk | 3 | -0.99% | -1.59% | -0.60% | -2.19% |
| options_leaps_trend | 41 | +2.35% | +2.07% | -0.28% | +1.21% |
| options_tactical_momentum_call | 619 | -0.88% | -0.10% | +0.77% | -0.57% |
| pharma_calendar | 7 | +4.33% | +1.04% | -3.29% | +0.38% |

## Recorded Entry-Price Diagnostic

This is the requested time/recorded-price view. It is NOT an executable backtest: entry marks may be stale or on a different split/symbol basis. Extreme apparent Lottery profits must not be called alpha. Flags are sensitivity labels, not proof of an error or permission to repair prices.

| Strategy | Five-Session N | Mean | Median | Win Rate | Scale-Flagged Episodes | Unflagged Sensitivity Mean |
|---|---:|---:|---:|---:|---:|---:|
| CORE | 648 | +2.21% | +0.73% | +53.55% | 2 | +1.07% |
| DARK_HORSE | 28 | -1.83% | -3.29% | +28.57% | 0 | -1.83% |
| FIRST_SEEN_UNATTRIBUTED | 995 | +26.37% | -1.87% | +38.59% | 24 | -1.97% |
| LOTTERY_BOARD_CATALYST_RUNNER | 2 | -8.35% | -8.35% | +0.00% | 0 | -8.35% |
| LOTTERY_BOARD_DAY2_CONTINUATION | 580 | +86.38% | -5.35% | +37.24% | 41 | -3.88% |
| LOTTERY_BOARD_SIGNAL_CONFLUENCE | 602 | +7.65% | -0.93% | +44.68% | 10 | -1.43% |
| LOTTERY_BOARD_UNCLASSIFIED | 2700 | +38.55% | -2.87% | +36.89% | 88 | -1.77% |
| MAX_CONVICTION | 38 | +1.90% | +1.62% | +57.89% | 0 | +1.90% |
| NARRATIVE_LOCK | 9 | +6.30% | +7.07% | +77.78% | 0 | +6.30% |
| X_FACTOR | 0 | -- | -- | -- | 0 | -- |
| earnings_calendar | 165 | -1.52% | -1.95% | +39.39% | 0 | -1.50% |
| earnings_core_overlap | 153 | +1.13% | +0.00% | +48.37% | 1 | -0.06% |
| lottery_catalyst_runner | 2 | -8.35% | -8.35% | +0.00% | 0 | -8.35% |
| lottery_day2_continuation | 99 | +183.53% | -18.44% | +29.29% | 11 | -9.17% |
| lottery_dilution_read | 72 | +226.81% | -7.54% | +30.56% | 3 | -5.46% |
| options_breakout_call | 593 | +0.01% | -0.48% | +45.70% | 2 | -0.21% |
| options_event_defined_risk | 4 | -1.31% | -3.69% | +25.00% | 0 | -1.31% |
| options_leaps_trend | 57 | +1.44% | +0.92% | +59.65% | 0 | +1.44% |
| options_tactical_momentum_call | 839 | +1.51% | -0.95% | +39.33% | 9 | -0.96% |
| pharma_calendar | 27 | -1.16% | -4.90% | +29.63% | 0 | -1.16% |
| sec_filings | 0 | -- | -- | -- | 0 | -- |

## Zero-Usable-Outcome Lanes

`lottery_supernova`, `lottery_red_green`, `lottery_serial_runner`, `lottery_signal_confluence`, `pharma_core_overlap`, `options_squeeze_call`, `sec_filings`, `X_FACTOR`. These have no resolved comparable observations under their specific identities in this export; this is not a 0% win-rate claim. Some have excluded price/timestamp records; others have no persisted matching lane. See entry-exclusions.csv and strategy reconstruction.

## What Should Change Next

1. Repair and persist entry-price provenance, security identity and corporate-action basis before using historical mark returns to tune the PM. Several-fold discrepancies are documented in data-schema-audit.md and the flagged-record CSV.
2. Keep strategy-specific holding horizons. Review all 1/3/5/10/20-session results rather than adopting a single winning horizon in hindsight. Sparse lanes and zero-data strategies have no estimated edge.
3. Treat any stop/floor advantage as a hypothesis for a prospective shadow trial, not an automatic live promotion. Daily bars cannot establish intraminute ratchet performance or limit-order fills.
4. Use paired market-relative results and entry-known market regimes. regime-summary.csv uses only the previous 20 completed SPY sessions, with fixed +/-2% regime boundaries. It does not filter on future market returns.
5. Capture actual paid fees and broker execution history. At $2-$6 sizing, fixed transaction costs can dominate apparently small gross edges. The 0/25/100/250 bps sensitivity is a scenario, not a fee estimate.
6. Separate signal quality from PM selection and execution. Nearest-ticker PM matches are descriptive, not lane-specific approvals; approved and rejected cohorts are not randomized.

## Actual Fills Are a Separate Evidence Tier

The fill review found 82 Trade Floor records and 9 mirrored Lottery tickets. Only 24 Trade Floor closes supported strict fill-price returns; one mirrored ticket supplied an additional distinct return. Of the 18 supported Public closes, gross dollar P&L summed to -$3.678685. This is an incomplete subset, not account net P&L. Five documented Alpaca paper closes summed to +$26.018672 gross. Missing fees, open positions, cash-only exits and legacy identities prevent a complete portfolio return. See fill-diagnostics.md.

## Reproducibility and Limits

Normalized input SHA-256: `7694ea504e77f2d32c759be1adfbb51a5e7df1273068728adae9c18929beb52a`.
Fresh Public history SHA-256: `33c6c06744c9e63aab61768b0d67617a0a2d52ec2d4d07582adc100055cf4282`.
Counterfactual exclusions: `{"invalid_signal_or_no_entry_session": 3137, "missing_or_invalid_window_ohlc": 2965, "outside_calendar": 9892}`.
- No survivorship correction, tick-level simulation, verified adjustment basis, or contemporaneous spread/liquidity history is available. Delisted/unavailable windows are reported, not forward-filled.
- Means are equally weighted observations, not portfolio returns. Cross-strategy duplicate tickers remain separate strategy tests; do not sum their profits or claim independent samples.
- Confidence intervals use ticker clusters only, not complete two-way ticker/date dependence. Small samples are inconclusive. Multiple hypotheses and hindsight mean no confirmatory p-value or expected future alpha is claimed.
- The common 20-session purge can leave sparse or empty training sets. The historical holdout has been discussed before; it is not prospectively untouched.
- The partial current session is excluded consistently. Daily-model stops activate from prior completed highs; gaps fill at modeled open, not at a guaranteed floor. Liquidity may prevent actual fills.

## Detailed Outputs

Private source exports and per-sighting outcomes stay outside Git, under `C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/final/`.
- all-raw-source-outcomes.csv: every valid source sighting across five horizons, including duplicate/superseded source records.
- all-sighting-outcomes.csv: every canonical sighting with recorded time/entry and exact terminal close where available.
- entry-exclusions.csv, selection-dispositions.csv, helper-exclusions.json: invalid, overlap-suppressed and unresolved evidence.
- signal-outcomes.csv and strategy-summary.csv: nonoverlapping recorded-mark diagnostics.
- policy-outcomes.csv and policy-summary.csv: all six matched policies and all horizons, including chronological partitions.
- policy-exclusions.json, regime-summary.csv, monthly-cohorts.csv: missing windows and market-conditioned descriptions.

## Offline Reproduction

Use the recorded immutable input files; do not recollect and call the new snapshot identical.
```powershell
python backend/research/run_equity_study.py --input <normalized.json> --public-bars <public-daily-bars-complete.jsonl> --output <results-directory>
python backend/research/analyze_equity_study.py --results <results-directory> --bars <public-daily-bars-complete.jsonl> --report <RESULTS.md>
python backend/research/validate_equity_outputs.py --results <results-directory>
```

The planned 1/5/10-session inactivity sensitivity was not executed; the actual rule is horizon-specific nonoverlapping windows. Spread, dilution and intraday timing filters were not backtested because point-in-time inputs are unavailable.

Primary references: [Public historical bars](https://public.com/api/docs/resources/market-data/get-bars-v2-with-aggregation), [backtest overfitting](https://escholarship.org/uc/item/4w1110bb).
