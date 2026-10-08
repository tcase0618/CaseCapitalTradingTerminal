# Equity Backtest Data Schema Audit

Audit date: 2026-10-08. Source checkout HEAD: `f9822e4a50c4f2bb5751e6217f576316602ba403`.

## Scope And Evidence

Static, read-only inspection of repository guidance and persistence/read/write code. The only authored artifact is this document. No live PostgreSQL connection, API call, service import/execution, credential inspection, source modification, commit, or deployment was performed. Existing unrelated changes were preserved. Root `AGENTS.md` was inspected; the repository file inventory exposed no nested AGENTS.md.

Schema below means **implemented storage contract**, not confirmed populated production data. No runtime coverage counts, completeness percentages, earliest/latest production dates, or provider readiness are asserted. Local `docs/scan_performance_2026-09-09*.csv` files exist; the inspected unique-export header contains `scan_layer, scan_ts, trigger, ticker_raw, ticker_normalized, scan_price, close_price, return_pct, band, selection_basis`. This is persisted local scan/mark evidence, not an OHLC archive, broker fill ledger, or proof of current database coverage. Test fixtures/reports are not market coverage evidence.

## Physical PostgreSQL Schema

[`db.py`](../../backend/services/db.py) routes all collection-style access through `postgres_store.get_database()`; collection names are values, **not separate SQL tables**.

| Table | Exact columns | Meaning |
| --- | --- | --- |
| `cc_collection_snapshots` | `collection text`, `doc_key text`, `payload jsonb`, `updated_at timestamptz`; PK `(collection, doc_key)` | Current document per key. Payload fields can be missing or JSON null; there is no typed per-collection schema. |
| `cc_events` | `id bigserial`, `collection text`, `natural_key text`, `event_type text`, `source text`, `occurred_at timestamptz`, `payload jsonb`, `created_at timestamptz` | Document-version journal, not ticks or executions. Insert/upsert payloads are full documents. |

Source: [`postgres_store.py`](../../backend/services/postgres_store.py), `init_schema`, `doc_key`, `occurred_at`, `_persist_updated`, `upsert_snapshot`, `mirror_document`.

`updated_at` is storage-write time. `cc_events.occurred_at` selects payload `created_at`, then `finished_at`, `generated_at`, `snapshot_at`, `ts`, `routed_at`, else now; it does **not** select `observed_at`, `decision_at`, `first_seen_ts`, or quote time. `stamped()` supplies write-time `created_at` and `feature_version`. Never use these generic storage clocks as first observation or market clocks.

Updates to `price_cache`, `bot_state`, and `public_trade_learning` do not retain events through the ordinary update path. Trade polling-only changes also suppress events for `pm_last_ratchet_check`, `last_checked_at`, `last_quote_at`, `last_reconciled_at`, `updated_at`. Other changes generally retain versions, but snapshot and event writes are separate: journal completeness is not guaranteed. Deletes remove snapshots without a deletion event. Scan insertion also explicitly mirrors the document, so journal scan copies can duplicate. Migration and historical code versions require separate validation.

## Observation And Decision Collections

| Exact collection / key | Payload fields | Granularity and limitations |
| --- | --- | --- |
| `strategy_observations` / `observation_id` | `cycle_id`, `observed_at`, `ticker`, `screener_id`, `scanner_family`, `strategy_lane`, `strategy_version`, `entry_price`, `entry_quote_timestamp`, `entry_quote_age_seconds`, `entry_price_source`, `signals`, `signal_groups`, `case_score`, `target`, `stop`, `raw_source`, `created_at`, `feature_version` | Preferred frozen per-cycle/ticker/screener entry evidence. ID is truncated SHA-256 of cycle, uppercase ticker, screener. Entry contract is `$setOnInsert`; outcomes later update it. `observed_at` uses scan_finished_at / finished_at / generated_at / observed_at, otherwise write time. Not necessarily the quote time or first-ever ticker sighting. |
| `signal_first_seen` / ticker fallback key | `ticker`, `first_seen_date`, `first_seen_ts`, `first_seen_price`, `first_signals`, `first_strategy_lanes`, `first_signal_score`, `first_risk_level`; mutable `last_seen_date`, `last_seen_price`, `times_found`; optional repair `first_seen_price_source`, `first_seen_price_refreshed_at` | Core ticker discovery ledger when `include_first_seen=True`. `first_seen_date` is scan-derived ET date; normal `first_seen_ts` is ledger-write UTC time, **not frozen scan time**. No normal first-quote timestamp/bid/ask. Backfill can derive date/price from daily or synthetic performance and first_seen_ts from legacy `ts` (possibly null). |
| `signal_performance` / `(ticker,date,screener_id)` when all present | `ticker`, `date`, `screener_id`, `scanner_family`, `entry_price`, `observed_at`, `cycle_id`, `signals`, `strategy_lanes`, `signal_score`, `last_seen_at`, `created_at`, `return_7d`, `return_30d`, `return_90d`; synthetic `synthetic`, `synthetic_source`, `synthetic_meta`, `ts` | One ET-date/screener record: entry/time/cycle initially frozen, signals/context overwritten by subsequent scans that day. **Not an intraday observation archive.** Normal quote metadata is absent. `created_at` is overwritten by stamped refreshes. Missing entry may be backfilled from daily close without a provenance flag here. |
| `scan_results` / fallback `_id`, otherwise `finished_at` | `started_at`, `finished_at`, `created_at`, `cycle_id`, `triggered_by`, `results[]`, `candidate_ledger`, `freshness`, `scan_signature`, `v32` | Frozen scan packet. `results[].ticker`, `.price`, `.price_source`, `.price_timestamp`, `.price_age_seconds`, `.price_fresh`, `.premarket_confirmed`, `.price_warning`, `.price_meta` hold sampled mark/provenance. `.price_meta.provider_ts` and `.price_meta.raw` can preserve underlying Public quote timestamp/last/bid/ask if provider supplied them. Times differ across tickers within a scan. Snapshot is not continuous tape. |
| `pm_company_observations` / `observation_id` | `ticker`, `cycle_id`, `observed_at`, `price`, `entry`, `target`, `stop`, `price_evidence`, `action`, `pm_score`, `scoring_version`, `score_breakdown`, `strategy_views`, `strategy_lanes`, `mode`, `evidence_integrity` | Frozen PM per-cycle company evidence. `price_evidence.source`, `.provider_timestamp`, `.status` originate in recommendation. Numeric normalization may turn missing price into zero; require positive finite prices. |
| `pm_decision_ledger` / `decision_id` | `ticker`, `cycle_id`, `decision_at`, `action`, `pm_score`, `price`, `allocation_usd`, `reason_codes`, `evidence_ref`, `execution_blocker` | Frozen decision, not a fill. Join `evidence_ref` to company observation's `observation_id`, not to SQL doc_key alone. IDs are `pm-decision:{cycle_id}:{ticker}` and `pm-observation:{cycle_id}:{ticker}`. |
| `pm_company_profiles` / ticker | `first_pm_observed_at`, latest observation/decision/profile state | First PM time is retained, but latest prices are mutable; join historical observations for first price. |
| `portfolio_manager_history` / `_id=pm:{scan finished_at}` | `scan_finished_at`, `generated_at`, `recommendations[]`, `summary` | PM recommendations include `ticker`, `price`, `price_evidence`, action/score/target/stop and plans. `$set` updates the same scan key; current state can represent a later policy evaluation. Use event versions to investigate original policy evidence. |

