# Case Capital Trading Terminal — daily audit 2026-10-13

## 1. Ground truth

- **Audit time:** 2026-10-13 02:51–03:45 EDT (America/New_York, UTC−4). Daily audit run 6.
- **Market session:** pre-market, CLOSED at audit time. Tue 2026-10-13 is a regular NYSE trading day. The regular session opens at 09:30 ET.
- **Repository:**
  - `main` @ `39ebd8b`, unchanged since 2026-10-08.
  - **VPS branch `origin/codex/desktop-checkpoint` moved `c265a52` → `aed0838`** on 2026-10-12 11:42–11:51 EDT, during market hours. `codex/vps-reviewed-release-20261012` points at the same SHA. New commits:
    - `13cdcb2`: frontend workflow changes.
    - `e62e5d0` and `b58da27`: patch-equivalent to `Claude-Gpt-audit` `146c7ce` and `97b5411`, the PostgreSQL session advisory locks.
    - `6a646ae`: merge titled "Deploy reviewed October 12 frontend and execution locks".
    - `aed0838`: IV Telegram test fixture fix.
  - `origin/main` is an ancestor of the VPS branch. `origin/Claude-Gpt-audit` is **not**. `git cherry` shows these nine branch commits are absent from the VPS branch: `cbb24d7` (exit recovery hardening, CRIT-001/002), `f9822e4`, `abb0627`, `87bbe9e`, `c962d96`, `1979970` (ambiguous-exit replay, HIGH-014), `fdc4784` (options close reconciliation, HIGH-003), `bfa3132` (MED-004) and `8b8fb01` (MED-005).
  - `origin/Claude-Gpt-audit` @ `97b5411`, unchanged since the 10-12 Codex update.
- **Deployed commit:** UNVERIFIED (no SSH, broker or DB access). The merge title says "Deploy", which suggests `aed0838` or `6a646ae` is live, but this is not verified.
- **Scope:** REPOSITORY-ONLY.

## Verdict

- **Overall:** NOT VERIFIED (runtime) / code-level **CRITICAL** for exits on `main` and on the VPS branch head `aed0838`.
- Exits: CRITICAL. `backend/services/public_execution.py:2232` on `aed0838` still skips any OPEN row with `public_phase_order_id` or `emergency_exit_order_id`, and nothing clears the field (CRIT-001). CRIT-002 is also unchanged.
- Entries: the kill switch and daily-loss breaker still do not cover Public (HIGH-004/005). The new full-cycle lock gates entry phases.
- Infrastructure, data, learning and reporting: NOT VERIFIED at runtime.

## 2. Positions and exit protection

NOT VERIFIED. **Operator read-only check required before 09:30 ET today:**
- List OPEN `tf_trades` rows with a non-null `emergency_exit_order_id` or `SUBMIT_UNKNOWN`, and compare each with its broker status.
- Confirm a broker protective order, or fresh terminal monitoring, for every held position.
- Confirm the deployed SHA.

## 3–5. Quotes, scheduler, funnel

NOT VERIFIED. No funnel counts can be stated.

## Delta reviewed: `c265a52..aed0838` (VPS branch)

- **CCTT-MED-006:** the options auto-execute lock now uses `advisory_locks.try_acquire("case-capital:options:auto-execute")`, a dedicated asyncpg connection running `pg_try_advisory_lock` (`options_desk.py:1801-1846`). It re-checks lease health before each options entry (`:1934`).
  - Moves to **Partially addressed**: on the VPS branch and `Claude-Gpt-audit`, not on `main`. No real multi-session PostgreSQL test has run; the isolated PG test is skipped.
  - The DSN example points directly at 127.0.0.1:5432 (`deploy/vps/cloud.env.example:4`), with no transaction pooler, so session locks are compatible with the documented topology.
- **CCTT-MED-008:** the full terminal cycle takes the session lock `case-capital:terminal:full-cycle` (`terminal_cycle.py:518-535`). Moves to **Partially addressed**. Remaining gaps: scheduler-wide leadership, the other jobs (the portfolio monitor among them), and leader status.
- **CCTT-MED-017:** CI run 38273421339 on `aed0838` succeeded (run 38273421354 on the release branch also succeeded). The previously failing IV fixtures are fixed. Moves to **Partially addressed**: no enforced green-CI deploy gate or deploy record exists.
- **CCTT-HIGH-012:** worse. The VPS branch now carries 5 commits not on `main`, and it still lacks the 9 `Claude-Gpt-audit` fixes, including all exit fixes. A deploy-titled merge shipped locks and frontend changes but not the CRITICAL exit repairs.

### New findings

