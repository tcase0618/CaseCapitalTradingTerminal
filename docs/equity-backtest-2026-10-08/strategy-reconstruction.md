# Equity Strategy Reconstruction and Pre-Registration

## Scope and Evidence Boundary

- Reconstruction date: 2026-10-08. Registration reference time: 2026-10-08 20:15:49 UTC (16:15:49 America/New_York), obtained before writing and before inspecting returns.
- Repository: `C:\Users\tcase\OneDrive\Documents\Case Cap\CaseCapitalTradingTerminal-audit`.
- Verified branch/upstream: `Claude-Gpt-audit...origin/Claude-Gpt-audit`; inspected HEAD: `f9822e4a50c4f2bb5751e6217f576316602ba403`.
- Read `AGENTS.md` first. Only this report is written. Existing dirty frontend edits and untracked files are not part of the strategy snapshot and must remain untouched.
- Evidence is local source and local Git metadata, not verified deployed behavior. No environment files, credential values, production endpoints, databases, orders, return datasets, or existing performance reports were accessed. No backtest was run.
- EQUITY means long stock/fractional-stock exposure. Option discovery overlaps are catalogued but option premiums, puts, prediction markets, and options execution are excluded from equity returns. Research and manual paper records never establish executed equity trades.
- All source paths below are relative to this repository. Line anchors refer to the inspected working tree; the named functions are the durable anchors.

## Routing Map

`terminal_cycle._run_full_terminal_scan` starts Core, dedicated Lottery, pharma calendar, and pharma shock discovery; passes the same Lottery result to specialist screeners; merges PM-routable, non-read-only rows into one PM recommendation per ticker; records PM memory before execution; creates candidate-ledger and shadow-contract evidence; optionally submits approved equity rows through Public. Full-cycle artifacts are subsequently attached to the original Core scan document.

Sources: `backend/services/terminal_cycle.py:167`, `backend/services/portfolio_manager.py:1035`, `backend/services/strategy_screeners.py:692`.

Public is the sole current equity execution path. Alpaca is not an equity fallback when Public is unavailable. Execution requires both the full-cycle execution flag and Public's enabled, non-research-only configuration, then separate safety gates. The existence of an implementation or default does not prove its runtime flag is enabled.

## Discovery and Strategy Inventory

