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

## Current completion boundary

Frontend workbench changes are deployed and their 26 local frontend tests pass.
Initial main JavaScript gzip decreased from approximately 422 KB to 132 KB;
this is bundle reduction, not a claim of equivalent end-to-end latency gains.
Backend PID stayed unchanged during final static deployment. This document is
a plan: its cleanup and backend optimizations have not been executed. Production
RAM pressure and backend availability remain unresolved and must not be described
as a fully healthy terminal.
