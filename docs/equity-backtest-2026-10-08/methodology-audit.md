# Independent Equity Backtest Methodology Audit

Date: 2026-10-08. Scope: offline, read-only source and test inspection, plus synthetic checks. Source baseline: `f9822e4a50c4f2bb5751e6217f576316602ba403`; working tree contained unrelated frontend/document changes, which were left untouched. Read repository `AGENTS.md` before inspection. This document is the only requested file change.

## Conclusion and Evidence Boundary

**Not sufficient to establish an extensive, investable out-of-sample equity backtest.** The inspected components provide descriptive signal outcomes and conditional single-position replay, not a reconciled portfolio simulation. Several existing labels and warnings appropriately acknowledge that distinction. The defects below limit what may be inferred; they do not establish that an actual historical study was contaminated or profitable/unprofitable.

No historical dataset, immutable export, corporate-action ledger, actual fill evidence, or backtest result artifact was supplied or queried. Accordingly, no strategy return, alpha, Sharpe, drawdown, coverage percentage, effective sample size, or statistical significance is asserted here. Actual affected counts and performance impact remain unknown.

No production endpoints, providers, databases, orders, credentials, health probes, application startup, commits, pushes, or deployments were used. In particular, `research_lab.dashboard`, refresh functions, and integration/API tests were not executed. Its advertised read-only layer can call health probes (`research_lab.py:254-268`), and `refresh_snapshot` persists data (`326-347`); neither is appropriate for this task.

Line references below are repository-relative and refer to the inspected working-tree source. Prior remediation test counts were not reused as evidence for this audit.

## Findings

Severity denotes the consequence **if the affected output is used as extensive backtest evidence**, not an observed loss. H = blocks an investable/OOS interpretation; M = material diagnostic or reporting weakness.

### F1 [H] Outcome joins do not preserve observation identity

**Evidence:** `backend/services/pm_backtest.py:307-335` joins each scan recommendation to `signal_performance` by ticker/date/screener, then assigns the scan's `finished_at` to that daily outcome. `backend/services/pnl_tracker.py:176-208` retains the first daily entry/cycle while updating signal attributes on later sightings. `backend/services/research_lab.py:215-235` is weaker: it joins only ticker/date, without screener/cycle or deterministic disambiguation.

**Bounded failure:** two same-day observations at different prices can share the first observation's return; the lab can also attach another strategy's daily outcome. If multiple matching documents exist, an unsorted `find_one` supplies no methodological tie-break. Current PM row price and quality fields need not describe the outcome's entry. This is an identity problem before any episode deduplication.

**Required contract:** join a unique immutable observation ID plus security ID, strategy version, price basis, horizon and outcome version; retain entry/exit timestamps and sources. Use the immutable candidate ledger (`pnl_tracker.py:126-133`) rather than infer observation-level facts from the daily UI ledger. Quarantine ambiguous/legacy joins. Fixture: two cycles, two prices and two strategies on one date must produce four correctly attributable outcomes, or explicit exclusions.

### F2 [H] Historical reconstruction is not a frozen historical policy

**Evidence:** `research_lab.py:198-222` always reevaluates archived rows under today's PM evaluator and BALANCED/default equity. `pm_backtest.py:274-309` mixes persisted recommendations with reconstructed legacy recommendations under the run's ruleset. `pm_backtest.py:326-335` does not attach a frozen/reconstructed flag or ruleset to each research record, although `233` describes the method as frozen PM decisions.

**Bounded failure:** this is not proof that future market data enter the evaluator. It is proof that the outputs can mix original decisions with a retrospective policy. A rule chosen using later outcomes is not historical decision evidence. Conversely, overrides need not affect rows already carrying persisted recommendations, so that mixture is not a uniform counterfactual policy comparison either.

**Required contract:** separate original-decision diagnostics from uniform counterfactual replay. Freeze policy/feature versions and prove each input's publication and availability time was no later than decision time. All training transformations and calibrated weights must be fitted using training data only. Exclude unversioned/unverifiable historical features from the primary OOS cohort; report them separately.

### F3 [H] Inactivity deduplication does not yield independent outcome episodes

