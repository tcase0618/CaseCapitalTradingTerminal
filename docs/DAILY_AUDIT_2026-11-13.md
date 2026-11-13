# Case Capital Trading Terminal — Daily Audit 2026-11-13

Run 35. Read-only. No order, preflight, cancel or mutation endpoint was called. No secrets, account identifiers or DSNs are reproduced.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-13 01:51–02:20 EST (America/New_York, UTC−5), Friday |
| Market session | CLOSED (overnight). Regular session today 09:30–16:00 EST; not an NYSE holiday. Next holiday Thanksgiving 11-26; early close 11-27 |
| Previous audit | 2026-11-10 (run 34). **No audit ran on 11-11 or 11-12**; there is no artifact, and Notion carries only Codex remediation notes for those days |
| `main` | `5369675` (1 new commit since `8a52299`: "Stabilize portfolio incidents and critical ledger probes; reuse timestamped exit observations"). CI run 113 success |
| VPS branch `codex/desktop-checkpoint` | `bde0cd5` (cherry-pick of 5369675). `main...VPS` = 18/403. CI run 114 success |
| `Claude-Gpt-audit` | `24173c3` (+3 since the last audit: ae0dc04 HIGH-015 bid-floor rounding, ea092ce HIGH-007 unevaluable holdings, 24173c3 MED-020 backup failure hook). Branch only; `main...Claude-Gpt-audit` = 31/48. CI run 115 success |
| Deployed commit | SHA UNVERIFIED. `/api/status` schema unchanged |
| Access | REPOSITORY + LIMITED LIVE: unauthenticated GETs and the GitHub Actions API. Authenticated surfaces (`/api/health`, `/api/system/health`, `/api/scheduler/status`, `/api/positions`, `/api/position_monitor/latest`) return 403. No broker, SSH, DB or logs. HTTPS: no response |

## Verdict

