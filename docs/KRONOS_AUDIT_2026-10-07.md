# Kronos Audit - October 7, 2026

## Verdict

Repair the terminal integration and measurement layer before evaluating replacement models. The deployed service is a local heuristic/proxy, not an integration of shiyu-coder/Kronos. No production code, execution flag, order, or learning parameter was changed for this audit. Live requests were read-only and forecast probes used persist=False.

Live evidence collected around 12:01 PM America/New_York (EDT). The current equity executor remains Public and the options executor remains Alpaca paper; this audit does not propose changing those boundaries.

## Confirmed Live Evidence

- Kronos status returned LIVE with a snapshot about four minutes old, four positions, zero mapped PM rows, and three stale-position-context rows.
- An LSE SPY 5m request with limit=60, order=asc, and no date bounds returned September 10, 2003 bars.
- The deployed candle_forecast('SPY', '5m', persist=False) returned ok=True, degraded=False, a newly generated October 7, 2026 timestamp, and features.latest_timestamp September 12, 2003. Its last close was 102.24 and displayed confidence was 80.
- The same provider requested with order=desc returned current October 7, 2026 5m bars around 776. This establishes a request-window bug; it does not establish a defective provider or pretrained model.
- Public held ASST, EVER, STOK, ABEO, BA, RZLT, LMT, LDOS, GIII, and MMS at the read-only check. Kronos forecast EPD, ABBV, CRSP, and IBRX instead.
- There were 7,760 forecast-run documents, zero candle-prediction documents, and 7,715 accuracy snapshots. The latest accuracy snapshot had sample=0.
- A subsequent snapshot-key inventory found 7,747 daily-latest documents but only 30 distinct daily keys. Several individual day keys appeared 289 times. Counts changed slightly while the live scheduler continued running.

## Findings

### K01 - Critical forecast correctness: earliest candles used as current input

backend/services/kronos.py:495 requests ascending candles without start/end bounds. LSE returns the first historical rows under the limit. The same issue affects the accuracy resolver at line 1375, and the frontend's non-SPY charts and sandbox request ascending daily history without bounds.

Fix: fetch the newest bounded window in descending order, normalize timestamps, deduplicate, sort chronologically for calculations, and remove unfinished/future bars. Do not globally change every LSE caller because research/backtests need explicit historical windows.

Regression: provider stub returns 2003 bars for ascending unbounded calls and current descending rows for latest calls; assert the newest completed bar becomes the forecast anchor.

### K02 - High: generated-at freshness masks source staleness

candle_forecast has no source-candle freshness enforcement. status() evaluates the forecast wrapper's generated_at, not the input timestamps or failed context components. The 2003 probe was accepted as non-degraded. A scheduled rerun can therefore refresh the timestamp without refreshing the information.

Fix: expose generated_at, input_as_of, latest completed candle, expected latest session candle, input-age status, and per-source errors separately. Stale/missing historical input must produce an unavailable forecast, not a confident prediction. Closed-market daily bars need session-aware interpretation.

Regression: old, missing, future, incomplete, and valid prior-session timestamps across market-open/closed cases.

### K03 - High: wrong equity book

_latest_context uses trade_floor.list_positions() and open_positions_view(), both scoped to Alpaca. The runtime forecast omitted the ten Public holdings and instead used four Alpaca/legacy records. _instrument_rows also permits Alpaca option positions to enter its equity branch without instrument-type filtering.

Fix: read Public portfolio and Public-scoped ledger for equities; keep Alpaca option contracts in the distinct option branch. Normalize broker-native position shapes explicitly and preserve account/broker identity. Report a provider failure instead of silently substituting another book.

Regression: mixed Public equity, Alpaca option, and legacy paper fixtures; no duplicated or misclassified contracts.

### K04 - High: portfolio forecasts are PM-conditioned fixed scenarios

_bias assigns fixed returns such as +5.5 for ACCUMULATE, +3.8 for STARTER, and +1.2 for WATCH. _horizons multiplies these constants. _probabilities and confidence are formulas, not calibrated outcome probabilities. This can appear unchanged every day even after the data refresh is repaired.

