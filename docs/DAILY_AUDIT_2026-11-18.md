# Case Capital Trading Terminal — Daily Audit 2026-11-18

Run 40. Read-only. No order, preflight, cancel or mutation endpoint was called. This file contains no secrets, account identifiers or DSNs.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-18 01:51–02:20 EST (America/New_York, UTC−5), Wednesday |
| Market session | CLOSED (overnight). Regular session today runs 09:30–16:00 EST. Tuesday 11-17 was a full session that this audit did not observe. Thanksgiving 11-26 is closed; 11-27 closes early at 13:00 |
| Previous audit | 2026-11-17 run 39, `docs/DAILY_AUDIT_2026-11-17.md` (branch `claude/stoic-knuth-wv5brz`, commit `df5508e`) |
| `main` | `5369675`, unchanged (`git ls-remote`). CI run 113 succeeded |
| VPS `codex/desktop-checkpoint` | `bde0cd5`, unchanged. CI run 114 succeeded |
| `Claude-Gpt-audit` | `24173c3`, unchanged. CI run 115 succeeded. No newer CI runs |
| Deployed commit | SHA UNVERIFIED. The `/api/status` schema is unchanged |
| Access | REPOSITORY + LIMITED LIVE (unauthenticated GETs and the GitHub Actions API). `/api/health` returns 403. No broker, SSH, database or log access. HTTPS: no response (curl code 000) |

## Verdict

**CRITICAL (code) / DEGRADED (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED. `db_available=true`, `db_reason=null` at 06:52:36Z and 06:54:09Z (93 s apart). HTTP only. First `/api/status` took 6.64 s, second 0.80 s |
| Data | PARTIAL. `last_scan_at=2026-11-18T05:00:19Z` was the Wednesday 00:00 EST `midnight_scan`: 3 results, 3 pre-filter, 0 Claude calls. Whether that cycle and Tuesday's daytime cycles were persisted, and their funnels, is NOT VERIFIED |
| Entries | NOT VERIFIED |
| Exits | CRITICAL. CRIT-001/002 and HIGH-014/015/016 are still present on main 5369675 and VPS bde0cd5. Lines re-read today: `public_execution.py:1942-1945` (OPEN rows with `emergency_exit_order_id` queried), `:2310` (rows with `emergency_exit_order_id` skipped), `:2352` (stable client id keyed on stop) and `execution_safety.py:68` (24 h claim TTL). `protection_status=NOT_ASSESSED`. Tuesday's session was another exposure; today's open is next |
| Learning / reporting | NOT VERIFIED |

## Position coverage and funnel

NOT VERIFIED (no authenticated or broker access). Public status shows the last scan had 3 results, 3 pre-filter and 0 Claude calls; `watchlist_count=0`; `active_alerts=0`; `telegram_configured=true`; `claude_configured=false`.

## New findings

None verified. No branch has changed since 11-12.

## Prior findings

All unchanged. CRIT-003: database stayed up across 93 s (Partially addressed). HIGH-011: re-confirmed (HTTP only; no HTTPS response). Codex heartbeat on 11-17 (Notion) reports no new commits on `Claude-Gpt-audit`; independently confirmed via `git ls-remote`. Register: 56 items (Critical 3, High 16, Medium 20, Low 17); 43 CONFIRMED / 4 SUSPECTED / 0 UNVERIFIED / 9 RESOLVED.

## Tests (fresh Python 3.12 venv, `env -i` with proxy only, `POSTGRES_ENABLED=false`, `RUN_EXTERNAL_API_TESTS=false`, no credentials)

| Command | Result |
|---|---|
| `pip install -r backend/requirements.txt flake8` | exit 0 |
| `flake8 --select=E9,F63,F7,F82 .` (backend) | exit 0 |
| `pytest backend/tests tests -q -rs` | exit 0. 815 passed, 13 skipped, 153 deselected, 126 subtests, 25 warnings (10.52 s) |

Skips: 12 external-API tests, and 1 PostgreSQL contract test that requires an isolated DSN. Deselected (153) by the repository's `pytest.ini` markers.

Not exercised: real broker lifecycle, live quotes, PostgreSQL concurrency, Telegram delivery, restore.

## Remediation order (unchanged)

1. Reconcile and promote the exit-protection fixes with a bid ≥ limit invariant (CRIT-001, CRIT-002, HIGH-014, HIGH-015, HIGH-016), and have the operator verify broker-side protection.
2. Fix the backup lifecycle and alerting (HIGH-013, MED-020, MED-016).
3. Establish release parity (HIGH-012) and TLS (HIGH-011).

## Operator actions before 09:30 EST today

- Confirm broker-side protection for every Public and Alpaca options position.
- Look for OPEN rows with a non-null `emergency_exit_order_id` whose broker order is no longer working (Monday's and Tuesday's sessions could have created them).
- Confirm Tuesday's full scans and the Wednesday 00:00 midnight scan persisted their cycles.

## Not verified

Positions, orders, buying power, ledger, protection, quotes, the scheduler registry and run history, the funnel, outcomes, the deployed SHA, backups and restore, logs.