| Lane or Source | Actual Input/Qualification | Current Equity Authority |
|---|---|---|
| Core insider cluster | OpenInsider cluster-buy records; `insider_cluster_buy`, counts, values, filing metadata | Core input, not a standalone entry |
| Core short interest | Finviz high-short universe (collector default minimum 10%); `high_short_interest` | Core input; no automatic stock short |
| Core upcoming earnings | Finviz/Yahoo calendars; `upcoming_earnings` | Core input, unlike independent research-only earnings screener |
| Government contract surge | `CONTRACT_SURGE`: trailing 30-day awards >= $10M and >= 1.4 times prior 30-day equivalent, positive prior baseline | Core input |
| Government concentration | Award >= $20M creates provisional concentration; scanner finalizes `CONCENTRATION_WIN` only with market cap < $2B | Core input; provisional label excluded |
| Government agency stack | `MOMENTUM_STACK`: >= 3 distinct agencies and >= $20M over 30 days | Core input |
| Government new winner/budget | `NEW_WINNER`, `BUDGET_SURGE` from recipient/budget detection | Core input; retain underlying award/publication evidence |
| Congressional buying | `CONGRESSIONAL_BUY`, buyer counts, committee match, weight | Core input, based on disclosed information availability, not private transaction date |
| Core flow/squeeze overlay | `UNUSUAL_FLOW`, `CALL_SWEEP`, options analytics and squeeze score | Adds evidence to surviving Core rows; not an independent equity order |
| Core PEAD overlay | Active earnings PEAD including `PEAD_BULLISH_CONFIRMED` and `PEAD_BEARISH_CONFIRMED` | Added to Core signals; code counts signal strings without a bullish-only filter |
| Dark Horse | FINRA short-volume/context evaluation attached to existing Core tickers | Enrichment/badge/conviction, not a separately merged stock-entry lane |
| X-Factor | Attention/social/news/trending/unusual-options evaluation plus discoveries outside current Core universe | Discovery ledger/research; out-of-universe discoveries are not automatically inserted into PM Core rows |
| Narrative Lock / Max Conviction | Post-scan confluence; Narrative Lock adds 20 to signal score and elevates displayed Lottery tier; Top 3 marked max conviction | Core enrichment, not independent execution authority |
| Lottery broad discovery | Five Finviz screens: under-$20 with average volume >100K; under-$20 RVOL >1.5; under-$20 short >10%; small-and-under cap/under-$20/average volume >100K; under-$20 `ta_change_u5` (preserve literal provider filter) | Dedicated universe; source label is not independent evidence |
| Lottery additional discovery | Separate high-short screen, attention/trending/unusual-options screen, pharma catalyst screen | Merged by ticker before scoring; top 220 selected after full-universe scoring |
| `lottery_day2_continuation` | MOMENTUM and (VOLUME or ROTATION) | PM eligible, but Public Day-2 automation defaults OFF |
| `lottery_supernova` | RVOL component >=9 or ROTATION component >=6, plus momentum/short/attention | PM eligible subject to common gates |
| `lottery_red_green` | STRUCTURE component >=4 and momentum or rotation | PM eligible; label does not itself establish an observed intraday red-to-green reclaim |
| `lottery_catalyst_runner` | CATALYST evidence family present | PM eligible subject to common gates |
| `lottery_serial_runner` | Prior-runner events, RUNNER trigger text, or raw RVOL >=8 | PM eligible subject to common gates |
| `lottery_signal_confluence` | Fallback when qualified evidence has no named fit | PM candidate; without source target there is no lane proxy, so often not routable |
| `lottery_dilution_read` | Active dilution flag/penalty | Read-only; not a veto or executable bearish strategy |
| `pharma_calendar` | PDUFA/catalyst calendar within 90 days; dynamic lane = event type or FDA_CALENDAR | Specialist PM route only with usable price, target, stop and sufficient evidence |
| `pharma_core_overlap` | Pharma/PDUFA evidence on Core rows | Read-only overlay; Core itself may still qualify |
| Pharma binary discretionary PM | `pharma.build_pm_candidate` and `route_to_pm`: PHARMA_PDUFA/BINARY_FDA_CATALYST plus phase-3, insider, short, cheap-IV evidence | Persisted `PM_DISCRETION_NO_PHARMA_EXECUTION`; separate from full-cycle calendar adapter |
| Pharma catalyst shock | News-based shock discovery, separately refreshed/persisted | Research evidence; no direct inclusion as a separate executable PM lane in the inspected full cycle |
| `earnings_calendar`, `earnings_core_overlap` | Calendar timing/EARNINGS and Core overlap | Both independent screeners read-only; do not alter PM routing |
| Earnings discretionary PM | Setup >=58 by default, TRADEABLE rating, or active PEAD; `build_pm_candidate`/`route_week_to_pm` | `PM_DISCRETION_NO_EARNINGS_EXECUTION`, not automatic Public entry |
| `sec_filings` | Recent 7-day filings, first 120 rows; BULLISH_FILING, BEARISH_FILING, NEUTRAL_FILING | All read-only; bearish filings cannot veto PM |
| Options specialist overlaps | `options_tactical_momentum_call`, `options_breakout_call`, `options_leaps_trend`, `options_squeeze_call`, `options_event_defined_risk` | Option-intent candidates, not an equity execution fallback; Public rejects explicit OPTION route/preference |
| Accountant, Case Court, macro/georisk, QC, Kronos | Research/oversight/context or quality/safety controls | No independent equity discovery-to-order authority established by this routing graph; Accountant attachment cannot block or authorize orders, Case Court excluded |
| Lottery manual/paper league | `issue_ticket`, default V1_DAY2_CONTINUATION, manual play/track records | Fenced synthetic/manual book, not Public execution |

Inventory anchors: `backend/services/scanner.py:46`, `:90`, `:143`, `:642`; `scrapers.py:422`; `usaspending.py:315`; `lottery.py:28`, `:53`, `:96`, `:274`, `:685`; `strategy_screeners.py:18`, `:232`, `:296`, `:518`, `:550`, `:603`; `earnings_engine.py:1169`, `:1247`; `pharma.py:994`, `:1081`, `:1650`; `candidate_ledger.py` (`build_from_scan`). All these files are under `backend/services/`.

### Important Discovery Semantics

Core requires at least two unique signal strings BEFORE flow, PEAD and post-scan overlays. Single-letter tickers require company identity. Surviving rows get Python risk/targets/squeeze/time-target calculations, batched Claude analysis, learned signal weights and trade scores. Persist these observations rather than regenerating historical Claude output or querying current fundamentals.

Lottery independent families are MOMENTUM, VOLUME, ROTATION, CATALYST, SHORT, ATTENTION, STRUCTURE. Positive component points or qualifying triggers can establish a family; raw source counts cannot. Float-tier and universe points contribute to native score but not independent families. Native eligibility is score >=60 and not halted; specialist eligibility instead accepts >=2 families and either that eligibility flag or score >=35 with positive momentum/volume/rotation/catalyst/short component. This second path must not be silently replaced by a universal 60 cutoff.

Lottery native points: surge up to20, RVOL15, float15, rotation15, catalyst contribution, short10, attention8, universe up to5, structure10; penalties include spread >1% (10), active halt (15), failed breakout (5), reverse split <30 days (10), with further enrichment/dilution evidence. Float can be shares-outstanding proxy and must retain confidence label. Strategy fits use component points, not always raw RVOL or raw turnover.

