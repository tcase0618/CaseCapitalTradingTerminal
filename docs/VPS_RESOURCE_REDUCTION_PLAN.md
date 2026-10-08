# VPS Resource Reduction Plan

## Scope and measured baseline

Read-only inventory on October 7, 2026, approximately 23:30-23:34 ET.
No services stopped, trading settings changed, database records deleted, or
resource limits applied while preparing this plan.

| Resource | Observed |
| --- | --- |
| Physical memory | 1,966 MB total; 62-76 MB available |
| Swap | 2,047 MB total; 1,309-1,800 MB used |
| Root filesystem | 48 GB; 18 GB used; 30 GB available |
| Backend process | PID 1071909; RSS approximately 1.23 GiB at first sample |
| Backend service cgroup | 1.09 GiB current; 1.73 GiB peak at separate sample |
| PostgreSQL database casecapital | 5,197 MB |
| PostgreSQL filesystem directory | 5.3 GB |
| PostgreSQL backup directory | 2.6 GB |
| Journal files | 1.6-1.7 GB |
| Active checkout backend/frontend | 947 MB / 535 MB |
| Package caches | root npm 238 MB; root cache 64 MB; apt archives 277 MB |

Process and cgroup readings are separate samples, not additive. PostgreSQL
process RSS includes shared pages and must not simply be summed either.
Two one-second vmstat samples showed approximately 40-62 MB/s swap-out and
44-58 MB/s swap-in. This is actual memory pressure, not merely old swap pages.
Backend and nginx remained active, but an unauthenticated API health request
returned 403 after 7.9 seconds; that is not proof of application readiness.
The browser also displayed a backend-unavailable login state during the final
check. Earlier authenticated UI checks showed read timeouts and monitor jobs
skipped because an earlier invocation was still running.

Disk is not close to full. RAM and backend read amplification take priority.
No unused browser/VNC process appeared among the largest resident processes;
stopping hypothetical services is not a justified optimization.

## Build order

### 1. Establish a reproducible baseline

Collect a full scheduled scan and at least 30 monitor cycles: service RSS and
peak, available memory, swap activity, read endpoint latency, monitor duration,
missed invocations, database pool wait, and query row counts. Attribute memory
growth to workloads before calling it a leak. Use bounded staging profiling;
do not attach a heavyweight profiler to live execution by default.

Preserve order IDs, broker reconciliation state, configuration, database schema,
and a tested backup. Record current commit and deployment asset manifest.

### 2. Remove full-collection materialization from hot reads

`backend/services/postgres_store.py` currently has a narrow SQL fast path:
simple equality filters plus supported ordering and an explicit limit. Its
fallback fetches every document in the collection before Python matching.
`PostgresAggregateCursor.to_list` starts with a full-collection read, and async
iteration materializes a list rather than streaming it.

First trace which callers trigger those paths. Push supported predicates,
projection, ordering, aggregation, and pagination into parameterized SQL. Use
server-side cursor batches for genuinely large research exports. Add indexes
only after EXPLAIN and measured query evidence. Keep unsupported predicates
explicit and observable; do not silently truncate results to reduce memory.

Run old/new result-equivalence tests for null/missing fields, numeric versus
string values, compound predicates, ordering ties, broker/account scope, and
pagination. Execution, ratchet, and learning results must remain equivalent.
Do not remove the compatibility API until its callers are migrated and tested.

Expected RAM savings are unmeasured; this is a demonstrated architectural
allocation risk, not yet a proven explanation for every observed failure.

### 3. Bound expensive research work, not protective monitoring

Profile Kronos grading and strategy-evidence history reads, including their
unbounded `to_list(None)` calls. Batch work and retain compact summaries rather
than parallel full-history copies. Keep public portfolio/quote monitoring and
exits independent of research queues. Set separate, bounded research concurrency.
Do not slow the requested monitoring cadence or weaken quote-freshness gates.

Audit caches for byte size, retention, invalidation, and per-account keys.
Bound by bytes where objects vary in size; TTL alone does not bound memory.
Do not add more backend workers on this 2 GB machine without a measured budget.
Tune PostgreSQL pool/query memory only after reading actual settings and load.

### 4. Keep production builds off the VPS

The final frontend deployment was built locally and uploaded with SFTP, with
the index promoted after assets and its hash verified. Keep that deployment
pattern: build/test in CI or locally, upload versioned static assets, and leave
the trading backend running. Preserve dependency locks and rollback manifests.

After proving deployment no longer needs on-host Node tooling, inventory the
535 MB frontend directory. Remove build-only dependencies only when the rollback
and rebuild path no longer relies on them. Do not delete the Python virtualenv,
SDK dependencies, broker credentials, or any database files.

### 5. Reduce logs and disposable caches

After agreeing evidence retention, export older audit-relevant logs off-host
and verify checksums. Set journald retention around 256 MB with a free-space
reserve, and rotate nginx/application logs with bounded size and age. From the
current journal footprint, this could reclaim roughly 1.3 GB; it is not a RAM fix.
Never manually delete PostgreSQL WAL files or live journal files.

Package caches total approximately 579 MB across the measured npm/root/apt
archive paths. They are cleanup candidates only when no install/build is active
and dependencies can be reproduced. Preserve lockfiles and installed packages.
Remove exact inventoried paths with native package-cache commands, not broad rm.

Old frontend hashed assets need a grace period for already-open lazy-loaded
clients. Retain at least two known-good releases plus a seven-day asset grace
period, increasing it if real client session lifetimes require it. Delete from
verified manifests, not by assuming every non-current file is unused.

### 6. Backups and database maintenance