Source: [`pnl_tracker.py`](../../backend/services/pnl_tracker.py) `_observation_contract`, `record_scan_picks`, `ensure_first_seen_backfill`, `refresh_all_entry_prices`; [`scanner.py`](../../backend/services/scanner.py) `run_scan`; [`pm_brain.py`](../../backend/services/pm_brain.py) `build_observation`, `record_pm_cycle`; [`portfolio_manager.py`](../../backend/services/portfolio_manager.py) recommendation construction/history write.

**Important field mismatch:** Core scan rows retain `price_timestamp` / `price_age_seconds`, but `_observation_contract` looks for `quote_timestamp` / `quote_age_seconds` or nested `quote.timestamp/ts/age_seconds`. It does not copy Core `price_timestamp` or `price_meta`. A frozen strategy entry can therefore lack quote timing even when the matching scan has it. Join scan cycle + ticker + screener where available; preserve both timestamp namespaces and conflict flags. Do not invent quote times from scan completion.

**Exactness:** A provider-time-stamped sampled quote is not an exact executable entry. In `pricer.live_price_meta`, missing Public quote time is replaced by local now and marked age zero; Finnhub fallback also stamps local now and is delayed. PM status `PROVIDER_TIMESTAMP_PRESENT` checks presence, not provider authenticity. Recover raw source time where available and label synthesized/unknown timestamps separately. Quote source/age/freshness alone cannot prove contemporaneous bid/ask liquidity.

**`pm_decision_capsules` is not implemented as a collection in this checkout.** [`trade_journal.py`](../../backend/services/trade_journal.py) builds `decision_time_capsules` in memory from latest PM recommendations, current rules, scan date, and daily performance joins; these are not historical policy snapshots. Its joins, and research_lab joins, omit screener_id and may select ambiguous daily rows. `pm_decisions` in the critical-collection list is not equivalent to `pm_decision_ledger` or these derived capsules.

## Historical Prices And OHLC

| Source | Retained fields / location | Classification |
| --- | --- | --- |
| PostgreSQL `price_cache` / ticker | `ticker`, `price`, `fetched_at`, `source`; some writers also set `provider_ts`, `age_seconds`, `fresh`, `premarket_confirmed`, `warning` | Latest point mark, mixed live/intraday quote or prior daily close, not OHLC. Ten-minute read TTL is not physical retention. Overwritten; ordinary updates do not journal history. Other writers set only basic fields, so old metadata can survive beside a newer price. |
| PostgreSQL `price_history_cache` / ticker | `ticker`, `closes` object `{YYYY-MM-DD: close}`, `fetched_at`, `source` (`public` or `massive`) | **Daily closes only**, 24-hour read TTL. Requested range/adjustment/session metadata is not persisted. Cache reads do not verify requested days are covered. Replaced per ticker; event versions may retain earlier maps, not guaranteed complete. `clear_cache()` deletes both caches. |
| `PublicAPIClient.bars()` | returned `symbol`, `bars` from regularMarket, `dataProvider=PUBLIC_BARS`, `dataFeed=public` | Daily regular-session provider bar response. `pricer.get_history` extracts only timestamp/time and close/c, discarding O/H/L/volume and truncating dates. No full Public OHLC collection identified. |
| `PublicAPIClient.intraday_bars()` | raw session-preserving response plus `dataProvider=PUBLIC_BARS_V2`, `dataFeed=public`, `tradingSessionToggle` | Supports minute/hour and other aggregations, default ONE_MINUTE/DAY/ALL_SESSIONS. **Fetch contract only**: repository search found no production caller/persistence path beyond definition (tests call it). No PostgreSQL intraday bar cache identified. |
| LSE `candles()` | `rows` from provider, timeframe/start/end/dataset | Potential daily/intraday OHLC API source; wrapper `_CACHE` is process memory with 90-second TTL, not PostgreSQL. Backtest extracts closes; no complete candle-series persistence identified in the inspected paths. |
| Massive grouped/range | grouped map in `_grouped_cache`; range return is daily close map | Grouped cache is bounded process memory. Daily OHLC provider responses do not establish persisted OHLC. |
| `kronos_candle_predictions`, `kronos_candle_outcomes` | prediction/features/input_contract/predicted_next_candle; outcome `actual_at`, `actual_pct`, timeframe/target/model identifiers | Forecast and resolved-return evidence, not historical OHLC series. Predicted OHLC is **not actual OHLC**; outcomes do not persist the full actual candle. |

