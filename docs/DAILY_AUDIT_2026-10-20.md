# Case Capital Trading Terminal — daily morning audit, 2026-10-20

Run 13. Read-only. No orders, flags, restarts, deploys, migrations or DB writes. No secrets recorded.

## 1. Header and verdict

| Item | Value |
|---|---|
| Audit time | 2026-10-20 02:51–03:25 EDT (America/New_York, UTC−4), Tuesday |
| Market session | Pre-market, CLOSED at audit time. Tue 10-20 is a regular NYSE session (no holiday or early close); it opens 09:30 ET. |
| `main` | `aab210a`, moved from `9ef8dfc` (2026-10-19 08:02 EDT, "Promote reviewed cycle reporting and persisted options PM authority"). CI run 39249201956 was a success. |
| VPS release branches | `codex/vps-reviewed-release-20261019` and `codex/desktop-checkpoint` both @ `0e3bbd2`, moved from `81879a2`. CI 39249211684 / 39249210690 were successes. That head contains `aab210a` plus an execution-cost analytics change in `public_execution.analytics()` (read-only metrics). |
| `Claude-Gpt-audit` | `d98f80e`. It is 27 ahead of and 7 behind `main`. The CRIT-001/002, HIGH-014, LOW-014 and LOW-015 repairs remain only here. |
| Deployed commit | **NOT VERIFIED.** There is no SSH, broker, DB or monitoring access. |
| Scope | **REPOSITORY-ONLY.** |
| Overall | **NOT VERIFIED (runtime) / code-level CRITICAL (exits).** |

Readiness by area, at code level only:

- **Infrastructure:** NOT VERIFIED.
- **Data:** NOT VERIFIED.
- **Entries:** DEGRADED. HIGH-004/005 kill switch and loss breaker are still missing for Public.
- **Exits:** CRITICAL. CRIT-001/002 and HIGH-014 are unreleased on `main` and on the VPS head.
- **Learning:** DEGRADED (HIGH-008).
- **Reporting:** improved. MED-018/019 are partially addressed on `main`.

## 2. Findings

### Regression promoted to `main` and VPS: CCTT-LOW-014 (stable ID kept)

- **Status:** CONFIRMED (offline mocked reproduction on `main` @ `aab210a`). Current status: **Open on `main` and the VPS head `0e3bbd2`. Regressed by promotion.** It was previously present only on `Claude-Gpt-audit`, which has the fix `8c3f890`.
- **Component:** `backend/services/terminal_cycle.py`
- **Evidence:**
  - `aab210a` changed `_run_full_terminal_scan` to continue after the cycle lease is lost, with `lock_lost=True` and equity/options marked skipped (`:342-383`).
  - The 10:30 resolver still runs unconditionally: `:395` `if triggered_by == "stale_retry_scan_1030":` → `_resolve_stale_execution_retries(...)` (`:396-403`).
  - The resolver has no skip check. For an approved ticker that is neither executed nor rejected, it writes `REJECTED_1030` (`:137`).
  - The companion resolver guard from `8c3f890` was not promoted.
- **Reproduction:** offline mock, scratch script with no network or DB. One PENDING row for ABC, PM action STARTER, and `equity_execution={"skipped":True,"reason":"full_terminal_scan_lock_lost","executed":[],"rejected":[]}`. Result: `{'reviewed': 1, 'retried': 1, 'executed': 0}`, and the row was updated to `status='REJECTED_1030'`.
- **Consequence:**
  - A 10:30 cycle whose lease is lost consumes every pending stale-retry row as a rejection, and reports them as "retried".
  - The one-shot retry opportunity is lost, and the stale-retry evidence and funnel are wrong.
  - There is no direct exit-protection impact.
- **Repair:** promote the `8c3f890` resolver guard. Skip resolution when `lock_lost` or when equity was skipped, leave rows PENDING, and report `retried=0`. Promote it together with the LOW-015 date-scoping repair `c78e62a`.
- **Acceptance:** with `lock_lost=True` on the 10:30 trigger, pending rows remain `PENDING_1030_FULL_RESCAN` and `retried == 0`.
- **Regression test:** port the `test_claude_audit_lock_reporting.py` cases from `Claude-Gpt-audit` that cover lock loss before equity, during quote refresh, after equity, and a direct resolver call.

### Prior findings: status changes from this promotion (deployment unverified)