Specialist rows scale native score to `signal_score=score/10`, `trade_score=score/2.5`, build a strategy case/confidence, then compute stop with stop_engine and generic 30-day hold input. A structural target is preferred; Lottery fallback uplifts are Day2 40%, Supernova60%, RedGreen20%, Catalyst25%, Serial35%. These are explicitly `thesis_lane_proxy_pending_validation`, not measured upside. Pharma and SIGNAL_CONFLUENCE do not have this lane fallback. Missing price/target/stop makes a row research-only.

Case score/confidence are not calibrated win probabilities. `strategy_ideology.case_score` combines native score, evidence count, volume/catalyst/structure and missing/stale/dilution penalties. Lottery learning can change score/confidence after >=10 samples; use only the configuration available then. `backend/services/strategy_ideology.py:166`, `:224`.

## Actual PM Entry Recommendation Rules

Source: `backend/services/portfolio_manager.py:16`, `:67`, `:146`, `:185`, `:236`, `:258`, `:318`, `:494`, `:700`, `:852`, `:979`; configurable overrides: `backend/services/pm_rules.py`.

### Merge and Score

One row per uppercase ticker. Core remains base when present; specialist views/sources append; merged signals union, signal/trade scores take maximum, highest strategy-case score supplies case/confidence. Price/sector/targets/stop generally fill missing base values rather than replace existing ones. A Lottery view makes the row Lottery for profile and uncapped plan selection even with a Core base. `_signals` prefers `evidence_signals` over `signals`; therefore the visible union can differ from the counted evidence on specialist-first rows. Reproduce this literal precedence for baseline, do not silently repair it in replay. Do not count one fill once for every non-exclusive lane.

Price = recorded row price. Target precedence: top-level blended, nested blended, top-level high, nested high. Stop precedence: positive top-level stop_loss, nested risk.stop_loss, otherwise 88% of price. Upside=(target/price-1)*100; downside=max(0,(1-stop/price)*100); RR=max(0,upside/downside), zero when denominator is nonpositive.

PM score (clamped 0..100, rounded to 0.1):

```text
min(30, 7.5 * unique_signal_count)
+ min(22, 0.55 * trade_score)
+ min(14, 1.4 * signal_score)
+ min(10, max(0, learning_score))
+ min(8, 0.08 * squeeze.score)
+ min(16, 5 * RR)
+ min(10, 0.10 * case_score) if nonzero
+ min(6, 6 * confidence) if nonzero
- min(25, 0.12 * risk.score)
- (max(0, (0.45-confidence)*12) if confidence nonzero else 2)
```

| Profile | Accumulate Score/RR | Starter Score/RR | Watch Score | Name % / Risk % / Gross % / Sector % |
|---|---|---|---|---|
| Core RISK_OFF | 82 / 2.4 | 72 / 1.9 | 55 | 2.5 / 0.4 / 10 / 18 |
| Core CONSERVATIVE | 76 / 2.1 | 64 / 1.6 | 50 | 5 / 0.8 / 22 / 22 |
| Core BALANCED | 70 / 1.8 | 58 / 1.3 | 45 | 8 / 1.25 / 35 / 25 |
| Core AGGRESSIVE | 64 / 1.5 | 52 / 1.1 | 40 | 12 / 1.8 / 55 / 30 |
| Lottery RISK_OFF | 84 / 2.4 | 76 / 1.9 | 55 | 2 / 0.3 / 8 / 10 |
| Lottery CONSERVATIVE | 78 / 2.0 | 58 / 1.3 | 40 | 3.5 / 0.5 / 12 / 15 |
| Lottery BALANCED | 72 / 1.8 | 52 / 1.15 | 38 | 5 / 0.6 / 15 / 18 |
| Lottery AGGRESSIVE | 66 / 1.55 | 48 / 1.05 | 35 | 7 / 0.8 / 20 / 20 |

ACCUMULATE additionally needs >=3 unique signals and risk score <75. STARTER needs >=2 signals, with no parallel risk<75 test. WATCH/REJECT receive zero sizing. Ruleset overrides are authoritative, including over Lottery defaults; do not assume the active persisted ruleset equals the static table.

### Regime and Allocation

AUTO selects RISK_OFF for red/doomsday/unknown or halt_new_entries, CONSERVATIVE for yellow/downtrend, otherwise BALANCED; AGGRESSIVE requires explicit selection. Regime implementation uses ordered SPY closes and an EMA with alpha `2/(min(200,n)+1)`, not necessarily 200 full sessions; last-close change <=-2% gives red, configured crash threshold gives doomsday, below EMA gives downtrend. VIX is absent here. Preserve the actual snapshot and data-as-of; avoid replacing it with later daily closes. `backend/services/trade_floor.py:769`.

Unknown/doomsday downgrade new sizing to WATCH. Red permits only insider/contract/gov/PEAD anchor evidence with score>=72 and RR>=1.7, at STARTER posture. Downtrend allows anchored action unchanged; unanchored Lottery needs starter threshold+10 and RR>=1.8; other unanchored candidates need score>=84 and RR>=2.2, at STARTER. Anchor matching includes substrings and nonempty insider/gov/contracts/active PEAD objects, not validated bullish direction.

