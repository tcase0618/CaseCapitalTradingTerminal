# Case Capital Trading Terminal — Daily Audit 2026-10-25

## 1. Ground truth

- Audit time: 2026-10-25 02:51–03:45 EDT (America/New_York, UTC−4), Sunday. Daily audit run 18.
- Market session: CLOSED (weekend). Next regular session Mon 2026-10-26 09:30 ET; no holiday or early close before it. US DST ends Sun 2026-11-01.
- Repository:
  - `main` @ `250f5eb`, unchanged since 2026-10-21. CI green.
  - VPS heads unchanged: `codex/vps-reviewed-release-20261021` @ `f05b9bb` (382 ahead / 0 behind `main`), `codex/desktop-checkpoint` @ `e48177c`.
  - `Claude-Gpt-audit` moved `ff26279` → `29b1296` ("fix(auth): require operator configuration for sensitive local runtimes"; `backend/server.py`, `docker-compose.yml`, new `backend/tests/test_claude_audit_local_auth.py`). Now 33 ahead / 8 behind `main`. CI run 40151357171: success.
- Deployed commit: **UNVERIFIED** — no SSH, broker or DB access.
- Scope: **REPOSITORY-ONLY**. Local tests are not proof of VPS behaviour.
- Overall verdict: **NOT VERIFIED (runtime) / code-level CRITICAL for exits** on `main` and both VPS heads. Unchanged for 16 days.
  - Infrastructure: NOT VERIFIED. Data: NOT VERIFIED. Entries: NOT VERIFIED (HIGH-004 open in code). Exits: CRITICAL in code (CRIT-001/002, HIGH-014). Learning: partially addressed (HIGH-008). Reporting: partially addressed (MED-012/018/019).

## 2. Critical findings

No new critical findings. CRIT-001 and CRIT-002 are unchanged and still open on `main` and the VPS heads; re-checked on `250f5eb`:

- CRIT-001: `backend/services/public_execution.py:2232-2235` still skips any row with `emergency_exit_order_id` or `public_phase_order_id`, with no age/status check.
- CRIT-002: `backend/services/execution_safety.py:68` still defaults `ttl_seconds = 24 * 60 * 60`.
- HIGH-004: `backend/services/public_execution.py:1499-1502` `execute_pm_equity` still gates only on `enabled()`.

## 3. Position coverage and funnel

NOT VERIFIED — no broker, ledger or DB access. No position table can be produced without inventing data.

## 4. Prior-finding status register

- **CCTT-MED-011** (local mode has no auth; Docker publishes API on all interfaces): Open → **Partially addressed (branch only)**.
  - Independently reviewed `29b1296`: `_auth_required()` now returns true when `ENABLE_SCHEDULER` (default true) or `ENABLE_TRADE_EXECUTION`/`ENABLE_OPTIONS_EXECUTION` is enabled; the write gate rejects every non-exempt request when no operator code is configured; in-memory setup sessions lose authority once auth is required; CORS falls back to no origins instead of `*`; compose binds `127.0.0.1:8001`. Flag names match those read elsewhere in `backend/`.
  - Re-ran `pytest backend/tests/test_claude_audit_local_auth.py`: 14 passed.
  - Deployment note: after promotion, any local/desktop runtime with the scheduler enabled and no operator access code returns 403 on all non-exempt routes (fail-closed by design). Operators must configure the access code and explicit `CORS_ORIGINS` before promoting.
  - Not on `main` or VPS heads; deployed auth behaviour unverified.
- **All others:** unchanged and carried forward. RESOLVED stays at 4 (HIGH-006, MED-009, MED-010, LOW-012).

Totals: 51 items (Critical 2, High 14, Medium 19, Low 16) · CONFIRMED 43, SUSPECTED 4, UNVERIFIED 0, RESOLVED 4. Full per-item evidence is kept in the Notion task "Case Capital Trading Terminal — Upgrade Backlog".

## 5. Tests (fresh Python 3.11.17 venv from `backend/requirements.txt`; execution, scheduler and Postgres disabled; all mocked)

| Target | Command | Result |
|---|---|---|
| `main` @ 250f5eb | `pytest backend/tests tests -q -rs` | exit 0; 532 passed, 27 skipped (14 archive, 12 external API, 1 isolated PG), 153 deselected, 126 subtests |
| `main` | `flake8 --select=E9,F63,F7,F82 backend` | clean |
| `Claude-Gpt-audit` @ 29b1296 | `pytest backend/tests tests -q` | exit 0; 953 passed, 14 skipped, 153 deselected, 126 subtests |
| `Claude-Gpt-audit` @ 29b1296 | `pytest backend/tests/test_claude_audit_local_auth.py -q` | 14 passed |

Not exercised: live broker lifecycle, PostgreSQL concurrency (advisory locks, leadership), real Telegram, deployed systemd/nginx/TLS, VPS backup/restore, Docker networking.

## 6. Remediation order (unchanged)

1. Promote and deploy the exit-lockout fixes (CRIT-001, CRIT-002, HIGH-014, HIGH-001), then verify every held position's protection.
2. Public kill switch and loss breaker (HIGH-004, HIGH-005).
3. Release parity and green-CI deploy gate (HIGH-012, MED-017), plus backup pruning (HIGH-013).
4. TLS (HIGH-011).
5. Remaining items (MED-011 promotion needs the operator-code precondition above).

## 7. Unknowns / operator actions

- Before Mon 10-26 09:30 ET (read-only): confirm deployed SHA; list OPEN `tf_trades` rows with `emergency_exit_order_id` or `SUBMIT_UNKNOWN` and compare to broker status; confirm protection for every held position; run `df` and measure backup growth (HIGH-013).
- Deployed SHA, positions, orders, buying power, quote ages, scheduler run history, disk and backups: all UNVERIFIED.
- Promoting `Claude-Gpt-audit` work to `main` and the VPS requires operator review. This audit made no production changes.
