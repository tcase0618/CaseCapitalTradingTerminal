# Stock Intelligence Telegram Bot — AXIOM v3.0
A token-efficient stock intelligence platform combining Telegram bot delivery,
free-source signal scanning, batched Claude analysis, options intelligence,
P&L tracking, backtesting, and a progressive learning engine.

## Architecture
- Backend: FastAPI on **port 8001** (supervisor)
- DB: **MongoDB** — `MONGO_URL` in `/app/backend/.env`
- Frontend: React (port 3000) — multi-page CRT Bloomberg-terminal aesthetic
  (Courier New, amber `#c8a84b`, scanlines, no rounded corners)
- LLM: Claude Sonnet 4.5 via Emergent Universal Key, 24h cache
- Bot: Telegram webhook `@Quantninjabot`, Chat ID `8073083936`
- Scheduler: APScheduler — 3 scans/day (08:00/12:01/15:30 ET), 5m alerts,
  15m unusual-flow refresh, nightly P&L refresh, Sunday 02:00 learning cycle

## What's Implemented
### Core Intelligence
- OpenInsider, Finviz, USASpending, Quiver/curated congressional scrapers
- 11 signal types: insider_cluster_buy, high_short_interest, upcoming_earnings,
  CONTRACT_SURGE, NEW_WINNER, CONCENTRATION_WIN, MOMENTUM_STACK, BUDGET_SURGE,
  CONGRESSIONAL_BUY, UNUSUAL_FLOW, CALL_SWEEP
- Pure-Python risk + 3-method targets + stop loss + squeeze + time targets
- FY seasonality multiplier (Jul-Sep gov signals ×1.5)
- Batched single Claude call per scan, 24h cache

### Options Intelligence (v3.0)
- `services/options_engine.py` — chain fetch via yfinance, NaN-safe IV extraction,
  strategy selector (LONG_CALL / BULL_CALL_SPREAD / LOTTERY_CALL / AVOID_OPTIONS),
  best-contract finder, spread builder, unusual flow detection, IV crush risk
- **All functions never raise** — return None or clean dict on failure
- New API endpoints: `/api/options/{ticker}`, `/api/flow/{ticker}`, `/api/iv/{ticker}`,
  `/api/spread/{ticker}`, `/api/options/flow/today`, `/api/options/low_iv`

### P&L Tracker (v3.0)
- `services/pnl_tracker.py` — every scan pick recorded; nightly job fills
  7/30/90d returns; options return logged as both **delta×stock-move proxy**
  AND **actual price refetch** for accuracy comparison
- API: `/api/pnl/refresh`, `/api/performance/summary`

### Backtest Engine (v3.0)
- `services/backtest.py` — **synthetic congress replay** seeded against
  historical yfinance prices; forward engine grows from each daily scan
- API: `/api/backtest/seed`, `/api/backtest/summary`
- Verified: 12 synthetic congress trades seeded with real returns immediately

### Progressive Learning Engine (v3.0)
- `services/learning_engine.py` — weekly Sunday 02:00 cycle adjusts 13 signal
  weights based on 30d win rates. Min 10 samples per weight, ±15% max change,
  hard floors/ceilings enforced.
- All 13 default weights persisted on startup via `ensure_weights_exist()`
- API: `/api/learning/status`, `/api/learning/run`, `/api/learning/reset`,
  `/api/learning/combos`, `/api/learning/runs`
- Insights auto-broadcast to Telegram on completion

### Frontend (React Router multi-page)
- `/` — Main Bloomberg-style dashboard (bigger fonts 13-15px, options panel inline,
  clickable tickers, new badges for UNUSUAL FLOW / CALL SWEEP / IV CRUSH)
- `/learning` — Learning Engine status, weight tracker, combo performance
- `/performance` — P&L summary, signal combos, options strategy returns,
  backtest synthetic + forward
- `/ticker/:ticker` — Deep-dive per-ticker page with options, flow, P&L history
- `/settings` — Integration status, scheduler jobs, learning config, manual triggers

### Telegram Commands (full set)
- Scans: `/scan`, `/scan_gov`, `/analyze TICKER`
- Government: `/contracts`, `/agency NAME`, `/watchlist_contracts`
- Analysis: `/risk`, `/target`, `/squeeze`, `/compare T1 T2`, `/performance`,
  `/backtest`, `/backtest_seed`