Theoretical size = minimum of risk-budget shares and name-cap shares. STARTER scales both budgets by0.55. Risk budget multiplies by score factor `0.65+min(0.35,max(0,score-58)/42)`, case factor `clamp(0.75+case/200,0.65,1.18)` when nonzero, and confidence factor `clamp(0.45+confidence,0.48,1.12)` when nonzero. Name cap multiplies by min(1.15,case_factor*confidence_factor); risk/share=max(0.01,price-stop). Shares rounded4 decimals, dollars2.

Allocation ordering favors Lottery first, then ACCUMULATE, STARTER, score (stable ties). Outer account gross/sector caps use broad Core profile, accumulating this recommendation docket rather than full held exposure. The named Lottery gross/sector defaults are not independently enforced by a separate sleeve-total pass in evaluate_rows. This is an implementation caveat, not a recommendation to change it.

Public-book constraints then reject already-held names or unavailable/insufficient cash (<$6); preserve `pre_execution_action`/allocation and demote final action to WATCH. Otherwise each stock approval is normalized to $6, with cash decremented per row. This normalization occurs AFTER theoretical gross/sector clipping and is not followed by a second cap pass; an exact replay must retain that order. Manual equity override skips these Public-book constraints. Fallback equity can be $1,000 and is not verified account equity.

### Recommendation Is Not Entry

Public `_allocation` maps positive requested size to nearest of $2/$4/$6; normal Public PM outputs become $6. CORE-session fractional notional minimum is $5; outside CORE only whole shares within allocation are allowed. Actual legal session/asset eligibility remains broker preflight-dependent; weekday clock classification is not a complete holiday calendar.

Entry requires reconciled health, safety permission, account buy permission, known cash, available halt feed/no active symbol halt, execution/QC/truth/kill-list gates, daily-loss check, eligible route, valid stop and fresh two-sided Public quote. Explicit route or preferred route OPTION is rejected. A Lottery proxy target is allowed only with enabled ratchet plan; other proxy-target entries are rejected. Existing positions are rejected, not automatically added to.

Public limits rest at rounded bid/ask midpoint, NOT scanner mark, ask, or entry_high. Default spread maximum300bps (configurable), quote maximum age90s (configurable). Immediately before submission quote is refreshed: unavailable/stale quote or absolute price movement >1% rejects. Stop is floored at90% of initial quoted price and must remain strictly below final limit. CORE uses amount, outside CORE floors allocation/final price to integer shares. Public does not enforce recorded entry_low/entry_high here; the legacy Alpaca path does.

Day-2 attribution anywhere in the merged lanes can block the entire ticker unless `LOTTERY_DAY2_LIVE_AUTOMATION_ENABLED` is enabled. If enabled, requires MOMENTUM+VOLUME+one of ROTATION/CATALYST/SHORT/ATTENTION/STRUCTURE and PM score>=60 default (configurable55..95). Overlapping Supernova/Day2 labels are not separately executable exemptions.

Sources: `backend/services/public_execution.py:102`, `:201`, `:550`, `:724`, `:1074`, `:1500`; `backend/services/execution_gate.py:170`, `backend/services/safety.py:215`, `backend/services/terminal_cycle.py:245`. Pending buys have default900s TTL during reconciliation; submission/acceptance/partial fill/full fill/cancellation/unknown must remain distinct. No fill can be inferred from a submitted count.

## Current Hold, Exit and Ratchet Rules

### Public Full-Position Floors (Current Source Baseline)

`backend/services/pm_ratchet.py:29` defines `uncapped_floors_v2`, based on broker fill price BEFORE fees. Set peak=max(previous observed peak, entry, fresh executable bid mark). Stop=max(previous current_stop/pm_active_stop/stop_price, 0.90*entry, armed floor). Stops never fall; the stored plan's anchor and RUNNER/CORE/TACTICAL ladder do not set Public profit floors.

Strict arming boundaries:

| Observed Peak Gain | Armed Price Gain Floor |
|---|---|
| <=5% | None; initial/tighter previous stop only |
| >5% through10% | +5% |
| >10% through20% | +10% |
| >20% through30% | +20% |
| >30% through40% | +25% |
| >40% through50% | +35% |
| Higher | Highest strictly crossed multiple of10, minus5 percentage points |

Exactly5%,10%,20%,30%,40% does NOT arm that new milestone; inclusive stop touch does trigger exit. Formula above20: milestone=max(20,(ceil(round(gain,10)/10)-1)*10); floor20 at milestone20, otherwise milestone-5. Dollar stop rounded6 decimals. Initial 10% distance and floors do not guarantee net realized loss/profit limits.

