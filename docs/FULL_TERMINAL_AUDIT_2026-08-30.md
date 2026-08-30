# Case Capital Terminal: Full Audit

Date: 2026-08-30
Scope: backend, frontend, scheduler, data-quality controls, integrations, deployment configuration, and runtime compatibility surface.

## Executive Verdict

The terminal is operationally deployable, but it is not reasonable to claim that every research source is always live. Execution-critical data has explicit freshness gates; optional research feeds can be stale, delayed, unavailable, or cached and must remain visibly labeled. The primary cleanup risk is not a single broken feature. It is accumulated compatibility code, oversized modules, and multiple asynchronous refresh paths that make ownership and diagnosis harder.

The current release makes three safe corrections:

1. Options candidate evidence is `STANDBY` outside a market session instead of appearing live from an old cache.
2. The duplicated credential blocks in `.gitignore` are consolidated.
3. The unreachable `ResearchPage` component is removed; `/research` remains an intentional redirect to Settings.

## Confirmed Findings

| ID | Area | Finding | Impact | Disposition |
|---|---|---|---|---|
| F-01 | Scheduler | Options candidates were not marked market-session-only. | Weekend/holiday cache could look live. | Fixed |
| F-02 | Data quality | Aggregate integration probes can time out on slow optional providers. | Optional rows remain cached and may be stale while critical Alpaca evidence is fresh. | Fixed behavior; keep visible |
| F-03 | Data quality | Freshness must be calculated from provider evidence, not cache generation time. | Cache metadata could mask old provider data. | Fixed |
| F-04 | Telegram | Consolidated scan delivery and standalone alert paths coexist. | Duplicate messages remain possible when deployment env flags drift. | Verify with a single outbound policy test |
| F-05 | API compatibility | Frontend still consumes `/v32/*` routes. | Removing legacy routes would break live pages. | Retain; migrate by contract |
| F-06 | Scan identity | Scan signatures are order-stable and price-sensitive. | Good protection against duplicate-signature confusion. | Covered by tests |
| F-07 | Scan freshness | Fully stale scan prices block new entries. | Correct safety behavior, but may explain zero orders. | Retain; expose exact blocker |
| F-08 | Options data | Basic-plan indicative snapshots may omit OI/greeks and are delayed. | Option contracts can be research-valid but execution-ineligible. | Retain conservative policy; improve explainability |
| F-09 | Execution | Equity and options use separate account/config paths. | Misconfigured credentials can silently route to the wrong account or no account. | Add startup account fingerprint check |
| F-10 | Scheduler | Many jobs run on independent cadences. | Overlap and duplicate work remain possible without a per-job lease. | Add durable job lease |
| F-11 | Scheduler | Runtime job status exists, but historical run records are not a uniform contract. | Diagnosis requires reading logs or provider documents. | Add run ledger |
| F-12 | Source quality | Source status distinguishes live/stale/down, but optional degradation is not a single dashboard KPI. | Operators can miss noncritical degradation. | Add `research_degradation_score` |
| F-13 | Frontend | Several pages perform their own polling and request handling. | Duplicate requests and inconsistent loading/error states. | Centralize query/cache layer |
| F-14 | Frontend | `CrtShell`, `IntelPage`, and `MacroPage` use compatibility endpoints directly. | API version drift is hard to detect. | Add generated route contract map |
| F-15 | Backend structure | `server.py` is a very large monolith. | High merge risk and slow code navigation. | Split by bounded route domain |
| F-16 | Backend structure | `options_desk.py`, `pharma.py`, `kronos.py`, and `telegram_service.py` are oversized. | Strategy, I/O, policy, and rendering are coupled. | Extract pure policy modules first |
| F-17 | Backend structure | Legacy aliases are used intentionally by current callers. | Names suggesting dead code are misleading. | Document compatibility ownership |
| F-18 | Dead-code sweep | `nlq` is dynamically imported by Telegram. | Static import scans falsely identify it as dead. | Retained |
| F-19 | Dead-code sweep | `ticker_hygiene` is imported by scanner and execution gate. | Removing it would weaken safety boundaries. | Retained |
| F-20 | Dead-code sweep | `ResearchPage` had no route/import path and `/research` redirects elsewhere. | Unreachable frontend code and maintenance burden. | Removed |
| F-21 | Repository hygiene | `.gitignore` contained repeated credential blocks and invalid `-e` entries. | Noise and possible dependency confusion. | Fixed |
| F-22 | Config | HTTP deployment correctly skips HTTPS webhook registration. | Prevents invalid Telegram webhook startup calls. | Fixed |
| F-23 | Config | Preview/read-only and execution flags are spread across environment values. | Unsafe configuration combinations are possible. | Add validated config matrix |
| F-24 | Integration | IBKR is research-only and local-host dependent unless Gateway runs on VPS. | No remote data guarantee when the laptop is off. | Surface listener check in readiness |
| F-25 | Integration | FinanceToolkit/FMP is research-only. | It must not influence PM or order eligibility accidentally. | Keep explicit read-only boundary |
| F-26 | Database | Mongo remains primary while Postgres is optional. | Duplicate persistence semantics may diverge. | Define per-collection ownership before cutover |
| F-27 | Database | Large operational documents are stored in Mongo collections. | Storage growth can recur. | Add retention/TTL policy by collection |
| F-28 | Observability | Health checks mix endpoint reachability with data readiness. | HTTP 200 alone can be misread as trading readiness. | Keep separate readiness contract |
| F-29 | Deployment | VPS branch and local branch are separate release surfaces. | A clean local tree does not prove VPS parity. | Add commit/config parity endpoint |
| F-30 | Tests | A significant part of the suite requires Mongo, env, and a running backend. | Local green focused tests do not equal full integration coverage. | Add hermetic unit tier and explicit integration marker |
| F-31 | Tests | FastAPI startup uses deprecated `on_event` hooks. | Warning today; future framework migration cost. | Move to lifespan handler |
| F-32 | Tests | No complete contract test proves consolidated Telegram exclusivity. | Standalone pharma/alert regressions can return. | Add policy-level test |
| F-33 | Execution | Order transmission depends on multiple gates, quote freshness, account state, and broker response. | Approved PM actions are not equivalent to submitted orders. | Add per-candidate rejection waterfall |
| F-34 | Execution | Partial fills and broker position truth require reconciliation. | Local position state can diverge after partial fills/corporate actions. | Run reconciliation before/after every order batch |
| F-35 | Options | Candidate selection and execution eligibility are separate. | A routed option can still have zero executable contracts. | Keep separate and render both states |

