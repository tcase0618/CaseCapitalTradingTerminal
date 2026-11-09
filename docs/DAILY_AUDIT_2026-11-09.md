# Case Capital Trading Terminal — Daily Audit 2026-11-09

Run 33. Read-only. No order, preflight, cancel or mutation endpoint was called. No secrets, account identifiers or DSNs are reproduced.

## Ground truth

| Item | Value |
|---|---|
| Audit time | 2026-11-09 01:52–02:20 EST (America/New_York, UTC−5), Monday |
| Market session | CLOSED (overnight). Regular session today 09:30–16:00 EST; not a holiday. Next holiday Thanksgiving 11-26; early close 11-27 |
| `main` | `96edd6e` (unchanged since the 11-08 audit) |
| VPS branch `codex/desktop-checkpoint` | `72ccc36` (was `924804e`; cherry-pick of `96edd6e`). `main...VPS` = 11/395, still separate lineages |
| `Claude-Gpt-audit` | `e050aad` (new: backup completion metadata/freshness in health; not on main or VPS) |
| Deployed commit | SHA UNVERIFIED. `/api/status` schema matches the 11-08 VPS deploy (`db_reason`, `db_failed_read`, `protection_status`) |
| Access | REPOSITORY + LIMITED LIVE: unauthenticated GETs to the public terminal host, GitHub Actions API. Authenticated surfaces (`/api/health`, `/api/system/health`, `/api/scheduler/status`, `/api/positions`) return 403 without an operator session. No broker, SSH, DB or log access. HTTPS: no listener |

## Verdict

**CRITICAL (code) / DEGRADED (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED — DB reports available across 3 samples (06:54:11Z, 06:55:02Z, 06:55:46Z; 95 s); HTTP only; outage root cause unknown |
| Data | PARTIAL — a scan persisted at 2026-11-09T05:02:09Z (`midnight_scan` 00:00 ET; 6 results, 6 pre-filter passed). First persisted scan since 11-02 05:00Z |
| Entries | NOT VERIFIED (no session-hours scan yet; funnel not observable without auth) |
| Exits | CRITICAL — CRIT-001/002 and HIGH-014 remain in main and VPS lineage; `protection_status=NOT_ASSESSED` |
| Learning / reporting | NOT VERIFIED |

## Position coverage and funnel

Broker positions, orders, buying power and ledger are **NOT VERIFIED** (no authenticated or broker access). Public status exposes only `last_scan_summary`: results 6, pre-filter passed 6, Claude calls 0. No PM docket, approval, submission, acceptance or fill counts are observable. No coverage table can be honestly populated.

## New findings

### CCTT-LOW-017 — CI never runs the repository-root test suite (82 path-replay tests)

- Severity Low · CONFIRMED (source + local run) · Open
- Component: `.github/workflows/ci.yml`, `tests/test_path_replay.py`
- Evidence: `ci.yml:16-17` sets `working-directory: backend` and `:35` runs `python -m pytest tests`, which collects only `backend/tests`. Local: `backend` `pytest tests` → **694 passed**; repository root `pytest` → **776 passed**; `tests/test_path_replay.py` alone → **82 passed**. It covers `services/path_replay.py` and `services/research_path_service.py`, reachable from `server.py:2711` (`research_path_replay`).
- Consequence: a regression in research path replay (cost assumptions, train/holdout split, no-fabricated-returns guarantee) can merge with green CI. Research-only; no execution path.
- Repair: add a root-level `python -m pytest tests -q -rs` step (with `pythonpath=backend` from `pytest.ini`) or move the file under `backend/tests`.
- Effort S. Dependencies: none.
- Acceptance: CI backend log shows the 82 path-replay tests executed.
- Test: CI run on the fix commit whose test count equals the local root count.

## Prior findings

- **CRIT-003** (PostgreSQL unavailable): stays **Partially addressed**. New live evidence: DB available across 95 s and the 00:00 ET Monday scan persisted. Still missing: root cause, outage chronology, authenticated health/coverage, and a session-hours scan.
- **MED-020** (backup headroom guard halts silently): stays **Partially addressed** on `Claude-Gpt-audit` `e050aad` only (completion metadata + freshness in authenticated health; CI run 42495182200 green). Not on main or VPS; no failure alert; VPS `backup-postgres.sh` still has no pruning (HIGH-013).
- **HIGH-012** release parity: VPS head moved to `72ccc36`; still 11/395 divergent from main.
- **HIGH-011** HTTP only: re-confirmed (HTTPS no listener).
- All other items unchanged (no code change on main).

## Tests (fresh Python 3.12 venv under `env -i`; `POSTGRES_ENABLED=false`, external/live tests disabled; no credentials)

| Command | Result |
|---|---|
| `python -m flake8 --select=E9,F63,F7,F82 .` (backend) | exit 0 |
| `python -m pytest tests -q -rs` (backend, CI-equivalent) | exit 0 — 694 passed, 13 skipped, 153 deselected, 126 subtests, 25 warnings |
| `python -m pytest -q` (repo root) | exit 0 — 776 passed, 13 skipped, 153 deselected |
| `python -m pytest tests/test_sealed_archive.py -q` | 14 passed |
| CI | main 96edd6e run 99 success; VPS 72ccc36 run 100 success; Claude-Gpt-audit e050aad run 101 success |

Skips: 1 isolated-PostgreSQL lock test (no verified DSN), 12 external API tests. Deselected: 153 `live_destructive`/`external_integration`/`postgres_integration`. No frontend build today. All lifecycle evidence is mocked; no broker lifecycle observed.

## Remediation order

1. Before 09:30: operator confirms broker-side protection for every Public and Alpaca-options position, and that the first session scan persists.
2. Promote and deploy the exit-lockout fixes (CRIT-001, CRIT-002, HIGH-014).
3. Restore backup pruning plus failure alerting on the VPS path (HIGH-013, MED-020).
4. Release parity (HIGH-012), TLS (HIGH-011).
5. CI root tests (LOW-017) and other test/observability items.

## Unknowns

Positions, orders, buying power, stops, quote ages, scheduler run history, backup timer result, restore evidence, deployed SHA, learning jobs. Nothing here shows that "everything works".
