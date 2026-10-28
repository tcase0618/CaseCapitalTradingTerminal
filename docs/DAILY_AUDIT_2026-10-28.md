# Case Capital Trading Terminal — Daily Audit 2026-10-28

- Audit time: 2026-10-28 02:52–03:20 EDT (America/New_York, UTC−4), Wednesday. Daily run 21.
- Market session: CLOSED/PRE-MARKET at audit time. Regular session today 09:30–16:00 ET, with no holiday or early close. US DST ends Sun 11-01.
- Repository: `main` moved `250f5eb` → **`afdfd37`**. Five commits were promoted from `Claude-Gpt-audit` on 2026-10-27 (b67965c, d9fe245, 165b553, b03bf8e, afdfd37). CI run 40618664389 is green.
- VPS branch `codex/desktop-checkpoint` moved `e48177c` → **`efd9ac9`**, a merge of `main` @ afdfd37 at 2026-10-27 22:00 EDT. CI run 40618793210 is green.
  - `main...desktop-checkpoint` = 0/384.
  - `codex/vps-reviewed-release-20261021` is unchanged @ `f05b9bb`.
  - `Claude-Gpt-audit` @ `05ccb0b`: `main...Claude-Gpt-audit` = 13/35, `desktop-checkpoint...Claude-Gpt-audit` = 385/23.
- Deployed commit: **UNVERIFIED**. `/api/status` exposes no commit or version.
- Scope: **REPOSITORY + LIMITED LIVE (read-only, unauthenticated public GETs only)** against `http://129.121.101.96`. There was no SSH, broker, database or operator session. No order endpoint, preflight or mutation request was made.
- Tools: git, a Python 3.11 venv (execution, scheduler and Postgres disabled; no credentials), the GitHub Actions API (read-only), and curl.

## 1. Verdict

- **Overall:** NOT VERIFIED at runtime / code-level **CRITICAL** for exits on `main` and the VPS branch, unchanged for 19 days.
- **Infrastructure:** partially observed. API up, `db_available=true`, nginx 1.24 over HTTP only.
- **Data:** NOT VERIFIED. `last_scan_at` was 2026-10-28T04:00:23Z, `results_count=6`.
- **Entries:** NOT VERIFIED (HIGH-004 is still open on `main` and the VPS branch).
- **Exits:** CRITICAL in code.
- **Learning:** improved on `main` (MED-015).
- **Reporting:** partially addressed.

## 2. Live read-only observations (2026-10-28 06:59–07:02Z)

| Check | Result |
|---|---|
| `GET /api/status` (auth-exempt) | 200, `bot.online=true`, `db_available=true`, `telegram_configured=true`, `claude_configured=false`, `last_scan_at=2026-10-28T04:00:23Z`, `results_count=6`. No commit field |
| `GET /api/auth/config` | `cloud=true`, `operator_login_enabled=true`, `setup_enabled=false`. Cloud mode with an operator code configured, so the newly promoted MED-011 change cannot lock the operator out |
| `GET /api/scheduler/overview`, `/api/position_monitor/latest`, `/api/admin/trading_status` without a session | 403 "Operator session required" (fails closed) |
| `https://129.121.101.96/api/status` | no response (TLS not served) → HIGH-011 still live |
| `http://129.121.101.96:8001/api/status` | no response (the API port is not publicly exposed) |

The position coverage table and the scan/order funnel are **NOT VERIFIED**: there was no operator session, broker or database access. No held position can be confirmed as protected.

## 3. New findings

None verified. I reviewed the 5 promoted commits (server.py auth gate, accountant_research deadline/packet validation, strategy_evidence rotation/attempt persistence, sealed-archive import, CI and compose) and found no new defect.
- Precondition noted: any non-cloud runtime with the scheduler enabled and no access code now 403s on all non-exempt routes, by design.

## 4. Prior-finding status changes

