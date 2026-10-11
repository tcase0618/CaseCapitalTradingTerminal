# Case Capital Trading Terminal — daily audit 2026-10-11

## Ground truth

- **Audit time:** 2026-10-11 02:51–03:30 EDT (America/New_York, UTC−4). Daily audit run 4.
- **Market session:** CLOSED. Sunday, not a trading day. Mon 2026-10-12 (Columbus Day) is a **regular NYSE session**; the bond market is closed. Public session detection is weekday-and-clock based (`public_execution.py:1065-1070`), so it correctly treats Monday as CORE.
- **Repository `main`:** `39ebd8b`, unchanged since 2026-10-09. Clean worktree. The audit branch `claude/stoic-knuth-hv9su2` equals `main` before this artifact.
- **VPS branch** per `AGENTS.md`: `origin/codex/desktop-checkpoint` @ `c265a52`, unchanged. It still does not contain `Claude-Gpt-audit`.
- **Remediation branch:** `origin/Claude-Gpt-audit` @ `fdc4784`, unchanged since 2026-10-09 15:30 EDT.
- **Commits since the previous audit:** only `a47b6d9`, the 2026-10-10 audit artifact.
- **Deployed commit:** NOT VERIFIED. No SSH, broker or database access.
- **Scope:** REPOSITORY-ONLY.

## Verdict

**Overall: NOT VERIFIED (runtime). Code level: CRITICAL** for exits on `main` and the VPS branch. This is unchanged, and Monday is a trading day.

| Area | Status |
|---|---|
| Infrastructure | NOT VERIFIED |
| Data | NOT VERIFIED |
| Entries | Kill-switch (HIGH-004) and daily-loss (HIGH-005) gaps open on main/VPS branch |
| Exits | **CRITICAL.** CRIT-001/002 open on main and the VPS branch. The fixes exist only on the unpromoted `Claude-Gpt-audit`, and HIGH-014 is partially addressed there. |
| Learning | Forward labels fixed; historical labels not regraded |
| Reporting | Protection alerts fixed on main |

## Position coverage table / funnel

**NOT VERIFIED.** There was no broker, ledger or persisted-cycle access.

Before Monday's open, an operator read-only check is required:

- List every OPEN `tf_trades` row with a non-null `emergency_exit_order_id` and compare it with the broker order status.
- Flag any `emergency_exit_status = SUBMIT_UNKNOWN`.
- Confirm that each held position has a live broker protective order or fresh terminal coverage.

## Tests (main @ 39ebd8b; Python 3.11 venv; execution, scheduler and Postgres disabled; proxy pointed at a dead port)

- `python -m pytest -q`: exit 0. **502 passed, 12 skipped, 153 deselected**, 25 warnings, 126 subtests passed. Identical to 2026-10-10.
- `flake8 --select=E9,F63,F7,F82 backend`: 0 findings.
- `flake8 --select=F backend`: 29×F401, 22×F841, 2×F811. Unchanged.
- **CI:**
  - main 39ebd8b run 37857109552: success.
  - `Claude-Gpt-audit` fdc4784 run 37980694014: success.
  - VPS branch c265a52 run 37857123976: **FAILURE**, still not fixed (CCTT-MED-017).
- All tests are mocked. There is no live read-only or broker lifecycle evidence.

## New or regressed findings

None verified. No backend, deploy or frontend source changed on any branch since the previous audit, so all file:line references stand.

## Corrections to prior evidence

**CCTT-HIGH-013 timeline.** The 2026-10-10 note said "roughly 3 weeks remain". That figure does not follow from the recorded inputs:

- The VPS branch doc says about 32 GB was available after the deploy (`docs/VPS_RESOURCE_REDUCTION_PLAN.md:209-210`).
- The prior estimate used about 0.87 GB per dump.
- At that rate, about 37 nights remain from the deploy date, which puts the fill date near mid-November.

The inputs are documentation, not measured growth. The estimate also excludes database, journal and log growth, and the deploy date is unverified. **Treat the date as unknown until `df` and backup-directory size are measured on two nights.**

## Prior findings

There are no status changes. The 46 items keep their 2026-10-10 statuses:

- Critical 2, High 14, Medium 17, Low 13.
- CONFIRMED 39, SUSPECTED 4, RESOLVED 3.

## Remediation order (unchanged)

1. **Operator read-only checks before Mon 10-12 09:30 ET:**
   - the deployed SHA
   - OPEN rows with an emergency id or `SUBMIT_UNKNOWN`
   - broker protective orders per position
   - `TELEGRAM_WEBHOOK_SECRET` and `TELEGRAM_CHAT_ID` are set (names only)
   - `df` and backup-directory size
2. **Review and promote the branch exit fixes** (CRIT-001/002, HIGH-014, HIGH-003, HIGH-001/002/004/005, MED-001/002) to main **and** the VPS branch, with green CI on the exact deploy SHA (MED-017, HIGH-012).
3. **Fix HIGH-013 backup retention** on the VPS branch.

## Unknowns / blocked

All of the following are blocked or unverified:

- broker positions, orders and buying power
- quotes
- scheduler registry and run history
- PostgreSQL, disk and backups
- logs
- the deployed commit
- Public and Alpaca behaviour for the SUSPECTED items

There is no restore-test evidence.
