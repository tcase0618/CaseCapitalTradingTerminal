# Equity Study Evidence Index

`RESULTS.md` is the final integrated study. The four independent review reports
document the evidence and limitations available when each reviewer ran; their
earlier population counts and partial Public-history prefixes are intentionally
not rewritten as final coverage claims. Final scope and arithmetic checks are in
`RESULTS.md` and `validation.json`.

## Independent Reviews

- `strategy-reconstruction.md`: strategy identities and execution-policy limits.
- `data-schema-audit.md`: timestamp, identity, price-basis and matching defects.
- `methodology-audit.md`: independent methodology review and integration repairs.
- `fill-diagnostics.md`: actual-fill evidence, separately from modeled signals.

The `comparability-*` aggregates and manifests describe an earlier immutable
diagnostic snapshot, not the final expanded study population. Their referenced
per-record CSVs are retained privately under
`C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/comparability-evidence/`, outside
Git. They are not missing zero-return observations or corrected prices.

## Reproduce

Run the offline commands in `RESULTS.md` with authorized local source exports.
The scripts do not import order-execution services. Historical collection uses
only market-data reads; source database exports use a read-only transaction.
Do not run collection on a production host merely to view this report.

No result automatically updates live strategy thresholds, portfolio allocations,
execution gates or broker orders. Future policy changes require a separately
reviewed, prospective trial with costs and fill-quality evidence.
