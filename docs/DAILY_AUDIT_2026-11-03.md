# Case Capital Trading Terminal — Daily Audit 2026-11-03

- **Audit window:** 2026-11-03 01:51–02:25 EST (America/New_York, UTC−5), Tuesday. Daily routine, run 27.
- **Market session:** CLOSED (overnight / pre-market). Today's regular session is 09:30–16:00 EST. Election Day is not an NYSE holiday. Next holiday: Thanksgiving 11-26, with an early close on 11-27.
- **Repository:**
  - `main` @ `2c768c3`, unchanged since 10-28.
  - VPS branch `codex/desktop-checkpoint` @ `06703a5`, unchanged.
  - `Claude-Gpt-audit` moved `97d7055` → **`e178c9a`** ("bind automatic entries to frozen candidate authority", 11-02 13:04Z). Not promoted.
- **Deployed commit:** UNVERIFIED (`/api/status` exposes no SHA).
- **Access:** REPOSITORY + LIMITED LIVE. Read-only unauthenticated GETs over HTTP, plus the GitHub Actions API. No SSH, broker, DB or operator session. No order, preflight or mutation endpoint was touched.

## 1. Verdict

**CRITICAL (live).** The terminal's public status reports PostgreSQL unavailable. The exit-lockout defects remain CRITICAL in code on main and the VPS branch.

| Area | Readiness |
|---|---|
| Infrastructure | **DEGRADED live**: API up, `db_available=false`, HTTP-only |
| Data | NOT VERIFIED; last scan state unreadable |
| Entries | NOT VERIFIED (HIGH-004 open) |
| Exits | CRITICAL: code defects CRIT-001/002 and HIGH-014, plus live DB unavailability (CRIT-003) |
| Learning / reporting | NOT VERIFIED |

## 2. New finding

### CCTT-CRIT-003 — Live terminal reports PostgreSQL unavailable before the open (NEW 2026-11-03)

- **Severity / status:** Critical · CONFIRMED (live symptom) · root cause UNVERIFIED · Open.
- **Evidence:** `GET http://<terminal-host>/api/status` returned 200 (0.06 s) four times at 07:01:29Z, 07:01:46Z, 07:02:21Z and 07:02:56Z. Each response had `bot.db_available=false`, `last_scan_at=null` and all stats at 0.
  - On 2026-11-02 at 06:58Z the same endpoint returned `db_available=true` and `last_scan_at=2026-11-02T05:00:36Z`.
  - `/api/scheduler/overview`, `/api/position_monitor/latest`, `/api/health` and `/api/desktop/diagnostics` return 403 without a session, so the reason cannot be read publicly.
- **Source:** `backend/server.py:299-317`. `db_available` turns false when any of five reads raises: `bot_state`, `claude_cache`, `scan_results`, `watchlist` or `alerts`. The exception is logged as a warning ("status degraded; PostgreSQL unavailable") and the endpoint still returns HTTP 200.
  - Possible causes, none verified: PostgreSQL down, a connection-pool or credential failure, a missing or renamed table after a deploy, or a statement timeout.
- **Consequence:** if the backend cannot read PostgreSQL, the trade ledger, position-monitor state, pending-exit records, stop/ratchet persistence and cycle persistence all depend on an unavailable store.
  - Terminal-side exit coverage cannot be assumed for today's 09:30 EST session.
  - Whether the 00:00 EST `midnight_scan` ran is unknown.
- **Immediate operator action (read-only first):**
  - Check the PostgreSQL service and backend logs on the VPS.
  - Confirm that broker-side protection exists for every held Public position and every Alpaca options position before the open.
  - Do not loosen any gate to compensate.
- **Proposed repair:**
  - Return the failing component in a sanitized `db_reason` field.
  - Make the authenticated health and position-monitor surfaces degrade to explicit `protection_unverified` status.
  - Send a deduplicated Telegram critical alert when the DB is unavailable during or before a session.
- **Acceptance:** `/api/status` shows `db_available=true` and a fresh `last_scan_at` across two samples at least 60 s apart. The authenticated position monitor shows every broker position priced and monitored.
- **Regression test:** mock each of the five reads raising; assert `db_available=false`, a sanitized reason, and exactly one alert per outage.

## 3. Prior-finding register (51 → 52 items)

- **New:** CCTT-CRIT-003.
- **Progressed (branch only): CCTT-HIGH-010** on `Claude-Gpt-audit` @ `e178c9a`.
  - The automatic options entry batch is frozen at function entry, and 15 authority fields are compared on reload. A mismatch or missing snapshot fails closed with `candidate_batch_identity_changed_or_missing`.
  - Independently verified: `test_claude_audit_options_authority.py` gives **21 passed**, and the full branch suite gives **1,108 passed, 14 skipped, 153 deselected**. CI run 41449889455 succeeded.
  - Still open: manual contract/risk binding and immutable PM decision artifacts.
  - Status: Open on main/VPS; Partially addressed on branch.
- **Re-checked unchanged on main 2c768c3:**
  - CRIT-001: `public_execution.py:2232-2235` skips any row with a phase or emergency order ID.
  - CRIT-002: `execution_safety.py:68`, 24 h claim TTL.
- **Live re-confirmed:** HIGH-011 (HTTPS fails with status 000).
- **Totals:** 52 items (Critical 3, High 14, Medium 19, Low 16) · CONFIRMED 40, SUSPECTED 4, UNVERIFIED 0, RESOLVED 8.

## 4. Position coverage and funnel

NOT VERIFIED. No broker or operator session was available. Given CRIT-003, the operator must verify protection directly at each broker before 09:30 EST.

## 5. Tests

| Target | Command | Env | Result |
|---|---|---|---|
| main @ 2c768c3 | `pytest backend/tests tests -q -p no:cacheprovider` | fresh Python 3.12 venv under `env -i`; execution and scheduler disabled; no credentials | exit 0, **700 passed, 13 skipped, 153 deselected**, 126 subtests, 25 warnings |
| main @ 2c768c3 | `flake8 backend --select=E9,F63,F7,F82` | same | 0 findings |
| Claude-Gpt-audit @ e178c9a | full suite | same | **1,108 passed, 14 skipped, 153 deselected** |
| Claude-Gpt-audit @ e178c9a | authority suite | same | 21 passed |

- Deselected by markers: `live_destructive`, `external_integration` and `postgres_integration`.
- Not run: frontend build, Docker, real-PostgreSQL contracts, any broker lifecycle.
- All results come from mocked tests, not live broker checks.

## 6. Remediation order

1. **CRIT-003, operational and urgent:** restore DB availability, then verify that every position has protection.
2. Promote and deploy the exit-lockout fixes: CRIT-001, CRIT-002, HIGH-014, then HIGH-003.
3. Add a Public entry kill switch and loss-breaker coverage (HIGH-004/005).
4. Add a deployed-SHA endpoint and a release-parity check (HIGH-012).
5. TLS (HIGH-011) and backup pruning (HIGH-013).

## 7. Unknowns

- Root cause of `db_available=false`.
- Broker positions, orders and quotes.
- Scheduler run history, including whether the midnight scan ran.
- Telegram/frontend parity.
- Backups and restore tests.

All are NOT VERIFIED.
