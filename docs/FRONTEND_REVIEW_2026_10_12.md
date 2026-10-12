# Frontend Review - October 12

Status: implemented and locally verified; not pushed, merged or deployed.
Base: main 39ebd8b. Branch: codex/frontend-review-oct12. Pending execution repairs were not included.

## Six-Agent Work

1. Innovation: ranked improvements using existing data, without inventing confidence or performance.
2. Features: mapped all 23 routes and inspected declared nested views against backend response contracts.
3. Testing: added independent navigation, accessibility and missing/zero/stale-value regressions.
4. Overlap/flow: reviewed mobile grids, repeated displays, navigation identity and refresh behavior.
5. Shell builder: navigation/search/drawer/error recovery and responsive calendar rules.
6. Page builder: Lottery, Options, Pharma and PM layouts, sorting, filtering and data-state repairs.

Parent integration added source-status, profile forecast and research-display repairs, then ran the combined suite and offline browser checks.

## Implemented

- Drawer focus management, Escape handling, focus restoration and inert background content.
- Accessible workspace search and page aliases, safe stored display preferences, correct company-profile navigation identity.
- Mobile seven-column PM calendars and scoped responsive overflow safeguards.
- Options filtering/sorting/keyboard selection, Pharma score sorting and clearer Lottery paper-ticket labels.
- PM source failures no longer manufacture zero positions/orders or a confirmed non-paper account.
- PM connection diagnostics do not claim execution authorization.
- PM calendar labels describe signal/proxy observations, not fund P/L. Weekly summaries no longer sum cumulative observations into a return.
- Command Center exposes each display source's unavailable, cached-failure and retrieval state. Retrieval timestamps are not labelled quote freshness.
- Company profiles consume actual Kronos horizon arrays. Removed locally invented forecast cones; missing/failed forecasts remain unavailable. Missing PM alignment is unknown.
- Profile options/flow/free-data reads distinguish loading, unavailable and no returned data.
- Learning/Performance counts retain missing values rather than substituting zero.
- GeoRisk cannot claim clear exposure when event/overlay sources failed.
- Saved strategy evidence has a read-only retry plus local strategy/mode filters. It never calls report generation or order endpoints.
- Legacy Performance detail is collapsed and defers row rendering until opened.
- Stabilized Trade Engine derived combo memoization, removing its build hook warning.

Core scanner criteria, strategy scores, orders, mutation payloads, authorization and backend files were not changed. The established visual identity and all registered pages remain.

## Verified

- Jest: 24 suites, 204 tests passed.
- Production build: compiled successfully. Main JS approximately 133.48 kB gzip; no before/after speedup claim is made.
- git diff --check passed.
- Offline Playwright/installed Chrome: 23 routes and 26 named subtab states at 1440x1000 and 390x844, 98 states total. No page errors, workspace error-boundary failures, page-level horizontal overflow, or mutation requests in those fixtures.
- Additional PM calendar sweep: 16 states passed; weekday grid remained seven columns at both widths. Screenshots were visually inspected for Command Center, scanner, PM and calendar.

Browser assets were served by intercepted requests from the local production build; API responses were explicit local fixtures. No actual HTTP server or production backend was used. External chart/widget resources were deliberately blocked, so their live rendering remains unverified. Browser checks do not prove every populated-data layout, real prices, source freshness, backend synchronization or execution readiness.

Local evidence: C:/Users/tcase/Documents/Codex/2026-10-12/frontend-browser-evidence/results.json, calendar-results.json and PNGs. Reproducible harness: C:/Users/tcase/Documents/Codex/2026-10-12/frontend-review-browser.cjs.

## Remaining Gaps

- Global reload still resets local workflow selections; navigation-state persistence/display-only refresh needs a separate coherent implementation across legacy and shared-resource pages.
- Sidebar group taxonomy and search group taxonomy are not yet unified; labels/aliases were improved without silently moving existing workspaces.
- Read-only PM portfolio/policy and signal-to-order reconciliation endpoints remain candidates for dedicated views. Account scope and metric meaning should be reviewed before exposing them as investor performance.
- Large populated datasets, real backend latency, external market charts and live authentication require controlled staging/production read verification. No production claims are supported by these local fixtures.

Provisional source/layout assessment: improved from the reviewers' 6/10 baseline to approximately 7/10; this is qualitative, not a benchmark or certification. Reaching a stronger rating requires the remaining workflow fixes and real-data verification, not more decorative UI.