Public monitor processes FILLED/PARTIALLY_FILLED open quantities. On fresh bid<=active stop, fetch broker quantity and submit DAY limit sell at fresh bid, not a stop-price guaranteed fill. Wide-spread exception applies to emergency SELL; still needs valid fresh two-sided quote. Existing pending legacy phase order or active/unverified emergency order delays another submission. Timeout becomes SUBMIT_UNKNOWN and is reconciled; do not simulate retry as a second fill. Outside CORE, whole-share exits leave fractional residual for a later CORE session. Resting broker stop/bracket capability is not presumed; the normal fallback is terminal-monitored protection.

Critical scheduler order is reconciliation -> protective exits -> ratchet, nominally each minute Sunday20:00 through Friday19:59 ET; capital-management/scorecard/rebalance runs separately every5 minutes. A subscription may also update ratchets. Do not raise the stop from a bar high and retrospectively trigger a sale at an earlier low. REST exits examine old stop before that tick's ratchet. Scheduled cadence is not evidence of observed uptime or quote delivery.

There is NO enforced Public lifecycle session time-stop or generic hold-window expiry in the inspected protective-exit implementation. No new Public partial-profit submissions: `_public_phase_plan` disabled, `process_public_phase_exits` retired except reconciliation of previously pending phase orders. Positions otherwise hold until protective fill, explicitly authorized PM/operator exit, broker event, or enabled discretionary rebalance. End-of-backtest marks are unrealized, not invented exits.

Sources: `backend/services/pm_ratchet.py:121`, `:155`, `:295`; `backend/services/public_execution.py:1127`, `:2262`, `:2341`, `:2461`; `backend/services/scheduler.py:681`, `:945`.

### Discretionary Hold/Replace Layer

PM opportunity-cost review is advisory. Holding edge=clamp(current PM score or45 + clamp(unrealized_pct*0.65,-18,12),0,100). Deep loser <=-7% and edge<48 gets EXIT_REVIEW; <=-3% and edge<55 gets TRIM_REVIEW. Replacement review requires edge gap>=18 and no protected winner (pnl>=8% and holding edge>=50). The displayed winner gap28 does not by itself authorize a winner sale.

`pm_portfolio` builds another advisory scorecard: 35% thesis health,20% path quality,20% risk quality,10% portfolio fit,15% opportunity quality. EXIT_REVIEW at pnl<=-7% and (PM<=35 or no current recommendation); REPLACE_REVIEW at pnl<=-3%, replacement gap>=18, overall<55; TRIM_REVIEW at pnl<=-3%/overall<55; RATCHET_REVIEW at pnl>=8%/overall>=75. These labels do not all have executable actions.

`pm_rebalance` can execute only when Public and `PUBLIC_PM_REBALANCE_ENABLED` are enabled (default false), using fresh plan (default maximum4h), current holdings and scorecard. EXIT_REVIEW scorecard yields EXIT_TO_CASH without needing replacement, before protected-winner handling. Otherwise eligible replacement must be unheld, pre-execution ACCUMULATE/STARTER, non-OPTION, nonproxy, positive stop. Candidate edge=PM+min(6,case*0.04)+min(4,confidence*4). Weak-loss/edge criteria are <=-7%/edge<48 or <=-3%/edge<55, both with replacement edge gap>=18. When scorecard exists it must permit EXIT_REVIEW/REPLACE_REVIEW. Default maximum1 exit/cycle; protected winners retained absent independent exit review; unknown pnl prevents discretionary rotation. Active protective orders preserved; replacements wait for CORE and confirmed sell fill, not sale submission. Sales rest at midpoint, unlike emergency bid exits.

Sources: `backend/services/portfolio_manager.py:758`, `:769`; `backend/services/pm_portfolio.py:107`; `backend/services/pm_rebalance.py:20`, `:45`, `:81`, `:95`, `:222`, `:271`. Runtime enablement and historical scorecard/config snapshots were not verified.

### Advisory Lifecycle vs Legacy Enforced Rules

`backend/services/strategy_lifecycle.py:39` contracts are SHADOW_ONLY with execution_effect=none: Day2 2 sessions; RedGreen1/no overnight; Supernova5; Catalyst3; Serial3; pharma20 trading days/event-window; Core40 trading days. They describe VWAP/reclaim/volume/catalyst/financing invalidations but do not enforce them. SIGNAL_CONFLUENCE and unrecognized strategy IDs fall back to Core; a merged row can pick a different lifecycle from its full set of views. Options contracts are not stock hold rules. `strategy_contracts.py:124` records conflicts without changing execution.

Legacy PM recommendation ladders remain in `portfolio_manager.py:351`: RUNNER if upside>=60% or high-vol signal/upside>=35% (15% initial SL,25% TP,10% triggers,7.5% stop raises,18% target raises,8 levels); CORE if upside>=25% or RR>=2 (10/15/5/5/10,6 levels); otherwise TACTICAL (7/10/3/3/5,4 levels). STARTER reduces initial SL by2 points (minimum5), TP by3 (minimum8), maximum levels by1 (minimum3). Initial SL may tighten to supplied distance, bounded minimum5 and profile maximum. Lottery recommendation plan sets no_capped_tp. Generic `compute_active_levels` floors gain/trigger count and clamps max levels. This is NOT Public's actual uncapped_floors_v2 policy.