**Evidence:** `research_replay.py:78-115` resets after a gap greater than five calendar days since the last sighting, with no exit or label end. `pm_backtest.py:219-244` then summarizes seven-session returns using these episodes. Repeated suppressed sightings extend the activity period (`106-108`).

**Bounded failure:** observations six calendar days apart are separate episodes even when their seven-session outcome windows overlap. Continuous sightings can suppress a legitimate new trade after an actual exit. Cross-strategy/ticker-market correlation remains regardless of deduplication. The helper's module description (`4-5`) overstates statistical independence; the function's own approximation warning (`81-83`) is more accurate.

**Required contract:** exact duplicate removal first, then chronological nonoverlapping label windows or explicit entry/exit/reset lifecycle episodes. Predeclare session-based reentry rules and preserve suppressed IDs/reasons. `strategy_evidence.py:58-99` already provides disjoint ticker/strategy/horizon windows, but it is not proof of cross-lane independence (`259-264`). Do not pool different horizons as independent samples.

### F4 [M] Same-date episode ordering can retain a later signal

**Evidence:** `research_replay.py:17-30,87-94` reduces timestamps to dates and sorts same-date observations by observation ID, not time. `pm_backtest.py:326-335` supplies no observation ID, so equal-date ties depend on input order; scans were fetched newest first (`285`). Dates are not normalized to ET in this helper.

**Reproduced:** on the same UTC date, observation A at 15:00 is kept before observation Z at 14:00. No market data are involved. Late-UTC observations can also disagree with the ET dates used by the outcome ledger.

**Required contract:** sort by normalized aware timestamp, then immutable ID only for exact-time ties. Resolve exchange session labels separately. Never choose the best-return sighting. Fixture: permuting input order must retain the same earliest eligible observation across intraday, overnight and DST cases.

### F5 [H] Session and benchmark entry conventions are inconsistent

**Evidence:** `pnl_tracker.py:299-315,365-377` targets dates N exchange sessions after the ET observation date, while entries are recorded signal marks (`73-76`). `pm_backtest.py:186-205` uses SPY close on that date and N-session close, not a simultaneous signal-time SPY mark, and calls the entry/exit lookup without `exact=True`. `pricer.py:808-843` permits prior-date fallback and, when there is no prior available date, returns the earliest fetched date, potentially after the requested date. Separately, `strategy_evidence.py:38-48` counts the first close strictly after the observation, including today's close if still ahead; its SPY basis is explicitly prior completed close (`102-123`).

**Bounded failure:** identically named horizons are not interchangeable. An intraday equity entry compared to same-day SPY close includes different exposure intervals; prior-close SPY includes another interval again. A fallback price without its actual date can silently alter a benchmark interval. For after-close signals, a same-day close may precede signal availability and is not an eligible execution price.

**Required contract:** declare the entry event and horizon convention. For executable studies use the first eligible quote/open after signal availability plus latency; benchmark at the same timestamp/session and matched end. For mark diagnostics label the mark-to-close convention separately. Require exact session prices, completed-session checks and returned price timestamps. Keep prior-close excess as a diagnostic, never risk-adjusted alpha. Exchange calendars must cover holidays, early closes, year boundaries and DST.

### F6 [H] Corporate-action and price provenance are caller assumptions, not enforced evidence

**Evidence:** `path_replay.py:3-7` requires compatible split adjustment by documentation only. Its validation (`66-100`) accepts positive OHLC without action/basis metadata. `research_path_service.py:23-26,135-139` validates a source string and correctly labels it caller-supplied/unverified. Signal entries and outcomes are simple price ratios (`pnl_tracker.py:73-76,299-315`). Some provider primitives request `adjusted=true` (`pricer.py:446,468`); that parameter alone does not prove compatible entry and outcome bases or dividend treatment.

**Bounded failure:** a raw entry combined with split-adjusted history can create an artificial return or stop crossing. No inspected replay cashflow mechanism accounts for dividends, stock distributions, merger proceeds or delisting proceeds. Provider behavior and actual affected securities are unverified, so no corporate-action error rate is claimed.