Sources: [`pricer.py`](../../backend/services/pricer.py) `_store_latest`, `_store_history`, `get_history`, `get_history_range`, `get_close_on_date`, `clear_cache`; [`public_api.py`](../../backend/services/public_api.py) `bars`, `intraday_bars`; [`public_price_stream.py`](../../backend/services/public_price_stream.py); [`london_strategic_edge.py`](../../backend/services/london_strategic_edge.py); [`kronos.py`](../../backend/services/kronos.py) `candle_forecast`, `candle_accuracy`.

Public price-stream callbacks drive held-equity ratchets and persist `bot_state` with `_id=public_price_stream`: worker status/counters/last-symbol/last-quote-at, not a quote tape or OHLC store. Ratchet events are sparse risk-level changes, not bars.

## Return And Fill Completeness

`refresh_due_returns` computes daily-close outcomes at 7/30/90 **trading sessions**, requesting `exact=True` and requiring target before today's ET date. Unavailable closes remain null. Strategy observations have 1/3/5/7-session `return_{n}d`, `return_{n}d_close`, `return_{n}d_date`, `outcomes_refreshed_at`. These are endpoint outcomes, not intervening paths. Bounded batches, exceptions and later deployment of writers can leave gaps; no historical fill/refresh completeness can be inferred statically.

`synthetic_congress_backtest` mixes retrospective rows into `signal_performance`: `synthetic=true`, `synthetic_source=congress_curated`; it uses calendar-day offsets and close on-or-after the requested date, unlike forward trading-session outcomes. Exclude from prospective first-observation cohorts. Legacy rows without screener_id use fallback keys rather than the current composite key; inspect identities before deduplicating. `get_close_on_date(exact=False)` may use prior close or even the earliest available later close if none precedes the date. Entry repairs must not be called exact intraday fills.

The equity execution ledger is PostgreSQL **`tf_trades`**, generally keyed by fallback `client_order_id` (preceded by `_id/id/order_id` if present). Inspect [`public_execution.py`](../../backend/services/public_execution.py) and [`trade_floor.py`](../../backend/services/trade_floor.py).

- Entry decision/quote: `ticker`, `instrument=EQUITY` on Public entries, `broker_base`, `cycle_id`, `client_order_id`, `public_order_id`, `submitted_at`, `limit_price`, `quote_source`, `execution_quote`, `entry_decision.market.entry_quote`, `entry_decision.market.entry_limit_price`. Alpaca entries use `quote_meta`. These are sampled order-decision evidence, not fills.
- Public normalized `execution_quote` and `entry_decision.market.entry_quote` contain `bid`, `ask`, `mid`, `spread_bps`, `limit_price`, `quote_time`. Quote time prefers executableQuoteTime/executable_quote_time, otherwise the older bidTimestamp/askTimestamp pair (both required if either exists), otherwise generic quote timestamp. Preserve this clock separately from submitted_at and filled_at.
- Fill state: `status`, `fill_status`, `qty_total`, `qty_remaining`, `filled_avg_price`, `filled_at`, `last_order_status`. Public filled_at can fall back to order updatedAt or local reconciliation time; imported holdings use broker position opened_at and cost basis. A FILLED label alone does not establish an exact execution timestamp or fill-by-fill history.
- Exit evidence: `closed_at`, `close_reason`, `broker_exit_verified`, `broker_exit_price`, `broker_exit_quantity`, `realized_pnl`, `realized_pl_pct`, `realized_pnl_source`, plus protective/emergency/phase-specific fields. `CLOSED` also covers canceled/unfilled entries. Require positive fill quantity/price and corroborated exit evidence; do not use limit_price as an actual fill.
- `broker_imported`, management/protection state, strategy attribution, and `realized_pnl_source=public_history_unattributed_round_trip` separate imported/unattributed history from strategy executions. Keep Public versus Alpaca and paper versus live identity distinct; broker_base alone may not establish account execution mode for every legacy row.
- Supplemental PostgreSQL collections: `tf_journal`, `tf_phase_outcomes`, `public_phase_exits`, `pm_ratchet_events`, `public_trade_learning`. They retain phase/risk/outcome evidence, not a complete transaction tape; public_trade_learning is mutable snapshot-only on updates.

**Completeness verdict:** Historical first-quote fidelity, full OHLC coverage, partial-fill history, exact fill timing, delisted-universe coverage, corporate-action adjustment basis and benchmark alignment are **unverified**. The inspected persistence paths provide no complete Public intraday OHLC archive. No data-volume numbers are inferred.

## Research Entry Points

[`research_lab.py`](../../backend/services/research_lab.py) reconstructs PM decisions with current evaluator, default equity and BALANCED mode from bounded recent scans, joins daily outcomes, and optionally persists aggregate `research_lab_snapshots` plus `bot_state.research_lab_latest`. It is not an exact historical strategy replay. Its dashboard also invokes provider health probes: **do not execute it for an offline/read-only export**.

[`research_path_service.py`](../../backend/services/research_path_service.py) is a bounded pure facade, no persistence/I/O: caller supplies bars, entry_price, timezone-aware entry_timestamp, source, explicit costs and optional benchmark/terminal marks. It labels provenance caller_supplied_not_independently_verified. [`path_replay.py`](../../backend/services/path_replay.py) requires actual `timestamp` interval start and `open/high/low/close`, optional end_timestamp, consistent split-adjustment basis. It skips the bar containing an intrabar entry and models conservative OHLC path ambiguity; simulated fills are not broker fills. Supply an actual exchange/session expected-timestamp grid to identify gaps; no guessed cadence. Daily close maps cannot satisfy this contract.

## Read-Only Export SQL (Not Executed)

Use an authorized operator and a SELECT-only role against a restored snapshot or approved read replica. Resolve the actual schema through `information_schema`, then schema-qualify the two tables below if needed. No connection strings, secrets, server-file COPY, DDL, service calls, repair/backfill or cache refresh commands are needed. Use one consistent transaction, stream SELECT results client-side, record checkout/schema/export timestamps and a file hash. Psql `\copy (SELECT ...) TO 'local-path.csv' CSV HEADER` writes only a client file; statements below are plain SQL.

