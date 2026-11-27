# Trading Terminal Daily Audit — 2026-11-27 (run 49)

- Audit time: 2026-11-27 01:51–02:15 EST (America/New_York, UTC−5), Friday.
- Market session: **CLOSED (overnight)**. Today's regular session 09:30 EST with **NYSE early close 13:00 EST** (day after Thanksgiving).
- Repository: `main` @ `c17babc` (unchanged since 2026-11-18; CI run #127 success, no newer runs). VPS branch `codex/desktop-checkpoint` @ `d28c80c` unchanged; `Claude-Gpt-audit` @ `24173c3` unchanged (`git ls-remote`). Audit branch: `claude/stoic-knuth-jqonp9`.
- Deployed commit: SHA UNVERIFIED.
- Scope: **REPOSITORY + LIMITED LIVE** (unauthenticated `GET /api/status` only). `/api/health` 403. HTTPS: TLS connect failed (curl exit 35, code 000). No SSH, broker, database or operator session. No order, preflight or mutation endpoint touched.

## 1. Verdict

**CRITICAL (code) / DEGRADED (live).**
Infrastructure DEGRADED · Data PARTIAL · Entries NOT VERIFIED (MED-021) · Exits CRITICAL (`backend/services/public_execution.py:2310`, `:2352`; `backend/services/execution_safety.py:68` re-read unchanged) · Learning NOT VERIFIED · Reporting NOT VERIFIED.

## 2. Live observations (sanitized)

| UTC | HTTP | latency | db_available | protection_status | last_scan_at | results |
|---|---|---|---|---|---|---|
| 06:52:38Z | 200 | 1.69 s | true | NOT_ASSESSED | 2026-11-27T05:01:16Z | 29 |
| 06:53:13Z | 200 | 1.13 s | true | NOT_ASSESSED | same | 29 |
| 06:53:47Z | 200 | 2.38 s | true | NOT_ASSESSED | same | 29 |

Span 69 s. `last_scan_at` = Friday `midnight_scan` (00:00 ET, `backend/services/scheduler.py:64`): 29 results, 29 pre-filter passed, 0 Claude calls. Previous observed scan was Wednesday 11-25 `evening_scan` (23:31Z); no scan is visible between them, consistent with the Thanksgiving hold in `_stock_scan_market_day_now` (`scheduler.py:112-142`). The skip itself is not persisted (LOW-009), so the Thanksgiving skip remains INFERRED.

## 3. Position coverage and funnel

NOT VERIFIED — no broker or ledger access. `protection_status=NOT_ASSESSED` is not evidence of protection.

## 4. Findings

New: none. Status changes: none. Evidence updated:
- MED-013: **today is the concrete exposure window.** `public_execution.py:1135-1141` `_public_session_now` returns CORE for any weekday 09:30–16:00 ET; it does not model today's 13:00 early close, so 13:00–16:00 ET today is labelled CORE. Runtime behavior (what order shape/session is sent, and how Public responds) UNVERIFIED.
- CRIT-001, CRIT-002: code unchanged going into today's shortened session.
- CRIT-003: DB up across 69 s; Friday midnight scan completed.

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

## 7. Operator actions before 09:30 EST today (early close 13:00)

Confirm broker-side protection for every position; check OPEN rows with non-null `emergency_exit_order_id`; watch terminal behavior 13:00–16:00 ET (MED-013) — any equity entry/exit attempt after 13:00 is outside the regular session; confirm `OPTIONS_RESEARCH_ONLY=true` / `ENABLE_OPTIONS_EXECUTION=false` on the VPS if the pause is intended; confirm deployed SHA.
