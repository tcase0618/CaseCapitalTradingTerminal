# Case Capital Trading Terminal — Daily Audit 2026-11-15

Run 37. Read-only. No order, preflight, cancel or mutation endpoint was called. No secrets, account identifiers or DSNs are reproduced.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-15 01:50–02:05 EST (America/New_York, UTC−5), Sunday |
| Market session | CLOSED (weekend). Next regular session Monday 11-16 09:30–16:00 EST. Thanksgiving 11-26; early close 11-27 |
| Previous audit | 2026-11-14 run 36, `docs/DAILY_AUDIT_2026-11-14.md` on `claude/stoic-knuth-mtetaw` @ `ae50513` |
| `main` | `5369675`, unchanged. CI run 113 success |
| VPS `codex/desktop-checkpoint` | `bde0cd5`, unchanged. CI run 114 success |
| `Claude-Gpt-audit` | `24173c3`, unchanged. CI run 115 success. No newer CI runs |
| Deployed commit | SHA UNVERIFIED; `/api/status` schema unchanged |
| Access | REPOSITORY + LIMITED LIVE (unauthenticated GETs, GitHub Actions API). `/api/health` 403 ("Operator session required"). No broker, SSH, DB, logs. HTTPS: no response |

## Verdict

**CRITICAL (code) / DEGRADED (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED. `db_available=true`, `db_reason=null` at 06:52:10Z, 06:52:52Z, 06:53:42Z (92 s). HTTP only |
| Data | PARTIAL. `last_scan_at=2026-11-13T23:30:14Z` (Friday 18:30 EST). No weekend scan, consistent with the documented weekend standby (`scheduler.py:95-115`) |
| Entries | NOT VERIFIED |
| Exits | CRITICAL. CRIT-001/002, HIGH-014/015/016 still on main 5369675 and VPS bde0cd5 (re-checked: `public_execution.py:1943-1947`, `:2310`, `:2352`; `execution_safety.py:68`). `protection_status=NOT_ASSESSED`. Monday open is the next exposure |
| Learning / reporting | NOT VERIFIED (Sunday 02:00 ET learning cycle not observable without operator session) |

## Position coverage and funnel

NOT VERIFIED (no authenticated or broker access). Public status: last scan 5 results, 5 pre-filter, 0 Claude calls.

## New findings

None verified. No branch changed since 11-13.

## Prior findings

All unchanged; CRIT-001/002 code paths re-confirmed on 5369675; CRIT-003 DB up across 92 s; HIGH-011 re-confirmed (HTTP only).

## Tests (fresh Python 3.12 venv, `env -i`, `POSTGRES_ENABLED=false`, `RUN_EXTERNAL_API_TESTS=false`, no credentials)

| Command | Result |
|---|---|
| `pip install -r backend/requirements.txt flake8` | exit 0 |
| `flake8 --select=E9,F63,F7,F82 .` (backend) | exit 0 |
| `pytest backend/tests tests -q -rs` | exit 0 — 815 passed, 13 skipped, 153 deselected, 126 subtests, 25 warnings |

Skips: 12 external-API; 1 PostgreSQL contract without isolated DSN. Not exercised: real broker lifecycle, live quotes, PostgreSQL concurrency, Telegram delivery, restore.

## Remediation order (unchanged)

1. Reconcile and promote exit-protection fixes with a bid ≥ limit invariant (CRIT-001, CRIT-002, HIGH-014, HIGH-015, HIGH-016) before Monday's open, with operator verification of broker-side protection.
2. Backup lifecycle and alerting (HIGH-013, MED-020, MED-016).
3. Release parity (HIGH-012) and TLS (HIGH-011).

## Operator actions before Monday 11-16 09:30 EST

Confirm broker-side protection for every Public and Alpaca options position; check OPEN rows with non-null `emergency_exit_order_id` whose broker order is no longer working; confirm the Sunday learning cycle ran and Monday's scans persist.

## Not verified

Positions, orders, buying power, ledger, protection, quotes, scheduler registry/run history, funnel, outcomes, deployed SHA, backups/restore, logs.
