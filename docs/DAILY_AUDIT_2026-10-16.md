# Case Capital Trading Terminal — daily audit 2026-10-16

## 1. Ground truth

- **Audit time:** 2026-10-16 02:52–03:45 EDT (America/New_York, UTC−4). Daily audit run 9.
- **Market session:** pre-market, CLOSED at audit time. Fri 2026-10-16 is a regular NYSE trading day; the regular session opens at 09:30 ET.
- **Repository changes since the 10-15 audit:**
  - `main` @ `9ef8dfc`, unchanged. CI run 38561361831: success.
  - VPS branches `codex/desktop-checkpoint` and `codex/vps-reviewed-release-20261012` @ `81879a2`, unchanged.
  - `Claude-Gpt-audit` moved `97b5411` → `14ae96f` ("fix(terminal): preserve cycle evidence and report lock failures truthfully", 10-15 04:03 EDT). The commit touches `scheduler.py`, `terminal_cycle.py`, `telegram_events.py` and adds `test_claude_audit_lock_reporting.py`. CI run 38703014481: success.
  - `git cherry`: 10 `Claude-Gpt-audit` commits are absent from the VPS head and 22 from `main`, including `cbb24d7` (CRIT-001/002), `1979970` (HIGH-014), `fdc4784` (HIGH-003) and now `14ae96f` (MED-018/019).
  - Re-verified on `main` and the VPS head: `public_execution.py:2232` still `continue`s for any OPEN row with `public_phase_order_id` or `emergency_exit_order_id`.
- **Deployed commit:** UNVERIFIED. No SSH, broker or DB access exists in this session.
- **Scope:** REPOSITORY-ONLY.

## Verdict

- **Overall:** NOT VERIFIED (runtime) / code-level **CRITICAL** for exits on `main` and the VPS head. Unchanged, on a trading day.
- Entries: HIGH-004/005 are open.
- Exits: CRIT-001/002 and HIGH-014 are unreleased.
- Infrastructure, data, learning and reporting: NOT VERIFIED at runtime.

## 2–5. Positions, quotes, scheduler, funnel

NOT VERIFIED. No position coverage table or funnel counts can be stated.

The operator read-only check is still required before 09:30 ET:
- Confirm the deployed SHA.
- Find OPEN `tf_trades` rows with a non-null `emergency_exit_order_id` or `SUBMIT_UNKNOWN`, and compare each with broker status.
- Confirm broker protection or fresh terminal monitoring for every held position.

## Status changes

