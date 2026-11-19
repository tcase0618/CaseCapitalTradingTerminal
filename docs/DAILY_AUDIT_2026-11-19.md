# Case Capital Trading Terminal — Daily Audit 2026-11-19

Run 41. Read-only. I called no order, preflight, cancel or mutation endpoint. This file contains no secrets, account identifiers or DSNs.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-19 01:52–02:30 EST (America/New_York, UTC−5), Thursday |
| Market session | CLOSED (overnight). Today's regular session runs 09:30–16:00 EST. This audit did not observe Wednesday 11-18's full session. Thanksgiving 11-26 is closed; 11-27 closes early at 13:00 |
| Previous audit | 2026-11-18 run 40, `docs/DAILY_AUDIT_2026-11-18.md` (branch `claude/stoic-knuth-f0d6cz`, commit `74f77bf`) |
| `main` | **`c17babc`** (was `5369675`). Six new commits on 11-18: discovery repairs, Lottery evidence, government-history novelty, options research-only pause, and research quote age. CI run 127 succeeded. Run 125 on `7e18e30` failed; its successor passed |
| VPS `codex/desktop-checkpoint` | **`d28c80c`** (was `bde0cd5`), carrying the same six changes. CI run 126 succeeded. It still differs from `main` across 80 files (release parity, HIGH-012) |
| `Claude-Gpt-audit` | `24173c3`, unchanged |
| Deployed commit | SHA UNVERIFIED. The midnight scan returned 32 results (it returned 3 on 11-18). That fits the discovery changes being deployed, but it does not prove it (SUSPECTED) |
| Access | REPOSITORY + LIMITED LIVE: unauthenticated GETs and the GitHub Actions API. `/api/health` returns 403. No broker, SSH, database or log access. HTTPS got no response (curl code 000) |

## Verdict

