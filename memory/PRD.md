# Stock Intelligence Telegram Bot — PRD

## Original Problem Statement
Build a stock intelligence Telegram bot. Backend-first full-stack app — only UI is Telegram + a simple admin status page. Daily 8 AM ET scan from OpenInsider (insider cluster buys), Finviz (high short interest), Yahoo Finance (upcoming earnings). Pre-filter in code; only stocks with 2+ signals go to Claude (claude-sonnet-4-5). Single batched Claude call. 24h MongoDB cache. Telegram bot commands /scan /analyze /watchlist /alert.

### v2 (Apr 30, 2026)
Added USASpending.gov government contract intelligence as a 4th signal source — 5 new signal types, pure-Python risk score, 3-method price target, expanded Telegram message format, 7 new commands, dashboard updates.

## Stack
- Backend: FastAPI + MongoDB + APScheduler
- Frontend: React (Bloomberg/terminal-style admin dashboard)
- LLM: claude-sonnet-4-5 via Emergent Universal Key (`EMERGENT_LLM_KEY`)
- Telegram: webhook auto-registered at startup
- Data sources: OpenInsider, Finviz, Yahoo Finance, USASpending.gov, yfinance

## Env vars
- `MONGO_URL`, `DB_NAME`
- `EMERGENT_LLM_KEY` (active)
- `ANTHROPIC_API_KEY` (empty — fallback path; user keys hit credit limit)
- `TELEGRAM_BOT_TOKEN` = 8423505655:AAHSAlZvKRrHpc_1I1TtPRStkPGXdIZqVms (Quantninjabot)
- `TELEGRAM_CHAT_ID` = 8073083936
- `PUBLIC_BASE_URL` = preview URL

## Implemented
### v1 (Apr 29)
- 3 scrapers (OpenInsider, Finviz, Yahoo + Finviz earnings) with 2+ signal pre-filter
- Single batched Claude call with 24h cache
- Telegram bot + 7 v1 commands, 8 AM ET cron, 5-min alert checks
- Admin dashboard (terminal aesthetic) with status, results, watchlist, alerts, activity log

### v2 (Apr 30)
- USASpending integration (`services/usaspending.py`): 5 signal types
  - CONTRACT_SURGE (+2): 30d total ≥ 1.4× prior 90d avg, awards >$10M
  - NEW_WINNER (+2): first award from agency in 12 months, >$5M
  - CONCENTRATION_WIN (+3): single contract >$20M to mkt-cap <$2B
  - MOMENTUM_STACK (+2): ≥3 distinct agencies in 30d, cum >$20M
  - BUDGET_SURGE (+2): agency monthly obligations ≥1.5× 3mo avg, attribute to exposed public contractors
- Static recipient → ticker map (~120 public contractors)
- Pure-Python risk scoring (`risk_target.py`): 6+ factors → score → LOW/MEDIUM/HIGH/EXTREME with emoji 🟢🟡🔴☠️ (clamped ≥0)
- 3-method price target: contract revenue multiple (capped at 25% TTM rev), analyst consensus, signal-adjusted uplift; blended with divergence guard (drops contract method if >2× analyst consensus)
- Updated Claude prompt: receives pre-computed risk + targets, generates only thesis/conviction/horizon/stop_loss/score/entry band
- New Telegram message format with ━━━ separators, risk emoji, 3 targets, risk factors, gov contract line
- 7 new commands: /contracts /scan_gov /agency /watchlist_contracts /risk /target /compare
- New API endpoints: /contracts, /agency/{name}, /risk/{ticker}, /target/{ticker}, /compare/{a}/{b}, /scan/gov
- Dashboard: Risk column, Target column with divergence color-warn, gov signal badges (gold/amber), Government Contracts panel, Agency Budget Tracker panel, GOV SCAN button

## Verified
- 16/16 v2 tests pass + 12/12 v1 tests pass
- Token efficiency: 1 batched Claude call for 11 candidates; 2nd run: 0 fresh, 11 cached ✅
- Real Telegram delivery confirmed (msg_id returned, Quantninjabot online)
- Gov signals firing on real tickers (LDOS, BAH, SAIC, LHX, DELL, HON, etc.)

## Backlog
### P1
- Reddit/StockTwits sentiment as a 5th signal source (free)
- /history command — show prior scan deltas
- Persist scheduler state across restarts

### P2
- Twitter/X integration (paid Basic tier)
- Backtesting historical signal accuracy
- Multi-user RBAC for shared deployments
- Active short-seller report detector (currently no free feed)
