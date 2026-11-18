# Options research-only pause

Set `OPTIONS_RESEARCH_ONLY=true` and `ENABLE_OPTIONS_EXECUTION=false` on the running backend. The first flag independently blocks manual, automatic and Tail Hunter entries even if the older execution flag is accidentally enabled. Existing position reconciliation and closes are deliberately unchanged. This does not cancel previously submitted broker orders or liquidate existing positions.

Authoritative PM `STARTER`/`ACCUMULATE` approvals routed `OPTION`/`BOTH` are saved before the Options Desk display limit, regardless of execution-readiness blockers. Contracts come from the original research packet, not a rebuilt Alpaca selection. Missing contracts remain in the journal as unpriced approvals. Standalone UI refreshes are not new authoritative approvals.

PostgreSQL collection `options_research_approvals` preserves the first observation per cycle/candidate. Markdown exports are rebuildable read models in `backend/data/options-research/YYYY-MM-DD.md` (or `OPTIONS_RESEARCH_DIR`). They contain private research, not credentials, and are excluded from git. An export failure appears in the candidate packet; it does not disable equity execution.

At Sunday 02:50 America/New_York, `options_research_sunday_review` reads Public option chains once per underlying/expiration and updates dated files plus `latest-sunday-review.md`, before the existing 03:00 recalibration. The review never submits orders or adjusts weights. A fresh original two-sided quote (maximum 120 seconds old at observation) is required for an entry ask. Review uses a later timestamped bid, with age disclosed and a three-day weekend allowance. It is a quoted ask-to-bid comparison, not a simulated fill, fees-adjusted backtest, intraday ratchet replay or realized P&L.

Unsupported spreads, missing prices, unavailable expired chains and unverifiable timestamps remain unavailable, never zero-valued losses or wins. Repeated approvals remain identifiable observations, not independent trades. Existing last valid marks are retained after expiration, not treated as expiry settlement values. Historical approvals predating this feature are not automatically invented or backfilled.

The bounded export/review raises an explicit capacity error above 10,000 records rather than silently truncating. Data retention and longer-history review batching will need revisiting before that size is reached.