```sql
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '60s';
SELECT table_schema, table_name, column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('cc_collection_snapshots', 'cc_events')
ORDER BY table_schema, table_name, ordinal_position;

-- Inventory only; storage times are NOT observation/market times.
SELECT collection, count(*) AS documents,
       min(updated_at) AS earliest_storage_update,
       max(updated_at) AS latest_storage_update
FROM cc_collection_snapshots
WHERE collection IN (
 'strategy_observations','signal_first_seen','signal_performance',
 'scan_results','pm_company_observations','pm_decision_ledger',
 'pm_company_profiles','portfolio_manager_history','research_lab_snapshots',
 'price_cache','price_history_cache','tf_trades','tf_journal',
 'tf_phase_outcomes','public_phase_exits','pm_ratchet_events',
 'kronos_candle_predictions','kronos_candle_outcomes',
 'pm_decision_capsules') -- Diagnostic only: not a known implemented collection.
GROUP BY collection ORDER BY collection;

-- Frozen entry export: retain strings; validate timestamps/numbers offline.
SELECT doc_key, updated_at, payload->>'observation_id' AS observation_id,
 payload->>'cycle_id' AS cycle_id, payload->>'ticker' AS ticker,
 payload->>'screener_id' AS screener_id,
 payload->>'observed_at' AS observed_at,
 payload->>'entry_price' AS entry_price,
 payload->>'entry_quote_timestamp' AS entry_quote_timestamp,
 payload->>'entry_quote_age_seconds' AS entry_quote_age_seconds,
 payload->>'entry_price_source' AS entry_price_source,
 payload->'signals' AS signals, payload->'strategy_version' AS strategy_version
FROM cc_collection_snapshots WHERE collection = 'strategy_observations'
ORDER BY doc_key;

-- Scan row evidence: WITH ORDINALITY preserves original packet ordering.
SELECT s.doc_key AS scan_key, s.payload->>'cycle_id' AS cycle_id,
 s.payload->>'started_at' AS started_at,
 s.payload->>'finished_at' AS finished_at, r.ordinality AS row_number,
 r.row->>'ticker' AS ticker, r.row->>'price' AS price,
 r.row->>'price_source' AS price_source,
 r.row->>'price_timestamp' AS price_timestamp,
 r.row->>'price_age_seconds' AS price_age_seconds,
 r.row->>'price_fresh' AS price_fresh,
 r.row->>'price_warning' AS price_warning,
 r.row->'price_meta' AS price_meta,
 r.row->'strategy_scanner' AS strategy_scanner,
 r.row->'signals' AS signals
FROM cc_collection_snapshots s
CROSS JOIN LATERAL jsonb_array_elements(
 CASE WHEN jsonb_typeof(s.payload->'results') = 'array'
 THEN s.payload->'results' ELSE '[]'::jsonb END
) WITH ORDINALITY AS r(row, ordinality)
WHERE s.collection = 'scan_results'
ORDER BY s.doc_key, r.ordinality;

-- Daily closes, explicitly not OHLC. No unsafe numeric/date casts.
SELECT s.doc_key, s.payload->>'ticker' AS ticker,
 s.payload->>'source' AS source, s.payload->>'fetched_at' AS fetched_at,
 d.key AS session_date, d.value AS close
FROM cc_collection_snapshots s
CROSS JOIN LATERAL jsonb_each_text(
 CASE WHEN jsonb_typeof(s.payload->'closes') = 'object'
 THEN s.payload->'closes' ELSE '{}'::jsonb END
) d
WHERE s.collection = 'price_history_cache'
ORDER BY ticker, session_date;

-- Whitelist fields for first-seen/performance, PM joins and broker fills.
SELECT s.collection, s.doc_key, s.updated_at,
 coalesce((SELECT jsonb_object_agg(k, s.payload->k)
 FROM unnest(ARRAY[
 'ticker','date','screener_id','scanner_family','observation_id',
 'observed_at','cycle_id','decision_id','decision_at','evidence_ref',
 'first_seen_date','first_seen_ts','first_seen_price','first_seen_price_source',
 'first_seen_price_refreshed_at','first_pm_observed_at','price','price_evidence',
 'entry_price','return_1d','return_3d','return_5d','return_7d','return_30d',
 'return_90d','return_1d_close','return_3d_close','return_5d_close',
 'return_7d_close','return_1d_date','return_3d_date','return_5d_date',
 'return_7d_date','outcomes_refreshed_at','synthetic','synthetic_source',
 'ts','created_at','feature_version','action','pm_score','scoring_version',
 'target','stop','signals','instrument','broker_base','client_order_id',
 'public_order_id','submitted_at','filled_at','filled_avg_price','limit_price',
 'status','fill_status','qty_total','qty_remaining','execution_quote',
 'quote_meta','quote_source','broker_imported','strategy_attribution',
 'closed_at','close_reason','broker_exit_verified','broker_exit_price',
 'broker_exit_quantity','realized_pnl','realized_pl_pct','realized_pnl_source'
 ]) AS keys(k) WHERE s.payload ? k), '{}'::jsonb) AS evidence
FROM cc_collection_snapshots s
WHERE s.collection IN ('signal_first_seen','signal_performance',
 'strategy_observations','pm_company_observations','pm_decision_ledger',
 'pm_company_profiles','tf_trades')
ORDER BY s.collection, s.doc_key;

-- Version discovery for an independently authorized historical export.
SELECT collection, count(*) AS versions, min(id) AS first_event_id,
 max(id) AS last_event_id, min(created_at) AS first_journal_write,
 max(created_at) AS last_journal_write
FROM cc_events
WHERE collection IN ('scan_results','strategy_observations',
 'signal_first_seen','signal_performance','pm_company_observations',
 'pm_decision_ledger','portfolio_manager_history','price_history_cache',
 'tf_trades','tf_journal','tf_phase_outcomes','public_phase_exits',
 'pm_ratchet_events')
GROUP BY collection ORDER BY collection;
ROLLBACK;
```