**Required contract:** security-master identity (not reusable ticker alone), vendor/version/as-of timestamps, adjustment factors and action events. Maintain executable raw prices with share/stop/cash adjustments, or a documented consistently split-normalized equivalent. Treat dividends as dated cashflows for total-return NAV and match the benchmark's total-return basis. Never use dividend-adjusted extrema as executable stop prices. Missing terminal recovery/delisting history is unresolved risk, not an automatic zero or silent survivor exclusion. Test splits/reverse splits, dividends, ticker changes, mergers and delisting recoveries.

### F7 [H] Unassessed calendar coverage can still be labeled complete/available

**Evidence:** `path_replay.py:150-155` checks absent bars only when expected timestamps are supplied; `225-244` can emit metrics with coverage `complete` while missing-bar assessment is `not_assessed`. `research_path_service.py:59-78` bounds bars against period end but does not require final-bar coverage through that end or a nonempty authoritative expected grid.

**Reproduced:** the first supplied bar starts two days after entry; with explicit zero costs and a stop crossing, metrics are `available` despite the unobserved entry-to-bar interval. This proves acceptance of an unassessed gap, not that a real stop was missed.

**Required contract:** an independent exchange/session/cadence grid and authoritative interval ends are mandatory for primary path claims. Require complete entry-to-exit coverage (and exit-to-end marks for portfolio comparisons), distinguish halts from provider gaps, and reconcile partial boundary bars. Existing midbar-entry suppression is useful (`189-197`) but does not establish coverage when the containing bar is entirely absent. Do not count an empty expected list as assessed calendar coverage. Exclude unresolved gaps from path metrics and retain their counts.

### F8 [H] Split metadata does not make the returned replay out of sample

**Evidence:** `research_path_service.py:82-89` replays the full input before `118-126` computes train/holdout counts. `path_replay.py:248-285` partitions bars and optionally purges trailing training row counts; defaults are zero. It does not receive observation/feature availability or label interval ends, fit a model, or enforce an embargo. `research_lab.py:314-320` describes walk-forward testing as a pipeline stage, not an implemented evaluation loop in that dashboard.

**Reproduced:** a two-bar request reports one train bar and one holdout bar, but the returned exit occurs on the training bar. The split is descriptive metadata; the service does not claim a separately computed holdout return.

**Required contract:** derive splits at the decision/label level and evaluate only frozen policies on declared OOS entries. Purge exact overlapping label/lifecycle intervals across all symbols; row counts of supplied bars are not sufficient when sessions are missing or horizons vary. Apply the embargo and walk-forward protocol below. The existing immutable preregistration helper (`research_trials.py:11-43`) is useful bookkeeping, not proof of label purging or isolated fitting.

### F9 [H] Early-exit returns are compared with a different-length SPY investment

**Evidence:** `path_replay.py:219-244` reports returns only for realized full exits, with exit timestamp set to the containing bar's start. `research_path_service.py:90-116` puts that beside old-way/SPY entry-to-`period_end` marks. Open paths remain without realized returns (`path_replay.py:120,224-245`). Exact comparison timestamp validation does not align the early-exit cashflow trajectory.

**Bounded failure:** directly subtracting these metrics is not same-period strategy excess return. Excluding open paths selects on future exit behavior. A bar-start timestamp for an intrabar fill is not an observed execution time and cannot support exact exit-time benchmark matching.

**Required contract:** choose one estimand: (a) trade-return diagnostics with benchmark over the same evidenced exposure interval, or (b) common-end wealth with proceeds held in a predeclared cash instrument after exit, and open positions marked at the same end. Report open/censored paths explicitly. Use exit intervals rather than invented intrabar instants when only OHLC exists.

### F10 [H] OHLC path and terminal-clipping simulations are conditional, not fills

**Evidence:** `path_replay.py:170-221` evaluates two continuous O-L-H-C/O-H-L-C paths, selecting the lower immediate exit/close value (`201-203`), and fills descending segments at the active stop. Ambiguity is retained, but returns are not suppressed solely for ambiguity (`225`). `pm_backtest.py:83-122` instead infers stop/target/ratchet exits from terminal return alone and labels the method `close_only_terminal_return`.

