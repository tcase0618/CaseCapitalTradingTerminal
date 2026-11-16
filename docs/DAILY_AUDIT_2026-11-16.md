# Case Capital Trading Terminal — Daily Audit 2026-11-16

Run 38. Read-only. No order, preflight, cancel or mutation endpoint was called. No secrets, account identifiers or DSNs are reproduced.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-16 01:52–02:10 EST (America/New_York, UTC−5), Monday |
| Market session | CLOSED (overnight). Regular session today 09:30–16:00 EST — the first exposure since 11-13. Thanksgiving 11-26 closed; 11-27 early close 13:00 |
| Previous audit | 2026-11-15 run 37, `docs/DAILY_AUDIT_2026-11-15.md` on `claude/stoic-knuth-w23nur` @ `37b4165` |
| `main` | `5369675`, unchanged. CI run 113 success |
| VPS `codex/desktop-checkpoint` | `bde0cd5`, unchanged. CI run 114 success |
| `Claude-Gpt-audit` | `24173c3`, unchanged. CI run 115 success. No newer CI runs |
| Deployed commit | SHA UNVERIFIED; `/api/status` schema unchanged |
| Access | REPOSITORY + LIMITED LIVE (unauthenticated GETs, GitHub Actions API). `/api/health` 403. No broker, SSH, DB, logs. HTTPS: no response |

## Verdict

**CRITICAL (code) / DEGRADED (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED. `db_available=true`, `db_reason=null` at 06:55:12Z and 06:56:26Z (74 s). HTTP only |
| Data | PARTIAL. `last_scan_at=2026-11-16T05:00:25Z` (Monday 00:00 EST `midnight_scan`, `scheduler.py:63-64`, mon-fri cron `:541-546`): 3 results, 3 pre-filter, 0 Claude calls. Scheduler resumed after the weekend standby as configured. Persistence of that cycle and its funnel NOT VERIFIED |
| Entries | NOT VERIFIED |
| Exits | CRITICAL. CRIT-001/002, HIGH-014/015/016 still on main 5369675 and VPS bde0cd5 (no code change since the 11-15 re-check of `public_execution.py:1943-1947`, `:2310`, `:2352`; `execution_safety.py:68`). `protection_status=NOT_ASSESSED`. Today's open is the exposure |
| Learning / reporting | NOT VERIFIED (Sunday 02:00 ET learning cycle not observable without operator session) |

## Position coverage and funnel

NOT VERIFIED (no authenticated or broker access). Public status: last scan 3 results, 3 pre-filter, 0 Claude calls.

## New findings

None verified. No branch changed since 11-12.

## Prior findings

All unchanged; CRIT-003 DB up across 74 s (Partially addressed); HIGH-011 re-confirmed (HTTP only, HTTPS no response).

## Tests (fresh Python 3.12 venv, `env -i`, `POSTGRES_ENABLED=false`, `RUN_EXTERNAL_API_TESTS=false`, no credentials)

| Command | Result |
|---|---|
| `pip install -r backend/requirements.txt flake8` | exit 0 |
| `flake8 --select=E9,F63,F7,F82 .` (backend) | exit 0 |
| `pytest backend/tests tests -q -rs` | exit 0 — 815 passed, 13 skipped, 153 deselected, 126 subtests, 25 warnings |

Skips: 12 external-API; 1 PostgreSQL contract without isolated DSN. Not exercised: real broker lifecycle, live quotes, PostgreSQL concurrency, Telegram delivery, restore.

## Remediation order (unchanged)

1. Reconcile and promote exit-protection fixes with a bid ≥ limit invariant (CRIT-001, CRIT-002, HIGH-014, HIGH-015, HIGH-016), with operator verification of broker-side protection.
2. Backup lifecycle and alerting (HIGH-013, MED-020, MED-016).
3. Release parity (HIGH-012) and TLS (HIGH-011).

## Operator actions before 09:30 EST today

Confirm broker-side protection for every Public and Alpaca options position; check OPEN rows with non-null `emergency_exit_order_id` whose broker order is no longer working; confirm the Sunday learning cycle ran and that the 00:00 midnight scan persisted a cycle.

## Not verified

Positions, orders, buying power, ledger, protection, quotes, scheduler registry/run history, funnel, outcomes, deployed SHA, backups/restore, logs.