**CRITICAL (code) / DEGRADED (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED. `db_available=true` at 06:54:13Z, 06:55:25Z and 06:58:10Z (237 s); HTTP only; root cause of the 11-03 outage still unrecorded |
| Data | PARTIAL. `last_scan_at=2026-11-13T05:00:17Z` (00:00 ET scan; 5 results, 5 pre-filter). Whether the 11-11/11-12 session scans persisted is not observable |
| Entries | NOT VERIFIED |
| Exits | CRITICAL. CRIT-001/002, HIGH-014 and HIGH-015 are still on main and VPS. New HIGH-016 adds a second above-bid exit-pricing path on main/VPS. `protection_status=NOT_ASSESSED` |
| Learning / reporting | NOT VERIFIED |

## Position coverage and funnel

Broker positions, orders, buying power and ledger: **NOT VERIFIED** (no authenticated or broker access). The public status exposes only the last scan summary (results 5, pre-filter passed 5, Claude calls 0). No coverage table or funnel can be honestly filled in.

## New findings

### CCTT-HIGH-016 — Cached ratchet observations price protective sells above the bid and can replace a live bid quote (main/VPS)

- Severity High · CONFIRMED (source + offline reproduction) · Open
- Component: `backend/services/public_execution.py` `_cached_protective_exit_quote` (:626-639) and `process_protective_exits` (:2318-2323); `backend/services/pm_ratchet.py:217` (`pm_last_ratchet_reference`). Introduced by main 5369675 / VPS bde0cd5 on 2026-11-11.
- Evidence: the cached path rebuilds a limit from the stored ratchet observation with `math.ceil(price * scale)` and sets `bid=None, ask=None`. Offline on main 5369675 (no network, mocked trade row):
  - bid observation 9.003 (5 s old) → `limit_price` **9.01**, above the bid;
  - last-trade observation 9.80 → limit 9.80, with no bid check.
  
  At :2321-2323 the cached observation **replaces** a resolved live bid-based quote whenever the cached one is newer. A stream observation priced from the last trade can therefore displace a fresh bid. The cached price is also used as the breach reference (`current`).
- Consequence: protective DAY limits can rest above the bid, so they are more likely not to fill. On main/VPS an unfilled emergency order then hits the CRIT-001 permanent lockout. The `Claude-Gpt-audit` fix ae0dc04 (ROUND_FLOOR at or below bid) does **not** contain this function. Promoting that branch would not remove this path unless it is reconciled.
- Repair: never price a protective sell from a cached observation. Use it only for breach detection or alerting, or floor it to a bid that is still fresh. Never let a last-trade observation replace a fresh bid. Land the CRIT-001 retire/retry first.
- Effort S. Dependencies: CRIT-001, HIGH-015, HIGH-012 (branch reconciliation).
- Acceptance: for any cached or live input, the emergency SELL limit is ≤ the freshest bid. When the bid is stale and only a cached or last-trade observation exists, no order is sent and a critical alert fires.
- Test: unit tests on `_cached_protective_exit_quote` (bid 9.003 → limit ≤ 9.00), plus a fake-client `process_protective_exits` run in which a newer cached last-trade observation coexists with a fresh live bid. The submitted limit must be ≤ that bid.

## Prior findings

- **CRIT-001 still Open** on main 5369675. The emergency-order poll (:1941-1997) records `EXPIRED/CANCELLED` but never clears `emergency_exit_order_id`, and :2310 still skips every row with that id.
- **CRIT-002 still Open**: `execution_safety.py:68` 24 h intent TTL; `stable_client_order_id` is pinned to the trigger stop (:2350).
- **HIGH-014 still Open**: :2352-2353 silently `continue`s on a refused claim.
- **HIGH-015 still Open on main/VPS** (last-trade fallback :606-617; wide-spread rejection still applies to emergency sells on main). Branch-only fix ae0dc04 is CI-green (run 111) but is not promoted.
- **HIGH-007 Partially addressed**: main 5369675 adds reference persistence only. Branch fix ea092ce (unevaluable holdings → DEGRADED) is not on main.
- **MED-020 Partially addressed**: branch 24173c3 adds a systemd `OnFailure` notifier (CI run 115 green). It is not on main/VPS, not deployed and has no live delivery proof.
- **CRIT-003** stays Partially addressed. DB was up across 237 s, and the midnight scan persisted. Root cause and chronology are still missing.
- **HIGH-011** re-confirmed (no HTTPS response). **HIGH-012**: main...VPS 18/403; main...Claude-Gpt-audit 31/48.
- All other items unchanged.

## Tests (fresh Python 3.12 venv under `env -i`; `POSTGRES_ENABLED=false`, external/live tests disabled; no credentials)

| Command | Result |
|---|---|
| `pip install -r backend/requirements.txt` | exit 0 |
| `python -m flake8 --select=E9,F63,F7,F82 .` (backend) | exit 0 |
| `python -m pytest backend/tests tests -q -rs` (repo root, CI-equivalent) | exit 0 — **815 passed, 13 skipped, 153 deselected**, 126 subtests, 25 warnings |
| CI run 113 (main 5369675), 114 (VPS bde0cd5), 115 (Claude-Gpt-audit 24173c3) | success |

Skips: 12 external-API cases disabled, 1 PostgreSQL contract without an isolated DSN. `test_monitor_reliability.py` covers the cached path but asserts only `limit_price == 106` for a last-trade observation. It has no bid ≤ limit assertion. Not exercised: real broker lifecycle, live quotes, PostgreSQL concurrency, Telegram delivery, restore.

## Remediation order

1. Reconcile `Claude-Gpt-audit` (CRIT-001/002, HIGH-014, HIGH-015 bid floor) with the new main cached-quote path (HIGH-016), then promote together. Verify with an expired-emergency-order test and a bid ≤ limit invariant across live and cached inputs.
2. Operator: before 09:30 EST, confirm broker-side protection for every Public and Alpaca options position. Check for OPEN rows with a non-null `emergency_exit_order_id` whose broker order is no longer working.
3. Backup lifecycle (HIGH-013, MED-020 promotion), TLS (HIGH-011), release parity (HIGH-012).

## Unknowns

Positions, orders, buying power, protection, scheduler history, session-hours scans, backups, logs, deployed SHA. Why the 11-11 and 11-12 audit runs did not complete. Never conclude "everything works" from HTTP 200.