**Bounded failure:** two OHLC orderings omit intra-segment jumps, halt/reopen behavior, spread and stop liquidity. Lower immediate exit/close selection is not a proven worst outcome across all possible future paths or executable fills. Terminal clipping cannot detect a stop hit and recovery before the final close, and clipping losses at a stop ignores gap loss. These are acknowledged research approximations, not an independent fill engine.

**Required contract:** exclude terminal clipping from path strategy evidence. Treat OHLC results as scenarios, preserving ambiguity rate and interval-valued outcomes; use finer post-entry bars/quotes for actionable timing. Model gap-through stops, latency and liquidity explicitly. Do not market a pessimistic scenario as an execution guarantee.

### F11 [H] Summed recommendation P&L is not a portfolio

**Evidence:** `pm_backtest.py:126-155,298-345` sums every recommendation's allocation and allocation-times-return. There is no carried cash, shared open-position state, dated settlement, mark-to-market NAV, or capital recycling in this loop. Pending allocations enter deployed capital (`129-134`) while only matured outcomes contribute P&L. `research_lab.py:272-273` averages category win rates and category average returns without sample weighting.

**Bounded failure:** concurrent/repeated allocations can reuse the same capital, while pending capital depresses the reported ratio. The ratio is neither time-weighted portfolio return nor a maturity-matched investment return. Equal-category averages need not equal pooled observation averages. No claim about actual overdeployment is possible without allocation timestamps/data.

**Required contract:** preserve these as recommendation diagnostics only. A portfolio requires an event-driven shared cash/position ledger, constraints, fills and daily NAV as defined below. For pooled signal diagnostics sum wins/returns over the eligible observations rather than average bucket summaries; disclose macro-averaged category statistics if intentionally used.

### F12 [H] Explicit costs cover only a narrow full-position fill scenario

**Evidence:** `path_replay.py:43-63,225-233` and `research_path_service.py:29-37` correctly require explicit per-side fees, slippage and half-spread. However, costs are fixed per-share/notional inputs without quantity, volume participation or impact modeling. `pm_backtest.py:94-113,126-139` and the lab's return buckets do not deduct costs. `strategy_evidence.py:103-107,174-183` deliberately reports net return unavailable.

**Bounded failure:** a valid cost payload proves arithmetic, not that the rates are plausible for a name/session/order size. It cannot establish capacity or an executable net edge. Applying a common dollar-per-share fee to differently priced equity and SPY positions is not inherently wrong, but needs declared quantities and fee schedule justification. Gross legacy summaries must not be compared as net portfolio results.

**Required contract:** freeze dated cost schedules and base/stress scenarios before holdout; include commissions/minimums, per-side spread, latency/slippage, impact/participation, relevant transaction fees and financing/borrow where applicable. Require liquidity/volume evidence, reject trades beyond predeclared participation limits, and test gap/illiquid sessions. Explicit zero costs are sensitivity cases, not the primary investable result unless independently justified. No cost values are estimated in this audit.

### F13 [M] Mean CI and missingness accounting are insufficient for inference

**Evidence:** `research_replay.py:135-163` computes `1.96 * sample stdev / sqrt(n)` on raw row returns. It does not account for repeated windows, shared dates/tickers or strategy selection, nor validate finiteness. Benchmark statistics use only paired rows but return means use all valid-return rows; paired count/paired return mean are not emitted. `pm_backtest.py:219-245` discards nonqualified episodes from the qualified summary without a dedicated episode-level exclusion ledger; raw quality reason counts are a different denominator.

**Reproduced:** `[NaN, 1]` returns raised `AttributeError` in the available Python runtime instead of producing an invalid-value exclusion. This is a synthetic malformed-input result, not evidence of nonfinite persisted market data.

**Required contract:** reject nonfinite/bool prices and outcomes at all boundaries. Track mutually exclusive eligibility statuses plus separate multi-reason flags; distinguish immature, invalid, missing outcome, missing benchmark and quality exclusions. Emit paired N and paired strategy/benchmark means so paired excess reconciles. Replace IID-looking significance claims with the dependence-aware uncertainty plan below; raw percentiles remain descriptive distribution summaries, not confidence bounds.

### F14 [H] Curated congressional replay uses private transaction date and calendar-day labels