- Options (NEW): `/options`, `/flow`, `/iv`, `/spread`, `/calls`, `/puts`, `/noiv`
- Tracking: `/watch`, `/unwatch`, `/watchlist`, `/alert`, `/alerts`
- Live: `/congress`, `/geo`, `/premarket`
- Plus NLQ — any plain-English message routes to Claude

## Bug Fixes
### May 2026 — Performance page fetch race condition + slow tracker
- Root cause 1: `Promise.all` in `useEffect` rejected on the slowest endpoint, blanking all dashboard data when one of 10 calls was slow
- Root cause 2: `/api/signals/tracker` was hitting yfinance batch download live (8+ seconds, 58 tickers)
- Fixed:
  - Both Dashboard and Performance pages now use independent `axios.get().then().catch()` so one slow/failing endpoint can't blank the rest
  - Added `price_cache` MongoDB collection with 10-min TTL → tracker endpoint went from 8.2s to **0.12s** on cached pass
- Verified: 58 signals tracked, real entry vs current prices, gain $ + gain % displaying correctly

### May 2026 — UI requests
- Top-left logo: `INTEL_SYS` → **`AXIOM`** (all caps, larger font)
- Added **PRE-FILTER UNIVERSE** stat tile at top of Dashboard showing total tickers swept
  across S&P 500 · NASDAQ · Russell 2000 (430 in latest scan)
- Added **ALL BUY SIGNALS — DAILY P/L** section on Performance page tracking every
  ticker AXIOM has ever surfaced (signal date, entry price, current price, gain since)
- Bumped all dashboard fonts to hedge-fund-readable sizes (8-11px → 13-15px body, 24-26px metrics)

## What's Implemented
### Feb 2026 — Telegram delivery dropping all stocks
- Stray `<` chars in dynamic content broke Telegram HTML parse mode
- Fixed: `_esc()` HTML-escape helper + greedy N-message chunking (never splits
  mid-card) + plain-text fallback on parse errors
- Verified: 30 stocks → 3 messages, all delivered

### May 2026 — Options engine NaN crash
- yfinance returns NaN/missing `impliedVolatility` for illiquid tickers (CRWD, LMT)
- Old code: `int(NaN)` raised `ValueError: cannot convert float NaN to integer`
- Fixed: `_is_nan()`, `_safe_int()`, `_safe_float()` helpers throughout;
  `analyze_ticker` wrapped in top-level try/except — never raises
- Verified: 13/18 stocks in real scan now carry full options data, zero crashes

## DB Collections (all stamped with feature_version=3.0 + created_at)
- `scan_results` — historical scan output
- `claude_cache` — 24h LLM cache
- `nlq_cache` — 6h NLQ cache
- `signal_performance` — every scan pick, 7/30/90d returns
- `options_performance` — strategy + proxy + actual returns
- `backtest_results` (forward) + synthetic rows in `signal_performance` with
  `synthetic=true` flag
- `learning_weights` — 13 dynamic signal weights
- `learning_runs` — every weekly cycle's full record
- `combo_stats` — per-combo win rates and avg returns
- `flow_snapshots` — 15-min unusual flow time series
- `watchlist`, `alerts`, `congress_trades`, `bot_state`, `activity_log`

## Pending / Future
- Subcontractor mapping (USASpending sub_awards) — P1
- Geopolitical Trigger Map (Google News RSS, every 4h) — P1
- SAM.gov pre-award solicitation signal — P2
- Migrate congressional scraping to paid API — P2
- Massive API as primary price source (already wired with yfinance fallback) — P2

## Verified Working
- Real scan: 18 tickers surfaced, 13 with full options data, zero crashes
- Backtest seed: 12 synthetic congressional trades, real historical returns
- Dashboard renders with options inline + AXIOM score + IV crush badges
- All 5 routes resolve: `/`, `/learning`, `/performance`, `/ticker/:ticker`, `/settings`

