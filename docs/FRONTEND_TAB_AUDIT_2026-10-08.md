# Frontend Tab Audit - October 8, 2026

## Scope

Completed frontend remediation pass on `Claude-Gpt-audit`, including all 23 routed views and principal nested views. No backend, broker, order routing, risk rules, credentials, main-branch changes, or VPS deployment. Existing Core scanner scoring and mutation handlers remain unchanged. Browser API responses were isolated synthetic fixtures; external requests were blocked. This report is not a live-data, execution-readiness, or zero-defect certification.

## Implemented

- Shared GET display cache: bounded idle retention, lazy expiry, queued/active cancellation, session isolation, subscriber-specific polling cadence, and failure backoff.
- Command Center: daily funnel counts no longer borrow latest-cycle statistics; missing marks remain unknown; tables have local scrolling; labels wrap; launch dialog is bounded, scrollable, and keyboard accessible.
- Scanner: scheduler-backed countdown, first-mount subscription publishing, refresh coalescing, selection cancellation, stored-snapshot labels, Case Court availability explanation, and pagination beyond 80 family rows.
- Scanner mobile calendar: preserved seven columns despite the legacy blanket grid rule; compact two-column subtab selector. Large counts retain exact values and tooltips.
- Performance: nine cached display reads, range-specific keys, visible failure warnings, responsive grids, missing price-source handling, and immediate chart updates without draw animation.
- Learning: five cached display reads, invalid-list detection, unavailable-data warning, and guarded list rendering. No active-weight changes.
- Settings: eight cached display reads, guarded nested lists, source-specific failure warnings, and scoped responsive grids. Existing operator actions unchanged.
- Shared shell: Focus view unmounts news subscriptions rather than only hiding their DOM.
- Intel: missing FRED availability is UNKNOWN, not fabricated ONLINE.
- Error boundary: diagnostic text and panel constrained to the viewport.

## Verification

- Frontend unit suite: 160 tests passed across 20 suites.
- Production build: passed. Node emitted its existing `fs.F_OK` deprecation warning.
- Browser: all 23 routes and principal nested views rendered at 390px and 1440px, 136 checks total, with no error-boundary failure or page-level overflow. Screenshot review subsequently identified fragmented mobile KPI values; a minimum-width wrapping repair and regression assertion were added. Final repeat is recorded below.
- Scanner: all eight subtabs exercised; 183 Lottery fixture rows accessible by pagination, including row 80 on page two. Calendar verified with a four-digit new-ticker count.
- Command Center: populated funnel and 25-position table, missing values, empty state, mobile launch dialog, focus trapping, and Escape close checked.
- Performance: three populated charts and five drawn curve paths checked on mobile.
- No mutation requests were sent during browser checks.
- `git diff --check` passed.

Browser screenshots are local generated artifacts in `C:/Users/tcase/.codex/tmp/frontend-final-audit/`; final JSON is `C:/Users/tcase/AppData/Local/Temp/case-capital-ui-audit.json`. Reproduction script: `frontend/scripts/fixture-ui-audit.cjs`, run after `npm run build`, with `PLAYWRIGHT_MODULE_PATH` pointing to the bundled Playwright module. The script owns and closes a temporary loopback server. No percentage speed improvement is claimed.

## Completed Deep-Tab Pass

The prior backlog received source review, targeted tests, and nested-view checks:

| Group | Repairs and coverage |
| --- | --- |
| Portfolio Manager / Trade Floor / Options Desk | Bounded cancellable reads, plan-generation ownership, retained last-good data, unavailable-source warnings, current selected records, legacy Alpaca notice, false close-success message removed |
| Lottery / Pharma | All principal views guarded; calendar races and empty-day selection fixed; original side-effecting GET cadence preserved; 30-row Lottery pagination exposes the full board |
| Kronos | All five views isolated; safe proof reads cached; original context cadence guarded; chart cancellation; stale duplicate setters removed; 80-row disagreement pagination |
| Quality / Truth Review | Non-overlapping cancellable overview reads; safe auxiliary cache reads; unknown/malformed states and local table bounds |
| Intel / Contracts / SEC / Earnings / GeoRisk / Macro | Partial-source retention, finite timeouts, keyboard controls, malformed lists, calendar/filter races, responsive layouts; GeoRisk no-match filter no longer shows all news |
| Learning Engine / Case Court / Audit Logs / Company Profile | Cancelled superseded requests; safe display caches; numeric strings; old-company responses ignored; evidence-source and selected-record guards |
| Trade Journal | Three parallel cancellable reads, retained partial sources, guarded lists across five equity views plus options/equities, missing totals remain unknown |

## Evidence Limits

Many browser fixtures are empty or partial; selected charts and diagrams use populated synthetic data. This is not every possible live payload or manual action. Intel feeds do not all supply individual refresh timestamps; retained sources may differ in age. No sustained-load benchmark or live production certification was performed. Changes remain local and uncommitted.

The legacy global mobile rule that collapses inline grids still exists. The tested calendar, funnel, and Scanner selector have targeted overrides; other fixed-format diagrams require individual verification before changing that broad rule.

`GET /scan/tabs` can persist specialist data on a backend cache miss. This work did not introduce a forced retry against that endpoint. A frontend GET is not proof of a side-effect-free backend operation.

The planned frontend pass is complete; no claim is made that every live payload is compatible, no further defects exist, or the VPS is running these changes.

## Final Repeat

Final browser repeat after the screenshot-driven mobile KPI correction: **136 checks passed; zero failures; zero mutation requests**. The added Macro KPI assertion verifies the availability value stays on one line at 390px. Mobile Macro/Contracts and desktop Contracts/Kronos/PM screenshots were visually inspected. Final optimized build passed without ESLint warnings.

Local static preview: `http://127.0.0.1:3002`, served by `frontend/scripts/preview-build.cjs`. HTTP 200 verified. Unlike the isolated fixture audit, this preview uses the build's configured backend; its availability is not a live backend health check. No frontend-preview operator action was taken.