**Evidence:** `backtest.py:75-108` enters selected curated purchases on transaction date and calculates 7/30/90 calendar-day offsets, fetching a close on or after each date. Stored labels are `return_7d/30d/90d` (`113-131`), also used by session-based forward tracking. The function stores no disclosure/availability timestamp. `backtest_summary` separates forward/synthetic cohorts (`140-170`), which must be preserved.

**Bounded failure:** transaction-date entry is not a tradable public congressional-disclosure signal without evidence the transaction was known then. Calendar offsets are not exchange-session horizons. The curated sample is not an exhaustive historical eligible universe; its survivorship/selection impact cannot be quantified from this inspection.

**Required contract:** keep this synthetic dataset separate from scanner and OOS evidence. Public-signal research enters only after independently evidenced filing publication/ingestion plus latency, and declares a session horizon. Retain all eligible disclosures, amendments, unavailable securities and exclusions; do not retrospectively choose purchases/names using outcome success.

## Three Distinct Dataset Definitions

### 1. Signal-Level Diagnostic

One immutable strategy observation per `(security_id, strategy_id, strategy_version, cycle_id, observed_at)`, with an exact source observation ID and timestamped feature/price evidence. Remove true duplicate ingestion records only; keep legitimate repeats. Multi-tag associations may appear in multiple descriptive groups but do not multiply the number of underlying observations.

Define mark return at one preregistered horizon as `100 * (terminal_mark / entry_mark - 1)` on a compatible price basis, with separately accounted distributions when reporting total return. A mark is not a fill. Predeclare whether horizon means N closes strictly after availability or N full subsequent sessions; do not combine conventions. Report by strategy/version/horizon/cohort date, with raw N, unique securities/dates and matched benchmark N. No portfolio CAGR, portfolio Sharpe or deployable dollar P&L from this panel.

### 2. Nonoverlapping Episode Diagnostic

Start from that same frozen eligible observation universe; select the earliest eligible observation using only then-known facts. One episode per security/strategy/version has a declared entry and label end or explicit lifecycle exit/reset. Reentry cannot overlap the preceding outcome/lifecycle window; any additional cooldown is measured in declared exchange sessions. Exact-time tie-break is deterministic and outcome-independent. Preserve every suppressed observation's parent episode and reason.

Deduplicate before knowing outcomes; do not replace an unresolved or losing first entry with a later complete/winning entry. Quality-based eligibility must be available at entry. Different horizon panels have separate episode boundaries. If a position remains open, it does not reset merely because scans stop. Version changes do not make overlapping exposures statistically independent. Call these nonoverlapping episodes, not IID samples; cluster by security and shared market time for inference. Retain activity-gap dedup only as a separately labeled sensitivity analysis.

### 3. Real Portfolio Simulation

One chronological event stream with starting cash, positions, orders/fills, liabilities, corporate actions and common-clock NAV. Each decision is processed after signal availability and declared latency. Enforce strategy competition for the same security, portfolio ownership, available cash/buying power, position/gross/net/sector concentration, volume/participation and tradability constraints. Define fractional shares, rounding, partial fills, rejected orders, settlement and reentry explicitly. A filled allocation consumes capital until it is sold and settlement/reuse policy permits reuse.

NAV equals cash plus marked holdings minus liabilities, including accrued/paid fees, financing and distribution cashflows. Reconcile daily cash, quantities and equity; mark all open holdings, not only exited winners/losers. Declare missing-mark/halt valuation policy and quarantine periods whose NAV cannot be supported. Benchmark starts with identical capital and evaluation dates; benchmark transaction costs, total returns and cash treatment are declared. After strategy exit, proceeds earn the fixed declared cash return through the common reporting end, not an invented reinvestment.

Compute daily portfolio returns from reconciled NAV, not averaged trade returns. CAGR, volatility, maximum drawdown, Sharpe and turnover require that full series and declared annualization/risk-free conventions. Trade win rate and average episode return remain separate diagnostics. Report gross and net series, exposure, rejected opportunities and capacity sensitivity. None of these portfolio results is currently established by the inspected code or by this audit.

## Holdout and Walk-Forward Protocol

