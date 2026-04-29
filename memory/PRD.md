# Stock Intelligence Telegram Bot — PRD

## Original Problem Statement
Build a stock intelligence Telegram bot. Backend-first full-stack app — only UI is Telegram. Simple admin status page.

Core features:
1. Daily 8 AM ET scheduled scan from OpenInsider (cluster buys), Finviz (high short interest), Yahoo Finance (upcoming earnings). Pre-filter in code; only stocks with 2+ signals go to Claude.
2. Claude API analysis (claude-sonnet-4-5) — receives pre-filtered tickers, returns structured JSON {ticker, signal_score 1-10, thesis, entry_zone, catalyst_date}. Single batch call.
3. Telegram bot webhook with commands /scan, /analyze, /watchlist, /alert.
4. Telegram delivery — clean formatted messages with emoji.
5. Token efficiency: prefilter in code, batch Claude calls, 24h cache, never call Claude twice for same ticker/day.

User chose: FastAPI + MongoDB + React (env constraint). Emergent Universal LLM key as ANTHROPIC.

## Architecture
- Backend (FastAPI): `server.py` + `services/{db,scrapers,claude_service,scanner,telegram_service,scheduler}.py`
- Frontend (React): single-page Dashboard at `/` (terminal/Bloomberg aesthetic)
- MongoDB collections: `scan_results`, `claude_cache`, `watchlist`, `alerts`, `activity_log`, `bot_state`
- Scheduler: APScheduler — 8AM ET cron daily scan + 5min interval alert checks

## Env vars
- `MONGO_URL`, `DB_NAME` (preset)
- `EMERGENT_LLM_KEY` (set; used for Claude)
- `TELEGRAM_BOT_TOKEN` (placeholder — user provides)
- `TELEGRAM_CHAT_ID` (placeholder — user provides)
- `PUBLIC_BASE_URL` (preview URL — used to register webhook)

## Implemented (Apr 29, 2026)
- ✅ All 3 scrapers (OpenInsider, Finviz short, Yahoo + Finviz earnings) — paginated, deduped
- ✅ 2+ signal pre-filter in code; only candidates go to Claude
- ✅ Single batched Claude call (claude-sonnet-4-5-20250929) returning JSON array
- ✅ 24h cache by (ticker, date) — verified zero re-calls within window
- ✅ Telegram webhook handler + commands: /start /help /scan /analyze /watch /unwatch /watchlist /alert /alerts
- ✅ Auto webhook registration at startup if TELEGRAM_BOT_TOKEN present
- ✅ Daily 8AM ET cron scan + 5min alert price-cross detection
- ✅ Admin dashboard: status, run-scan-now, latest results table, watchlist + alerts CRUD, activity log
- ✅ End-to-end tested: scan→pre-filter→Claude batch→cache works (7 candidates, 7 fresh first run, 7 cached second run)

## Backlog (P1)
- Twitter / Reddit social sentiment as 4th signal source
- More Telegram commands: /history, /top, /clearcache
- Price chart screenshots in Telegram

## Backlog (P2)
- Multi-user support (per chat_id watchlists are already isolated)
- Backtesting historical signal accuracy
- Sector/industry breakdowns
