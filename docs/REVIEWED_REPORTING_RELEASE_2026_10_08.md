# Selected October 8 audit repairs

This is a selective release based directly on main, not a merge of the
Claude-Gpt-audit branch. It deliberately excludes Public order submission,
Public reconciliation, options close/ratchet changes, PostgreSQL changes,
frontend changes, and VPS deployment/configuration changes.

## Included

- Holiday-aware research dates and exact-date close labels; no prior-session
  substitution or same-day premature outcome grading.
- Fix the undefined daily-PNL date and data-quality logger.
- Preserve the scheduled scan trigger, including the 10:30 stale-retry tag.
- Only mark portfolio failure/recovery alerts delivered after successful send.
- Allow degraded/recovered operational alerts under consolidated-scan policy.
- Reject Telegram command handling without a configured chat allowlist.
- Explicitly keep Telegram and QC-remediation Core scans evidence-only.
- Add critical-error/undefined-name lint to CI.

## Verification

354 passed, 12 skipped, 153 deselected in the network-blocked offline suite,
including 11 focused reporting/alert/calendar regressions. Critical-error
lint, compilation and diff checks passed. No frontend source changed.
Broker/network tests are intentionally not execution evidence.

No broker orders or production mutations were used for testing. This release
does not certify the unresolved Public/options execution coordination issues.
Execution repairs and persisted lifecycle regressions are separately committed
on Claude-Gpt-audit at f9822e4; its offline suite has 691 passing tests.

VPS rollout is separate from this GitHub merge. Existing historical outcome
labels are not rewritten; a reviewed historical regrading job is still needed.
