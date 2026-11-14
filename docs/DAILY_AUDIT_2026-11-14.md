# Case Capital Trading Terminal — Daily Audit 2026-11-14

Run 36. Read-only. No order, preflight, cancel or mutation endpoint was called. No secrets, account identifiers or DSNs are reproduced.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-14 01:51–02:15 EST (America/New_York, UTC−5), Saturday |
| Market session | CLOSED (weekend). Next regular session Monday 11-16 09:30–16:00 EST. Next holiday Thanksgiving 11-26; early close 11-27 |
| Previous audit | 2026-11-13 (run 35), `docs/DAILY_AUDIT_2026-11-13.md` on `claude/stoic-knuth-l8o139` @ `27f6080` |
| `main` | `5369675`, unchanged since 11-13. CI run 113 success |
| VPS branch `codex/desktop-checkpoint` | `bde0cd5`, unchanged. `main...VPS` = 18/403. CI run 114 success |
| `Claude-Gpt-audit` | `24173c3`, unchanged. `main...Claude-Gpt-audit` = 31/48. CI run 115 success. No Codex remediation note was added on 11-13 |
| Deployed commit | SHA UNVERIFIED. `/api/status` schema unchanged |
| Access | REPOSITORY + LIMITED LIVE: unauthenticated GETs and the GitHub Actions API. `/api/health`, `/api/position_monitor/latest` and `/api/scan/latest` return 403 ("Operator session required"). No broker, SSH, DB or logs. HTTPS: no response (curl 000) |

## Verdict

**CRITICAL (code) / DEGRADED (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED. `db_available=true`, `db_reason=null` at 06:52:58Z and 06:56:44Z (226 s); HTTP only; 11-03 outage root cause still unrecorded |
| Data | PARTIAL. `last_scan_at=2026-11-13T23:30:14Z` (Friday 18:30 EST scan; 5 results, 5 pre-filter). No Saturday 00:00 ET scan — the same weekend pattern as 10-31 (last scan Friday 10-30 22:30Z), so consistent with the schedule, not a missed run. This is positive evidence that at least the last 11-13 session-day scan persisted |
| Entries | NOT VERIFIED |
| Exits | CRITICAL. CRIT-001/002, HIGH-014, HIGH-015 and HIGH-016 are still on main 5369675 and VPS bde0cd5. `protection_status=NOT_ASSESSED`. Market closed until Monday, so no exit can fire before then |
| Learning / reporting | NOT VERIFIED |

## Position coverage and funnel

Broker positions, orders, buying power and ledger: **NOT VERIFIED** (no authenticated or broker access). The public status shows only the last scan summary (results 5, pre-filter passed 5, Claude calls 0). No coverage table or funnel can be honestly filled in.

## New findings

None verified today. No finding invented. No branch changed since the 11-13 audit, so no new code was reviewed.

## Prior findings (re-checked on main 5369675)

- **CRIT-001 still Open.** `public_execution.py:1941-1997`: the emergency-order poll records `EXPIRED/CANCELLED` into `emergency_exit_status` only; `emergency_exit_order_id` is never cleared. `:2310` still skips any row with that id.
- **CRIT-002 still Open.** `execution_safety.py:68` 24 h intent TTL; `stable_client_order_id("public_stop", …, trigger_stop)` pinned to the trigger stop at `public_execution.py:2352` (cited as :2350 on 11-13).
- **HIGH-014, HIGH-015, HIGH-016 still Open on main/VPS.** Branch-only fixes (ae0dc04 for HIGH-015; none for HIGH-016) remain unpromoted.
- **HIGH-007 / MED-020 Partially addressed** (branch-only ea092ce / 24173c3; not on main/VPS; not deployed).
- **CRIT-003 Partially addressed.** DB up across 226 s; Friday evening scan persisted. Root cause and chronology still missing.
- **HIGH-011** re-confirmed (HTTP only; no HTTPS response). **HIGH-012**: main...VPS 18/403; main...Claude-Gpt-audit 31/48 — unchanged.
- All other items unchanged.

## Tests (fresh Python 3.12 venv under `env -i`; `POSTGRES_ENABLED=false`, `RUN_EXTERNAL_API_TESTS=false`; no credentials)

| Command | Result |
|---|---|
| `pip install -r backend/requirements.txt flake8` | exit 0 |
| `python -m flake8 --select=E9,F63,F7,F82 .` (backend) | exit 0 |
| `python -m pytest backend/tests tests -q -rs` (repo root, CI-equivalent) | exit 0 — **815 passed, 13 skipped, 153 deselected**, 126 subtests, 25 warnings |
| CI run 113 (main), 114 (VPS), 115 (Claude-Gpt-audit) | success; no newer runs |

Skips: 12 external-API cases disabled; 1 PostgreSQL contract without an isolated DSN. Not exercised: real broker lifecycle, live quotes, PostgreSQL concurrency, Telegram delivery, restore.

## Remediation order (unchanged)

1. Reconcile and promote the exit-protection fixes with a bid ≥ limit invariant: CRIT-001 retire/retry, CRIT-002 retry key, HIGH-014 refused-claim alert, HIGH-015 bid anchoring, HIGH-016 cached-observation pricing. Deploy before Monday's open if possible, with operator verification of broker-side protection.
2. Backup lifecycle and alerting (HIGH-013, MED-020, MED-016).
3. Release parity (HIGH-012) and TLS (HIGH-011).

## Operator actions before Monday 11-16 09:30 EST

Confirm broker-side protection for every Public and Alpaca options position; check for any OPEN row with a non-null `emergency_exit_order_id` whose broker order is no longer working; confirm Monday's session scans persist.

## Not verified

Positions, orders, buying power, ledger, protection, quotes, scheduler registry/run history, funnel counts, outcomes, deployed SHA, backups/restore, logs. No claim that the system is healthy or that exits are protected.