## New Features With Highest Operational Value

1. **Decision trace waterfall**: one immutable trace per ticker from scanner eligibility through PM action, contract selection, gate checks, broker submission, and final status.
2. **Account fingerprint panel**: masked account id, paper/live mode, buying power, options permission, and provider endpoint; fail closed on unexpected account identity.
3. **Data readiness matrix**: critical versus research-only sources, evidence timestamp, age, provider, delay class, and exact effect on execution.
4. **Durable scheduler leases**: one lease per job key with owner, heartbeat, timeout, and missed-run state to prevent overlapping scans.
5. **Run ledger**: persisted start/end/status/error/row-count/provider metrics for every scheduled job.
6. **Single-flight refresh cache**: coalesce simultaneous UI/watchdog/manual refreshes for the same source.
7. **Provider budget manager**: request budget, remaining quota, backoff, and last throttling event per provider.
8. **Contract-level options rejection waterfall**: show exactly how many contracts failed each filter and the first blocking reason.
9. **Options paper shadow book**: compare selected contract, conservative simulated fill, realized underlying move, IV move, theta, and counterfactual P/L.
10. **Telegram outbox**: one idempotent consolidated digest per cycle, separate critical-event channel, persisted send state, and duplicate suppression.
11. **Route contract registry**: endpoint owner, version, frontend callers, response schema, and deprecation date.
12. **Feature registry**: canonical name, units, definition, version, source, and consumers; reject unregistered duplicate calculations.
13. **Snapshot manifest**: cycle id, source hashes, code version, config version, and completeness vector.
14. **Research-only firewall**: automated test that finance toolkit, IBKR, SEC, earnings, and other read-only sources cannot create order intents.
15. **Collection retention dashboard**: document counts, estimated bytes, TTL coverage, and top growth collections.
16. **Replay command**: rebuild a past scan from its snapshot manifest without calling live providers.
17. **Market-session calendar service**: one canonical holiday/early-close/extended-session decision used by scheduler, freshness, and execution.
18. **Frontend query coordinator**: shared polling, request cancellation, stale-while-revalidate behavior, and consistent error states.
19. **Release parity check**: compare local/VPS commit, env fingerprint, frontend asset hash, backend health, scheduler state, and database connectivity.
20. **Readiness drill**: scheduled paper-only dry run that verifies the complete order path without transmitting an order.

## Features To Avoid For Now

- A general optimizer while the book is small and input quality is uneven.
- Automatic strategy-weight mutation without sample-size, walk-forward, and rollback gates.
- Pixel/iframe analysis of TradingView charts.
- Treating delayed indicative option data as execution-grade.
- Deleting all `/v32` compatibility routes before callers are migrated.
- Adding more alert types while Telegram delivery policy is not enforced centrally.

## Recommended Next Build Order

1. Add the execution decision trace and rejection waterfall.
2. Add account fingerprint and provider-mode verification.
3. Add scheduler leases plus a run ledger.
4. Add Telegram outbox exclusivity tests and persisted idempotency.
5. Add provider/route/feature registries.
6. Add collection retention and growth telemetry.
7. Extract pure policy from the largest backend modules.
8. Add hermetic unit tests and mark live integration tests explicitly.
9. Add replay and release-parity checks.
10. Only then perform broad compatibility-route migration.

## Current Release Verification

- Backend bytecode compilation: pass.
- Focused scheduler, freshness, and execution-gate tests: pass before this audit change; rerun after this change.
- Frontend production build: pass before this audit change; rerun after this change.
- VPS release: previous deployed commit was `6a6295c`; redeploy only after this release passes local verification.
