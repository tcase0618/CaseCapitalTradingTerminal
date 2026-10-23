# Case Capital Trading Terminal — daily audit 2026-10-23

- Audit time: 2026-10-23 02:53–04:10 EDT (America/New_York, UTC−4), Friday.
- Market session: CLOSED (pre-market). Fri 10-23 is a regular NYSE trading day; no holiday or early close. Opens 09:30 ET.
- Repository: `main` @ `250f5eb` (unchanged since 10-21; CI 39598659904 success). Audit branch `claude/stoic-knuth-uln4gl` = `main`.
  - `Claude-Gpt-audit` advanced `3404100` → `2011d6d` ("Reject stale or unvalidated Accountant research packets"; `backend/services/accountant_research.py` and its tests only). CI 39848325440: success.
  - VPS branches unchanged: `codex/vps-reviewed-release-20261021` @ `f05b9bb`, `codex/desktop-checkpoint` @ `e48177c`.
- Deployed commit: UNVERIFIED. No deploy record since 10-21; no SSH, broker or database access.
- Scope: **REPOSITORY-ONLY**. Nothing here is evidence of VPS or broker behaviour.

## Verdict

- Overall: **NOT VERIFIED** (runtime) / code-level **CRITICAL** for exits on `main` and both VPS heads, unchanged for 14 days. Today is a trading day.
- Infrastructure: NOT VERIFIED. Data: NOT VERIFIED. Entries: NOT VERIFIED (HIGH-004 Public kill-switch gap open). Exits: CRITICAL at code level (CRIT-001, CRIT-002, HIGH-014). Learning: NOT VERIFIED (HIGH-008 open). Reporting: NOT VERIFIED.

## Evidence re-checked on 250f5eb

- CCTT-CRIT-001: `backend/services/public_execution.py:2231-2234` still `continue`s for any row with `emergency_exit_order_id`, with no age/status check — unchanged.
- CCTT-CRIT-002: `backend/services/execution_safety.py:62-71` `claim_execution_intent(... ttl_seconds=24*60*60)` — unchanged.
- CCTT-HIGH-004: `backend/services/public_execution.py:1499-1502` `execute_pm_equity` gates only on `enabled()` — unchanged.
- No `backend/services` change on `main` since `aab210a`.

## Position coverage table and order funnel

NOT VERIFIED — no broker or database access. Operator read-only checks are due **before Fri 10-23 09:30 ET**:
confirm the deployed SHA; list OPEN `tf_trades` with non-null `emergency_exit_order_id` or `SUBMIT_UNKNOWN` and compare with broker status; confirm broker or fresh terminal protection for every held position; `df` and backup-directory growth (HIGH-013).

## Tests (fresh Python 3.11 venv from `backend/requirements.txt`; `POSTGRES_ENABLED=false RUN_EXTERNAL_API_TESTS=false RUN_LIVE_TRADING_TESTS=false`; all mocked; execution disabled)

- `python -m pytest backend/tests tests -q -p no:cacheprovider` on `main`: exit 0, **532 passed, 27 skipped, 153 deselected**, 126 subtests, 25 warnings (deprecated `on_event`). Skips as on 10-22: 14 sealed-archive (no paramiko), 12 external-API, 1 isolated-PG.
- Not run: frontend build, live read-only broker checks, Postgres integration tests.

## New findings

None verified today. `main` and the VPS heads are byte-identical to the 10-22 audit for backend code.

## Prior findings — status changes

- **CCTT-LOW-007 — Open, worsened on the unpromoted audit branch**: `2011d6d` removes the fresh-cache short-circuit in `ticker_research` (`accountant_research.py:260-320` on that commit). Every call now performs two live Accountant HTTP requests, including from `pm_brain.py:249` on the PM path, still with no total time budget. Not on `main` or any VPS branch. Positive: the consumer now rejects unvalidated, stale, future-dated or authority-conflicting packets, which matches the Accountant producer's current `accepted=false` state.
- All other items: carried forward unchanged. The backend is unchanged, so this is not an independent re-validation of each item beyond the three spot checks above.

## Remediation order (unchanged)

1. Release the exit-lockout fixes (CRIT-001, CRIT-002, HIGH-014) from `Claude-Gpt-audit` after review, with production-shaped tests.
2. Public kill switch and loss breaker on the Public account (HIGH-004, HIGH-005).
3. Release parity check (HIGH-012) and VPS backup pruning (HIGH-013).
4. Everything else per the Notion backlog.

## Unknowns / blocked

- Deployed SHA, live positions, broker protective orders, quotes, scheduler history, Telegram/frontend parity, Postgres health, disk, backups: all NOT VERIFIED.
