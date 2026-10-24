# Case Capital Trading Terminal — Daily Audit 2026-10-24

## 1. Ground truth

- **Audit time:** 2026-10-24 02:51–03:40 EDT (America/New_York, UTC−4), Saturday.
- **Market session:** CLOSED (weekend). The next regular session is Mon 2026-10-26 09:30 ET. No holiday or early close falls in between.
- **Repository:**
  - `main` @ `250f5eb`, unchanged since 2026-10-21. CI is green.
  - VPS heads are unchanged: `codex/vps-reviewed-release-20261021` @ `f05b9bb` and `codex/desktop-checkpoint` @ `e48177c`.
  - `Claude-Gpt-audit` moved `2011d6d` → `ff26279` with two commits: `a008515` (archive tests without paramiko) and `ff26279` (Accountant research total deadline). CI run 39984028371: success.
- **Deployed commit:** UNVERIFIED. There was no SSH, broker or database access.
- **Scope:** REPOSITORY-ONLY.
- **Overall verdict:** NOT VERIFIED (runtime). Code-level verdict: **CRITICAL** for exits. CRIT-001/002 and HIGH-014 are still unreleased on `main` and on both VPS heads.

| Area | Readiness |
|---|---|
| Infrastructure | NOT VERIFIED |
| Data | NOT VERIFIED |
| Entries | NOT VERIFIED. Code gap: HIGH-004/005 |
| Exits | **CRITICAL** at code level (CRIT-001, CRIT-002, HIGH-001, HIGH-014) |
| Learning | NOT VERIFIED (HIGH-008 partial) |
| Reporting | NOT VERIFIED (MED-012, MED-018/019) |

## 2. Critical / new findings

- **No new findings verified.** Backend code on `main` and on the VPS heads is byte-identical to the 2026-10-23 audit.
- **Critical evidence re-read on `250f5eb`, unchanged:**
  - CRIT-001: `public_execution.py:2231-2235` skips any row with `emergency_exit_order_id` forever.
  - CRIT-002: `execution_safety.py:62-71` has `ttl_seconds = 24*60*60`.
  - HIGH-004: `public_execution.py:1499-1502` checks only `enabled()`, with no `execution_gate`.

## 3. Position coverage and funnel

NOT VERIFIED: there was no broker or database access. Before the Mon 10-26 09:30 ET open, the operator should run read-only checks:

- Confirm the deployed SHA.
- List OPEN `tf_trades` rows that have `emergency_exit_order_id` or `SUBMIT_UNKNOWN`, and compare each with its broker status.
- Confirm that every held position has a broker protective order or fresh terminal exit coverage.
- Run `df` and check backup-directory growth (HIGH-013).

## 4. Prior-finding register changes

- **CCTT-LOW-016** (sealed-archive tests skipped in CI): Open → **Partially addressed**.
  - Independently reproduced on `Claude-Gpt-audit` @ `ff26279` in a clean venv from `backend/requirements.txt` (paramiko not installed): `pytest backend/tests/test_sealed_archive.py -rs` gave 14 passed, 0 skipped.
  - On `main` the 14 tests are still skipped: the full main suite shows 27 skipped, 14 of them archive tests.
  - Not on `main` or the VPS heads.
- **CCTT-LOW-007** (Accountant enrichment time budget): Open → **Partially addressed** (branch only).
  - `ff26279` wraps `ticker_research` and the batch in `asyncio.wait_for(…, 15.0)`, and `test_accountant_research.py` passes (101 passed).
  - Design note: one slow ticker makes the whole batch return `enriched: 0`, which fails closed, not partial. This is acceptable for research-only data; record it as intended behaviour.
  - Not on `main` or the VPS heads.
- **CCTT-HIGH-012** (release parity): evidence refreshed. `Claude-Gpt-audit` is 32 ahead / 8 behind `main`; the VPS release head is 382 ahead / 0 behind `main`.
- **All others:** unchanged and carried forward. RESOLVED stays at 4 (HIGH-006, MED-009, MED-010, LOW-012).

Totals: 51 items (Critical 2, High 14, Medium 19, Low 16) · CONFIRMED 43, SUSPECTED 4, UNVERIFIED 0, RESOLVED 4.

## 5. Tests

Environment: fresh Python 3.11.17 venv from `backend/requirements.txt` plus pytest 9.0.3 and pytest-asyncio 1.3.0. Execution, scheduler and Postgres disabled by the `pytest.ini` marker filter; all mocked.

| Target | Command | Result |
|---|---|---|
| `main` @ 250f5eb | `pytest backend/tests tests -q -rs` | exit 0; 532 passed, 27 skipped (14 archive, 12 external API, 1 isolated PG), 153 deselected, 126 subtests |
| `main` | `flake8 --select=E9,F63,F7,F82 backend` | clean |
| `Claude-Gpt-audit` @ ff26279 | `pytest backend/tests/test_sealed_archive.py -rs` | 14 passed, 0 skipped |
| `Claude-Gpt-audit` @ ff26279 | `pytest backend/tests tests -q` | 939 passed, 14 skipped, 153 deselected, 126 subtests |

Not exercised:
- Live broker lifecycle.
- PostgreSQL concurrency (advisory locks, leadership).
- Real Telegram.
- Deployed systemd/nginx/TLS.
- Backup/restore on the VPS.

## 6. Remediation order (unchanged)

1. Promote and deploy the exit-lockout fixes (CRIT-001, CRIT-002, HIGH-014, HIGH-001), then verify every held position's protection.
2. Public kill switch and loss breaker (HIGH-004, HIGH-005).
3. Release parity and green-CI deploy gate (HIGH-012, MED-017), plus backup pruning (HIGH-013).
4. TLS (HIGH-011).
5. Remaining items.

## 7. Unknowns / operator actions

- Deployed SHA, positions, orders, buying power, quote ages, scheduler run history, disk and backups: all UNVERIFIED.
- Promoting `Claude-Gpt-audit` work to `main` and the VPS requires operator review. This audit made no production changes.
