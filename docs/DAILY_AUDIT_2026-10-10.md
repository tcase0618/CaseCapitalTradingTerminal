# Case Capital Trading Terminal — daily audit 2026-10-10

## Ground truth

- **Audit time:** 2026-10-10 02:51–03:40 EDT (America/New_York, UTC−4).
- **Market session:** CLOSED. Saturday, so not a trading day. Next session is Mon 2026-10-12, which is a regular NYSE session (Columbus Day is not an exchange holiday).
- **Repository `main`:** `39ebd8b`, unchanged since the 2026-10-09 audit. Clean worktree. Audit branch `claude/stoic-knuth-63k5j2` equals `main`.
- **VPS branch** per `AGENTS.md`: `origin/codex/desktop-checkpoint` @ `c265a52`, unchanged.
  - It does **not** contain `origin/Claude-Gpt-audit`: `git merge-base --is-ancestor` returns false.
- **Remediation branch** `origin/Claude-Gpt-audit`: moved from `f9822e4` to `fdc4784` (19 ahead / 4 behind main). New commits:
  - `1979970` — replays ambiguous Public exits using the same order identity (aimed at HIGH-014).
  - `fdc4784` — reconciles working options closes before reserving replacement sells (aimed at HIGH-003).
  - `abb0627`, `87bbe9e`, `c962d96` — frontend and equity-study work.
- **Deployed commit:** NOT VERIFIED. No SSH, broker, or database access.
- **Scope:** REPOSITORY-ONLY.

## Verdict

**Overall: NOT VERIFIED (runtime). Code level: CRITICAL** for exits on `main` and on the VPS branch.

| Area | Status |
|---|---|
| Infrastructure | NOT VERIFIED |
| Data | NOT VERIFIED |
| Entries | Kill-switch and daily-loss gaps open on main/VPS branch |
| Exits | **CRITICAL.** CRIT-001/002 unchanged on main and the VPS branch. The fixes are still only on the unpromoted branch. |
| Learning | Forward labels fixed; historical labels not regraded |
| Reporting | Protection alerts fixed on main |

## Position coverage table / funnel

**NOT VERIFIED.** There was no broker, ledger, or persisted-cycle access.

An operator read-only check is still required. List every OPEN `tf_trades` row that has a non-null `emergency_exit_order_id` and compare it with the broker order status. Flag any `emergency_exit_status = SUBMIT_UNKNOWN`.

## Changes verified this run

### CCTT-HIGH-014 on `Claude-Gpt-audit` @ fdc4784 — Partially addressed (branch only)

Changes in `backend/services/public_execution.py`:

- The immutable `emergency_exit_request` (including `account_id`) is now persisted before placement.
- `_replay_unknown_emergency_request` replays the request when all of these hold:
  - the status is still `SUBMIT_UNKNOWN`
  - `get_order` raises
  - the previous attempt was at least 60 s ago
  - it is the same ET date
  - the account is the same
  - broker-held quantity equals the request quantity
  - the order id equals `uuid5(client_id)`
- The replay uses the same client id, relying on Public's documented deduplication. This contract has not been live-verified.
- A deferred `SUBMIT_UNKNOWN` row now adds an error (`ok=False`) and emits the critical `public_exit_attempt_blocked` event on every pass.
- `submit_equity_order` accepts `account_id` (`public_api.py:762`), so the kwargs expansion is valid.

Residual risk, by design:

- Cross-date, changed-holding, changed-account, and legacy rows with no saved request are never replayed. They stay blocked, with a critical alert on every pass, until an operator acts.
- A partially-filled-then-unknown position is therefore unprotected apart from that alert.

This is not on `main` or the VPS branch.

### CCTT-HIGH-003 on `Claude-Gpt-audit` — Partially addressed (branch only)

- `options_desk.py` gained +92 lines. It reconciles working sells, confirms cancellation before replacement, and uses deterministic ids with an intent reservation.
- 8 new tests in `test_claude_audit_option_closes.py` pass inside the full suite.
- Two-process PostgreSQL concurrency is not verified.

### Other findings

All other 44 findings are unchanged. `main` and the VPS branch did not move, so the 2026-10-09 register and line numbers stand.

## New findings

None. No new defect was verified this run. `main` and the deploy branch are byte-identical to yesterday's audit, and the branch delta was reviewed above.

## Tests

| Target | Command / environment | Result |
|---|---|---|
| `main` @ 39ebd8b | Python 3.11 venv; `POSTGRES_ENABLED=false RUN_EXTERNAL_API_TESTS=false RUN_LIVE_TRADING_TESTS=false ENABLE_SCHEDULER=false ENABLE_TRADE_EXECUTION=false ENABLE_OPTIONS_EXECUTION=false ALLOW_LIVE_EQUITY_EXECUTION=false`; proxy env pointed at a dead port; `python -m pytest -q` | exit 0. 502 passed, 12 skipped, 153 deselected, 25 warnings, 126 subtests |
| `main` lint | `flake8 --select=E9,F63,F7,F82 backend` | 0 findings |
| `Claude-Gpt-audit` @ fdc4784 | Same environment | 756 passed, 14 skipped, 153 deselected, 126 subtests |
| CI, `main` 39ebd8b | run 37857109552 | success |
| CI, `fdc4784` | run 37980694014 | success |
| CI, VPS branch `c265a52` | run 37857123976 | **FAILURE** (unchanged; CCTT-MED-017) |

- Frontend was not re-run, since there was no `main` change.
- All tests were mocked. There is no live read-only evidence and no broker lifecycle evidence.

## Totals

46 items:

- By severity: Critical 2, High 14, Medium 17, Low 13.
- By status: CONFIRMED 39, SUSPECTED 4, UNVERIFIED 0, RESOLVED 3.

## Remediation order (unchanged, with today's note)

0. **Operator, read-only:**
   - Confirm the deployed SHA.
   - Check OPEN rows that carry an emergency id.
   - Check `df` and backup-directory growth. The VPS branch does not prune backups (HIGH-013), and roughly 5 weeks of headroom was estimated on 2026-10-08.
1. Review and promote the CRIT-001/002, HIGH-014, HIGH-003, HIGH-001/004/005/002 and MED-001/002 fixes from `Claude-Gpt-audit` to `main` **and** the VPS branch. Require green CI on the exact deploy SHA.
2. Fix HIGH-013 retention on the VPS branch.
3. Add a deployed-commit endpoint and a green-CI deploy gate (HIGH-012, MED-017).

## Not verified

- Broker positions, orders and buying power
- Quote ages
- Scheduler registry and run history
- PostgreSQL, disk and backups
- Telegram/frontend parity on a persisted cycle
- Public's real deduplication and 404 behaviour for replayed orders
- Deployed commit
