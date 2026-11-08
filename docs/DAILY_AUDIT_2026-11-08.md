# Case Capital Trading Terminal — Daily Audit 2026-11-08

- **Audit window:** 2026-11-08 01:50–02:10 EST (America/New_York, UTC−5), Sunday. Daily run 32.
- **Market session:** CLOSED (weekend). Next regular session is Monday 11-09, 09:30–16:00 EST. Next holiday: Thanksgiving 11-26; early close 11-27.
- **Repository:** `main` moved during the audit: `2c768c3` → `1eb6fe6` (01:50 EST) → **`96edd6e`** (01:57 EST). This audit is pinned to `96edd6e`.
  - VPS branch `codex/desktop-checkpoint` → **`924804e`** (cherry-picks of main's 7 commits up to 1eb6fe6; does not contain 96edd6e). `main...desktop-checkpoint` = 11/394, so they are still separate lineages.
  - `Claude-Gpt-audit` @ `cd96fc1` (Codex 11-08 update). Its outage/health commits are now on main and VPS. Its exit-lockout fixes are not.
- **Deployed commit:** SHA UNVERIFIED. `/api/status` at 06:53:57Z had the old schema (no `db_reason`). From 06:55:04Z it returns `db_reason`, `db_failed_read` and `protection_status`, so a deploy happened during the audit. The new fields match VPS 924804e (or an equivalent build); this is not SHA proof.
- **Scope:** REPOSITORY + LIMITED LIVE (unauthenticated read-only GETs, GitHub Actions API). No SSH, broker, DB or operator session. No order, preflight or mutation endpoint was touched.

## 1. Verdict

**CRITICAL (code) / DEGRADED (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED. The ledger DB reports available (3 samples, 252 s span). HTTP only. Root cause of the 11-03 to 11-07 outage UNVERIFIED. |
| Data | NOT VERIFIED. `last_scan_at=2026-11-02T05:00:36Z`: no scan has been persisted for the whole 11-02 to 11-06 trading week. |
| Entries | NOT VERIFIED. |
| Exits | CRITICAL. CRIT-001, CRIT-002 and HIGH-014 are still in code on main and VPS. Live `protection_status=NOT_ASSESSED`. |
| Learning / reporting | NOT VERIFIED. |

## 2. Live observations

| Time (UTC) | Endpoint | Result |
|---|---|---|
| 06:53:57Z | `/api/status` | 200 in 4.63 s; `db_available=true`; old schema (no `db_reason`); `last_scan_at=2026-11-02T05:00:36Z` |
| 06:55:04Z | `/api/status` | 200 in 0.65 s; `db_available=true`, `db_reason=null`, `db_failed_read=null`, `protection_status=NOT_ASSESSED` |
| 06:58:09Z | `/api/status` | 200 in 0.63 s; same as 06:55:04Z |
| 06:54Z | `/api/health`, `/api/scheduler/overview`, `/api/positions` | 403 without an operator session |
| 06:54Z | HTTPS | No TLS listener (HIGH-011) |

Position coverage table: NOT VERIFIED (no broker or operator session). Scan/order funnel: NOT VERIFIED (no persisted scan since 11-02).

## 3. New finding

### CCTT-MED-020 — Backup headroom guard halts all future backups silently; pruning never runs once headroom is short (NEW 2026-11-08)

- Severity: Medium · CONFIRMED (source, both branches) · Open
- Evidence:
  - `deploy/vps/backup-postgres.sh:5` sets `set -euo pipefail`. `:60` runs `backup-headroom.py`, which exits 2 when free space is below 1.25 × the largest of the last three dumps plus 2 GiB.
  - On main, retention pruning (`:68-69`) runs only after `pg_dump` (`:61`). When headroom is short, the script exits before pruning, so space is never reclaimed and every later night fails the same way.
  - The VPS branch `924804e:deploy/vps/backup-postgres.sh:67` has no pruning at all ("separate operator step").
  - `case-capital-postgres-backup.service` has no `OnFailure=` and no alert hook.
  - The in-app "Daily DB Backup" job (`scheduler.py:1117-1135`) only stores a stats checkpoint, so it does not show whether a backup ran.
- Consequence: after the first low-headroom night, nightly backups stop with only a failed systemd unit in the journal. Recovery-point age grows with no notification. This is the same failure shape as ACC-MED-015 in the Accountant.
- Repair: alert on unit failure (`OnFailure=` to a DB-independent notifier). Expose last-successful-backup age in the authenticated health view. Off-host-verified retention should run before the headroom check, never deleting the newest verified copies.
- Acceptance: a simulated low-headroom run exits non-zero and emits an alert. A second run after verified off-host copies exist can prune and succeed. Health shows backup age.
- Test: extend `backend/tests/test_backup_headroom.py` with a shell-level harness (fake `pg_dump` and `df`). Post-deploy: `systemctl list-timers` and the newest dump mtime on two consecutive nights.

## 4. Prior findings — status changes

- **CCTT-CRIT-003 (Open → Partially addressed).** Live `db_available=true` in three samples (06:53:57Z to 06:58:09Z, 252 s). The DB-independent outage watchdog (`database_watchdog.py`; `scheduler.py:508`) and sanitized health categories are now on main (68b9eab, 27c2b8c, 3d25a35, 96edd6e) and VPS (55c4f95, 486c39f, 065d6f9).
  - Still needed: a fresh persisted scan (Monday), authenticated position and protection coverage, live alert-delivery proof, and a root cause. Disk exhaustion is a SUSPECTED cause: 1eb6fe6 adds a backup headroom guard, and HIGH-013 projected disk fill in mid-November. This is a hypothesis, not evidence.
- **CCTT-HIGH-010 (Partially addressed; now promoted).** Frozen candidate authority and manual persisted-PM resolution are on main (97b3a27, d2e5e0a, a65412f) and VPS (4a20916, 6545afa, 584d08b). The residual (immutable PM decision artifacts; manual contract and risk binding) is not independently re-verified.
- **CCTT-HIGH-012 (Open; evidence refreshed).** The deployed build now includes the 11-04 health fields. `main` (96edd6e) and VPS (924804e) are still separate lineages (11/394), the VPS lacks 96edd6e, and there is no SHA endpoint.
- **CCTT-HIGH-013 (Partially addressed).** The headroom guard (1eb6fe6 / 924804e) stops backups from consuming the database's last 2 GiB. The VPS branch still never prunes. See MED-020 for the silent halt this creates.
- **CCTT-CRIT-001 / CRIT-002 / HIGH-014: still Open** on main 96edd6e and VPS 924804e (`public_execution.py:2232`, `execution_safety.py:68` with a 24 h TTL). The fixes exist only on `Claude-Gpt-audit` (`_retire_terminal_emergency_order` at `:2307`).
- **Live re-confirmed:** HIGH-011 (HTTP only).
- **Totals:** 53 items. Critical 3, High 14, Medium 20, Low 16 · CONFIRMED 41, SUSPECTED 4, UNVERIFIED 0, RESOLVED 8.

## 5. Tests

| Command | Env | Result |
|---|---|---|
| `env -i HOME=… PATH=venv python -m pytest -q -p no:cacheprovider` | main 1eb6fe6, fresh Python 3.12.3 venv from `backend/requirements.txt`, no credentials, default markers exclude live_destructive / external_integration / postgres_integration | exit 0; 775 passed, 13 skipped, 153 deselected, 126 subtests |
| same | main **96edd6e** | exit 0; **776 passed, 13 skipped, 153 deselected**, 25 warnings, 126 subtests |

Skipped: 12 external-API cases (`test_stock_bot_api.py`) and 1 PostgreSQL lock contract (needs an isolated DSN). Deselected: 153 by marker. CI: run 95 (main 1eb6fe6) success; run 96 (VPS 924804e) success; run 99 (main 96edd6e) in progress at 06:57Z.

Not exercised: real PostgreSQL concurrency; broker lifecycle; live Telegram delivery; backup-script shell behaviour.

## 6. Remediation order

1. **Before Monday 09:30 EST.** Operator: confirm broker-side protection for every Public and Alpaca options position, confirm the first scheduled scan persists, and capture the outage root cause (disk, service, credentials).
2. **Exit fixes.** Promote and deploy the CRIT-001, CRIT-002 and HIGH-014 fixes from `Claude-Gpt-audit` with their regression tests.
3. **Backups.** Add a backup-failure alert and verified off-host retention (MED-020, HIGH-013).
4. **Release parity.** Add a SHA endpoint and a single release lineage (HIGH-012), then TLS (HIGH-011).

## 7. Unknowns

- No positions, orders, buying power or protective-order data; position coverage UNVERIFIED.
- Deployed SHA, disk, backups and journal were not observed.
- Outage start and end times, and its root cause, are unknown. Whether any outage alert was delivered is unknown.