- **CCTT-HIGH-010: Partially addressed on `main`/VPS** (was: branch only).
  - `options_desk.py:1972-1999` `refresh_and_auto_execute_latest` now consumes the persisted full-cycle scan. It requires `cycle_id`, `full_cycle_finished_at`, no `lock_lost`, a matching `pm_payload.scan_finished_at`, list recommendations and age ≤ 4 h. Otherwise it returns `persisted_pm_cycle_unavailable_or_stale` (`:1993`).
  - `cycle_id` is carried to candidates and order records (`:1454`, `:1486`, `:1774`).
  - Standalone/manual and legacy `auto_execute_latest` paths remain non-authoritative.
- **CCTT-MED-018: Partially addressed on `main`/VPS.** `scheduler.py:143-167` persists a `scheduler_runs` row with SKIPPED/DEGRADED for skipped or failed cycles. It emits a `scheduled_cycle_incomplete` Telegram alert, except for `full_terminal_scan_already_running`, and it no longer logs "complete".
- **CCTT-MED-019: Partially addressed on `main`/VPS.** A lock lost after equity no longer returns early. The cycle persists the funnel, scan (`lock_lost`, `cycle_id`) and Telegram report, with a DEGRADED banner (`telegram_events.py:339-340, 683`). This introduced the LOW-014 regression above.

### Unchanged on `main` and the VPS head (re-checked)

- **CCTT-CRIT-001: Open on `main`/VPS.** `public_execution.py:2232` still skips any OPEN trade with `public_phase_order_id` or `emergency_exit_order_id`. `_retire_terminal_emergency_order` and `_emergency_retry_ready` are absent on `main` and `0e3bbd2`, and present only on `Claude-Gpt-audit` (7 references).
- **CCTT-CRIT-002 and CCTT-HIGH-014:** Open on `main`/VPS; unchanged.
- **CCTT-LOW-015: Open on `main`/VPS.** The resolver at `terminal_cycle.py:113-116` is still not date-scoped. The fix is on `Claude-Gpt-audit` `c78e62a` only.
- All other items: no relevant change in the diff, so prior status is carried. This is not an independent re-validation of each one.

No other new defects were verified.

## 3. Position coverage and funnel

NOT VERIFIED. There is no broker or DB access. **Operator action before 09:30 ET today:**

- Confirm the deployed SHA.
- List OPEN `tf_trades` rows with a non-null `emergency_exit_order_id` or `SUBMIT_UNKNOWN`, and compare them with Public order status.
- Confirm broker-side protection or fresh terminal monitoring for every held position.
- If the 10:30 cycle logs `lock_lost`, treat its stale-retry resolutions as invalid (LOW-014).

## 4. Tests

The environment was a fresh Python 3.11 venv from `backend/requirements.txt`, with:

- `POSTGRES_ENABLED=false`
- `ENABLE_TRADE_EXECUTION=false`
- `ENABLE_OPTIONS_EXECUTION=false`
- `RUN_EXTERNAL_API_TESTS=false`
- `RUN_LIVE_TRADING_TESTS=false`
- HTTP(S) proxy pointed at a dead port

All broker, storage and delivery calls were mocked.

| Command (`main` @ `aab210a`) | Result |
|---|---|
| `pytest -q backend/tests tests` | exit 0: **532 passed, 13 skipped, 153 deselected**, 25 warnings, 126 subtests passed |
| `cd backend && pytest tests -q` (CI scope) | exit 0: 450 passed, 13 skipped, 153 deselected |
| `flake8 --select=E9,F63,F7,F82 .` (backend) | exit 0 |
| LOW-014 reproduction (scratch, offline) | reproduced, see above |

- The deselected tests are `live_destructive`, `external_integration` and `postgres_integration`, by design.
- Not exercised: actual PostgreSQL lease and lock behaviour, Public/Alpaca responses, and any broker lifecycle.
- Mocked lifecycle tests are not live evidence.

## 5. Totals

50 items: Critical 2 · High 14 · Medium 19 · Low 15.
Finding status: CONFIRMED 43 · SUSPECTED 4 · UNVERIFIED 0 · RESOLVED 3. No new IDs.

## 6. Remediation order

1. **Exits first.** Promote and deploy the CRIT-001/CRIT-002/HIGH-014 repairs from `Claude-Gpt-audit` after review. They have been open on `main` and the VPS for 11 days, across trading days.
2. Promote the LOW-014 resolver guard (`8c3f890`) and the LOW-015 date scoping (`c78e62a`). Today's promotion carried half of the lock-lost change without its guard.
3. Add the Public kill switch and loss-breaker scope (HIGH-004/005). These are control-design changes and need operator approval.
4. Release parity and deploy record (HIGH-012, MED-017).

## 7. Unknowns

Deployed SHA, positions, protective orders, quotes, scheduler run history, Telegram/frontend parity, disk, backups and TLS are all NOT VERIFIED. No conclusion that the system works is drawn from CI or offline tests.