Fix: label the existing output heuristic/scenario-only, separate it from independent forecasting, and remove unsupported calibrated-probability claims. Define targets/horizons and validate a numerical model before presenting probabilities as measured forecasts. No change to PM execution authority is implied.

### K05 - High: dead scheduled prediction-to-learning path

refresh_snapshot calls forecast, which calls market_forecast, which invokes candle_forecast_suite with persist=False. The scheduled task therefore writes forecast/accuracy snapshots but not the candle predictions that candle_accuracy reads. Live count was zero predictions and zero measured samples despite thousands of accuracy snapshots.

Fix: an explicit scheduler-owned prediction ledger with immutable prediction identity, model version, input hash, anchor price/time, target time, horizon, and source quality. Persist a new prediction only for new valid completed inputs. Browser page visits must not own evidence collection.

Regression: run scheduler-shaped generation twice on the same input; exactly one prediction per symbol/timeframe/target/model, then a resolvable outcome.

### K06 - High: daily snapshot identity breaks in PostgreSQL

The service upserts by snapshot_key, but postgres_store.doc_key does not register that collection's snapshot_key and falls back to changing generated_at. _persist_updated calculates the key after replacing the timestamp and writes another row without removing the previous key. Runtime duplicate daily keys confirm the effect.

Fix: give daily snapshots a stable natural key (or explicit immutable _id); add focused adapter regression coverage. Preserve append-only full runs separately. Back up and repair duplicate daily-latest rows with deterministic newest-per-day selection; do not delete the historical run ledger.

### K07 - High measurement: calendar truncation and misleading day counts

calendar_month reads the oldest 500 ascending snapshot rows; duplicates can exhaust that limit in about two days, hiding later dates. status.scored_days counts documents, not days or resolved outcomes; it reported 1,908 for the current month. Calendar keys are UTC dates although the operator uses America/New_York.

Fix: ET session/date keys, distinct daily records, bounded one-per-day retrieval, and separate predicted-day/resolved-day counts. Validate month boundaries and DST.

### K08 - High measurement: horizon and outcome alignment

candle_accuracy uses the first bar-open timestamp later than prediction generation and its close, without checking completion or a stored target timestamp. It requests an unbounded oldest window. Calendar evaluation compares the latest daily snapshot's short-horizon forecast to a whole-day SPY return. Disagreement resolution uses the first daily close on/after generation through the latest available close rather than a frozen fixed horizon.

Fix: explicit completed-bar availability times and target horizon, frozen outcome windows, immutable issuance anchors, no same-day close look-ahead, and matched benchmark periods. Do not call an intraday next-bar forecast a full-day forecast.

### K09 - High: daily close fallback impersonates intraday OHLCV

When SPY candle coverage is short, candle_forecast synthesizes open=high=low=close and volume=0 from daily close history, with empty timestamps, but retains the requested intraday timeframe. It is marked degraded but still returns numerical candle predictions when enough values exist.

Fix: prohibit timeframe substitution. A daily-close-only scenario must be explicitly daily, separate, and unable to enter the intraday accuracy ledger.

### K10 - Medium: browser refresh does not refresh the selected candle panel

KronosPage refreshes global state each minute, but the chart/candle-suite effect depends only on chartChoice.ticker. Leaving the same ticker selected does not retrigger its price history and candle request. Force refresh updates the portfolio payload but not this effect.

Fix: one bounded refresh controller for selected symbol/timeframe and server-owned forecast state; display input_as_of next to each panel. Keep prior data visibly aged when requests fail rather than labeling local render time as market freshness.

### K11 - Medium: competing request paths and timeout/load amplification

The page's recurring GET /forecast?persist=true regenerates and writes forecasts. It concurrently requests accuracy and learning, and learning itself recalculates accuracy. Scheduler refreshes compute the same expensive work. LSE calls are serialized by a single semaphore; four forecast timeframes can queue behind grading. Frontend 12-16 second timeouts can expire while provider requests allow 60 seconds.