**CCTT-MED-018 — A skipped or lock-unavailable full cycle is logged as "complete" with no alert or persisted skip** (NEW 2026-10-13)
- Severity Medium · CONFIRMED (source, `aed0838`) · Open.
- Evidence:
  - `terminal_cycle.py:524-529` returns `{"ok": False, "skipped": True, "reason": "full_terminal_scan_lock_unavailable" | "full_terminal_scan_already_running"}` whenever `advisory_locks.try_acquire` raises or returns None.
  - `try_acquire` raises whenever Postgres is disabled, the DSN is empty, asyncpg is missing, or a connect fails within 5 s (`advisory_locks.py:38-41`).
  - `scheduler.py:142-150` ignores `ok`/`skipped` and logs "Scheduled full terminal cycle complete" at info. No Telegram event fires and no cycle row is written.
- Consequence: a transient DB connect failure or exhausted `max_connections` at a scheduled scan time silently drops the whole cycle: discovery, PM, entries and the consolidated Telegram report. Logs say "complete". This is the misleading-status class from HIGH-007 and LOW-009.
- Repair: in `_daily_scan_job`, branch on `result.get("skipped")`. Persist a scheduler-run row with the reason, emit a deduplicated warning event when the reason is not `already_running`, and log at warning.
- Effort: S. Dependencies: LOW-009.
- Acceptance and test: with `try_acquire` mocked to raise, the scheduled job persists `skipped/full_terminal_scan_lock_unavailable`, emits one alert, and does not log "complete".

**CCTT-MED-019 — Losing the lock after Public equity execution skips funnel, stale-retry, cycle persistence and the Telegram report** (NEW 2026-10-13)
- Severity Medium · CONFIRMED (source, `aed0838`; not runtime-reproduced) · Open.
- Evidence:
  - `terminal_cycle.py:358` runs `public_execution.execute_pm_equity(...)`, which may submit live orders.
  - `:372-373` then returns `full_terminal_scan_lock_lost` if `cycle_lease.healthy()` fails before options execution.
  - That return comes before `_persist_stale_execution_retries` (`:381`), `_persist_execution_funnel` (`:402`), `telegram_events.dispatch_scan_report` (`:436`) and full-cycle persistence (`:438-463`).
  - The lock tests (`test_claude_audit_cycle_lock.py:63-76`) cover only an unhealthy session *before* the scan.
- Consequence: orders submitted in that cycle have no funnel or cycle record and no consolidated report. Stale-retry candidates are lost, and the operator cannot reconcile attempts against the PM docket for that cycle. Order rows in the trade ledger are unaffected.
- Repair: once any execution phase has run, a lost lease should skip only the *remaining execution phases* (options) and still run funnel, retry-queue and cycle persistence plus reporting, with `lock_lost` recorded.
- Effort: S. Dependencies: MED-007 and MED-008.
- Acceptance and test: mock `healthy()` to return True, True, False. Assert that equity execution ran, options were skipped, and the funnel, cycle document and Telegram dispatch were all called with `lock_lost=True`.

## 8. Tests

Environment: Python 3.11 venv; `POSTGRES_ENABLED=false RUN_EXTERNAL_API_TESTS=false ENABLE_TRADE_EXECUTION=false ENABLE_OPTIONS_EXECUTION=false DISABLE_SCHEDULER=true`; HTTP(S) proxy pointed at a dead port. All tests mocked.

- `main` 39ebd8b: `python -m pytest -q` exit 0, 502 passed, 12 skipped, 153 deselected, 126 subtests, 25 warnings.
- VPS branch `aed0838`: exit 0, 713 passed, 15 skipped, 153 deselected, 126 subtests, 25 warnings.
- `flake8 --select=E9,F63,F7,F82 backend`: 0 on both.
- CI: `main` 37857109552 success; `aed0838` 38273421339 success; `Claude-Gpt-audit` 97b5411 38247345781 success.
- Not exercised: live broker reads, real Postgres advisory-lock contention, scheduler timing, frontend build.

## Totals

48 items (Critical 2, High 14, Medium 19, Low 13) · CONFIRMED 41, SUSPECTED 4, UNVERIFIED 0, RESOLVED 3.

Status changes:
- MED-006, MED-008 and MED-017 → Partially addressed.
- HIGH-012 → worse.
- New: MED-018 and MED-019.

## Remediation order

1. Today, read-only: the operator position/protection check above, plus confirming the deployed SHA.
2. Release `cbb24d7`, `1979970` and `fdc4784` (CRIT-001/002, HIGH-014, HIGH-003) to a CI-green deploy branch, then confirm the deployed commit (HIGH-012, MED-017).
3. HIGH-004/005: kill switch and loss breaker on Public.
4. MED-018/019: lock-skip visibility and persistence.
