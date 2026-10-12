# Case Capital Trading Terminal — daily audit 2026-10-12

## 1. Ground truth

- **Audit time:** 2026-10-12 02:51–03:40 EDT (America/New_York, UTC−4). Daily audit run 5.
- **Market session:** pre-market, CLOSED at audit time. Mon 2026-10-12 (Columbus Day) is a **regular NYSE trading day**; the bond market is closed. Regular session opens 09:30 ET.
- **Repository:**
  - `main` @ `39ebd8b`, unchanged since 2026-10-08.
  - VPS branch `origin/codex/desktop-checkpoint` @ `c265a52`, unchanged. It still does not contain the remediation branch (`git merge-base --is-ancestor origin/Claude-Gpt-audit origin/codex/desktop-checkpoint` = false).
  - `origin/Claude-Gpt-audit` moved `fdc4784` → `8b8fb01` (21 ahead / 4 behind `main`). New commits: `bfa3132` (rebalance sells through the monitored exit ledger) and `8b8fb01` (halt cancels verified entry buys). CI run 38197452288 on `8b8fb01`: success.
- **Deployed commit:** UNVERIFIED (no SSH, broker or DB access).
- **Scope:** REPOSITORY-ONLY. Nothing here is evidence of VPS behavior.

## Verdict

- **Overall:** NOT VERIFIED (runtime) / code-level **CRITICAL** for exits on `main` and on the VPS branch. Today is a trading day.
- Infrastructure: NOT VERIFIED. Data: NOT VERIFIED.
- Entries: kill-switch and daily-loss gaps open on `main`/VPS branch.
- Exits: CRITICAL. CCTT-CRIT-001/002 unchanged; `backend/services/public_execution.py:2232` still skips any OPEN row that has an `emergency_exit_order_id`, on both `main` and `c265a52`.
- Learning: forward labels fixed on `main`. Reporting: protection alerts fixed on `main`.

## 2. Positions and exit protection

NOT VERIFIED. No broker, ledger or DB access. **Operator read-only check required before 09:30 ET today:** list OPEN `tf_trades` rows with a non-null `emergency_exit_order_id` or `emergency_exit_status = SUBMIT_UNKNOWN`, compare each with its broker order status, and confirm a broker protective order or fresh terminal monitoring for every held position.

## 3–5. Quotes, scheduler, funnel

NOT VERIFIED (no runtime access). No funnel counts can be stated.

## Branch delta reviewed (`fdc4784..8b8fb01`, not on `main` or the VPS branch)

- **CCTT-MED-004 (rebalance sells invisible to exit monitor):** `bfa3132` routes rebalance sells through `public_execution.submit_exit_to_cash` with reason `pm_rebalance:<intent_id>`, so the monitored ledger owns them. Legacy rotation intents without a linked order block protective exits with `unlinked_rebalance_exit_requires_reconciliation` and record the block. Tests: `backend/tests/test_claude_audit_rebalance_exits.py`. Status: **Partially addressed (branch only)**. Residual: a legacy unlinked intent now blocks the stop exit for that ticker until an operator reconciles it; `_record_exit_attempt_blocked` emits a critical `public_exit_attempt_blocked` Telegram event (`public_execution.py:2390-2391` on the branch), so it is visible, but the position has no automated stop exit until reconciliation.
- **CCTT-MED-005 (halt doesn't cancel live buys):** `8b8fb01` adds `backend/services/halt_orders.py`. `/admin/halt` now cancels only verified opening BUY orders on Public and Alpaca options, polls each to confirm CANCELLED, and returns `ok=False` on any unverified row. Tests: `backend/tests/test_claude_audit_halt_orders.py`. Status: **Partially addressed (branch only)**. Residuals:
  - Classification requires the Public order-search response to echo `openCloseIndicator=OPEN`, `instrument.type=EQUITY` and `orderClass=SIMPLE`. The terminal sends `openCloseIndicator` on submit (`public_api.py:712`), but whether search results echo it is not live-verified. If absent, `halt_orders.py:37` (and `:40` for asset type) evaluates `str(None)` = `"NONE"` (the conditional binds looser than `or`), so every Public buy is reported as `entry_or_nested_exit_binding_unverified` and none is cancelled. Fail-visible, not fail-safe.
  - `_public_entries` returns an error without cancelling when `public_execution.enabled()` is false, so buys placed before an env-flag disable are left working.

## 6–7. Controls, outcomes, learning

Unchanged from the 2026-10-11 register. NOT VERIFIED at runtime.

## 8. Tests

Environment: Python 3.11 venv; `POSTGRES_ENABLED=false RUN_EXTERNAL_API_TESTS=false RUN_LIVE_TRADING_TESTS=false ENABLE_SCHEDULER=false ENABLE_TRADE_EXECUTION=false ENABLE_OPTIONS_EXECUTION=false ALLOW_LIVE_EQUITY_EXECUTION=false`; HTTP(S) proxy pointed at a dead port. All tests mocked.

- `main` 39ebd8b: `python -m pytest -q` exit 0 — 502 passed, 12 skipped, 153 deselected, 126 subtests, 25 warnings. Same as 10-11.
- `Claude-Gpt-audit` 8b8fb01: 774 passed, 14 skipped, 153 deselected (was 756 at fdc4784; +18 from the halt and rebalance-exit tests).
- `flake8 --select=E9,F63,F7,F82 backend` (main): 0.
- CI: main run 37857109552 success; branch 8b8fb01 run 38197452288 success; **VPS branch c265a52 run 37857123976 still FAILURE** (CCTT-MED-017).
- Not exercised: any live read-only broker call, real Public order-search schema, real Postgres, scheduler in-process timing.

## Findings

- **New verified findings:** none.
- **Status changes:** CCTT-MED-004 and CCTT-MED-005 → Partially addressed (branch only). Neither fix is on `main` or the VPS branch.
- **Totals:** 46 items (Critical 2, High 14, Medium 17, Low 13) · CONFIRMED 39, SUSPECTED 4, UNVERIFIED 0, RESOLVED 3.

## Remediation order (unchanged in substance)

1. Today, read-only: the operator position/protection check above.
2. Review and release CRIT-001/002, HIGH-014 and HIGH-003 from `Claude-Gpt-audit` to a CI-green deploy branch (MED-017), then confirm the deployed commit.
3. HIGH-004/005 (kill switch and daily-loss breaker on the Public account), HIGH-006/007 (truthful protection alerts).
4. HIGH-013 backup pruning before the disk fills (date unknown until growth is measured).

## Unknowns and blocked access

Deployed commit, positions, broker orders, quotes, scheduler history, Postgres, disk and backups: all NOT VERIFIED. All tests are mocked; there is no live read-only or broker lifecycle evidence.
