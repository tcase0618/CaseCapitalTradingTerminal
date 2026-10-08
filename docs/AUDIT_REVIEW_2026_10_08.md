# October 8 remediation review

Source reviewed: cbb24d76f7c8b445db1f368e6c107fb15b9bb809.

## Follow-up execution repairs

- Reset protective fill counters when a new protective order is reserved.
- Use remaining broker quantity for protective sizing, not original entry fills.
- Defer replacement when cancellation exposes unaccounted fills.
- Protect canceled/rejected/expired entries that retain partial fills, including TTL cancellation.
- Track Public Lottery exit fills by sell-order id and cumulative quantity/value.
  Repeated sequential polls do not subtract the same fill twice.
- Preserve fills before retiring a terminal emergency order.
- A missing Public equity read rejects the current Public entry attempt, rather
  than writing a permanent global halt. Verified-loss and operator halts remain.

Verification: 691 passed, 14 skipped, 153 deselected in the network-blocked
offline suite. Critical-error lint, compilation and diff checks passed.
New lifecycle fixtures retain writes across successive reconcile calls and
exercise cancellation races and partial fills. No real broker calls were made.

## Not certified for production

The compatibility store's read-then-write updates are not compare-and-set.
These tests do not prove two-process exit reservation or fill-ledger safety.
Options duplicate-close protection, distributed leadership, and shared
rebalance/phase/emergency reservations remain unresolved. Existing historical
Lottery partial fills without sell-order accounting markers require broker
history reconciliation; the repair does not invent a historical backfill.

Do not merge the full audit branch as a release. Select independently tested
calendar/reporting, scheduler/alert-delivery and lint changes first. Retain
Public execution changes on the audit branch pending isolated PostgreSQL
concurrency and broker-lifecycle verification. No VPS deployment or execution
setting changes form part of this review.
