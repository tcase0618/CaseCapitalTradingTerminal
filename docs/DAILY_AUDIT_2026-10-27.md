# Case Capital Trading Terminal — Daily Audit 2026-10-27

## 1. Ground truth

- Audit time: 2026-10-27 02:52–03:20 EDT (America/New_York, UTC−4), Tuesday. Daily audit run 20.
- Market session: CLOSED / PRE-MARKET at audit time. Regular session today 09:30–16:00 ET; not a holiday, no early close. US DST ends Sun 2026-11-01.
- Repository:
  - `main` @ `250f5eb` (unchanged since 2026-10-21; CI green).
  - VPS heads unchanged: `codex/vps-reviewed-release-20261021` @ `f05b9bb` and `codex/desktop-checkpoint` @ `e48177c`.
  - **`Claude-Gpt-audit` moved `29b1296` → `98507cd`** ("fix(options): preserve conservative earnings IV evidence"). CI run 40389962570: success.
- Deployed commit: **UNVERIFIED**. No SSH, broker or DB access.
- Scope: **REPOSITORY-ONLY**.
- Overall verdict: **NOT VERIFIED (runtime) / code-level CRITICAL for exits** on `main` and both VPS heads, unchanged for 18 days. Infrastructure and data: NOT VERIFIED. Entries: NOT VERIFIED (HIGH-004 open). Exits: CRITICAL in code. Learning: partial. Reporting: partial.

## 2. Critical findings

No new findings. Re-checked on `250f5eb`, all unchanged:

- CRIT-001: `backend/services/public_execution.py:2232-2235` skips any row that has `public_phase_order_id` or `emergency_exit_order_id`, with no age or status check.
- CRIT-002: `backend/services/execution_safety.py:68` `ttl_seconds = 24 * 60 * 60`. The same line is unchanged on `98507cd`.
- HIGH-004: `backend/services/public_execution.py:1499-1502` `execute_pm_equity` gates only on `enabled()`. It is at `:1525-1528` on `98507cd`.

## 3. Independent review of `98507cd` (Codex remediation 2026-10-26)

- **CCTT-MED-014 → Partially addressed (Claude-Gpt-audit only).** Verified in source:
  - `earnings_engine.py` now requires a finite `iv_rank` in [0,100] before calling IV cheap. A missing rank yields `AVOID` with `iv_evidence=UNKNOWN`.
  - `options_engine.select_strategy` routes earnings names with HIGH/VERY_HIGH absolute ATM IV to the event-scout/avoid lane.
  - `assess_iv_crush_risk` returns HIGH or SEVERE for elevated absolute IV within 14 days of earnings.
  - The helpers `_num`, `math` and `iv_history.absolute_iv_label` are all imported or defined.
  - 55/55 cases in `test_claude_audit_unknown_iv.py` pass.
- **Residual, confirmed:** `iv_rank is None` still passes these gates on `98507cd`:
  - `options_desk.py:993, 995, 998, 1023, 1025`
  - `options_engine.py:713, 716, 722, 725, 728, 731` (non-earnings selection rules)

  Main and the VPS heads are unchanged.
- **CCTT-MED-003 → Partially addressed (Claude-Gpt-audit only).**
  - `public_execution.py:1852-1857` on `98507cd` now compares `protective_stop_price` against the active stop, inside `reconcile()`, which iterates every OPEN Public trade.
  - `main` and VPS `:1787` still lack the stop-price comparison.
  - The re-arm path is still subject to CRIT-002, since the 24 h default TTL is unchanged.
  - Not lifecycle-tested against a real broker.

## 4. Position coverage and funnel

NOT VERIFIED: no broker, ledger or DB access. No table is produced, to avoid inventing data.

## 5. Prior-finding register

Status changes: MED-003 and MED-014 Open → Partially addressed (branch only, not on `main` or VPS). Totals: 51 items (Critical 2, High 14, Medium 19, Low 16) · CONFIRMED 43, SUSPECTED 4, UNVERIFIED 0, RESOLVED 4 (HIGH-006, MED-009, MED-010, LOW-012).

## 6. Tests (fresh Python 3.11 venv from `backend/requirements.txt`; execution, scheduler and Postgres disabled; no broker or DB credentials in the environment; mocked only)

| Target | Command | Result |
|---|---|---|
| `main` @ 250f5eb | `pytest backend/tests tests -q -rs -p no:cacheprovider` | exit 0; 532 passed, 27 skipped (14 archive, 12 external API, 1 isolated PG), 153 deselected, 25 warnings, 126 subtests |
| `Claude-Gpt-audit` @ 98507cd | same | exit 0; 1008 passed, 14 skipped, 153 deselected, 25 warnings, 126 subtests |
| `98507cd` | `pytest backend/tests/test_claude_audit_unknown_iv.py` | 55 passed |
| both | `flake8 --select=E9,F63,F7,F82 backend` | clean |

Not exercised: live broker lifecycle, PostgreSQL concurrency, Telegram, systemd/nginx/TLS, VPS backup/restore, Docker (CLI present, no daemon).

## 7. Remediation order (unchanged)

1. Promote and deploy the exit-lockout fixes (CRIT-001, CRIT-002, HIGH-014, HIGH-001), then verify protection for every held position.
2. Public kill switch and loss breaker (HIGH-004, HIGH-005).
3. Release parity and a green-CI deploy gate (HIGH-012, MED-017); backup pruning (HIGH-013).
4. TLS (HIGH-011).
5. Finish MED-014 (options_desk and non-earnings `iv_rank is None` gates).
6. Everything else.

## 8. Operator actions (read-only, before 09:30 ET today)

- Confirm the deployed SHA.
- List OPEN `tf_trades` rows that carry `emergency_exit_order_id`, `public_phase_order_id` or `SUBMIT_UNKNOWN`, and reconcile them with broker order status.
- Confirm broker or fresh terminal protection for every held position.
- Measure disk and backup growth (HIGH-013).

This audit made no production changes.
