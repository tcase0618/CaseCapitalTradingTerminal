# Case Capital Trading Terminal — daily audit 2026-10-22

Daily run 15. REPOSITORY-ONLY audit: no SSH, broker or database access from this session.

## Ground truth

- **Audit time:** 2026-10-22 02:51–03:40 EDT (America/New_York, UTC−4), Thursday.
- **Market session:** pre-market and closed at audit time. Thu 10-22 is a regular NYSE session (no holiday, no early close); it opens 09:30 ET.
- **Repository state:**
  - `main` @ `250f5eb` ("Promote authenticated archive recovery with adversarial regression tests"). CI 39598659904: success.
  - New VPS branch `codex/vps-reviewed-release-20261021` @ `f05b9bb`, which is `main` plus the VPS deltas (0/382). CI 39598664785: success.
  - `codex/desktop-checkpoint` @ `e48177c`, a frontend-only commit on top of `f05b9bb`. CI 39605901707: success.
  - `Claude-Gpt-audit` @ `3404100` is 29 commits ahead of `main` and 8 behind.
- **Backend changes since `aab210a`:** none in `backend/services`. The only change is `deploy/vps/sealed-archive.py` plus its tests, so every exit and control finding keeps its file:line evidence.
- **Deployed commit:** UNVERIFIED. There is no Terminal deploy record for 10-21.
- **Verdict:** NOT VERIFIED (runtime). Code level is **CRITICAL** for exits on `main` and on both VPS heads.

| Area | Readiness |
|---|---|
| Infrastructure | not verified |
| Data | not verified |
| Entries | not verified (HIGH-004 kill switch missing) |
| Exits | CRITICAL in code (CRIT-001/002, HIGH-014) |
| Learning | partial (HIGH-008) |
| Reporting | partial (MED-012) |

## Re-checked evidence on `250f5eb`

- **CRIT-001:** `backend/services/public_execution.py:2232` still skips OPEN rows that carry `public_phase_order_id` or `emergency_exit_order_id`.
- **CRIT-002:** `backend/services/execution_safety.py:68` still sets `ttl_seconds = 24*60*60`.
- **HIGH-004:** `public_execution.py:1497-1503` still has no `execution_gate.check`.
- **HIGH-013:** the VPS branch `deploy/vps/backup-postgres.sh:10, 66` still never prunes; `main` prunes at `:67-68`.

## Status changes

- **CCTT-LOW-012 → RESOLVED at repository level** (deployment UNVERIFIED).
  - Promoted to `main` (`250f5eb`) and the VPS release branch (`f05b9bb`).
  - Locally, with paramiko installed, `pytest backend/tests/test_sealed_archive.py` gives 14 passed. The cases cover a tampered tag, wrong key, modified AAD, size mismatch and truncation; none of them writes plaintext.
- **New: CCTT-LOW-016 — the sealed-archive regression tests are skipped in CI** (CONFIRMED).
  - `backend/tests/test_sealed_archive.py:13, 68` call `pytest.importorskip('paramiko')`, and `backend/requirements.txt` lacks paramiko.
  - In a clean requirements venv all 14 tests skip (`-rs`: 8 + 6 skipped). A green CI run therefore never exercises archive authentication.
  - Repair: add a dev or test requirement for paramiko, or split the crypto core so it can be imported without paramiko.
  - Acceptance: the CI log shows the 14 tests passing.
- **All other findings are unchanged.** The backend services did not change, so this is a carry-forward, not an independent re-validation of each item.

## Position coverage and funnel

NOT VERIFIED: no broker or ledger access. Before 09:30 ET, the operator should check read-only:

- deployed SHA
- OPEN `tf_trades` rows with a non-null `emergency_exit_order_id` or `SUBMIT_UNKNOWN`, compared with broker status
- protection for every held position
- `df` and backup-directory growth (HIGH-013 estimate)

## Tests

- Fresh Python 3.11 venv from `backend/requirements.txt`, with `POSTGRES_ENABLED=false`, execution and scheduler disabled, and the proxy pointed at a dead port.
- `pytest backend/tests tests -q` → exit 0, **532 passed, 27 skipped, 153 deselected**, 25 warnings, 126 subtests.
  - 14 skips are sealed-archive tests (no paramiko), 12 are disabled external-API tests, and 1 is the isolated-PostgreSQL lock test.
- With paramiko added, `test_sealed_archive.py` → 14 passed.
- `flake8 --select=E9,F63,F7,F82` (backend) → exit 0.
- Not exercised: any live broker, quote, scheduler or PostgreSQL behaviour, and any real lifecycle.

## Remediation order (unchanged)

1. Operator read-only exit check.
2. Review and promote the `Claude-Gpt-audit` exit fixes (CRIT-001/002, HIGH-014, HIGH-003, HIGH-001/002/004/005), plus LOW-014/015, to `main` and the VPS release branch with green CI.
3. HIGH-013 backup retention on the VPS branch.
4. HIGH-012 deploy record and endpoint.

## Unknowns

Deployed SHA, broker positions and protection, quote ages, scheduler run history, disk and backups, and Public/Alpaca behaviour for every SUSPECTED item.
