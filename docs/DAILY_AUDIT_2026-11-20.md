# Case Capital Trading Terminal — Daily Audit 2026-11-20

Run 42. Read-only. No secrets, account identifiers, environment files or connection strings appear here. No order, preflight or mutation endpoint was touched.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-20 01:51–02:20 EST (UTC−5), Friday |
| Market session | CLOSED (overnight). Today's regular session runs 09:30–16:00 EST. Thanksgiving 11-26 is closed; 11-27 closes early at 13:00 |
| `main` | `c17babc`, unchanged since 11-18 (verified with `ls-remote`). CI run 127 passed |
| VPS branch `codex/desktop-checkpoint` | `d28c80c`, unchanged. CI run 126 passed |
| `Claude-Gpt-audit` | `24173c3`, unchanged |
| Deployed commit | SHA UNVERIFIED (no SHA endpoint, no SSH) |
| Scope | REPOSITORY + LIMITED LIVE. Unauthenticated GETs plus the GitHub Actions API. `/api/health` returned 403. HTTPS: no response. No broker, database or operator session |

## Live observations (unauthenticated `/api/status`)

| Time (UTC) | HTTP / latency | db_available | protection_status | last_scan_at | results |
|---|---|---|---|---|---|
| 06:53:54Z | 200 / 1.61 s | true | NOT_ASSESSED | 2026-11-20T05:01:11Z | 29 |
| 06:54:36Z | 200 / 1.30 s | true | NOT_ASSESSED | same | 29 |
| 06:55:10Z | 200 / 1.29 s | true | NOT_ASSESSED | same | 29 |

- The database was reported up across 76 s, the third straight day. CRIT-003 stays Open until the cause of the 11-03 outage and pre-open health are established.
- The Friday midnight scan ran: 29 results, 29 passed the pre-filter, 0 Claude calls.
- Positions, broker orders, quotes and protective orders are **NOT VERIFIED**: there is no authenticated access, and `protection_status=NOT_ASSESSED`.

## Verdict
**CRITICAL (code) / DEGRADED (live).**
- Infrastructure: DEGRADED (HTTP only; no backup alert).
- Data: PARTIAL.
- Entries: NOT VERIFIED. The options pause still depends on an env flag the VPS template does not set (MED-021).
- Exits: CRITICAL. I re-read `backend/services/public_execution.py:2310-2352`. The emergency-exit skip at `:2310` and the stable stop client ID with a 24 h intent claim at `:2352` / `execution_safety.py:63-70` are still present on `main`.
- Learning and reporting: NOT VERIFIED.

## Findings
- **New:** none. No code changed.
- **Status changes:** none.
- **Register:** 58 items (Critical 3, High 16, Medium 21, Low 18). 45 CONFIRMED / 4 SUSPECTED / 0 UNVERIFIED / 9 RESOLVED.

## Tests (fresh Python 3.12 venv, `env -i`, no credentials, mocked), main @ c17babc

| Command | Result |
|---|---|
| `pytest backend/tests tests` | exit 0. **948 passed, 13 skipped, 153 deselected, 0 failed**, 126 subtests, 25 warnings |
| `flake8 backend --select=E9,F63,F7,F82` | exit 0 (0 findings) |

Not exercised: a real broker lifecycle, a PostgreSQL restore, the deployed scheduler registry and nginx/TLS.

## Operator actions before 09:30 EST
1. Confirm broker-side protection for every Public position.
2. Check OPEN rows that have a non-null `emergency_exit_order_id` (CRIT-001).
3. If the options pause is intended, confirm `OPTIONS_RESEARCH_ONLY=true` / `ENABLE_OPTIONS_EXECUTION=false` on the VPS.
4. Confirm the deployed SHA.

## Remediation order (unchanged)
1. CRIT-001/002, HIGH-014/015/016: exit lockout and exit pricing, with a bid ≥ limit invariant.
2. MED-021: make the options pause deploy-safe.
3. HIGH-013/MED-020: backup pruning and alerting.
4. HIGH-012: release parity.
5. HIGH-011: TLS.