## Feb 2026 — v4: Massive API + Options Curve + Learning Page Expansion
- **Massive API (Polygon.io rebrand) integrated** as primary price source via new `services/pricer.py`. Live key plugged into `/app/backend/.env` as `MASSIVE_API_KEY`. Two new MongoDB caches: `price_cache` (10-min TTL) and `price_history_cache` (24h TTL).
- **`/api/admin/refresh_prices`** rewrites current price cache only (no longer touches entry prices). UI exposes a `[ REFRESH PRICES ]` button on the Performance page.
- **`/api/admin/price_source`** drives the green `SRC · MASSIVE` badge at top-right of the Performance page.
- **AXIOM OPTIONS PERFORMANCE curve** — twin of stock curve, teal `#5eead4`. Sources from `options_performance` collection. New endpoint `/api/signals/options_curve?days=N`. Reusable `PerfCurve` component to avoid duplication.
- **Learning page rebuild**: PENDING ADJUSTMENTS dry-run card, WEIGHT EVOLUTION OVER TIME multi-line chart, SIGNAL LIFETIME PERFORMANCE league table, PENDING CHANGES tile.

## Feb 2026 — v5: Price Source Fixed + Learning Engine LIVE Basis
- **ROOT BUG**: Massive free-tier 5-req/min rate limit caused 53/58 ticker price fetches to fail. Fixed by routing current-prices through `yf.download` batch (1 HTTP call for all 58 tickers, intraday-delayed) and reserving Massive for historical date queries via the grouped-daily endpoint (1 call per date returns all 12,000+ tickers).
- **Intraday entry-price restore**: previous Massive refresh overwrote intraday entry fills with day-end closes (entry==current==0% gain). New `/api/admin/restore_entry_prices` walks `scan_results` and recovers the original yfinance intraday price. Verified live: 53/58 entries restored to truth.
- **Learning engine LIVE basis**: New `_collect_live_trades()` treats EVERY `signal_first_seen` row (with current price) as a trade. `MIN_SAMPLES_LIVE=3` (vs 30d `MIN_SAMPLES=10`). Live-basis confidence capped at 60% to weight 30d data heavier when both exist.
- **Per-scan combo refresh**: `scanner.run_scan` now calls `learning_engine.refresh_combo_stats_live()` after every scan.
- **Combo threshold lowered** from `>=3` to `>=2` to surface live data quickly.

## Feb 2026 — v6: Finnhub Real-Time Quotes Added
- **Finnhub free-tier integrated** as the primary current-price source. Live key in `/app/backend/.env` as `FINNHUB_API_KEY`. Real-time quotes (30-second freshness), 60 req/min throttled internally via rolling window.
- **Smart routing for current prices**: Finnhub primary → Massive grouped backfill → yfinance fallback.

## Feb 2026 — v7: Dashboard Polish + Finviz Scraper Repair + Universe Truthfulness
- **Critical bug fix**: Finviz changed URL pattern from `quote?t=` to `stock?t=` — our regex no longer matched, so `high_short_interest` returned 0 (~70% of the universe was missing). Both `fetch_finviz_high_short_interest` and `fetch_finviz_upcoming_earnings` patched. Universe went from 69 → 422, short signals 0 → 300, targets 4 → 10.
- **"PRE-FILTER UNIVERSE" tile relabeled to "UNIVERSE SWEPT"** with honest sub-label (`INSIDER N · SHORT N · GOV N`) instead of the misleading `S&P 500 · NASDAQ · RUSSELL 2K`.
- **Dashboard upgraded to match Performance/Learning aesthetic**: sticky SystemBar at top, accent stripe along metrics row, primary-tile amber bar + glow, color-coded tiles per signal type (insider purple / short red / gov teal / uplink green), tabular numerals, hover effects.

## Feb 2026 — v3.2: 7-FEATURE MEGA RELEASE
**Built across 6 new services + 9 new endpoints + 3 new frontend pages.**