**CRITICAL (code) / DEGRADED (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED. `db_available=true` at 06:54:24Z and 06:55:41Z (77 s apart). HTTP only |
| Data | PARTIAL. `last_scan_at=2026-11-19T05:01:12Z` (Thursday midnight scan): 32 results, 32 passed pre-filter, 0 Claude calls. Whether the scan persisted and what its funnel looked like is NOT VERIFIED |
| Entries | NOT VERIFIED. The options research-only pause relies on an env flag that defaults to off. The VPS template still enables options execution (new finding MED-021) |
| Exits | CRITICAL. CRIT-001/002 and HIGH-014/015/016 are unchanged on `main` c17babc and on VPS d28c80c. No diff to `public_execution.py` or `execution_safety.py`. I re-read `:2310` (skip on `emergency_exit_order_id`), `:2318` (cached quote), `:2352` (stop-pinned client id) and `execution_safety.py:68` (24 h TTL). `protection_status=NOT_ASSESSED` |
| Learning / reporting | NOT VERIFIED. PM history is now written only by the full terminal cycle (`persist_history=True`, `terminal_cycle.py:272`) |

## New findings

### CCTT-MED-021 — The options research-only pause fails open on the VPS template (CONFIRMED in the repo; runtime UNVERIFIED)
- Evidence: `options_research.py:21` defaults `OPTIONS_RESEARCH_ONLY` to `"false"`. `deploy/vps/cloud.env.example:33` still sets `ENABLE_OPTIONS_EXECUTION=true` and never sets `OPTIONS_RESEARCH_ONLY`. Only `backend/.env.example` (the local template) was changed to pause.
- Consequence: a VPS built from the deploy template keeps automatic Alpaca-paper options entries enabled, even though commit `7e18e30` says entries are paused. Paper account only, so no real-money loss.
- Repair: set `ENABLE_OPTIONS_EXECUTION=false` and `OPTIONS_RESEARCH_ONLY=true` in `deploy/vps/cloud.env.example`, and have the operator confirm both values on the VPS.
- Acceptance: the template contains both flags. Authenticated `/api/system/health` reports options entries blocked.

### CCTT-LOW-018 — Status and gate surfaces report options execution enabled while research-only blocks it (CONFIRMED)
- Evidence: these surfaces read the raw `ENABLE_OPTIONS_EXECUTION` flag instead of `options_execution_enabled()`:
  - `data_truth.py:138,145`
  - `execution_gate.py:239` (consumes the data_truth flag)
  - `options_desk.py:841` and `:1550`
  - `tail_hunter.py:307`

  The entry functions themselves (`options_desk.py:1716`, `:1890`, `:1931`; `tail_hunter.py:443`) are correctly blocked.
- Consequence: the frontend, preflight and Tail Hunter tickets show options as enabled, or omit the block, while entries are refused. The status is misleading, but no order is placed.
- Repair: route every surface through `options_desk.options_execution_enabled()` and expose `options_research_only` explicitly.
- Test: with research-only set and the execution flag true, `data_truth` execution flags and `execution_gate` both report options as blocked.

### I checked these and found no defect
- The options research-only pause leaves `close()` and `monitor_open_positions()` ungated, so existing option exits stay available (`options_desk.py:2068`, `:2729`).
- The pricer changes tighten freshness: a missing timestamp is no longer treated as fresh, and Finnhub is refused when the caller sets a budget. They apply to discovery and research callers only (scanner, strategy_screeners, lottery, scrapers, `server.py:2481`). No exit path calls `live_price_meta`.

## Prior findings
- CRIT-001, CRIT-002, HIGH-014, HIGH-015, HIGH-016: **STILL OPEN** on main c17babc and VPS d28c80c. The 11-18 promotions did not include exit fixes.
- CRIT-003: Partially addressed. The database stayed up across 77 s. Root cause UNVERIFIED.
- HIGH-011 (no TLS): re-confirmed. HIGH-012 (release parity): re-confirmed, 80 files differ between `main` and the VPS branch.
- All other items are unchanged.
- Register: **58 items** (Critical 3, High 16, Medium 21, Low 18). CONFIRMED 45, SUSPECTED 4, UNVERIFIED 0, RESOLVED 9.

## Tests
Environment: fresh Python 3.12 venv, `env -i` with only the proxy set, `POSTGRES_ENABLED=false`, `RUN_EXTERNAL_API_TESTS=false`, no credentials, scratch clone of c17babc.

| Command | Result |
|---|---|
| `pip install -r backend/requirements.txt flake8` | exit 0 |
| `flake8 --select=E9,F63,F7,F82 .` (backend) | exit 0 |
| `pytest backend/tests tests -q -rs` | exit 0. **948 passed**, 13 skipped (12 external-API, 1 isolated-PG DSN), 153 deselected, 126 subtests, 25 warnings (15.1 s). The 11-18 baseline was 815 passed; the 133 new tests come from the six commits |

Not exercised: the real broker lifecycle, live quotes, PostgreSQL concurrency, Telegram delivery, restore, and the Sunday options review job.

## Remediation order
1. Reconcile and promote the exit-protection fixes with a bid ≥ limit invariant (CRIT-001/002, HIGH-014/015/016). Operator: verify broker-side protection.
2. Make the options pause hold on the VPS (MED-021): template plus operator confirmation. Then fix the status truthfulness (LOW-018).
3. Backup lifecycle and alerting (HIGH-013, MED-020, MED-016). Then release parity (HIGH-012) and TLS (HIGH-011).

## Operator actions before 09:30 EST today
- Confirm broker-side protection for every Public and Alpaca options position.
- Look for OPEN rows with a non-null `emergency_exit_order_id` whose broker order is no longer working.
- On the VPS, confirm `OPTIONS_RESEARCH_ONLY=true` and `ENABLE_OPTIONS_EXECUTION=false` if the options pause is intended.
- Confirm the deployed SHA, and confirm that Wednesday's full scans and the Thursday midnight scan persisted.

## Not verified
Positions, orders, buying power, ledger, protection, quotes, the scheduler registry and run history, the funnel, outcomes, the deployed SHA, the effective VPS env flags, backups and restore, and logs.
