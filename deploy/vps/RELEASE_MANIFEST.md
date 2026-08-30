# Case Capital Release Manifest

The VPS is production-ready only when the following values match the tested
release:

- Git commit: recorded by the deployment script after checkout
- Working tree: clean
- Backend: `python -m compileall -q backend`
- Frontend: `npm run build`
- Execution: explicitly configured, never inferred from account type
- Options indicative execution: disabled unless paper-only policy is active
- Telegram: `TELEGRAM_SINGLE_CONSOLIDATED_SCAN_ONLY=true`
- Preview: valid server-side code required; invalid or empty code returns 403

The deploy operator must record the commit, build hash, service status, health
status, and smoke-test results in the release log before marking deployment
successful.