For historical versions, repeat the whitelist projection against `cc_events`, retaining `id, natural_key, event_type, source, occurred_at, created_at` instead of snapshot `doc_key, updated_at`. Flatten `portfolio_manager_history.recommendations` with the same guarded array pattern used for scan results, keeping scan_finished_at/generated_at, ticker, price/price_evidence, action, score, scoring_version, target, stop and plans. Add exact outcome fields before exporting phase/ratchet records. Avoid unrestricted bot_state or raw preflight/account exports; inspect/redact nested raw quote objects for account identifiers before distribution. Collection inventory may return no rows for a source; absence is not a SQL failure or proof it never existed.

## Export Recommendations

1. Export frozen strategy/company observations and PM decision ledger first. Join PM evidence_ref exactly; join scan evidence by cycle/ticker/screener where possible and report unmatched/multiple matches. Choose earliest **validated** observed_at per defined ticker/screener/strategy universe; do not collapse independent screeners or use write time as market time.
2. Export first_seen and daily performance separately, preserving synthetic flags, repair markers, nulls and all identities. Freeze training features from the original scan/decision rather than mutable daily signals or recomputed research_lab actions. Mixed calendar/trading-session return definitions need separate cohorts.
3. Export cache close maps as endpoint/daily-close evidence only. Seek event versions for historical maps with explicit version selection and conflict checks; do not silently combine conflicting adjusted/unadjusted prices or later revised history. An expired TTL does not make stored rows disappear, nor prove price coverage.
4. Export tf_trades and selected lifecycle versions independently of candidate observations. Label exact broker-history matches, aggregate fills, imported cost bases, pending/unfilled orders and local timestamp fallbacks separately. Retain realized source/verification and account-mode provenance before any execution-performance comparison.
5. A stop/ratchet equity path backtest needs a separately authorized, immutable actual-OHLC dataset with interval start/end, symbol, timeframe, sessions, provider/feed, retrieval/version time, split/dividend adjustment convention and expected-session grid, including SPY and exited/delisted names. This audit does not authorize fetching or backfilling it. Never turn close-only cache data or predicted candles into actual bars.
6. After an export exists, measure valid first timestamps/positive quotes, source-time authenticity, joins, duplicates, matured versus unresolved outcomes, per-symbol expected-bar gaps, partial-fill/exit coverage and benchmark alignment from the persisted artifact. Record those counts then; no complete-history claim is justified by this static audit.

## Immutable Local Export Fitness Audit

Added 2026-10-08 following availability of the actual local export. This section supplies **measured local-export evidence** and supersedes the earlier absence-of-export limitation only for the fields/populations tested. It does not establish live DB completeness or authorize fetching data. The main normalization runner was not invoked, modified or duplicated. Checks operate on raw exported cache maps, performance entries and scan timestamp fields; no normalized strategy dataset was built.

### Reproducible Boundary

- Input `C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/evidence.jsonl`: **410,145,120 bytes, 27,734 complete JSON records**, zero invalid/incomplete lines. Two full streaming passes produced identical size and SHA-256: `ee63c707b43065220f9616ee14a80eec7fac4a0488dff75951ff4ced541184cf`.
- Discovery was treated as an ongoing export: bounded at **489,313,336 bytes, 14,063 complete records**, zero invalid/incomplete lines within that prefix. Its size did not change during the measured pass; this does not prove the exporter completed. Prefix SHA-256: `dff4c32237dad9c8dc7a26f1db100b99f78a6c58b67e4816b41850a7678b906b`. Findings do not include any subsequent append.
- Evidence population used: `price_history_cache` 1,685 documents; `signal_performance` 7,643; `scan_results` 377 packets / 12,365 results rows. Discovery prefix includes 13,410 strategy observations, 145 PM history packets and 145 strategy-screener history packets, among other collections; those are inventory counts, not a coverage assertion or a second normalized cohort.
- Inputs were iterated line-by-line; no whole-input read or DataFrame materialization. Only cache maps, compact entry fields and counters were retained for the targeted comparisons. No secrets, full broker payloads or raw account data were printed. No network/DB access occurred.
- Inspectable local audit code: [datafitness_audit.py](C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/datafitness_audit.py), [datafitness_followup.py](C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/datafitness_followup.py). Persisted measurements: [datafitness-results.json](C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/datafitness-results.json), [datafitness-followup-results.json](C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/datafitness-followup-results.json). These are audit-only artifacts outside source code.

### Cache Source And Date Coverage

| Persisted source label | Documents / unique tickers | Close points | Earliest / latest stored date | Points per ticker min / median / max |
| --- | --- | --- | --- | --- |
| `public` | 1,662 | 92,944 | 2025-10-22 / 2026-10-08 | 3 / 55 / 220 |
| `yfinance` | 23 | 1,013 | 2026-07-14 / 2026-09-16 | 1 / 46 / 46 |
| Total | 1,685 | 93,957 | 227 distinct date keys across all tickers | Not a uniform history panel |

All cache documents contain exactly `ticker, source, closes, fetched_at`. No duplicate ticker documents were found, so there is **no same-ticker cross-provider overlap** in this snapshot for direct adjustment reconciliation. Actual `yfinance` records show legacy/source provenance not represented by the current pricer writer's Public/Massive source choices described earlier; do not relabel them as Public or Massive.

All 93,957 close values parse as finite positive numbers: **zero nonnumeric, nonfinite, zero/negative values, invalid ISO date keys or weekend dates**. This is scalar validity, not correct-price or complete-session proof. Date keys are `YYYY-MM-DD`, with no timezone, session-close timestamp or adjustment basis. All 1,685 fetched_at values are timezone-aware UTC (`+00:00`), spanning `2026-09-17T06:14:38.993540+00:00` to `2026-10-08T19:04:53.543110+00:00`.

Latest-date distribution is materially uneven: Public caches end on October 7 (832), October 6 (584), October 5 (134), October 8 (82), or earlier (30). Yfinance caches end September 16 (22) or September 9 (1). Five Public tickers and one Yfinance ticker have at most 20 points. These counts describe stored tails, not expected market-session gaps: listings, suspensions and missing-provider coverage were not resolved against an authoritative exchange calendar/security master.

**Current-day caution:** The 82 Public caches containing an October 8 date were fetched no later than 19:04:53 UTC = 15:04:53 America/New_York, before the ordinary 16:00 regular-session close. Their date-labeled values are not established completed daily closes; quarantine that date until a completed-session source is available. The export contains no final-bar flag to resolve this.

