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
