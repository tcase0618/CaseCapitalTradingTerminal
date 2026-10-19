# Case Capital Trading Terminal — Daily Audit 2026-10-19 (run 12, repository-only)

- **Audit time:** 2026-10-19 02:53–03:30 EDT (America/New_York, UTC−4), Monday.
- **Market session:** pre-market, CLOSED at audit time. Mon 10-19 is a regular NYSE trading day (no holiday); the session opens 09:30 ET.
- **Access scope:** REPOSITORY-ONLY. No SSH, broker, database or Telegram access. Deployed commit: UNVERIFIED.
- **Repository state:**
  - `main` @ `9ef8dfc` (unchanged since 10-14; CI 38561361831 success).
  - VPS branches `codex/vps-reviewed-release-20261012` / `codex/desktop-checkpoint` @ `81879a2` (unchanged).
  - `Claude-Gpt-audit` moved `8c3f890` → `c78e62a` (2026-10-18 19:48 EDT, "fix(terminal): scope stale retry attribution to its source date"). CI run 39178035360: success. The branch is 26 commits ahead of `main`.
  - Re-checked on `main`: `backend/services/public_execution.py:2232` still skips every OPEN row that has `public_phase_order_id` or `emergency_exit_order_id` (CRIT-001 root cause).

## Verdict
| Area | Readiness |
|---|---|
| Overall | NOT VERIFIED (runtime) / code-level **CRITICAL** for exits on `main` and the VPS head — unchanged for 10 days, on a trading day |
| Infrastructure / Data / Entries / Learning / Reporting | NOT VERIFIED |
| Exits | CRITICAL at code level (CRIT-001/002, HIGH-014 fixes only on `Claude-Gpt-audit`) |

## New findings
None verified.

## Status changes
- **CCTT-LOW-015 → Partially addressed** (finding status CONFIRMED). `c78e62a`:
  - The resolver now takes `observed_at=scan.finished_at` and loads only rows with `retry_key` in `[day + ":", day + ";")` (`terminal_cycle.py` resolver, call site passes `observed_at`).
  - Prior-day PENDING rows with a parseable date prefix move to `EXPIRED_UNRESOLVED`, including when the equity phase is skipped.
  - Independently checked that `postgres_store._sql_filter_inner` translates string `$lt/$gte` to `collate "C"` text comparison, so the range works on PostgreSQL, not only on the test `MemoryCollection`.
  - New tests `test_prior_day_retries_cannot_borrow_later_execution[False/True]` and `test_old_retry_backlog_does_not_starve_today` cover yesterday's reproduction; the file has 18 passing tests.
  - Residual: rows with non-date legacy keys are never expired (intentional, needs one-time reconciliation). Not on `main` or the VPS head; not deployed.
- All other items unchanged. Totals: 50 items (Critical 2, High 14, Medium 19, Low 15) · CONFIRMED 43, SUSPECTED 4, UNVERIFIED 0, RESOLVED 3.

## Position coverage and funnel
NOT VERIFIED — no broker or DB access. The operator check is due **before Mon 10-19 09:30 ET**:
- Confirm the deployed SHA.
- List OPEN `tf_trades` rows with a non-null `emergency_exit_order_id` or a `SUBMIT_UNKNOWN` status, and compare them with broker order status.
- Confirm broker protection or fresh terminal monitoring for every held position.

## Tests (fresh Python 3.11 venv; `ENABLE_TRADE_EXECUTION=false`, `ENABLE_OPTIONS_EXECUTION=false`, `DISABLE_SCHEDULER=true`, no `DATABASE_URL`, dead proxy; all mocked)
| Target | Command | Result |
|---|---|---|
| `main` 9ef8dfc | `python -m pytest -q -p no:cacheprovider` | exit 0 — 514 passed, 13 skipped, 153 deselected, 25 warnings, 126 subtests |
| `Claude-Gpt-audit` c78e62a | same | exit 0 — 804 passed, 15 skipped, 153 deselected, 25 warnings, 126 subtests |
| `Claude-Gpt-audit` c78e62a | `pytest backend/tests/test_claude_audit_lock_reporting.py` | 18 passed |

Deselected: `live_destructive`, `external_integration`, `postgres_integration` (pytest.ini). Not exercised: real PostgreSQL range query on `stale_execution_retries`, any broker lifecycle, Telegram delivery, the frontend.

## Remediation order (unchanged)
1. Release CRIT-001/002 and HIGH-014 exit repairs to a CI-green `main`, deploy, and verify the deployed SHA (HIGH-012, MED-017).
2. Public kill switch and loss breaker (HIGH-004/005), exit spread/freshness (HIGH-001), options close coordination (HIGH-003).
3. MED-018/019 and LOW-014/015 bookkeeping travel with the `Claude-Gpt-audit` promotion.

## Unknowns
Deployed SHA, live positions and protection, scheduler history, quote ages, DB/backup state: all NOT VERIFIED. Nothing here should be read as "everything works".