Benchmark cache evidence: SPY (`public`) has 220 points, 2025-11-21 through 2026-10-08; QQQ and IWM (`public`) each have 55 points, 2026-07-21 through 2026-10-06. Presence alone does not prove exact entry-time benchmark alignment, complete calendars or dividend-adjusted total returns.

### Corporate-Action Comparability And Jumps

**Adjustment comparability is not established.** Zero cache documents carry split/dividend/corporate-action/adjustment fields; the only four fields are listed above. There are no stored split factors, adjusted-versus-raw close labels, dividend reinvestment conventions, security identifiers, action effective dates or provider version histories in these cache documents. Absence of jumps cannot establish adjusted prices, and an integer-looking ratio cannot prove a split.

Screening successive **stored** closes for ratios `>=1.5` or `<=2/3` found **402 transitions across 177 tickers**, all labeled Public. Thirty-six flagged transitions span more than three calendar days, so they are not necessarily adjacent market sessions. There were zero threshold hits in the 23 Yfinance maps, which is not an endorsement of their adjustment convention.

| Ticker | Stored transition | Close before / after | Ratio | Interpretation |
| --- | --- | --- | --- | --- |
| CFNB | 2026-09-24 to 2026-09-25 | 33.95 / 1,334.00 | 39.293078 | Severe discontinuity; cause unverified |
| GWHWW | 2026-07-06 to 2026-07-08 | 0.0007 / 0.014 | 20.000000 | Sparse/low-price instrument; not split proof |
| JAGX | 2026-09-21 to 2026-09-22 | 2.67 / 34.46 | 12.906367 | Large jump requiring action/source reconciliation |

The cache population includes warrant-like tickers such as GWHWW and SOARW; ticker spelling alone is not an instrument classifier. A pure common-stock equity backtest needs a persisted security master and exclusion policy. Market moves, splits, distributions, illiquidity, ticker reuse or bad prints can produce these patterns; this offline audit does not determine which occurred. No external corporate-action verification was attempted.

### Timestamp Availability: Performance Versus Scans

| Field / population | Present timezone-aware UTC | Missing | Meaning |
| --- | --- | --- | --- |
| signal_performance `date` | 0 | 0 | All 7,643 are date-only strings, range 2026-09-08 to 2026-10-08 |
| signal_performance `observed_at` | 4,237 | 3,406 | Frozen scan observation clock where retained |
| signal_performance `ts` | 3,406 | 4,237 | Legacy clock; not proven provider quote or scan clock |
| signal_performance `created_at` | 7,643 | 0 | Write-time metadata, not entry timing |
| signal_performance `last_seen_at` | 4,498 | 3,145 | Later appearance/write clock |
| signal_performance `entry_quote_timestamp`, `quote_timestamp`, `price_timestamp` | 0 each | 7,643 each | No exact quote clock in this daily ledger |
| scan_results `started_at`, `finished_at`, `created_at` | 377 each | 0 each | Packet clocks |
| scan results row `price_timestamp` / `price_meta.provider_ts` | 5,444 each | 6,921 each | Sampled price clocks; not necessarily executable quotes |
| scan results row `quote_timestamp` / `quote_time` | 0 each | 12,365 each | Confirms field-namespace mismatch risk |
| scan row `price_meta.raw.quoteTime`, `.bidTimestamp`, `.askTimestamp` | 3,928 each | 8,437 each | Raw Public provider/side timestamps available for this subset |

There were zero naive, invalid or non-string values among nonempty timestamp fields tested above. All observed_at values convert to the same **America/New_York date** as their performance date (4,237 matches, zero disagreements); in this subset UTC date also happens to match ET date. That empirical coincidence is not permission to use UTC date in all sessions. observed_at spans September 17 22:30:29 UTC through October 8 19:00:35 UTC; legacy ts spans September 8 23:10:14 UTC through September 17 19:02:18 UTC. Scan finished_at spans July 22 08:51:02 UTC through October 8 19:00:35 UTC: scan history predates the daily performance export's date range.

Scan source counts: Public quote 3,928; Alpaca overnight latest trade 785; Alpaca IEX latest trade 247; Alpaca generic latest trade 90; Alpaca delayed SIP latest trade 394; Yfinance latest close 1,077; unavailable 179; source missing 5,665. The 1,516 timestamped non-Public rows are not authenticated as fresh executable bid/ask quotes by this audit.

Among 5,444 timestamped scan rows, **2,194 price clocks are more than 15 minutes before scan finish**, including **577 more than one day old**; zero are later than packet finish. These elapsed ages are computed directly from timestamps, not persisted freshness booleans. Overnight/closed-market age is not by itself bad data, but these observations cannot be described uniformly as contemporaneous intraday fills. Missing/non-numeric scan price: one row; nonfinite/nonpositive scan prices: zero. The 7,643 performance entries contain 207 missing/non-numeric prices and zero nonfinite/nonpositive numeric prices.

### Entry-Date Price Mismatch Samples

Exact join for this diagnostic: unchanged ticker string + `signal_performance.date` to the cache's identical ISO date key; no normalization, forward-fill, nearest-date fallback or split adjustment. Signed difference is `(entry_price / cached_close - 1) * 100`.

Of 7,643 performance rows, **6,981 have finite positive entry/exact-date-close pairs**: 2,778 differ by at most 1%, and **4,203 differ by more than 1%**. Remaining rows: 260 lack the ticker cache; 278 have no exact date close; 124 have an invalid entry despite a matching cached date. The 124 are a subset of the 207 invalid entries because cache availability is tested first. All performance documents lack a synthetic flag; this does not prove every record is prospective or exclude unlabelled legacy repairs.

