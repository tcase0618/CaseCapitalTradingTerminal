# Case Capital Trading Terminal — Daily Audit 2026-11-22

Run 44. Read-only. No secrets, account identifiers, environment files or connection strings appear here. No order, preflight or mutation endpoint was touched.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-22 01:50–02:25 EST (UTC−5), Sunday |
| Market session | CLOSED (weekend). Next regular session Monday 11-23 09:30 EST. Thanksgiving 11-26 closed; 11-27 early close 13:00 EST |
| `main` | `c17babc`, unchanged since 11-18 (`ls-remote`). CI run 127 passed; no newer runs |
| VPS branch `codex/desktop-checkpoint` | `d28c80c`, unchanged (CI run 126 passed) |
| `Claude-Gpt-audit` | `24173c3`, unchanged |
| Audit branch | `claude/stoic-knuth-ah2blt` |
| Deployed commit | SHA UNVERIFIED (no SHA endpoint, no SSH) |
| Scope | REPOSITORY + LIMITED LIVE (unauthenticated GETs, GitHub Actions API). `/api/health` 403. HTTPS: connection failed. No broker, database or operator session |

## Live observations (unauthenticated `/api/status`)

| Time (UTC) | HTTP / latency | db_available | protection_status | last_scan_at | results |
|---|---|---|---|---|---|
| 06:51:23Z | 200 / 1.81 s | true | NOT_ASSESSED | 2026-11-20T23:31:11Z | 27 |
| 06:52:00Z | 200 / 1.06 s | true | NOT_ASSESSED | same | 27 |
| 06:52:36Z | 200 / 0.86 s | true | NOT_ASSESSED | same | 27 |

- Samples span 73 s; database reported up (fifth straight day).
- Last scan is still Friday's `evening_scan` (18:30 ET, `backend/services/scheduler.py:76`). Weekend slots are not due (`mon-fri` cron, `scheduler.py:544`), so no scan is missing. 27 results, 27 pre-filter passed, 0 Claude calls.
- Positions, broker orders, quotes and protective orders: **NOT VERIFIED** (`protection_status=NOT_ASSESSED`).

## Verdict

**CRITICAL (code) / DEGRADED (live).** Infrastructure DEGRADED (HTTP only, no backup alert); Data PARTIAL; Entries NOT VERIFIED (MED-021: `OPTIONS_RESEARCH_ONLY` defaults false at `backend/services/options_research.py:21`); Exits CRITICAL (`backend/services/public_execution.py:2310` emergency-exit skip; `:2352` stop-pinned client ID; 24 h claim TTL at `backend/services/execution_safety.py:66`); Learning/Reporting NOT VERIFIED.

## Findings

- **New:** none. **Status changes:** none.
- **Evidence updated:** CRIT-001/002 (code unchanged). **Citation correction:** the 24 h TTL default is at `execution_safety.py:66` (unchanged since d014be1, 2026-10-07); earlier entries cited :67/:68. CRIT-003 (DB up across 73 s).
- **Register:** 58 items (Critical 3, High 16, Medium 21, Low 18). CONFIRMED 45 / SUSPECTED 4 / UNVERIFIED 0 / RESOLVED 9.

## Tests (fresh Python 3.12 venv, `env -i`, no credentials, mocked), main @ c17babc

| Command | Result |
|---|---|
| `pytest backend/tests tests` | **948 passed, 13 skipped, 153 deselected, 0 failed**, 126 subtests, 25 warnings |
| `flake8 backend --select=E9,F63,F7,F82` | 0 findings |

Not exercised: real broker lifecycle, PostgreSQL restore, deployed scheduler registry, nginx/TLS.

## Operator actions before Monday 11-23 09:30 EST

1. Confirm broker-side protection for every Public position.
2. Check OPEN rows with a non-null `emergency_exit_order_id` (CRIT-001).
3. If the options pause is intended, confirm `OPTIONS_RESEARCH_ONLY=true` / `ENABLE_OPTIONS_EXECUTION=false` on the VPS.
4. Confirm the deployed SHA. Note the Thanksgiving-week calendar (11-26 closed, 11-27 13:00 close; MED-013 session detection ignores holidays/early closes).

## Remediation order (unchanged)

1. CRIT-001/002, HIGH-014/015/016 — exit lockout and exit pricing with a bid ≥ limit invariant.
2. MED-021 — deploy-safe options pause.
3. HIGH-013 / MED-020 — backup pruning and alerting.
4. HIGH-012 — release parity.
5. HIGH-011 — TLS.
