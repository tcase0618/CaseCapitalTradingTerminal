# Case Capital Trading Terminal — Daily Audit 2026-10-18 (run 11, repository-only)

- **Audit time:** 2026-10-18 02:50–03:25 EDT (America/New_York, UTC−4), Sunday.
- **Market session:** CLOSED (weekend). Next regular NYSE session Mon 10-19 09:30 ET; no holiday.
- **Access scope:** REPOSITORY-ONLY. No SSH, broker, database or Telegram access. Deployed commit: UNVERIFIED.
- **Repository state:**
  - `main` @ `9ef8dfc` (unchanged since 10-14; CI 38561361831 success).
  - VPS branches `codex/vps-reviewed-release-20261012` / `codex/desktop-checkpoint` @ `81879a2` (unchanged).
  - `Claude-Gpt-audit` moved `14ae96f` → `8c3f890` (2026-10-18 00:59 EDT, "fix(terminal): preserve pending retries when cycle execution is held"). CI run 39099008296: success.
  - Re-checked on `main` and the VPS head: `public_execution.py:2232` still skips every OPEN row that has `public_phase_order_id` or `emergency_exit_order_id` (CRIT-001 root cause).

## Verdict
| Area | Readiness |
|---|---|
| Overall | NOT VERIFIED (runtime) / code-level **CRITICAL** for exits on `main` and the VPS head — unchanged for 9 days |
| Infrastructure | NOT VERIFIED |
| Data | NOT VERIFIED |
| Entries | NOT VERIFIED |
| Exits | CRITICAL at code level (CRIT-001/002, HIGH-014 fixes only on `Claude-Gpt-audit`) |
| Learning | NOT VERIFIED |
| Reporting | NOT VERIFIED |

## New findings
### CCTT-LOW-015 — Pending 10:30 stale-retry rows are not date-scoped; a skipped cycle's rows are resolved by a later day's cycle
- **Severity:** Low. **Status:** CONFIRMED (offline mocked reproduction). **Branch:** `Claude-Gpt-audit` @ `8c3f890`; the cross-day lookup also exists on `main`.
- **Evidence:**
  - `_persist_stale_execution_retries` keys rows by `{today}:{ticker}:{source_scan}` (`backend/services/terminal_cycle.py:73,82`).
  - `_resolve_stale_execution_retries` loads every `PENDING_1030_FULL_RESCAN` row with no date or cycle filter (`:117-119`).
  - `8c3f890` adds an early return for **any** skipped equity phase (`:113-115`). Rows therefore stay PENDING when the skip reason is not lock loss: `ENABLE_TRADE_EXECUTION is off`, `pm_scan_mismatch` or `public_equity_execution_not_enabled_no_alpaca_fallback` (`:346-375`).
  - Repro (scratch test, mocked storage): a day-1 row left PENDING by a `pm_scan_mismatch` skip was resolved by the day-2 10:30 call as `EXECUTED_1030` with `retry_cycle_id=day2-1030`. Result: 1 passed.
- **Consequence:** bookkeeping and learning attribution only; there is no order path. A later-day entry can be credited to an earlier stale-quote retry, which inflates retry success. Rows also accumulate without expiry while execution is disabled; the resolver caps at `to_list(500)`.
- **Repair:** filter pending rows to the current session date (or `source_cycle_id` of today's 09:30 cycle). Expire older rows to an explicit `EXPIRED_UNRESOLVED` status.
- **Acceptance / test:** a parametrized regression where a prior-day PENDING row survives a skipped cycle. The next day's resolver must not mark it EXECUTED/REJECTED; it should mark it expired and leave today's rows correct.

## Status changes
- **CCTT-LOW-014 → Partially addressed** (finding status CONFIRMED). `8c3f890` skips resolution when the lock is lost and leaves rows PENDING with `retried == 0`.
  - Independently verified offline: `backend/tests/test_claude_audit_lock_reporting.py` has 15 passing tests, and their assertions at `:143-144, :158-159` check the row stays PENDING with `retried == 0`.
  - Not on `main` or the VPS head. Not deployed or verified.
- All other items unchanged. Totals: 50 items (Critical 2, High 14, Medium 19, Low 15) · CONFIRMED 43, SUSPECTED 4, UNVERIFIED 0, RESOLVED 3.

## Position coverage and funnel
NOT VERIFIED — no broker or DB access. The operator check from the 10-17 run is still due **before Mon 10-19 09:30 ET**:
- Confirm the deployed SHA.
- List OPEN `tf_trades` rows with a non-null `emergency_exit_order_id` or a `SUBMIT_UNKNOWN` status, and compare them with broker order status.
- Confirm broker protection or fresh terminal monitoring for every held position.

## Tests (fresh Python 3.11 venv; `ENABLE_TRADE_EXECUTION=false`, `ENABLE_OPTIONS_EXECUTION=false`, `DISABLE_SCHEDULER=true`, no `DATABASE_URL`, dead proxy; all mocked)
| Target | Command | Result |
|---|---|---|
| `main` 9ef8dfc | `python -m pytest -q -p no:cacheprovider` | exit 0 — 514 passed, 13 skipped, 153 deselected, 25 warnings, 126 subtests |
| `Claude-Gpt-audit` 8c3f890 | same | exit 0 — 801 passed, 15 skipped, 153 deselected, 25 warnings, 126 subtests |
| `Claude-Gpt-audit` 8c3f890 | `pytest backend/tests/test_claude_audit_lock_reporting.py` | 15 passed |
| LOW-015 repro | scratch test against `terminal_cycle._resolve_stale_execution_retries` | 1 passed (defect reproduced) |

Deselected: `live_destructive`, `external_integration`, `postgres_integration` (pytest.ini). Not exercised: real PostgreSQL locks (last proven 10-16), any broker lifecycle, Telegram delivery, the frontend.

## Remediation order (unchanged)
1. Release CRIT-001/002 and HIGH-014 exit repairs to a CI-green `main`, deploy it, and verify the deployed SHA (HIGH-012, MED-017).
2. Public kill switch and loss breaker (HIGH-004/005), exit spread/freshness (HIGH-001), options close coordination (HIGH-003).
3. Then MED-018/019 and LOW-014/015 bookkeeping before promoting `14ae96f`/`8c3f890`.

## Unknowns
Deployed SHA, live positions and protection, scheduler history, quote ages, DB/backup state: all NOT VERIFIED. Nothing here should be read as "everything works".