### New backend services
- `services/dark_horse.py` — Institutional accumulation detector via FINRA CNMS daily short-volume CSVs (`cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt`). Computes off-exchange ratio, block-size %ADV, and premium-above-prev-close. Caches FINRA file 12h.
- `services/x_factor.py` — Sentiment surge detector. StockTwits (mentions + bullish %) and Google Trends (search interest). Reddit disabled (now requires OAuth post-2023; reactivate when client_id/secret supplied). `evaluate_x_factor(ticker, fast=True)` skips Google Trends in scan-time path.
- `services/macro_pulse.py` — FRED upcoming-events tracker. Live FRED key in `.env` as `FRED_API_KEY`. Tracks FOMC, CPI, PPI, Jobs, GDP, Retail. Each event maps to warned/boosted sectors. `should_block_long_call(industry)` is the recommendation gate.
- `services/earnings_engine.py` — Full Mon-Fri earnings schedule + Beat Probability model (5-95%, clamped). 5 components: EPS streak, momentum 20D, revenue accel, short %, options-flow blend (60/40). Strategy selector emits LONG CALL / CALL SPREAD / AVOID / BEAR PUT SPREAD.
- `services/lottery.py` — 4-factor lottery scorer (squeeze · flow · catalyst · cheap IV) + SI bonus. Tiers JACKPOT≥80 / HOT≥65 / WARM≥50 / COLD<50. Contract finder picks calls 10-20% OTM, $0.10-$0.75 premium, 14-28 DTE. EV math (P(2x), P(10x), P(loss)). **Auto-buys every pick scoring ≥ 50 (JACKPOT/HOT/WARM)** into `lottery_history` for live track-record tracking.
- `services/conviction.py` — Max Conviction scoring (8 component flags), Top 3 daily designation, Narrative Lock detector (Dark Horse + X Factor + flow≥75 on same ticker — auto-elevates lottery tier to JACKPOT + adds 20 to AXIOM score).

### Scanner pipeline rewire
`scanner.run_scan()` now runs all 7 modules in sequence after the core scan: Dark Horse · X Factor · Macro · Earnings (parallel), then Lottery (uses results) → Conviction + Narrative Lock (uses all). Verified live in 14.3s for 10 results / 422 universe.

### New endpoints (all under `/api/v32/...`)
- `GET /v32/earnings_week` — full Mon-Fri grouped schedule
- `GET /v32/lottery` + `/v32/lottery/current` — track record + active picks
- `GET /v32/dark_horse` + `/v32/x_factor` — recent alerts
- `GET /v32/sentiment/{ticker}` — single-ticker live StockTwits + Google Trends
- `GET /v32/macro` — upcoming events + imminent warnings
- `GET /v32/conviction` — Top 3 + Narrative Locks (14d)

### Telegram v3.2 dispatch format (replaced existing)
Header: UNIVERSE / ACQUIRED / BATCH counts · raw-source breakdown · Macro CLEAR/WARNING line.
Per-card: AXIOM badge (🟢 if Max Conviction), Dark Horse line, X Factor line, Narrative Lock line, options strategy, Lottery tier + EV.
Footer: 🎰 LOTTERY · 📅 EARNINGS · 🐴 DARK HORSE · 🔒 NARRATIVE LOCK summary roll-ups + scan duration.

### Frontend
- **New sidebar** with grouped sections (CORE / v3.2 with `NEW` badge / ANALYSIS / SYSTEM), hover transitions, blinking active-state dot.
- **New pages**: `/earnings` (Mon-Fri table with Beat Prob color coding + AXIOM MATCH badge), `/lottery` (tier-stat tiles + current scan + track record tabs), `/intel` (Max Conviction Top 3 cards + Dark Horse table + X Factor table + Macro Pulse calendar).
- All pages use new CrtShell — sticky SystemBar, corner brackets, fade-in animations.




## Feb 2026 — v3.2.1: Lottery Auto-Buy + Track Record P&L
- **Threshold lowered from JACKPOT/HOT (≥65) to score ≥ 50** (any WARM-or-better pick).
  Every qualifying pick with a discovered contract is auto-logged into `lottery_history`
  with `auto_bought: true`.
- **`lottery.refresh_settlements()`** — re-fetches the live ask from yfinance for every
  open lottery row; when the contract's expiration is past, freezes `settled_ask` (treats
  no-data as $0 worthless). Wired into the nightly `_pnl_refresh_job`.
- **`POST /api/v32/lottery/refresh`** — manual settlement trigger.
- **`track_record()` extended** with `open`, `unrealized_avg_pct`, `unrealized_winners`.
- **LotteryPage UI** — new `OPEN POSITIONS` + `UNREALIZED P&L` stat tiles; TRACK RECORD
  table now shows `CURRENT` ask + `OPEN` status for live positions plus running P&L.