The 2.6 GB backup directory is a protection asset, not automatically waste.
Inventory backup age, compression, contents, encryption, and successful restores.
Establish encrypted off-host copies, checksum verification, and a tested restore
before pruning local generations. Keep recovery coverage across schema changes.

Check table/index sizes, dead tuples, autovacuum, and retention requirements.
Use routine VACUUM/ANALYZE where appropriate; avoid VACUUM FULL on the live
terminal because it rewrites/locks tables. Archive bulky raw research snapshots
only with a documented read-through/restore path. Preserve trades, fills,
prediction outcomes, strategy evidence, and audit provenance. Do not silently
delete history needed by learning or scorecards.

### 7. Right-size only after measurement

If bounded queries and research workloads still cannot fit alongside the live
monitor, move to a VPS with at least 4 GB RAM and measure again. This is a paid
capacity decision, not an automatic change. Keep swap as an emergency buffer;
do not disable swap, impose an OOM-inducing backend memory cap, or periodically
restart trading as a substitute for fixing allocations.

## Release and rollback gates

Land one resource change at a time on Claude-Gpt-audit. Run database-contract,
monitor, execution, ratchet, reconciliation, and frontend smoke tests. First
compare old/new reads in staging using representative snapshots, with zero
live order side effects. Deploy outside a scheduled scan and verify existing
order management continues uninterrupted.

Acceptance targets: unchanged financial decisions on identical inputs; no
cross-account data contamination; no increase in monitor failures or skipped
cycles; lower peak RSS and sustained swap traffic; no regression in p95 API or
quote-to-decision latency; verified history and backup recovery. Measure over a
full session, not a single quiet request. Revert the specific release if any
of those checks fails. Cleanup is performed only after its recovery path exists.

## Implementation record (October 8, 2026)

Implemented and deployed SQL predicate prefiltering, bounded fallback keyset
batches of 32 documents, SQL counts for exact supported predicates, and streamed
group-first aggregation. Python matching remains authoritative. Numeric and
unsupported predicates intentionally retain the Python matcher to avoid changing
bool/int/float semantics. Sorted unsupported queries and aggregations with a
leading sort can still materialize their filtered input; this is not a claim
that every compatibility operation has been rewritten in SQL. Batch iteration
does not provide an immutable transaction snapshot across concurrent writes;
stable-input equivalence was tested, and connection release avoids deadlocking
write-during-iteration consumers. No result truncation was added.

Strategy evidence now loads saved outcomes only for the active episode IDs in
200-ID batches. The grouped daily research price cache expires old entries and
retains at most eight maps; execution quote freshness is unchanged. PostgreSQL
pool size, work_mem, autovacuum, financial rules, and monitor cadence were left
unchanged. An EXPLAIN ANALYZE on the NVDA price-cache lookup used the existing
collection index and ran in 1.347 ms, so no speculative index was added.

CI now includes Claude-Gpt-audit, an isolated PostgreSQL service for real
contract tests, and a checksummed frontend build artifact. Production Node
build-only dependencies were removed after verifying nginx serves static build
files and no Node process uses the directory. The static build, old hashed
assets, lockfiles, and the existing dirty lockfile diff were preserved. Backend
virtualenv dependencies were retained. No browser/VNC process was removed.

One-minute read-only OS telemetry is installed as case-capital-resources.timer.
Journald is capped at 256 MB, with a 1 GB free-space reserve and 14-day maximum
retention, after checksum-verified encrypted off-host archival of old journals.
Logrotate was missing despite configuration files being present; it was installed
from Ubuntu's configured repository and its timer enabled. Nginx rotation uses
20 MB maxsize at rotation checks and 14 generations, retaining its reopen hook.
Application stdout continues to use journald. npm/apt/pip disposable caches were
cleared; installed Python dependencies and market/history data were not removed.

The latest dump restored into a separate scratch PostgreSQL database: 320,141
snapshot rows and 1,743,757 event rows, including trade and option-order history.
The scratch database was removed after verification; production was not restored
over or altered for this test. Backup creation is low-priority, and the former
automatic pruning based merely on pg_restore --list was removed. Current backup
generations are recent and retained. Unarchived backups are never auto-pruned.
Ongoing off-host backup transfer requires an authenticated destination; it has
not been scheduled using a saved SSH password.

Off-host archives use AES-GCM with a current-Windows-user DPAPI-protected key.
The key is not portable to another Windows account or machine without an
additional key-escrow design. The VPS retains current PostgreSQL backups; the
off-host copy is additional protection, not their replacement. Recovery is
available through deploy/vps/sealed-archive.py --decrypt-file and verified against
plaintext checksum and GCM authentication. No plaintext archives are written by
the copy operation, and no SSH password is persisted.

Early post-deploy readings: approximately 1.4-1.5 GB available RAM, 35-42 MB swap
used, and no active swap traffic in the short measured intervals. During the
next midnight scan, backend memory increased as expected; final sustained-load
measurements belong in the completion report rather than extrapolating from
startup. Root filesystem usage decreased from 18 GB to approximately 16 GB,
with about 32 GB available. Authenticated status, PostgreSQL, monitor, trading
status, and daily scan-funnel reads returned HTTP 200. No manually triggered
scan/order was used for verification, and no execution settings were changed.

The full offline backend suite passed 559 tests, with 13 skipped and 153
deselected by repository configuration. Real PostgreSQL TEMP-table tests also
passed on the VPS; they test scalar/array/null/missing values, account paths,
pagination, numeric fallback, streamed groups, and pool release. Encrypted
archive round-trip and tamper rejection were tested. These results do not certify
every external broker/provider, daytime load, or unrun live integration test.

A paid RAM upgrade was not performed: current measured headroom does not justify
one yet. No VPS services were removed or OOM-inducing memory caps imposed.