Legacy Alpaca `trade_floor_phases.py:260` enforces hard stop first, then elapsed CALENDAR days>=hold_window_days (default30), then 40%/30%/remaining30% phases. Phase1 target prioritizes pm_active_target over phase1 target; phase2 also prioritizes pm_active_target over fixed entry+1.5*(AXIOM target-entry). One phase advances per monitor tick; stops rise to entry then phase1 realized exit. Phase3 trails50% of peak gain, tightening to25% after90% of hold window. Lottery no_capped_tp bypasses phase1/2 trims, but legacy hold expiry remains. These legacy functions are not evidence they ran historically at a particular timestamp. `trade_floor.py:52`, `server.py:2494` retire Alpaca equity entry/API paths.

Stop engine is analytical, not ATR: stored coefficients add hold/sector/score/instrument/signal-subset/30-day realized-volatility deltas to base10%, clamp default5..25%; persist breakdown/coefficients as-of. `backend/services/stop_engine.py:40`, `:150`. Current Public caps initial price loss distance at10% even if recommendation/analytical stop is wider.

Manual Lottery paper tickets are another policy: $10 nominal, max2/day (1 in downtrend), max6 open, no same-name same-day reentry, red/doomsday/halt disabled, 40% initial stop, displayed thirds ladder at+30%,+100%,20% trail,7-calendar-day time-stop, entry1%/exit1.5% synthetic haircuts. Do not transplant these into Public PM replay or mix manual tickets with broker-confirmed linked Lottery trades. Source: `backend/services/lottery.py:970`.

## Historical Versions and Replay Caveats

1. Local Git anchors: `358eba0` (2026-10-07 11:35:54 EDT) introduced five-percent floor/initial-stop cap; `070da7f` (11:51:23) replaced Public trims with uncapped full-position floors; `a0f23b7` (22:45:26) added strategy evidence/path replay/shadow learning; `cbb24d7` (2026-10-08 06:38:54) hardened exits/monitoring; `f9822e4` (06:57:38) repaired Public fill lifecycle/transient-loss checks. Earlier `53ae05a` (2026-10-02 21:29:06) hardened outcome attribution/Day2 execution. These are commit times, NOT activation/deployment times. The full earlier strategy evolution was not reconstructed commit-by-commit.
2. Separate AS-TRADED historical reconstruction (version/config/deployment actually active) from CURRENT-RULE counterfactual replay at pinned HEAD. Applying today's floors, $6 size, disabled Day2 default, or source merge rules to old signals does not reproduce old execution. Missing deployment/config history means historical policy unknown.
3. `scan_results` starts Core-only then gets full-cycle updates; `portfolio_manager_history` and `strategy_screeners_history` upsert by scan finished time, so reruns can overwrite. Latest/current caches are not immutable decision history. `pharma_pm_decisions` and `earnings_pm_decisions` also upsert. Verify observation timestamps, full-cycle completion, fingerprints and write history before claiming point-in-time coverage.
4. `pm_backtest._simulate_exit` (`backend/services/pm_backtest.py:83`) uses terminal close return and labels method close_only_terminal_return. It cannot know path peaks, intraday stops, floor arming, gaps, midpoint fills, phase sequence or monitoring outages; do not treat it as current Public execution replay.
5. `path_replay.replay_equity_path` (`backend/services/path_replay.py:103`) is useful offline strict-floor OHLC research logic. It evaluates O-L-H-C and O-H-L-C and uses conservative outcome, handles gaps, explicit costs, and leaves open trades unrealized. It assumes modeled stop crossing/fills, not Public quote-monitor/limit execution. Its interface also retains mandatory5/10/20 floors, so it cannot directly express every alternative below. No replay code was changed here.
6. A current calendar, latest short interest, current shares outstanding, seeded FDA event, revised filing, later attention baseline, trained weight, or post-earnings reaction is not historic availability. Record event time AND disclosure/provider receipt/decision time. Delisted names, renames, splits, halts, missing prices and zero-information failures must remain in coverage accounting.
7. Discovery lanes are non-exclusive; snapshots repeat across cycles. Portfolio baseline allows no add to held Public ticker. Use a single account timeline, broker-scoped quantities and fixed episode rules for research. Signal-return averages, filled-trade returns and total account returns answer different questions.
8. Static defaults and version strings are not deployment proof. Current source even has stale five-minute language in monitored-exit documentation while scheduler uses one minute. Read call sites and persisted event cadence, not comments alone. No production-runtime claim is made.

## Fields Required From Recorded Signals

