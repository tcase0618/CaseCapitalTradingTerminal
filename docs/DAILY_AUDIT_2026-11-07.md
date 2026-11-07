# Case Capital Trading Terminal — Daily Audit 2026-11-07

- **Audit window:** 2026-11-07 01:51–02:15 EST (America/New_York, UTC−5), Saturday. Daily routine, run 31.
- **Market session:** CLOSED (weekend). Next regular session Monday 2026-11-09 09:30–16:00 EST. Next holiday: Thanksgiving 11-26; early close 11-27.
- **Repository:** `main` @ `2c768c3` (unchanged since 10-28). VPS branch `codex/desktop-checkpoint` @ `06703a5` (unchanged). `Claude-Gpt-audit` @ `5193d09` (unchanged since 11-04 04:08 EST), not promoted.
- **Deployed commit:** SHA UNVERIFIED. Live `/api/status` still lacks `db_reason` (added in 31090b4), so the deployed build contains neither 31090b4 nor 5193d09.
- **Access:** REPOSITORY + LIMITED LIVE (read-only unauthenticated HTTP GETs; GitHub Actions API). No SSH, broker, DB or operator session. No order, preflight or mutation endpoint touched.

## 1. Verdict

**CRITICAL (live).**

| Area | Readiness |
|---|---|
| Infrastructure | DEGRADED: DB unavailable for at least **95.9 h**; HTTP only |
| Data | NOT VERIFIED |
| Entries | NOT VERIFIED |
| Exits | CRITICAL: CRIT-001/002 and HIGH-014 in code, plus CRIT-003 live through **four** full trading sessions (11-03 to 11-06) |
| Learning / reporting | NOT VERIFIED |

## 2. Live evidence

- `GET /api/status` returned 200 at 06:52:13Z, 06:52:40Z and 06:53:44Z (91 s span): `bot.db_available=false`, `last_scan_at=null`, all stats 0, `telegram_configured=true`, no `db_reason` field.
- Every sample since 11-03 07:01:29Z has shown the DB unavailable: outage **≥ 95.9 h**. Last healthy sample 11-02 06:58Z.
- `/api/scheduler/overview`, `/api/health` → 403 "Operator session required". HTTPS → connection reset (HIGH-011).
- **Position coverage table and funnel: NOT VERIFIED** (no broker or authenticated access; ledger store unavailable).

## 3. New findings

None.

## 4. Prior-finding status

- **Live re-confirmed:** CRIT-003 (≥ 95.9 h), HIGH-011 (HTTP only), HIGH-012 (deployed response predates 31090b4).
- **Unchanged:** all other items (no new commits on main, VPS branch or `Claude-Gpt-audit`; no Codex remediation update since 11-04).
- **Totals:** 52 items (Critical 3, High 14, Medium 19, Low 16) · CONFIRMED 40, SUSPECTED 4, UNVERIFIED 0, RESOLVED 8.

## 5. Tests

| Branch | Command | Env | Result |
|---|---|---|---|
| main @ 2c768c3 | `pytest backend/tests tests -q -p no:cacheprovider` | fresh Python venv under `env -i`; no credentials; default markers deselect live/external/postgres | **700 passed, 13 skipped, 153 deselected**, 126 subtests, 25 warnings, 0 failed |

CI: latest run 41804953450 (`Claude-Gpt-audit` 5193d09) success; no new runs. All mocked/offline; no live broker lifecycle evidence. Not run: frontend build, PostgreSQL contract tests (no isolated DSN), Docker.

## 6. Remediation order

1. **Operator, before Monday 09:30 EST:** restore PostgreSQL (service, pool, credentials, schema, disk, backend logs); confirm broker-side protection for every Public and Alpaca options position; confirm deployed SHA; require two healthy `/api/status` samples ≥ 60 s apart plus a fresh scan and position-monitor run. The weekend is the safe window to do this.
2. Review and promote CRIT-001/002, HIGH-014 fixes and 31090b4/5193d09 through a green-CI gate (MED-017), then deploy and verify.
3. Public execution controls (HIGH-004/005), TLS (HIGH-011), backup pruning (HIGH-013).

## 7. Unknowns

- DB outage root cause; whether scans/monitor ran on 11-03..11-06; current positions and protective orders; deployed SHA; whether any operator alert was delivered.
