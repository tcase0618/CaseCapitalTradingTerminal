# Stock Intelligence Telegram Bot — PRD

## Original Problem Statement
Backend-first full-stack stock intelligence bot. Primary interface: Telegram. Admin dashboard for monitoring. Stack: FastAPI + MongoDB + React. Daily 8 AM ET cron scan with hard token-efficiency requirement — pre-filter in Python, single batched Claude call, 24h cache.

## Stack
- Backend: FastAPI + MongoDB + APScheduler
- Frontend: React (Bloomberg/terminal-style admin dashboard)
- LLM: claude-sonnet-4-5 via Emergent Universal Key (`EMERGENT_LLM_KEY`)
- Telegram: webhook auto-registered at startup (`@Quantninjabot`)
- Data sources: OpenInsider, Finviz, Yahoo Finance, USASpending.gov, yfinance, curated congressional dataset

## Env vars
- `MONGO_URL`, `DB_NAME`
- `EMERGENT_LLM_KEY` (active)
- `ANTHROPIC_API_KEY` (empty — fallback path)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (set)
- `PUBLIC_BASE_URL`

## Implementation History

### v1 (Apr 29, 2026) — MVP
- 3 scrapers (OpenInsider, Finviz, Yahoo + Finviz earnings) with 2+ signal pre-filter
- Single batched Claude call with 24h cache
- Telegram bot + 8 v1 commands, 8 AM ET cron, 5-min alert checks
- Admin dashboard (terminal aesthetic) with status, results, watchlist, alerts, activity log

### v2 (Apr 30, 2026) — USASpending
- 5 gov signal types (CONTRACT_SURGE, NEW_WINNER, CONCENTRATION_WIN, MOMENTUM_STACK, BUDGET_SURGE)
- Static recipient → ticker map (~120 public contractors)
- Pure-Python risk scoring (LOW/MED/HIGH/EXTREME)
- 3-method price target (contract-revenue / analyst consensus / signal-adjusted) with divergence guard
- New Claude prompt schema: receives pre-computed risk+targets, returns only thesis/conviction/horizon/stop_loss
- 7 new Telegram commands + new styled Telegram message format
- Dashboard: Risk + Target columns, gov signal badges (gold/amber), Government Contracts panel, Agency Budget Tracker, GOV SCAN button

### v3 Pass A (May 3, 2026) — Congress / Squeeze / Time / NLQ
- CONGRESSIONAL_BUY signal (+3 committee match, +1 otherwise). Curated public-domain dataset (swappable to paid API later) + committee→sector map
- 4D Squeeze Probability Score (0-100): short_pct × 0.35 + days_to_cover × 0.30 + borrow proxy × 0.20 + 30d ROC × 0.15
- Fiscal-year seasonality multiplier (Jul-Sep → 1.5× weighting of gov signals)
- Specific time-target date on every result + hold-period range (catalyst-aware + per-signal historical timing, clamped to future)
- Natural Language Telegram queries — any non-/-prefixed msg routes to Claude with filtered context (6h cache, ~300 token cap)
- 6 new Telegram commands: /squeeze /congress /performance /backtest /geo /premarket + /add, /remove aliases
- Dashboard: Squeeze + Time columns in scan table, Squeeze Leaderboard panel, Congressional Buys panel, FY banner (conditional)
- Backend endpoints: /squeeze/{ticker}, /squeeze/leaderboard/top, /congress/recent, /performance/summary, /fy/status

## Verified
- 45/45 automated tests pass (17 v3 + 28 v1/v2 regression)
- Token efficiency: single batched Claude call per scan; 0 fresh calls on same-day re-run
- Real Telegram delivery verified on @Quantninjabot
- NLQ routing confirmed: natural-language messages reach Claude with filtered context, cached 6h

## Backlog
### Pass B (P1) — next session
- SAM.gov pre-award solicitation signal (needs registered API key)
- Subcontractor mapping via USASpending sub-awards endpoint
- Geopolitical Trigger Map (Google News RSS every 4h)
- Portfolio P&L tracker with 7/30/90 day returns + signal attribution
- Backtesting engine (needs 24mo historical signal+price data)

### P2
- Swap curated congressional dataset for live API (Finnhub paid / Quiver paid / CapitolTrades)
- Twitter/X sentiment signal
- /digest Stripe checkout for friends to pay for top-3 weekly picks
- Active short-seller report detector

## Notes
- Congressional data is currently a curated public-domain snapshot in services/congress.py (_CURATED list) — clearly labeled and easily swapped for a live source by replacing fetch_recent_buys()