1. **Freeze the study manifest before evaluation.** Record hypothesis, eligible point-in-time universe, security master, feature availability rules, source snapshots/hashes, decision clock, strategy/config hash, horizon/exit policy, costs, benchmark, primary metric, permitted search space, trial ledger and exclusions. Archive both original-policy and counterfactual cohorts with distinct labels. No date window is declared a holdout merely because a bar split helper returns it.
2. **Use chronological development and one untouched final holdout.** Development contains rolling/expanding training and successive validation/test blocks. Final dates and embargo are fixed before inspecting final outcomes. For this audit no concrete dates, training length or embargo count can be justified without dataset availability and the longest label/feature horizon. Previously inspected retrospective periods cannot be called prospectively preregistered; label retrospective OOS honestly.
3. **Purge using exact information intervals.** Each sample records feature dependency times, decision/availability time and label/position interval. Before a validation/test boundary, remove any training label whose end reaches or crosses that boundary, including still-open labels. Do this across the entire cross-section on one common clock, not independently by ticker. No label unavailable at fit time may enter training, even if its decision is old.
4. **Embargo before reuse across folds.** A test/validation decision interval and its outcome-dependency span are unavailable for training until the protected information has matured and the preregistered session embargo has elapsed. In any scheme permitting later observations in training, exclude post-test samples whose feature/label information overlaps that protected span. For strictly forward-only fits there are no future-side training samples, but a delayed-reuse rule still applies when prior test periods enter later expanding training. Predeclare a conservative session gap covering maximum forward label/lifecycle horizon and any shared rolling-information dependency; explicit overlap purging is mandatory even when a scalar gap is used.
5. **Fit only inside the fold.** Imputation, standardization, clipping, feature selection, thresholds, weights and hyperparameters are trained on purged training data. Tune on development validation only. Log every variant, including failed variants. Freeze the selected policy before the next OOS block. Calendar/source transformations without fitted parameters still require correct as-of data.
6. **Evaluate disjoint OOS blocks once.** Save fold-specific model/config hash, fit cutoff, entry universe, exclusions, predictions, fills/marks and outcome timestamps. Concatenate each decision/NAV period once. Predeclare portfolio continuity: carry positions with the policy governing their exits, or reset/liquidate at boundaries with explicit costs. Do not silently reset cash or discard boundary trades.
7. **Open the final holdout once after label maturity.** Run the locked winner and predeclared benchmark/sensitivities; do not retune or change exclusions afterward. A failed holdout is a result, not a new training opportunity. Future changes require a new trial and new untouched evidence. A preregistration record alone cannot enforce this workflow.

## Uncertainty and Selection

- Show raw signals, exact duplicates, eligible observations, nonoverlapping episodes, benchmark pairs, distinct securities, cohort sessions, trades and NAV days as separate counts. There is no effective independent N available without the data.
- For signal/episode means and paired excess, resample common calendar-time blocks that preserve the entire cross-section and overlapping outcome span; account for persistent within-security dependence using a justified clustered/hierarchical design or appropriate robust inference. Choose block length from the maximum dependence horizon plus diagnostics, and freeze the rule before final holdout. Do not bootstrap individual rows independently.
- For portfolio metrics, resample the reconciled daily net-return series in sufficiently long contiguous blocks; disclose the loss of realism of resampled drawdown paths. Report empirical intervals and dispersion, not guaranteed future bounds. Suppress significance when there are too few independent time blocks or clusters; retain descriptive results with that limitation.
- Report median, tail quantiles, loss frequency, cohort/regime dispersion and leave-one-security/time-block sensitivity. Report paired benchmark uncertainty on the identical eligible paired sample. Excess over SPY is not factor-adjusted alpha; alpha requires a declared factor model and dependence-robust residual inference.
- Account for the entire strategy/threshold/universe/cost/horizon search ledger, not only the winning configuration. Predeclare multiplicity control or a documented selection-adjusted test; do not attach naive 95% CIs to a selected winner as confirmation. Distinguish exploratory rankings from confirmatory final-holdout evidence.

## Missing Data and Exclusion Rules