| Ticker / date / screener | Stored entry | Public cached same-date close | Signed difference | observed_at |
| --- | --- | --- | --- | --- |
| CTVA / 2026-09-10 / sec_filings | 85.9100036621 | 12.68 | +577.5237% | Missing |
| ZCSH / 2026-09-08 / sec_filings | 91.7099990845 | 30.57 | +200.0000% | Missing |
| JAGX / 2026-09-23 / lottery_dilution_read | 41.84 | 8.91 | +369.5847% | 2026-09-23T04:00:26.053835+00:00 |
| DCX / 2026-09-17 / lottery_day2_continuation | 0.4571 | 70.42 | -99.3509% | Missing |
| CTNT / 2026-09-17 / lottery_day2_continuation | 0.079 | 6.75 | -98.8296% | Missing |

Intraday entries are expected to differ from daily closes, so the 1% diagnostic is **not a count of errors**. However, repeated CTVA gaps above +566%, near-threefold ZCSH scaling and tiny DCX/CTNT entries versus much larger historical closes are material comparability warnings. ZCSH's approximately 3:1 ratio is consistent with an adjustment/identity-scale hypothesis, not confirmation. These samples were not corroborated against matching Core scan rows: the targeted Core scan search returned no rows for these symbols on the selected sample dates; strategy-screener history is a separate source. Do not silently repair the entry to the close or compute apparently valid returns from incompatible price bases.

### Data Fitness Decision

- **Usable with labels:** finite close maps for bounded endpoint diagnostics; timestamped scan observations for their actual source/session; the measured counts above as local-export facts.
- **Not yet fit for corporate-action-comparable equity performance:** cache adjustment bases are unverified, historical sources differ, extreme entry/close disagreements remain unresolved, and current-day cache values may be partial. Quarantine flagged symbols/periods pending persisted corporate-action/security-identity and adjustment evidence; do not infer factors from ratios alone.
- **Not fit for exact entry-time execution or OHLC path claims:** no daily-ledger quote timestamps; only a subset of scans retain provider times; older/missing mark provenance; close-only maps still lack O/H/L, interval ends and verified expected-session coverage. Keep legacy ts distinct from observed_at and provider time.
- Before the owning normalization/replay workflow consumes these inputs, enforce explicit `price_basis_verified`, `source_time_verified`, `completed_session`, `security_identity_verified` and `exact_date_available` statuses. Report exclusions rather than replacing data. These are export/research recommendations, not source-code changes made by this audit.

## Broad Severe-Ratio Classification

Added 2026-10-08 at the user's request. **These are flags, not proven errors, split factors, price corrections or instructions to exclude observations.** The earlier quarantine recommendation applies to making accurate raw-entry alpha claims, not to silently dropping flagged names from the primary counterfactual. Genuine runners and losers can cross these thresholds. The inclusive result remains the primary diagnostic; an unflagged-only result is a separately labelled, potentially selection-biased sensitivity.

### Rule And Input Boundary

Ratio = **first eligible session close / original recorded entry price**. Flag inclusive boundaries `ratio >= 2` or `ratio <= 0.5`, with no ratio fixing, winsorization, price replacement, ticker normalization or return-based selection. Prices are compared only when positive/finite and the exact selected symbol/session close exists. Records outside the session grid, with invalid entries, missing exact prices or unverified current-session completion remain **unresolved**, not unflagged.

For aware `observed_at`, select the first date in the frozen SPY provider-date grid whose **09:30 America/New_York RTH open is at or after observation time**. This follows the next-eligible-open counterfactual, rather than comparing a morning entry to a retrospectively convenient session. The observed SPY grid is not independently verified exchange-calendar evidence. No nearest available symbol date or forward fill is used. October 8 is conservatively unresolved; later-arriving completed bars are not incorporated into this frozen audit.

For raw daily `signal_performance` records lacking observed_at, use the first SPY-grid session on/after the recorded date and explicitly label `date_only_first_session_on_or_after_date_NOT_exact_time_eligible`. Legacy ts is retained but **not assumed to be observation time**. This fallback is a broad date-level comparability diagnostic only; it cannot support an exact next-eligible-open claim.

The complete immutable cache snapshot supplies broad coverage. The main Public OHLC file is read only to a fixed **14,874,382-byte prefix**, **330 successful symbol records**, SHA-256 `1d0b2f3a41c2203ac068b73f667615a671ccca34c35872d861b65a6d4f6cb820`; no duplicate-date conflicting closes were found in that prefix. The file continued growing during the audit. This is **not all 1,749 symbols** and not a completed fetch claim. Public-cache and freshly fetched Public-bar ratios are retained as separate columns, with source/availability statuses. Broad flag = either assessed ratio meets the threshold; unflagged membership = at least one assessed ratio and neither assessed ratio flags. Pending Public coverage therefore does not mean provider-verified unflagged status.

Normalized baseline input: `normalized.json`, **48,537,801 bytes**, SHA-256 `abeae365c2898c7f116aa422b31d6671e3fbf8c076f7805009ef8d793c9fade6`. The observations array was parsed incrementally without rerunning or modifying normalization. Immutable evidence cache hash remains the earlier `ee63c707...184cf`. Audit scripts are [severe_comparability_audit.py](C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/severe_comparability_audit.py) and [severe_signalrecords_audit.py](C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/severe_signalrecords_audit.py). Input boundaries, full hashes, source-specific counts and session rules are persisted in the manifests linked below.

### Normalized Observation Population

**27,828 inclusive records; 22,138 assessed; 91 flagged; 22,047 assessed-unflagged sensitivity members; 5,690 unresolved.** These are normalized observation records, not unique trades, episode counts or daily performance records. All 91 broad flags occur in the cache comparison; 13 also flag in the available Public-bar prefix. No additional Public-only severe flags occur in this prefix. Options-named strategy lanes here compare **underlying equity prices**, not option contract returns.

