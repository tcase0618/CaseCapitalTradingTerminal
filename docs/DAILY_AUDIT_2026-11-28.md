# Trading Terminal Daily Audit — 2026-11-28 (run 50)

- Audit time: 2026-11-28 01:50–02:10 EST (America/New_York, UTC−5), Saturday.
- Market session: **CLOSED (weekend)**. Next regular session Mon 2026-12-01 09:30 EST.
- Repository: `main` @ `c17babc` (unchanged since 2026-11-18; CI run #127 success, no newer runs). VPS branch `codex/desktop-checkpoint` @ `d28c80c` unchanged; `Claude-Gpt-audit` @ `24173c3` unchanged (`git ls-remote`). Audit branch: `claude/stoic-knuth-svncx4`.
- Deployed commit: SHA UNVERIFIED.
- Scope: **REPOSITORY + LIMITED LIVE** (unauthenticated `GET /api/status` only). `/api/health` 403. HTTPS: TLS connect failed (curl exit 35, code 000). No SSH, broker, database or operator session. No order, preflight or mutation endpoint touched.

## 1. Verdict

**CRITICAL (code) / DEGRADED (live).**
Infrastructure DEGRADED · Data PARTIAL · Entries NOT VERIFIED (MED-021) · Exits CRITICAL (`backend/services/public_execution.py:2310`, `:2352`; `backend/services/execution_safety.py:68` re-read unchanged) · Learning NOT VERIFIED · Reporting NOT VERIFIED.

## 2. Live observations (sanitized)

| UTC | HTTP | latency | db_available | protection_status | last_scan_at | tickers_in_last_scan |
|---|---|---|---|---|---|---|
| 06:52:07Z | 200 | 1.23 s | true | NOT_ASSESSED | 2026-11-27T23:31:55Z | 31 |
| 06:52:43Z | 200 | 1.19 s | true | NOT_ASSESSED | same | 31 |
| 06:53:19Z | 200 | 1.13 s | true | NOT_ASSESSED | same | 31 |

Span 72 s. `db_reason=null`, `online=true`, 0 Claude calls in last scan. `last_scan_at` = Friday 18:31 ET evening scan, i.e. scans continued after Friday's 13:00 early close. No Saturday midnight scan is expected: `_stock_scan_market_day_now` returns false for weekday ≥ 5 (`backend/services/scheduler.py:114-115`). The skip itself is not persisted (LOW-009).

## 3. Position coverage and funnel

NOT VERIFIED — no broker or ledger access. `protection_status=NOT_ASSESSED` is not evidence of protection.

## 4. Findings

New: none. Status changes: none. Evidence updated:
- MED-013: Friday 11-27 early-close window (13:00–16:00 ET) has passed. `_public_session_now` (`public_execution.py:1137-1142`) still returns CORE for any weekday 09:30–16:00 ET. What the terminal sent to Public in that window remains UNVERIFIED (no broker/log access). Next exposure: 2026-12-24 early close.
- CRIT-001, CRIT-002: code unchanged; positions are held over the weekend without verified broker-side protection.
- CRIT-003: DB up across 72 s; Friday evening scan completed.

Totals: 58 items (Critical 3, High 16, Medium 21, Low 18). CONFIRMED 45, SUSPECTED 4, UNVERIFIED 0, RESOLVED 9.

## 5. Tests (fresh Python 3.12 venv, `env -i`, no credentials, mocked)

| Command | Result |
|---|---|
| `pytest backend/tests tests -q -rs` | exit 0; 948 passed, 13 skipped, 153 deselected, 0 failed; 126 subtests; 25 warnings |
| `flake8 --select=E9,F63,F7,F82 backend tests` | exit 0 |

Skips: external API target disabled; postgres_integration/live_destructive/external_integration deselected by `pytest.ini`. No broker lifecycle observed.

## 6. Remediation order (unchanged)

1. Promote exit-lockout fix (CRIT-001), retry-at-same-stop fix (CRIT-002) and bid ≥ limit invariant (HIGH-015/016) with reviewed tests; deploy with SHA parity check (HIGH-012).
2. Early-close/holiday-aware session detection (MED-013), deploy-safe options pause (MED-021), status truthfulness (LOW-018).
3. Backup lifecycle alerting/pruning (HIGH-013, MED-020), TLS (HIGH-011).

## 7. Operator actions before Monday 09:30 EST

Confirm broker-side protection for every position; check OPEN rows with non-null `emergency_exit_order_id`; review Public order history for any equity orders sent 13:00–16:00 ET on 11-27 (MED-013); confirm `OPTIONS_RESEARCH_ONLY=true` / `ENABLE_OPTIONS_EXECUTION=false` on the VPS if the pause is intended; confirm deployed SHA.