Primary eligibility is frozen independently of outcome sign. Keep an append-only per-observation ledger with stage, exclusive primary status, all reason flags, source IDs, affected interval and reproducible rule version. Reconcile counts at every step, by strategy/session/security class, and disclose database/provider truncation or query caps. A latest-N-scans dashboard is not an exhaustive historical universe (`research_lab.py:198`, `pm_backtest.py:285`).

| Condition | Primary treatment |
| --- | --- |
| Outcome has not matured by cutoff | Pending/right-censored; excluded from matured mean, retained in universe and open-position NAV where supported. |
| Missing/invalid timestamp, identity, feature availability or nonfinite price | Ineligible/unverified; never infer a date or silently coerce to zero. |
| Stale quote, unknown source or unsupported adjustment basis | Unverified-mark diagnostic only; excluded from executable primary cohort. |
| Missing expected post-entry OHLC/quotes, duplicate/conflicting/overlapping bars | Path unavailable until repaired with versioned evidence; no invented bars or nearest-date substitution. |
| Missing exact endpoint after maturity | Missing outcome, separate from pending; do not substitute a longer horizon or newer available close. |
| Missing simultaneous benchmark | Gross-only diagnostic; excluded from paired excess, not all gross statistics. |
| Missing costs or liquidity inputs | Net/capacity unavailable; never assumed zero. Explicit zero-cost scenario remains separately labeled. |
| Halt, delisting, merger or missing terminal recovery | Corporate-action/unresolved-terminal status with retained exposure; disclose sensitivity/risk bounds only when grounded in actual recoveries or explicitly stated assumptions. |
| Open path with no realized exit | Open/censored trade; include common-end supported mark for wealth evaluation rather than selecting only closed paths. |
| Provider/query budget or truncated extraction | Partial study; preserve deferred observations and completeness checks before full-universe claims. |

Missingness can depend on illiquidity, distress or delisting. Therefore complete-case estimates describe that observed subset, not automatically the full eligible universe. Compare eligibility/missingness by pre-entry covariates and disclose how exclusions might select survivors. Do not invent missing returns or numerical uncertainty bounds without defensible data/assumptions.

## Verification Performed and Gaps

Executed only the pure synchronous path suite, disabling pytest configuration, conftest loading, automatic plugins, bytecode writes and cache output:

```text
python -B -c "import os,sys; os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'; sys.path.insert(0,'backend'); import pytest; raise SystemExit(pytest.main(['-c','NUL','--noconftest','-p','no:cacheprovider','-q','tests/test_path_replay.py']))"
82 passed in 0.48s
```

That suite checks strict stop arming, stop touches/gaps, two-path ambiguity, explicit cost arithmetic, invalid bars, midbar entry, caller-supplied missing-bar grids, split boundaries and facade validation. It does not prove historical data completeness, executable fills, corporate-action reconciliation, independent episodes or OOS policy fitting. Its bar fixture advances calendar days with six-hour intervals (`tests/test_path_replay.py:19-22`), not a verified market-calendar dataset.

Five additional in-memory synthetic probes imported only `research_replay`, `path_replay` and `research_path_service`. Four asserted and reproduced F3/F4/F7/F8 behaviors; the fifth caught and reported F13's nonfinite-input exception. Their results appear under the corresponding findings. No probe generated historical performance data or changed source files.

Inspected, but did not execute, `backend/tests/test_research_replay.py` (raw/episode separation, frozen cycle ledger and fixed seven-day research horizon), `backend/tests/test_strategy_evidence.py` (session endpoints, nonoverlapping windows, explicit benchmark mismatch, missing data and preregistration), and relevant mocked tests in `backend/tests/test_claude_audit_controls.py:182-211` (holidays/early close, completed targets and exact-date missing prices). These are useful safeguards, not substitutes for the missing extensive study.

Before a result may be called a clean extensive backtest, require offline adversarial fixtures for F1-F14 plus an immutable data manifest, exclusion/count reconciliation, actual point-in-time coverage, action-adjusted quantities/cash, matched benchmarks, locked fold/embargo evidence, plausible base/stress costs and reconciled portfolio NAV. Until those artifacts exist, report signal diagnostics and conditional scenarios only; do not infer readiness, promotion, live execution quality or profitability.