Fix: scheduler-owned generation and grading, GET endpoints that read persisted snapshots, non-overlapping jobs, bounded isolated forecast work, cached read views, and duration/error observability. Protect portfolio monitoring from research-job contention.

### K12 - Medium: PM replay and percentage normalization are inconsistent

battle_card re-evaluates only the Core scanner with DEFAULT_EQUITY and BALANCED mode instead of consuming the actual persisted merged terminal PM decision. _pct treats any value within +/-2 as a fraction, so already-percent values such as 1.2 become 120. Several `or` chains replace legitimate zero scores or P&L values.

Fix: consume the actual cycle-scoped PM decision and company/held-position context; explicit unit-specific normalizers and missing-value checks. A row absent from the latest Core scan is not by itself stale company knowledge.

### K13 - Medium: learning/reporting claims exceed the evidence

Current model output lacks a model/checkpoint version and baseline comparison. Zero mature candle predictions cannot establish forecasting quality or improving calibration. Scaling a heuristic confidence is not proof that its probabilities are calibrated. A wide cone can achieve coverage without useful predictive value.

Fix: immutable model registry and benchmarks, direction base-rate baseline, no-change/random-walk prices, interval coverage plus width, MAE/RMSE and proper probability scoring where a probability target exists. Validate out of sample by timeframe, regime, and strategy; make learning status reflect actual samples and last successful resolver run.

### K14 - Medium: insufficient dedicated contract regression coverage

No dedicated Kronos test module was found in the current test-file inventory. Existing tests elsewhere may touch related code, so this is not a claim of zero indirect coverage. The live 2003-input and wrong-book failures clearly escaped current tests.

Fix: add tests for latest-window retrieval, source freshness, Public/Alpaca context separation, scheduler persistence, stable PostgreSQL identity, calendar grouping, closed-bar/horizon resolution, and selected-panel refresh behavior.

## Model/Repository Decision

The official foundation-model repository is https://github.com/shiyu-coder/Kronos. It loads a tokenizer and learned model through KronosPredictor; no such model loading/inference path was found in the deployed local Kronos service. Official documentation lists a 4.1M-parameter mini model, 24.7M small model, and 102.3M base model. These specifications do not establish an edge for this book.

Do not switch repositories to solve an ascending-history request bug. Repair the plumbing first, preserve the old engine as a labeled heuristic baseline, and evaluate any actual Kronos implementation as an isolated research-only challenger. Measure CPU/memory/latency before placing inference on the execution VPS. No automatic promotion to PM sizing or orders.

## Dependency-Ordered Build Plan

1. Fix latest completed input selection and session-aware input freshness; eliminate synthetic intraday fallback.
2. Correct Public equity/Alpaca option context and consume real persisted PM decisions.
3. Repair stable PostgreSQL daily identity and migrate duplicates with a backup; correct calendar dates/counts.
4. Make forecast generation/persistence scheduler-owned; read-only frontend endpoints and selected-panel refresh.
5. Rebuild target-time/anchor-based grading and immutable prediction deduplication; collect actual resolved observations.
6. Remove unsupported confidence/probability claims and add benchmark/calibration diagnostics.
7. Add isolated official-Kronos inference as a research challenger only after the baseline and data contract are trustworthy.
8. Promote a forecasting model only after held-out results beat relevant nulls with measured costs/uncertainty. Infrastructure correctness alone does not imply trading alpha.

## Sources

- LSE official SDK candle contract: https://github.com/londonstrategicedge/lse-data/blob/main/lse/client.py
- Official Kronos model and examples: https://github.com/shiyu-coder/Kronos
- Kronos research paper: https://arxiv.org/abs/2508.02739

## Limitations

This audit verified selected deployed records and live read-only provider outputs. It did not interactively reproduce the frontend in a browser, load the official neural model, run an out-of-sample profitability backtest, or repair/deploy findings. UI defects are established from source dependency/request paths, not a claimed visual test.
