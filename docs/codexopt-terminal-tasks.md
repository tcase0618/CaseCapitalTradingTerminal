# CodexOpt Evidence - Case Capital Terminal

Use these as recurring validation themes when benchmarking `AGENTS.md` or future project skills.

## VPS Deploy Must Be Proven Live
- Pull `codex/desktop-checkpoint` on `/opt/case-capital/stock-intel`.
- Build frontend and compile touched backend modules.
- Restart `case-capital-terminal` and `nginx` when frontend is built.
- Verify deployed commit, backend service active, `/api/status`, and one feature-specific endpoint.
- Final answer must state what was verified, not just that code was pushed.

## Execution Gate Must Not Rot
- Live position authority and options risk marks are execution-critical.
- If cached execution evidence is stale, repull Alpaca positions and options risk before trusting the QC gate.
- Verify `/api/data_quality/overview`, `/api/scheduler/overview`, and `/api/position_monitor/latest` after scheduler or QC edits.
- The gate should not sit at a low score merely because a restart delayed a scheduled refresh.

## PM Asset Management Comes First
- Before assuming a new scan candidate should be bought, inspect PM route, sizing, current positions, open orders, capital, max-position rules, and replacement/trim logic.
- If the portfolio is full, PM must decide between hold, pass, trim, replace, or queue. Scanner output alone is not buy authority.
- Trade Floor and Options Desk are separate execution surfaces but both remain PM-governed.

## UI Changes Need Real Data Checks
- Command Center panels must not overlap at laptop widths.
- Mobile changes must preserve real tab content below overview cards.
- Tables with live position, option, QC, scheduler, and performance data should handle long values without clipping critical fields.
- Any sortable table must keep totals and row links working.

## Telegram Must Be Grouped And Quiet
- Group routine scan, QC, execution, daily, and weekly updates.
- Avoid sending test messages unless explicitly requested.
- High-severity events can interrupt; normal refreshes should stay quiet.

## Market Data Truth
- Never invent unavailable prices, fills, earnings data, options marks, or macro values.
- If a provider falls back or returns stale data, label it and avoid using it as clean execution authority.
- Backtests and learning engines should distinguish raw, haircut, stale, and unavailable data.