| Strategy | Inclusive N | Assessed N | Flagged N | Flagged names |
| --- | --- | --- | --- | --- |
| CORE | 12,364 | 8,400 | 1 | PDEX |
| earnings_calendar | 3,092 | 2,877 | 0 | None assessed flagged |
| earnings_core_overlap | 3,071 | 2,966 | 1 | PDEX |
| lottery_catalyst_runner | 16 | 16 | 0 | None assessed flagged |
| lottery_day2_continuation | 303 | 295 | 30 | BENF, CTNT, DCX, DLXY, GCDT, GRML, IMCC, IPDN, MEDS, MGN, NCT, RETO, SDEV, SSM, TRUG, VRME, WHLR |
| lottery_dilution_read | 668 | 547 | 10 | CNXU, IPW, JAGX, OPTT |
| options_breakout_call | 2,595 | 2,300 | 5 | ETHA, ZCSH |
| options_event_defined_risk | 31 | 31 | 0 | None assessed flagged |
| options_leaps_trend | 229 | 196 | 0 | None assessed flagged |
| options_tactical_momentum_call | 5,130 | 4,202 | 44 | AXTX, ETHA, LITZ, NBIZ, QBTX, SMU |
| pharma_calendar | 329 | 308 | 0 | None assessed flagged |

Deliverables:

- [comparability-by-strategy.csv](comparability-by-strategy.csv): per-strategy denominators, source-specific flags, unresolved counts and distinct flagged names.
- [comparability-inclusive-records.csv](comparability-inclusive-records.csv): **all 27,828** normalized observations, including flagged and unresolved rows.
- [comparability-flagged-observations.csv](comparability-flagged-observations.csv): **91** flagged normalized observations with original source references, selected session, original entry, both close/ratio columns and statuses.
- [comparability-unflagged-sensitivity.csv](comparability-unflagged-sensitivity.csv): **22,047** assessed-unflagged normalized observations; selection membership only, not a new primary backtest.
- [comparability-classification-manifest.json](comparability-classification-manifest.json): exact inputs and classification counts.

### Raw Daily Signal-Record Population

This separate pass preserves legacy discoveries such as CTVA that are not present in the normalized observation population above. **Do not add these counts to normalized observation counts or pool the CSVs.** Overlapping signals can occur in both representations.

**7,643 inclusive daily records; 6,840 assessed; 75 flagged; 6,765 assessed-unflagged sensitivity members; 803 unresolved.** All 75 broad flags occur in cache comparisons; six also flag in the available Public bars. There are no additional Public-only severe flags in this prefix. The approximately 2:1/1:2 flag threshold is deliberately much broader than the prior same-date 1% mismatch diagnostic; counts are not directly comparable because the first-session selection also differs where observations follow the RTH open.

| Strategy / screener_id | Inclusive N | Assessed N | Flagged N | Flagged names |
| --- | --- | --- | --- | --- |
| CORE | 760 | 740 | 0 | None assessed flagged |
| earnings_calendar | 603 | 577 | 0 | None assessed flagged |
| earnings_core_overlap | 573 | 562 | 0 | None assessed flagged |
| lottery_catalyst_runner | 3 | 3 | 0 | None assessed flagged |
| lottery_day2_continuation | 147 | 143 | 15 | CTNT, DCX, DLXY, GCDT, IPDN, MGN, NCT, RETO, SDEV, TRUG, VRME |
| lottery_dilution_read | 246 | 206 | 7 | CNXU, JAGX, OPTT |
| options_breakout_call | 1,225 | 1,129 | 2 | ETHA, ZCSH |
| options_event_defined_risk | 11 | 11 | 0 | None assessed flagged |
| options_leaps_trend | 42 | 42 | 0 | None assessed flagged |
| options_tactical_momentum_call | 2,548 | 2,202 | 21 | AXTX, ETHA, LITZ, NBIZ, QBTX, SMU |
| pharma_calendar | 82 | 65 | 0 | None assessed flagged |
| sec_filings | 1,403 | 1,160 | 30 | AREN, BIAFW, BTLN, CTVA, GOSS, IPDN, JAGX, LCGMF, NFE, NXXT, OPTT, TRUG, VIVK, ZCSH |

Deliverables:

- [comparability-signalrecords-by-strategy.csv](comparability-signalrecords-by-strategy.csv): per-strategy daily-ledger counts/names, with unresolved and source-specific denominators.
- [comparability-flagged-signalrecords.csv](comparability-flagged-signalrecords.csv): **75 flagged raw daily signal records**, including date, observed_at/legacy ts, source document key, selection basis, original entry, selected-session closes, ratios and flags.
- [comparability-inclusive-signalrecords.csv](comparability-inclusive-signalrecords.csv): **all 7,643 daily records**, unfiltered inclusive diagnostic.
- [comparability-unflagged-signalrecords-sensitivity.csv](comparability-unflagged-signalrecords-sensitivity.csv): **6,765** assessed-unflagged daily records; a separate sensitivity selection.
- [comparability-signalrecords-manifest.json](comparability-signalrecords-manifest.json): raw-ledger population definition and identical Public-prefix boundary.

### Interpretation And Claim Boundaries

1. **Primary counterfactual stays inclusive:** next-eligible RTH open to horizon close using one Public series avoids mixing legacy entry scale with a different historical denominator. This audit does not replace that entry with recorded entry, change the normalization, filter genuine runners, calculate counterfactual returns or rewrite the fetching runner.
2. **Same provider is not proven adjustment consistency:** split/reverse-split handling, action-time share counts, dividends, identifier continuity and adjusted-versus-raw conventions remain unverified. Provider-open/provider-close returns can still cross an action or include provisional/missing sessions. Keep those limitations on primary results.
3. **Raw-observation endpoints must not claim accurate alpha on flagged cohorts.** Publish raw entry-to-endpoint results, if retained, as inclusive unverified-mark diagnostics with flagged/unresolved counts alongside them, not accurate execution returns or established alpha. Do not calculate corrective split ratios from this screen.
4. **Sensitivity is not remediation:** report inclusive versus assessed-unflagged outcomes side by side, with full denominators and source coverage, only once the owning runner produces returns. These CSVs provide cohort membership, not performance metrics. Excluding real high-volatility runners/winners or losers can systematically alter apparent strategy efficacy. Threshold flags are not a new universe-selection rule.
5. **Provisional Public confirmation:** only 330 fetched symbols were in this audit prefix. Cache-based broad flags and missing/pending Public comparisons remain distinct. A later complete-fetch reclassification needs a new immutable byte/hash boundary, not silent updates to these claims.
