# Case Capital Trading Terminal — Daily Audit 2026-11-23

Run 45. Read-only. No secrets, account identifiers, environment files, webhook URLs or connection strings appear here. No order, preflight or mutation endpoint was touched.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-23 01:51–02:30 EST (UTC−5), Monday |
| Market session | CLOSED (overnight). Regular session opens today 09:30 EST. Thanksgiving 11-26 closed; 11-27 early close 13:00 EST |
| `main` | `c17babc`, unchanged since 11-18 (`ls-remote`). CI run 127 passed; no newer runs |
| VPS branch `codex/desktop-checkpoint` | `d28c80c`, unchanged (CI run 126 passed) |
| `Claude-Gpt-audit` | `24173c3`, unchanged |
| Audit branch | `claude/stoic-knuth-lmm57u` |
| Deployed commit | SHA UNVERIFIED (no SHA endpoint, no SSH) |
| Scope | REPOSITORY + LIMITED LIVE (unauthenticated GETs, GitHub Actions API). `/api/health` 403. HTTPS: connection failed (000). No broker, database or operator session |

## Live observations (unauthenticated `/api/status`, handler `backend/server.py:299`)

| Time (UTC) | HTTP / latency | bot.db_available | bot.protection_status | last_scan_at | results |
|---|---|---|---|---|---|
| 06:52:58Z | 200 / 1.39 s | true | NOT_ASSESSED | 2026-11-23T05:01:32Z | 39 |
| 06:53:34Z | 200 / 3.41 s | true | NOT_ASSESSED | same | 39 |
| 06:54:13Z | 200 / 1.33 s | true | NOT_ASSESSED | same | 39 |

- Samples span 75 s; database reported up (sixth straight day).
- **Scheduler resumed after the weekend:** `last_scan_at` advanced from Friday's `evening_scan` (2026-11-20T23:31Z) to Monday's `midnight_scan` (00:00 ET slot, `backend/services/scheduler.py:64`, `mon-fri` cron `:544`), finishing 00:01:32 EST. 39 results, 39 pre-filter passed, 0 Claude calls. Persisted cycle row, Telegram delivery and frontend parity: NOT VERIFIED.
- Positions, broker orders, quotes and protective orders: **NOT VERIFIED** (`protection_status=NOT_ASSESSED`).

## Verdict

**CRITICAL (code) / DEGRADED (live).** Infrastructure DEGRADED (HTTP only, no backup alert); Data PARTIAL; Entries NOT VERIFIED (MED-021: `OPTIONS_RESEARCH_ONLY` defaults false at `backend/services/options_research.py:21`); Exits CRITICAL (`backend/services/public_execution.py:2310` emergency-exit skip; `:2352` stop-pinned client ID; 24 h claim TTL at `backend/services/execution_safety.py:66`) — unchanged going into today's open; Learning/Reporting NOT VERIFIED.

## Findings

- **New:** none. **Status changes:** none.
- **Evidence updated:** CRIT-001/002 (code unchanged, first regular session since last fix review is today); CRIT-003 (DB up across 75 s); LOW-006/MED-018 context (Monday midnight scan observed).
- **Register:** 58 items (Critical 3, High 16, Medium 21, Low 18). CONFIRMED 45 / SUSPECTED 4 / UNVERIFIED 0 / RESOLVED 9.

## Tests (fresh Python 3.12 venv, `env -i`, no credentials, mocked), main @ c17babc

| Command | Result |
|---|---|
| `pytest backend/tests tests` | **948 passed, 13 skipped, 153 deselected, 0 failed**, 126 subtests, 25 warnings (14.5 s) |
| `flake8 backend --select=E9,F63,F7,F82 --count` | 0 |

Not exercised: real broker lifecycle, PostgreSQL restore, deployed scheduler registry, nginx/TLS.

## Operator actions before 09:30 EST today

1. Confirm broker-side protection for every Public position.
2. Check OPEN rows with a non-null `emergency_exit_order_id` (CRIT-001).
3. If the options pause is intended, confirm `OPTIONS_RESEARCH_ONLY=true` / `ENABLE_OPTIONS_EXECUTION=false` on the VPS.
4. Confirm the deployed SHA. Thanksgiving week: 11-26 closed, 11-27 13:00 close (MED-013).

## Remediation order (unchanged)

1. CRIT-001/002, HIGH-014/015/016 — exit lockout and exit pricing with a bid ≥ limit invariant.
2. MED-021 — deploy-safe options pause.
3. HIGH-013 / MED-020 — backup pruning and alerting.
4. HIGH-012 — release parity.
5. HIGH-011 — TLS.