| Evidence Set | Minimum Fields / Why |
|---|---|
| Frozen decision envelope | cycle_id, scan_id/history_key, scan finished_at, full_cycle_finished_at, generated_at/observed_at/receipt time, triggered_by, source revision/deployed SHA, ruleset ID AND complete effective values, schema/rubric/scoring/lifecycle versions; distinguish Core-only and complete cycles |
| Complete universe | Accepted AND rejected/watch candidates, prefilter evidence if testing discovery selection, source failures/counts, truncation/ranking order, raw_source/provenance, historical symbol identity; missing rejected universe limits selection claims |
| Signals and merge | Raw signals, evidence_signals, triggers, components, signal_groups, independent_signal_count, strategy_fits/views/scanner sources, read_only/pm_routable, source_scan, scanner family and ordered merge inputs; preserve multi-lane ownership and evidence precedence |
| PM inputs | Price/source/provider timestamp/age/freshness, signal_score, trade_score, learning_score, squeeze.score, risk.score, case_score/confidence, target hierarchy/source/proxy flag, stop_loss/risk stop, entry bands, sector, anchor summaries, regime snapshot/SPY input as-of, overrides/learned configurations |
| PM output | Final and pre_execution actions/allocations, shares/risk dollars, score breakdown, profile/mode, ratchet/lifecycle plan, caution/blocker reasons, ordered recommendations, cash/equity/book state; score alone cannot reconstruct decisions |
| Stock execution inputs | Public bid, ask, quote time/source/age, spread, first and final quote with receipt times, legal session/preflight, cash/account permission, halt feed, truth/QC/kill/safety/daily-loss status, live flags, Day2 override/min-score, idempotency intents and retry/rejection reasons |
| Fill and position ledger | broker_base/account identifier without secrets, client_order_id/order_id, submission/acceptance/unknown/rejection/cancellation/partial/full statuses, fill timestamps/quantities/average prices, fees, available quantity, imported/unattributed markers, manual/operator distinction, residuals and pending orders |
| Hold/exit path | Time-ordered executable bids/asks plus actual monitoring/subscription timestamps, peak and stop state before/after each event, public_stop_policy/version, pm_ratchet_events, protection coverage, breach/retry/limit/fill evidence, rebalance intents/config/scorecards, operator exit reason/time |
| Market-data research | Timestamped OHLCV and intraday quotes, start/end boundaries, session/holiday calendar, corporate-action basis/factors, delistings, halts, bid liquidity, data revisions and missing expected bars; adjusted bars must be consistent with entry/fills |
| Comparator/costs | Matching SPY prices and times, explicit fees per share/bps, spread/slippage assumption or observed fill economics, cash movements/dividends and mark valuation policy; no implicit zero cost |

Candidate local collection contracts: `scan_results` with attached strategy_payload/lottery_result/pharma_result/pharma_shock_result/pm_payload/execution_summary; `ll_scans`; `strategy_screeners_history`; `portfolio_manager_history`; `pm_company_observations`/PM memory from `pm_brain.record_pm_cycle`; `strategy_shadow_contract_history`; `execution_gate_checks`; `tf_trades` broker_base=public; `pm_ratchet_events`; `pm_rebalance_intents`; broker execution-intent records and monitor snapshots. This is a required export list, not verified availability. The PM history document does not itself preserve every full ruleset/input field; the frozen scan and additional config history are needed.

## PRE-REGISTERED Alternatives (Before Returns)

Registration ID: `EQUITY-2026-10-08-STRATEGY-RECONSTRUCTION-v1`. This file is the registration artifact, not a database registration or tamper-proof timestamp service. No returns have been inspected in this task. Freeze its SHA-256 in the eventual run manifest before unblinding; later edits require a new registration, not retroactive tuning.

Baseline B0: pinned CURRENT-RULE Public equity policy above, using frozen PM decisions with explicit configuration branch for rebalance ON/OFF when known. Unknown flags are not treated as approvals. Research paths without operational history must be labeled modeled policy, not AS-TRADED. Baseline excludes option-intent/research-only/manual/unattributed entries; separate coverage cohorts retain those records without counting them as live strategy successes. Default-disabled Day2 is excluded unless timestamped authorization proves otherwise.

Each alternative changes exactly the stated parameter/condition; all other PM eligibility, cash/position constraints, entry-limit mechanics, no-option rule, safety evidence requirements, tighter inherited stops and emergency exits remain unchanged. Test individually, never a post-hoc best combination. Filters apply immediately before commitment of an entry and do not rerun discretionary learning from future outcomes.

