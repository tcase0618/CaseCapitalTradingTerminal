# Trading Terminal Daily Audit, 2026-10-09

- Audit time: 2026-10-09 ~03:00 EDT (UTC-4). Market CLOSED (pre-market, Friday, regular trading day).
- Repository: `main` @ `39ebd8b`.
  - `Claude-Gpt-audit` @ `f9822e4` (remediation, unpromoted).
  - VPS branch per AGENTS.md: `codex/desktop-checkpoint` @ `c265a52`.
- Deployed commit: UNVERIFIED. No SSH or broker access.
- Scope: REPOSITORY-ONLY. Full backlog: Notion task "Case Capital Trading Terminal - Upgrade Backlog".

## Verdict

**NOT VERIFIED** at runtime. Code-level **CRITICAL** for exits.

| Area | Status |
|---|---|
| Infrastructure | NOT VERIFIED |
| Data | NOT VERIFIED |
| Entries | Kill-switch and daily-loss gaps still open on main and on the VPS branch |
| Exits | CRITICAL |
| Learning | Forward labels fixed on main |
| Reporting | Protection alerts fixed on main |

## Key fact

The VPS branch `codex/desktop-checkpoint` does not contain the exit remediation (`cbb24d7` / `f9822e4`). `git merge-base --is-ancestor` returns false.
- Its `public_execution.py:2232` still skips any row that has an `emergency_exit_order_id`.
- Its backup script does not prune old backups.

If production follows that branch, CRIT-001, CRIT-002 and HIGH-013 are live in code.

## Offline reproductions on main

- **CRIT-001:** a dead emergency order (EXPIRED, CANCELLED or REJECTED) gets no retry. After 5 passes there is still 1 submission, and the monitor reports `ok=True`.
- **CRIT-002:** a transient submit error leads to 0 retries over 5 passes, with no alert.
- **Audit-branch regression files run against main:**
  - Exit-lifecycle tests: 23/23 fail.
  - Control tests: 8/19 fail.
  - All of these pass on `Claude-Gpt-audit`.

## New findings

**CCTT-HIGH-014 (on `Claude-Gpt-audit`): an ambiguous emergency placement that never reached Public is never retried.**
- The order is left in `SUBMIT_UNKNOWN`, and the intent is never reclaimed.
- `get_order` returns 404, and the code treats that as "deferred" with `ok=True`.
- Reproduced on `f9822e4`.

**CCTT-MED-017: CI fails on the VPS branch.**
- Run 37857123976 on `c265a52` failed: 2 failed, 618 passed.
- The cause is test drift in `test_iv_history.py`.
- There is no rule requiring green CI before a deploy.

## Status changes

**RESOLVED on main** (deployment unverified):
- HIGH-006
- MED-009
- MED-010

**Partially addressed on main:**
- HIGH-008
- HIGH-009
- HIGH-010
- LOW-013

**Fixed only on the unpromoted branch:**
- CRIT-001 and CRIT-002 (partial)
- HIGH-001, HIGH-002, HIGH-004, HIGH-005
- HIGH-007 (partial)
- MED-001, MED-002
- MED-003, MED-007, MED-012 (partial)
- LOW-005
- LOW-007 (partial)
- LOW-010

**Worse than before:**
- HIGH-012
- MED-015 (now also on main)

## Tests

| Branch | Result |
|---|---|
| main | exit 0: 502 passed, 12 skipped, 153 deselected, 25 warnings |
| `Claude-Gpt-audit` | 691 passed, 14 skipped, 153 deselected |

- Test environment: execution, scheduler and Postgres disabled, dummy credentials, network blocked.
- flake8 E9/F63/F7/F82: exit 0 on both branches.
- Frontend: 160/160 passed.

**CI:**

| Target | Run | Result |
|---|---|---|
| main 39ebd8b | 37857109552 | success |
| f9822e4 | 37766914155 | success |
| c265a52 | 37857123976 | FAILURE |

All of this is mocked testing only. There is no live read-only or broker lifecycle evidence.

## Top actions

1. **Operator read-only checks:**
   - Confirm the deployed commit.
   - Check the broker status of every OPEN position with `emergency_exit_order_id` set.
   - Check VPS disk and backup-directory growth.
2. **Fix HIGH-014, then review and promote the exit and control fixes to main and the VPS branch.** The fixes are CRIT-001/002, HIGH-001/002/004/005 and MED-001/002.
3. **Restore backup retention on the VPS branch (HIGH-013), and add a green-CI deploy gate plus a deployed-commit endpoint (MED-017, HIGH-012).**

## Not verified

- Positions and protective orders
- Quote ages
- Scheduler history
- Funnel counts
- Postgres, disk and backups
- Telegram secret and chat-id presence
- Public's actual dedupe and 404 behavior
