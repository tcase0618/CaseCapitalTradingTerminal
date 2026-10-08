# Frontend Workbench Audit

## Scope

Frontend-only remediation. No strategy scoring, order submission, portfolio management,
execution gates, or broker configuration changes. The established black/gold/teal
identity, company artwork, scanner scoring displays, and source-backed research remain.

## Findings And Changes

1. All 23 workspaces were eager imports: split into route chunks and prefetch on navigation intent.
2. Navigation remounted shell subscriptions: share display-only GET resources across remounts.
3. Duplicate macro reads: coalesce subscribers onto one in-flight request.
4. Overlapping unbounded requests: use 12-second timeouts and in-flight coalescing.
5. Hidden-tab polling: pause shared resources and Command Center/Scanner polling when hidden.
6. Data loss on refresh failure: retain last loaded values with explicit degraded status.
7. Cache identity: invalidate when operator/preview session identity changes; never persist responses to disk.
8. Hardcoded green system indicators and obsolete stable build label: remove unsupported health claims.
9. Clock labelled NYSE even overnight: label clock-based ET windows, not exchange/broker eligibility.
10. Macro countdown assumed permanent UTC-4: require an offset-bearing timestamp.
11. Decorative route animations and repeated blur/shadow painting: simplify shared surfaces and respect reduced motion.
12. Navigation took excessive space: optional compact rail, searchable workspace/company navigation, focus view.
13. Command Center lacked hierarchy: introduce full-book overview and separate operations view.
14. Executed count fell back to current holdings: unknown remains unknown; only measured funnel counts are executed.
15. Missing daily funnel fell back to latest Core/PM statistics: show unavailable daily data instead.
16. Holdings totals included only the first 12 rows: calculate full-book totals, paginate/filter display separately.
17. Missing stop distance became zero: distinguish unavailable distance from a breached/tight stop.
18. Percentage formatting guessed units by magnitude: use Public percentage-point vs Alpaca fractional contracts explicitly.
19. Empty authoritative positions resurrected fallback holdings: preserve a genuinely empty broker result.
20. Command completion impersonated a trade/Telegram delivery: separate commands from order and dispatch evidence.
21. Scanner had an unused second-by-second clock rerender: remove the subscription.
22. Scanner repeated slow-changing contract/congress/calendar GETs every 15 seconds: bounded display-cache cadences.
23. Read completion could update an unmounted Scanner/Command Center: guard writes and clean up subscriptions.
24. Negative Scanner margins overflowed the revised workspace: constrain the content area.
25. Read cache could hide post-command changes: force display refresh after mutations and on explicit refresh.

## New Controls

Workspace/company search (navigation only), collapsible rail, persisted focus preference,
overview/operations view, searchable/paginated holdings, filtered CSV export with formula
prefix escaping, full-book totals, complete-sync timestamp, responsive tables, route skeletons.

## Verification And Limits

25 frontend tests cover resource coalescing, failure retention, session isolation,
visibility polling, pagination/filtering, full-book totals, return units, source fallback,
route inventory, and evidence display. Production build succeeds. Initial gzip JavaScript
was approximately 422 kB; first split build was approximately 132 kB (69% smaller).
This is bundle size, not a measured 69% reduction in end-to-end page latency.

This pass changes the shared shell across all routes and specifically restructures
Command Center and Scanner loading. It is not a claim that every specialized page,
backend metric, chart, or broker connection has been independently certified. Other pages
still have their own polling implementations. React Router browser behavior is checked
separately because CRA's Jest 27 resolver cannot resolve Router 7's subpath exports.
Combined equity and paper-options market value is explicitly display-only, not live NAV.
Cache timestamps measure successful retrieval, not the freshness of a market quote.
