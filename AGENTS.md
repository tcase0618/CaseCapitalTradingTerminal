# Case Capital Trading Terminal - Codex Operating Rules

## Mission
Case Capital is a paper-trading terminal with live market-data, portfolio-manager, options-desk, lottery, QC, scheduler, Kronos, and Telegram workflows. Treat data freshness, order safety, and VPS deployment verification as first-class requirements.

## Non-Negotiables
- Never fabricate prices, fills, scan results, or API health. Report stale, unavailable, partial, or unverified data explicitly.
- The VPS is the primary running environment. Local changes are incomplete until pushed to `codex/desktop-checkpoint`, deployed to the VPS, and verified through live API checks when the user asks for a push/deploy.
- Secrets stay in environment files or platform configuration. Do not print API keys, bot tokens, passwords, or broker secrets in final responses.
- Execution authority must use fresh Alpaca position and order data. If live positions or options risk marks are stale, repull before trusting the execution gate.
- The PM is the routing authority. Trade Floor, Options Desk, Lottery, Case Court, Kronos, and scanners may advise or execute their scoped duties, but they must not override PM ownership rules unless explicitly designed as a safety exit.
- Stock scans keep their scheduled cadence but only run on valid market-session days. Other data pulls may run 24/7 according to their own scheduler rules.

## Required Verification Before Claiming Done
- Backend Python touched files compile with `python -m py_compile`.
- Frontend changes build with `npm --prefix frontend run build` using `--legacy-peer-deps` when installing.
- For VPS deploys, verify:
  - deployed commit with `git log -1 --oneline`
  - `systemctl is-active case-capital-terminal`
  - `curl http://127.0.0.1:8001/api/status`
  - relevant endpoint for the feature changed, such as `/api/data_quality/overview`, `/api/scheduler/overview`, `/api/position_monitor/latest`, `/api/options_desk/risk`, or `/api/admin/trading_status`
- For mobile UI work, verify tab content exists and metrics remain readable, not just that the page scrolls.
- For Telegram work, avoid spam. Prefer grouped messages and do not send live test messages unless the user explicitly asks.

## Deployment Notes
- Primary VPS path: `/opt/case-capital/stock-intel`.
- VPS branch: `codex/desktop-checkpoint`.
- VPS public IP currently used by the terminal: `129.121.101.96`.
- Use `npm install --legacy-peer-deps` for frontend dependency installs.
- A dirty VPS `frontend/package-lock.json` has appeared during deploys before; do not let it distract from verifying the deployed commit and build unless it blocks the pull.

## Product Standards
- The UI should feel institutional, dense, readable, and data-first.
- Avoid decorative changes that hide data or create overlap.
- New dashboards must handle narrow/mobile screens without horizontal drift or nested scroll traps.
- Scheduler/QC features should self-diagnose stale data and expose repair actions without slowing the execution path.
- Trading and forecast features should create append-only evidence where practical so strategy quality can be audited later.