- **CCTT-MED-018 → Partially addressed.** `14ae96f` on `Claude-Gpt-audit` persists SKIPPED/DEGRADED `scheduler_runs` rows, logs a warning instead of "complete", and alerts on non-contention skips. `test_claude_audit_lock_reporting.py` passes locally (9 with today's repro). The fix is not on `main` or the VPS head, and runtime is unverified.
- **CCTT-MED-019 → Partially addressed.** In the same commit, lock loss no longer returns early. It blocks the remaining execution phases but still persists the funnel, stale-retry queue and cycle document, and sends one report labelled DEGRADED. The fix is not on `main` or the VPS head.
- **CCTT-MED-006 / CCTT-MED-008:** remain Partially addressed, but there is new evidence:
  - The opt-in real-PostgreSQL test `test_verified_isolated_postgres_session_exclusion` was skipped in every prior audit. It ran today and **passed** against an isolated throwaway PostgreSQL 16 cluster (`POSTGRES_TEST_ISOLATED=true`). The two lock test files: 13 passed, 0 skipped.
  - A cross-process probe using `services/advisory_locks.py` from `main` ran as three separate OS processes. The holder acquired the lock, a contender was refused (`None`) while it was held, and the holder was then killed with `kill -9`. A new contender acquired the lock and reported `healthy`. This proves session-scoped exclusion and crash release on PostgreSQL 16.
  - Still unverified: the production DSN and pooler mode (a transaction-mode pooler such as PgBouncer would break session advisory locks) and the deployed SHA.

## New finding

### CCTT-LOW-014 — 10:30 stale-retry rows are consumed as `REJECTED_1030` when the cycle lock was lost (branch `Claude-Gpt-audit` only)

- **Severity:** Low · **Finding status:** CONFIRMED (offline mocked reproduction) · **Current status:** Open
- **Component:** `backend/services/terminal_cycle.py` on `Claude-Gpt-audit` `14ae96f`.
- **Evidence:**
  - After `14ae96f`, a lost lock sets `equity_execution = {"skipped": True, "reason": "full_terminal_scan_lock_lost", "executed": [], "rejected": []}` and execution continues.
  - At `terminal_cycle.py:395-403`, `_resolve_stale_execution_retries` still runs whenever `triggered_by == "stale_retry_scan_1030"`, and nothing checks `lock_lost`.
  - Each `PENDING_1030_FULL_RESCAN` ticker that the PM re-approved finds no match in `executed` or `rejected`. `_is_stale_public_entry_rejection(None)` is False, so the row is written `REJECTED_1030` (`:106-141`, status at `:137`).
  - Repro: `_resolve_stale_execution_retries(pm_rows=[{"ticker":"TEST","action":"STARTER"}], equity_execution=<lock-lost dict>)` gives row status `REJECTED_1030` and returns `{"reviewed":1,"retried":1,"executed":0}`.
- **Before the fix:** on `main` the early return skipped resolution, so rows stayed PENDING. The regression comes from removing that early return.
- **Consequence:** the one 10:30 fresh-quote retry is permanently consumed, and the record is mislabelled as a rejection (and counted as "retried") although no order was attempted. There is no loss-of-money path; the effect is bookkeeping and one missed entry retry.
- **Repair:** skip resolution when `lock_lost` is true (leave rows PENDING), or write a distinct `LOCK_LOST_1030` status. Do not count such rows as retried.
- **Effort:** S. **Dependencies:** MED-019 fix (`14ae96f`).
- **Acceptance:** with `triggered_by="stale_retry_scan_1030"` and a lost lock, pending retry rows stay unresolved or carry an explicit lock-lost status, and `retried == 0`.
- **Test:** add a parametrized case to `test_claude_audit_lock_reporting.py` that drives `_run_full_terminal_scan('stale_retry_scan_1030', cycle_lease=<unhealthy>)` and asserts on the row status.

## 8. Tests

Environment: Python 3.11 venv; `POSTGRES_ENABLED=false RUN_EXTERNAL_API_TESTS=false ENABLE_TRADE_EXECUTION=false ENABLE_OPTIONS_EXECUTION=false DISABLE_SCHEDULER=true`; proxy pointed at a dead port. All tests mocked unless stated.

- `main` `9ef8dfc`: `python -m pytest -q`: exit 0, 514 passed, 13 skipped, 153 deselected, 126 subtests, 25 warnings.
- `Claude-Gpt-audit` `14ae96f`: exit 0, 795 passed, 15 skipped, 153 deselected, 126 subtests. The total includes today's 1-test repro file, which was added to a temporary worktree and removed afterwards.
- `test_claude_audit_lock_reporting.py` + repro: 9 passed.
- Lock tests with `POSTGRES_TEST_DSN` set to the isolated local PG16 and `POSTGRES_TEST_ISOLATED=true`: 13 passed, 0 skipped.
- `flake8 --select=E9,F63,F7,F82 backend` (`main`): 0.
- Paths not exercised: real broker lifecycle, production PostgreSQL/pooler, Telegram delivery, systemd units.

## Totals

49 items (Critical 2, High 14, Medium 19, Low 14) · CONFIRMED 42, SUSPECTED 4, UNVERIFIED 0, RESOLVED 3.

## Remediation order

1. Today, read-only: the operator position and protection check.
2. Release `cbb24d7`, `1979970` and `fdc4784` to `main` and the VPS branch with green CI, then confirm the deployed SHA.
3. HIGH-004/005: Public kill switch and loss breaker.
4. Before promoting `14ae96f` (MED-018/019), fix LOW-014 so the release does not regress 10:30 retries.
