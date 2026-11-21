# Case Capital Trading Terminal — Daily Audit 2026-11-21

Run 43. Read-only. No secrets, account identifiers, environment files or connection strings appear here. No order, preflight or mutation endpoint was touched.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-21 01:51–02:20 EST (UTC−5), Saturday |
| Market session | CLOSED (weekend). Next regular session Monday 11-23 09:30 EST. Thanksgiving 11-26 closed; 11-27 early close 13:00 |
| `main` | `c17babc`, unchanged since 11-18 (`ls-remote`). CI run 127 passed; no newer runs |
| VPS branch `codex/desktop-checkpoint` | `d28c80c`, unchanged (CI run 126 passed) |
| `Claude-Gpt-audit` | `24173c3`, unchanged |
| Audit branch | `claude/stoic-knuth-zef5i5` |
| Deployed commit | SHA UNVERIFIED (no SHA endpoint, no SSH) |
| Scope | REPOSITORY + LIMITED LIVE (unauthenticated GETs, GitHub Actions API). `/api/health` 403. HTTPS: no response. No broker, database or operator session |

## Live observations (unauthenticated `/api/status`)

| Time (UTC) | HTTP / latency | db_available | protection_status | last_scan_at | results |
|---|---|---|---|---|---|
| 06:52:21Z | 200 / 0.85 s | true | NOT_ASSESSED | 2026-11-20T23:31:11Z | 27 |
| 06:52:46Z | 200 / 0.83 s | true | NOT_ASSESSED | same | 27 |
| 06:53:49Z | 200 / 1.25 s | true | NOT_ASSESSED | same | 27 |

- Samples span 88 s; database reported up (fourth straight day).
- The last scan is Friday's `evening_scan` (configured 18:30 ET, `backend/services/scheduler.py:76`; observed 18:31 ET). Saturday 00:00 is not due (`mon-fri` cron, `scheduler.py:544`), so no scan is missing. 27 results, 27 passed pre-filter, 0 Claude calls.
- Positions, broker orders, quotes and protective orders: **NOT VERIFIED** (no authenticated access; `protection_status=NOT_ASSESSED`).

## Verdict

**CRITICAL (code) / DEGRADED (live).** Infrastructure DEGRADED (HTTP only, no backup alert); Data PARTIAL; Entries NOT VERIFIED (MED-021 options pause fails open on the VPS template — `OPTIONS_RESEARCH_ONLY` defaults false in `backend/services/options_research.py:21`, set only in `backend/.env.example:165`); Exits CRITICAL (`backend/services/public_execution.py:2310` emergency-exit skip and `:2352` stable stop client ID with the 24 h claim TTL at `backend/services/execution_safety.py:67`, unchanged); Learning/Reporting NOT VERIFIED.

## Findings

- **New:** none. **Status changes:** none.
- **Evidence updated:** CRIT-001/002 (code unchanged), CRIT-003 (DB up across 88 s).
- **Register:** 58 items (Critical 3, High 16, Medium 21, Low 18). CONFIRMED 45 / SUSPECTED 4 / UNVERIFIED 0 / RESOLVED 9.

## Tests (fresh Python 3.12 venv, `env -i`, no credentials, mocked), main @ c17babc

| Command | Result |
|---|---|
| `pytest backend/tests tests` | **948 passed, 13 skipped, 153 deselected, 0 failed**, 126 subtests, 25 warnings |
| `flake8 backend --select=E9,F63,F7,F82` | 0 findings |

Not exercised: real broker lifecycle, PostgreSQL restore, deployed scheduler registry, nginx/TLS.

## Operator actions before Monday 09:30 EST

1. Confirm broker-side protection for every Public position.
2. Check OPEN rows with a non-null `emergency_exit_order_id` (CRIT-001).
3. If the options pause is intended, confirm `OPTIONS_RESEARCH_ONLY=true` / `ENABLE_OPTIONS_EXECUTION=false` on the VPS.
4. Confirm the deployed SHA.

## Remediation order (unchanged)

1. CRIT-001/002, HIGH-014/015/016 — exit lockout and exit pricing with a bid ≥ limit invariant.
2. MED-021 — deploy-safe options pause.
3. HIGH-013 / MED-020 — backup pruning and alerting.
4. HIGH-012 — release parity.
5. HIGH-011 — TLS.