| ID | Fixed Alternative | Rationale | Required Evidence / Failure Handling |
|---|---|---|---|
| A1 Tighter Initial Floor | Set initial stop=max(previous PM protective stop,0.92*entry) instead of0.90*entry; all profit milestones unchanged | Small 10%-to-8% risk-distance change; tests reduced downside versus noise exits | Same execution/quote path as B0; never widen a tighter PM stop |
| A2 Delayed First Lock | First profit floor arms only at observed peak gain STRICTLY >7%, locking+5%; >10%, >20% and higher rules unchanged | Tests early-floor whipsaw without relaxing initial loss protection | Intraday peak/quote ordering; exact7% does not arm; OHLC-only model remains approximate |
| A3 Narrower Entry Spread | Maximum entry spread=min(recorded baseline max,150bps); emergency sell exemption unchanged | Tightens default300bps admission without loosening a stricter historical setting | Contemporaneous first AND final bid/ask; missing sides excluded from paired executable cohort, not guessed |
| A4 No Confirmed Dilution Entry | Skip a Lottery-attributed candidate if timestamped entry evidence reports dilution.active or a dilution penalty key; absence of the evidence record is UNKNOWN, not clear | Tests one observable financing-risk filter; does not turn SEC research into a blanket veto | Raw frozen Lottery dilution/penalty evidence. Compare on joint observable cohort and report UNKNOWN coverage separately; do not backfill future filings |
| A5 RedGreen Session Exit | Only primary-attributed `lottery_red_green` positions: submit full available quantity at fresh executable bid at15:55 ET on entry trading session if still open; pending entry not filled by15:55 is cancelled in model; stops retain priority | Converts one explicit shadow no-overnight intention into a minimal testable time rule | Exchange session calendar and observed quotes; shortened session uses close-minus5min; no new entries at/after cutoff; next valid bid after unavailable cutoff quote is deferred exit, not invented on-time fill. Mixed-lane primary attribution must be frozen before returns |

A5's bid-priced scheduled sale is an explicit hypothetical policy, not current Public midpoint discretionary exit behavior. Its primary lane uses the recorded Public attribution/entry decision packet; if absent or conflicting, lane-specific A5 is not testable. Do not assign RedGreen after learning which overlapping lane performed best.

### Evaluation Contract

- Freeze this registration and later dataset/run manifest before viewing outcomes. Date range: earliest through latest complete recorded equity decision available strictly before 2026-10-08 20:15:49 UTC; no extending cutoff in response to results. This reconstruction does not assert any start date, sample size or dataset availability.
- Entry timing is strictly after all required source receipt, PM decision and safety evidence; no execution at a bar open preceding signal availability. For recorded fills use broker timestamps; counterfactual fills need stated quote/liquidity/latency model and cannot inherit factual fills after changing admission/price rules.
- Primary endpoint: difference from B0 in net marked portfolio return at the common cutoff, including cash and unrealized holdings, with identical initial capital/cash flows. Also report realized net return, maximum equity drawdown, turnover, exposure, fill ratio, missing-data/rejection coverage, and lane counts. Floors do not imply net profit.
- Run sequential account replay: unfilled orders reserve/release cash correctly; partial fills/residuals persist; no future-sale proceeds finance current buys. Common opportunity tape, deterministic source order/tie rules and costs apply to every variant. Report both each variant's full eligible cohort and joint observable coverage; no favorable deletion of missing/delisted losers.
- Time split by unique entry sessions: earliest60% development (plumbing validation only), next20% validation, last20% untouched holdout, boundaries determined from timestamps without returns. Purge overlapping trade horizons at boundaries; still-open pre-boundary positions belong to earlier cohort and are not fresh holdout observations. Insufficient independent episodes => descriptive/inconclusive, not proven edge.
- Treat all five tests as one family; report every result including failures, Holm-adjusted significance if justified, date/episode-block uncertainty and economic magnitude, not isolated winning p-values. No parameter grid, lane cherry-picking, optimizing stop to observed returns, or combining winning alternatives under v1.
- Paired signal research may use ticker/strategy episodes with a fixed5-calendar-day observation cooldown, disclosed separately from exact portfolio holdings. Cluster uncertainty by ticker/episode and entry date, not repeated scan row. Overlapping lanes are tags, not independent samples.
- Costs must be explicit: use observed fees and spreads when available; otherwise no net claim until cost assumptions are frozen in run manifest before return inspection. OHLC research must show conservative intrabar ambiguity/gap handling, plus separate monitor-cadence/limit-fill sensitivity; a modeled crossing is never broker-confirmed realization.
- No production promotion or strategy approval follows from this report. Alternatives are research hypotheses only; no code, configuration, trading policy or execution authority is changed.

## Limitations and Handoff

Read-only source reconstruction is complete at the pinned checkout for the identified equity routing graph, including specialist overlaps, discretionary adapters, advisory contracts and retired/manual policies. Exhaustive historical deployment reconstruction, collection completeness, symbol/data coverage, real monitoring uptime, broker capabilities and effective flags remain UNVERIFIED. No secret/config-value lookup or external verification was performed.

An extensive defensible backtest is not yet demonstrated feasible: it requires authorized offline exports with point-in-time inputs, full config/deployment history and executable quote/market paths. Recorded signal scores/targets alone support a constrained research study, not faithful fill/hold/exit/account replication. Missing evidence must produce an explicit unsupported/unknown cohort rather than fabricated trade behavior.

Only output path: `docs/equity-backtest-2026-10-08/strategy-reconstruction.md`. No trading-code changes, commits, pushes, endpoints, secrets or orders are part of this task.