| ID | Change | Evidence |
|---|---|---|
| CCTT-MED-011 | Partially addressed → **RESOLVED on main** (repository level; also on the VPS branch; deployment unverified) | `server.py:155-163` `_auth_required` includes scheduler/execution flags; `:183-187` gate rejects when no operator code is configured; `:2310-2311` CORS defaults to none; `docker-compose.yml` binds `127.0.0.1:8001`. `test_claude_audit_local_auth.py` passes. Live: cloud gate 403s unauthenticated reads; port 8001 is not public |
| CCTT-MED-015 | Open → **RESOLVED on main** (repository level; PG persistence and deployment unverified) | `strategy_evidence.py:226-235` least-recently-attempted / oldest-target order; `:259-267` persists attempts to `strategy_evidence_attempts`; `postgres_store.py:621` `(version, episode_id)` key. 8 cases in `test_claude_audit_evidence_budget.py` pass, including 300 symbols within 3 runs |
| CCTT-LOW-007 | Partially addressed → **RESOLVED on main** (repository level; deployed timing unverified) | `accountant_research.py:262-275` per-ticker `wait_for(RESEARCH_BUDGET_SECONDS)`; `:379-391` batch deadline that fails closed with `enriched=0`. `test_accountant_research.py` passes |
| CCTT-LOW-016 | Partially addressed → **RESOLVED on main** | CI run 40618664389, backend job 122587054446: dedicated sealed-archive step success. The full-suite skip list contains no `test_sealed_archive` entries (13 skips: 1 PG DSN, 12 external API). Locally, 14 passed without paramiko installed |
| CCTT-HIGH-011 | unchanged, **live re-confirmed** | HTTPS not served; nginx/1.24.0 answers on HTTP |
| CCTT-HIGH-012 | Open; evidence refreshed | The VPS branch now contains `main`. It still lacks every `Claude-Gpt-audit` exit fix, and `/api/status` has no commit field |
| CCTT-CRIT-001 | Open (unchanged) | `public_execution.py:2232` on afdfd37 and efd9ac9 still skips rows with `emergency_exit_order_id`. The fix exists only on `Claude-Gpt-audit` (`:2307`, `:2342`) |
| CCTT-CRIT-002 | Open (unchanged) | `execution_safety.py:68` TTL is 24 h on all heads |
| CCTT-HIGH-004 | Open (unchanged) | `public_execution.py:1499-1501` gates only on `enabled()` |

Totals: **51 items** (Critical 2, High 14, Medium 19, Low 16) · CONFIRMED 39, SUSPECTED 4, UNVERIFIED 0, RESOLVED 8. Current status: Open 32, Partially addressed 11, Resolved 8.

## 5. Tests (fresh Python 3.11 venv; `ENABLE_TRADE_EXECUTION=false ENABLE_OPTIONS_EXECUTION=false ENABLE_SCHEDULER=false POSTGRES_ENABLED=false`; no credentials; mocked only)

| Target | Command | Result |
|---|---|---|
| `main` @ afdfd37 | `pytest backend/tests tests -q -rs` | exit 0; **667 passed, 13 skipped, 153 deselected**, 126 subtests |
| `main` @ afdfd37 | `(cd backend && pytest tests/test_sealed_archive.py -rs)` without paramiko | 14 passed, 0 skipped |
| `main` @ afdfd37 | focused: evidence budget, local auth, accountant research, strategy evidence | 140 passed |
| `desktop-checkpoint` @ efd9ac9 | `pytest backend/tests tests -q` | exit 0; 866 passed, 14 skipped, 153 deselected |
| both | `flake8 --select=E9,F63,F7,F82 backend` | clean |

Not exercised: real broker lifecycle, PostgreSQL concurrency (2 contracts lack an isolated DSN), Docker (no daemon), and frontend build (not touched by the promoted commits; CI frontend job green).

## 6. Remediation order (unchanged in substance)

1. Promote and deploy the exit-lockout fixes (CRIT-001, CRIT-002, HIGH-014), then verify every OPEN `tf_trades` row with `emergency_exit_order_id` against broker status.
2. Public kill switch and loss breaker (HIGH-004, HIGH-005).
3. Release parity: expose the deployed SHA in `/api/status` (HIGH-012), and TLS (HIGH-011).
4. Backup pruning and off-host lifecycle (HIGH-013, MED-016).

## 7. Unknowns and required operator actions (before 09:30 ET)

- Confirm the deployed SHA on the VPS: is it `efd9ac9`?
- List OPEN `tf_trades` rows with `emergency_exit_order_id`, `public_phase_order_id` or `SUBMIT_UNKNOWN`, and reconcile them with Public order status.
- Confirm a broker protective order or fresh terminal monitoring for every held position.
- Measure disk and backup growth.
