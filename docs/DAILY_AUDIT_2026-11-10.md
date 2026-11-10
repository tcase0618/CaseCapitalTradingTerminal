# Case Capital Trading Terminal — Daily Audit 2026-11-10

Run 34. Read-only. No order, preflight, cancel or mutation endpoint was called. No secrets, account identifiers or DSNs are reproduced.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-10 01:52–02:15 EST (America/New_York, UTC−5), Tuesday |
| Market session | CLOSED (overnight). Regular session today 09:30–16:00 EST; not an NYSE holiday (11-11 Veterans Day: NYSE open). Next holiday Thanksgiving 11-26; early close 11-27 |
| `main` | `8a52299` (new: 6 commits since `96edd6e` — 979344f backup freshness, 61a6450 CI root tests, 8e8eb66 Public ratchet/protective limit exits, 537f1f7 unsuccessful-quote rejection, 67b325b stop repolling of historical entries, 8a52299 uncovered positions → degraded protection) |
| VPS branch `codex/desktop-checkpoint` | `4558b94` (cherry-picks of the same 6). `main...VPS` = 17/401, separate lineages |
| Deployed commit | SHA UNVERIFIED. `/api/status` schema unchanged |
| Access | REPOSITORY + LIMITED LIVE: unauthenticated GETs, GitHub Actions API. Authenticated surfaces (`/api/health`, `/api/system/health`, `/api/scheduler/status`, `/api/positions`, `/api/position_monitor/latest`) return 403. No broker, SSH, DB or logs. HTTPS: no listener |

## Verdict

**CRITICAL (code) / DEGRADED (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED — `db_available=true` at 06:56:08Z, 06:56:22Z, 07:05:25Z (557 s); HTTP only; outage root cause unknown |
| Data | PARTIAL — `last_scan_at=2026-11-10T05:00:19Z` (00:00 ET scan; 6 results, 6 pre-filter). Whether 11-09 session scans persisted is not observable |
| Entries | NOT VERIFIED |
| Exits | CRITICAL — CRIT-001/002, HIGH-014 still on main 8a52299 and VPS; new HIGH-015 increases exposure to CRIT-001; `protection_status=NOT_ASSESSED` |
| Learning / reporting | NOT VERIFIED |

## Position coverage and funnel

Broker positions, orders, buying power and ledger: **NOT VERIFIED** (no authenticated or broker access). Public status exposes only the last scan summary (results 6, pre-filter passed 6, Claude calls 0). No coverage table or funnel can be honestly populated.

## New findings

### CCTT-HIGH-015 — Protective exits price the sell limit from the last trade, discarding a fresh bid; an unfilled limit then hits the CRIT-001 lockout

- Severity High · CONFIRMED (source + offline reproduction) · Open
- Component: `backend/services/public_execution.py` `_protective_exit_quote` (:590-616), `process_protective_exits` (:2290, :2296-2350); `pm_ratchet._public_ratchet_mark`; `public_price_stream`
- Evidence: `_protective_exit_quote` uses the bid only when `_execution_quote` succeeds. That function rejects any spread above `PUBLIC_MAX_EQUITY_SPREAD_BPS` (:571-572), so a fresh but wide quote falls through to `PUBLIC_FRESH_LAST_TRADE_LIMIT`, limit = ceil(last). Offline (main 8a52299, no network): fresh bid 9.00 / ask 10.00 / last 9.80 → limit **9.80** with `bid=None`; stale bid/ask + fresh last 9.40 → limit 9.40. The order is a DAY limit (:2336-2343). If it does not fill, `:2290` skips every row with `emergency_exit_order_id` forever (CRIT-001).
- Consequence: in a gap-down with a wide spread (the HIGH-001 case), the exit rests above the bid and may never fill. Protection then ends permanently for that row. The fallback also conflicts with the stated policy that a fresh last trade must not authorize an exit limit when the bid/ask is stale.
- Repair: when a fresh bid exists, price a protective sell at or below the bid (with an exit-specific spread ceiling) instead of the last trade. Use last trade only to detect a breach and alert, not to price the order. Land CRIT-001 retire/retry before this path ships.
- Effort S. Dependencies: CRIT-001, CRIT-002, HIGH-014.
- Acceptance: fresh bid below stop with 1,000 bps spread → limit ≤ bid; stale bid/ask → no order and a critical alert; an unfilled emergency order is retired and retried.
- Test: unit tests on `_protective_exit_quote` for the two cases above, plus a fake-client `process_protective_exits` run where the first emergency order expires.

## Prior findings

- **LOW-017 → RESOLVED.** main 61a6450 `ci.yml:36-38` runs `python -m pytest backend/tests tests` from the repository root; CI run 109 (main 8a52299) success; the same command locally: 807 passed, 13 skipped, 153 deselected (it includes the root `tests/`).
- **HIGH-007 → Partially addressed.** main 8a52299: the scheduler adds a failure when `quote_coverage_status=unavailable` (`scheduler.py`); `/api/position_monitor/latest` returns `ok:false, status:MISSING` when no snapshot exists (`server.py:398-401`); fractional residuals are now reported as unprotected. Remaining: `pm_ratchet` still returns `ok:True` with `partial` coverage; alert delivery is not live-verified.
- **HIGH-001 → Partially addressed (behaviour changed).** A wide-spread breach can now be detected via the last trade. It is priced unsafely, though (HIGH-015).
- **MED-020** stays Partially addressed. 979344f is promoted to main/VPS (completion metadata via `backup_health.py`). There is still no failure alert, and VPS `backup-postgres.sh` still has no pruning (HIGH-013).
- **CRIT-001 / CRIT-002 / HIGH-014: still Open** on main 8a52299 (`public_execution.py:2290` skip; `execution_safety.py:68` 24 h TTL; `:2328-2329` silent `continue` on a refused claim). New: the exit client id is now pinned to `emergency_exit_trigger_stop`, so a failed first submission is still blocked for 24 h.
- **CRIT-003** stays Partially addressed. DB was up across 557 s and the midnight scan persisted. Root cause, chronology and authenticated coverage are still missing.
- **HIGH-011** re-confirmed (no HTTPS listener). **HIGH-012** main...VPS 17/401.

## Tests (fresh Python 3.12 venv under `env -i`; `POSTGRES_ENABLED=false`, external/live tests disabled; no credentials)

| Command | Result |
|---|---|
| `python -m flake8 --select=E9,F63,F7,F82 .` (backend) | exit 0 |
| `python -m pytest tests/test_sealed_archive.py -q -rs` | 14 passed |
| `python -m pytest backend/tests tests -q -rs` (repo root, CI-equivalent) | exit 0 — **807 passed, 13 skipped, 153 deselected**, 126 subtests, 25 warnings |
| CI run 109 (main 8a52299), 110 (VPS 4558b94) | success |

Skips: 12 external-API cases disabled, 1 PostgreSQL contract without an isolated DSN. Not exercised: real broker lifecycle, live quotes, PostgreSQL concurrency, Telegram delivery, restore.

## Remediation order

1. Promote CRIT-001/002 + HIGH-014 fixes from `Claude-Gpt-audit` together with a HIGH-015 bid-anchored exit price; verify with an expired-emergency-order test.
2. Operator: confirm broker-side protection for every Public and Alpaca options position before 09:30 EST.
3. Backup lifecycle: pruning + failure alert on VPS (HIGH-013, MED-020); TLS (HIGH-011); release parity (HIGH-012).

## Unknowns

Positions, orders, buying power, protection, scheduler history, session-hours scans, backups, logs, deployed SHA. Never conclude "everything works" from HTTP 200.
