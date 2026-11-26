# Trading Terminal Daily Audit — 2026-11-26 (run 48)

- Audit time: 2026-11-26 01:50–02:30 EST (America/New_York, UTC−5), Thursday.
- Market session: **CLOSED — Thanksgiving (NYSE holiday)**. Next session Fri 2026-11-27 09:30 EST, **early close 13:00 EST**.
- Repository: `main` @ `c17babc` (unchanged since 2026-11-18; CI run #127 success, no newer runs). VPS branch `codex/desktop-checkpoint` @ `d28c80c` unchanged; `Claude-Gpt-audit` @ `24173c3` unchanged (`git ls-remote`). Audit branch: `claude/stoic-knuth-bpinkq`.
- Deployed commit: SHA UNVERIFIED.
- Scope: **REPOSITORY + LIMITED LIVE** (unauthenticated `GET /api/status` only). `/api/health` 403. HTTPS: TLS connect failed (curl exit 35, code 000). No SSH, broker, database or operator session. No order, preflight or mutation endpoint touched.

## 1. Verdict

**CRITICAL (code) / DEGRADED (live).**
Infrastructure DEGRADED · Data PARTIAL · Entries NOT VERIFIED (MED-021) · Exits CRITICAL (`backend/services/public_execution.py:2310`, `:2352`; `backend/services/execution_safety.py:68` re-read unchanged) · Learning NOT VERIFIED · Reporting NOT VERIFIED.

## 2. Live observations (sanitized)

| UTC | HTTP | latency | db_available | protection_status | last_scan_at | results |
|---|---|---|---|---|---|---|
| 06:52:23Z | 200 | 1.24 s | true | NOT_ASSESSED | 2026-11-25T23:31:23Z | 27 |
| 06:52:59Z | 200 | 1.01 s | true | NOT_ASSESSED | same | 27 |
| 06:53:35Z | 200 | 8.94 s | true | NOT_ASSESSED | same | 27 |

Span 72 s. `last_scan_at` = Wednesday `evening_scan` (18:30 ET, `backend/services/scheduler.py:76`): 27 results, 27 pre-filter passed, 0 Claude calls. **No Thursday `midnight_scan`** — consistent with the holiday hold in `_stock_scan_market_day_now` (`scheduler.py:112-142`, Alpaca market calendar), i.e. an expected holiday skip, but the skip itself is not persisted (LOW-009) so it is INFERRED, not verified. One 8.94 s response (vs ~1 s) noted; single sample, no finding.

## 3. Position coverage and funnel

NOT VERIFIED — no broker or ledger access. `protection_status=NOT_ASSESSED` is not evidence of protection.

## 4. Findings

New: none. Status changes: none. Evidence updated: CRIT-001, CRIT-002 (code unchanged; Friday early-close session is next), CRIT-003 (DB up across 72 s; Wednesday evening scan completed), MED-013 (Friday 11-27 early close at 13:00: `public_execution.py:1137-1140` `_public_session_now` returns CORE for any weekday 09:30–16:00 and ignores early closes — so it also labels today (Thanksgiving) 09:30–16:00 as CORE, and Friday 13:00–16:00 after the early close; runtime behavior UNVERIFIED).
Totals: 58 items (Critical 3, High 16, Medium 21, Low 18). CONFIRMED 45, SUSPECTED 4, UNVERIFIED 0, RESOLVED 9.

## 5. Tests (fresh Python 3.12 venv, `env -i`, no credentials, mocked)

| Command | Result |
|---|---|
| `pytest backend/tests tests -q -rs` | exit 0; 948 passed, 13 skipped, 153 deselected, 0 failed; 126 subtests; 25 warnings |
| `flake8 --select=E9,F63,F7,F82 backend tests` | exit 0 |

Skips: external API target disabled; postgres_integration/live_destructive/external_integration deselected by `pytest.ini`. No broker lifecycle observed.

## 6. Remediation order (unchanged)

1. Promote exit-lockout fix (CRIT-001), retry-at-same-stop fix (CRIT-002) and bid ≥ limit invariant (HIGH-015/016) with reviewed tests; deploy with SHA parity check (HIGH-012).
2. Deploy-safe options research-only pause (MED-021) and status truthfulness (LOW-018).
3. Backup lifecycle alerting/pruning (HIGH-013, MED-020), TLS (HIGH-011).

## 7. Operator actions before Friday 09:30 EST (early close 13:00)

Confirm broker-side protection for every position; check OPEN rows with non-null `emergency_exit_order_id`; confirm how the terminal treats 13:00–16:00 Friday (MED-013); confirm `OPTIONS_RESEARCH_ONLY=true` / `ENABLE_OPTIONS_EXECUTION=false` on the VPS if the pause is intended; confirm deployed SHA.
