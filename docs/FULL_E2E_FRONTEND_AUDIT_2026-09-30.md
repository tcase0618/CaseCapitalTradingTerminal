# Case Capital Terminal: Full End-to-End + Frontend Audit

Date: 2026-09-30
Branch: `claude/full-audit-e2e-frontend-ljihpi`
Base commit audited: `eb2c018` ("Split critical and management position monitoring")
Method: static code review, `py_compile`, pytest, production frontend build, contract diff of frontend calls vs backend routes, FastAPI TestClient probes, and a Playwright/Chromium sweep of every route at 1440px and 390px against a **stubbed** API.

No source code was changed by this audit. This file is the only addition.

---

## 0. Read this first: what this audit does and does not prove

| Not verified | Why | Consequence |
|---|---|---|
| **VPS state** (deployed commit, `systemctl`, `/api/status`, `/api/data_quality/overview`, etc.) | No route to `129.121.101.96` was attempted; I deliberately did not touch the live terminal. | Every finding is about the **code at `eb2c018`**. Whether the VPS runs that commit, and what its `.env` contains, is unverified. |
| **Live broker behaviour** (Public, Alpaca) | No credentials, no network path. | Order-path findings are code-read, not observed fills. |
| **Real Postgres** | No DSN. 5 `postgres_integration` tests were deselected. | Data-layer performance claims are from code, not measurement. |
| **Real-data UI rendering** | The browser sweep used a stub returning `{}` / HTTP 500 / 403 / connection-refused. | Crash findings show *fragility to malformed/partial payloads*, not "the page is broken in production". Mobile readability with real data is **not** verified; only "no horizontal drift on empty state" is. |
| **Tauri desktop build** | Rust toolchain not built. | Desktop findings are code-read. |
| **Telegram delivery** | Per AGENTS.md, no live sends. | Handler findings are code-read. |

Each finding below is tagged **[RUN]** (reproduced by executing code/browser), **[TEST]** (reproduced with a test harness on the real module), or **[READ]** (established by reading code/grep only).

---

## 1. Executive verdict

The mechanical health is good: everything compiles, the frontend builds with zero warnings, all 148 static API calls in the UI resolve to real backend routes, and the options path has genuine defense in depth.

The serious problem is **drift between what the safety UI and docs say and what the code enforces**, now that Public (real money) is the sole equity broker and Alpaca equity is retired:

1. The **daily-loss circuit breaker measures the Alpaca account, not Public** (F-01).
2. The **central execution gate and its kill switches are not consulted before Public equity orders** (F-02).
3. The Trade Floor **CLOSE button always fails and reports success** (F-03).
4. The sidebar **"SYSTEM HEALTH: ENGINE OK / LEARN OK / BOT OK / FEED OK" is hard-coded** and stays green while every API call returns 500 (F-04).
5. The **HALT button fails silently** (F-05).
6. Auth tokens and the access code travel over **plaintext HTTP** (F-06).

AGENTS.md's non-negotiable is "never fabricate ... API health; report stale, unavailable, partial, or unverified data explicitly." F-03, F-04, F-05 and F-14 violate that in the UI today.

Counts: 6 High, 13 Medium, 10 Low.

---

## 2. Verification results

| Check (per AGENTS.md) | Result |
|---|---|
| `python -m py_compile` on all tracked `backend/*.py` | **PASS** |
| `npm install --legacy-peer-deps` + `npm run build` | **PASS**, "Compiled successfully", 0 warnings. Main bundle 421.7 kB gzip, CSS 21 kB. Note: `npm install` rewrote `frontend/package-lock.json` (lock is out of sync with `package.json`); I reverted it. |
| `pytest backend/tests` in a clean venv from `requirements.txt` | **2 failed, 270 passed, 12 skipped, 153 deselected, 1 collection error** (F-19). |
| Same, after `pip install pymongo` | **272 passed, 13 skipped, 153 deselected.** |
| Deselected tests | 148 `external_integration`, 5 `postgres_integration`, 0 `live_destructive`. **Never ran here.** |
| Frontend → backend contract | 148 distinct static `axios` calls, **0 unmatched**. 4 dynamic calls not statically resolvable. |
| Backend → frontend coverage | 97 of 267 routes are never referenced by UI source (59 are GETs), including `/public/status`, `/public/execution/analytics`, `/public/price-stream`, `/readiness/overview`, `/pm/portfolio/latest`. |
| Browser sweep, 26 routes x 2 viewports, empty-state | No horizontal overflow at 390px on any of the 24 real routes (plus the 404 URL). 6 routes crash into the error boundary (F-13). |
| Frontend tests / CI | **None.** No `*.test.*` files, no `.github/`. |

---

## 3. Findings

### HIGH

**F-01 · Daily-loss breaker watches the wrong account** [READ]
- `services/safety.py:138-193` `check_daily_loss()` defaults to `trade_floor.get_account()`, which calls Alpaca `/v2/account` using `APCA_*` credentials.
- The only live caller, `services/scheduler.py:734`, passes no account. The other caller (`trade_floor.py:1072`) is on the retired path.
- Public is "the sole equity broker" (`terminal_cycle.py:341-347`). A Public drawdown never trips the 3% (`DAILY_LOSS_HALT_PCT`) halt. The options account (`OPTIONS_APCA_*`) is not measured either.
- Also fail-open: if Alpaca is unconfigured or unreachable it returns `{"ok": False, "reason": "current_equity_unavailable"}` and does **not** halt.
- The shell strip shows "DAILY DD x% / 3%" (`CrtShell.jsx:354`), which reads as protecting the live account.
- Fix: feed `check_daily_loss` the Public equity book (`public_execution.portfolio_state()`), add the options account, and treat "equity unavailable for N minutes while positions are open" as a halt or a hard warning.

**F-02 · Public equity orders bypass the central execution gate and kill switches** [READ, grep-proven]
- `execution_gate.check()` enforces `GLOBAL_EXECUTION_KILL`, `EQUITY_EXECUTION_KILL`, `OPTIONS_EXECUTION_KILL`, `QC_EXECUTION_KILL`, `TICKER_KILL_LIST`, `SECTOR_KILL_LIST`, and data-truth/QC blockers (`execution_gate.py:33-40, 182-243`).
- It is called from `options_desk.py` (1667, 1870), readiness/truth-review reporting, and `trade_floor.py:1049,1118`, which is unreachable because the function returns early via `_legacy_equity_execution_disabled()`.
- It is **not** called in `public_execution.execute_pm_equity()` (`public_execution.py:1172-1362`) or `terminal_cycle.py:329-352`. `public_execution.py` has zero references to `execution_gate`, kill lists, or truth grade.
- So setting `EQUITY_EXECUTION_KILL=true` or adding a ticker to `TICKER_KILL_LIST` does **not** stop a Public buy. Only `ENABLE_TRADE_EXECUTION`, the DB halt from `/admin/halt` (via `add_risk_allowed`), reconciliation health, the halts feed, and quote freshness do.
- The gate's `equity` scope also evaluates Alpaca (`data_quality.py:319-333` marks `blocks_trading` on the Alpaca probe), so the QC screens can show equity "blocked" for the wrong broker while Public trades regardless.
- Fix: call `execution_gate.check(scope="equity", ticker=...)` per row inside `execute_pm_equity`, and point the equity scope at Public evidence.

**F-03 · Trade Floor CLOSE button always fails and says it succeeded** [READ, both ends]
- `TradeFloorPage.jsx:92-97`: `await axios.post(.../trade_floor/close...).catch(() => {}); toast("Close request sent for X")`.
- `server.py:2532-2534`: `/trade_floor/close` unconditionally raises HTTP 410 ("Alpaca equity execution is retired").
- Result: the operator sees "Close request sent" and nothing happens. There is no other UI path that closes a Public position.
- Fix: surface the error; route the button to a Public exit (`submit_exit_to_cash`) or remove it.

**F-04 · Fake system-health indicators** [RUN]
- `CrtShell.jsx:707-710` renders `ENGINE OK / LEARN OK / BOT OK / FEED OK` with hard-coded colours and a static "OK" (`StatusRow`, line 1240). `CrtShell.jsx:220-222` renders static green/teal/amber dots for FEED/LEARNING/BOT.
- Reproduced: with every API call returning HTTP 500, the sidebar still shows all four "OK".
- Fix: bind to `/api/status`, `/api/scheduler/overview`, `/api/data_quality/overview`; show `SYNC`/`DOWN` on failure. (The execution strip itself correctly shows `SYNC` when unknown, which is the right pattern.)

**F-05 · HALT / RESUME fail silently** [READ]
- `CrtShell.jsx:358-368`: `await axios.post(.../admin/halt ...)` has no `try/catch`, no toast, and the follow-up `safety.refresh()` never runs on failure. A 403, 500 or timeout on the kill switch gives the operator no signal.
- The confirm text says "Halt all new **paper-trading** execution?", but the equity path is real-money Public.
- Fix: catch, toast success/failure, always refresh, and correct the copy.

**F-06 · Credentials and session token over plaintext HTTP** [READ]
- `frontend/src/config.js:2` hard-codes `http://129.121.101.96`; `nginx-case-capital.conf` listens on port 80 only; `bootstrap-ubuntu-24.sh` opens `Nginx Full` (443) but nothing serves TLS.
- The access code (`/auth/login`), the 7-day bearer token, and every trading payload cross the network unencrypted. Anyone on-path can replay the token.
- Related: `crypto.subtle` is unavailable on non-secure origins, so `hashCode()` falls back to a non-cryptographic 64-bit hash (`StartupGate.jsx:538-546`), which never matches the preview hashes (F-08).
- Fix: TLS (Let's Encrypt or a domain), redirect 80 to 443, HSTS, and switch `BACKEND_BASE_URL` to `https`.

### MEDIUM

**F-07 · Telegram command surface can be open** [READ]
- `/api/telegram/webhook` is auth-exempt (`server.py:49-55`). The secret check is skipped when `TELEGRAM_WEBHOOK_SECRET` is blank (`server.py:2124-2128`), and the chat allow-list is skipped when `TELEGRAM_CHAT_ID` is blank (`telegram_service.py:811-818`). Both are blank in `backend/.env.example`; `deploy/vps/cloud.env.example` leaves `TELEGRAM_CHAT_ID` blank and does not list `TELEGRAM_WEBHOOK_SECRET` at all.
- If both are unset on the VPS, any HTTP POST can run `/positions`, `/account`, `/add`, `/remove`, `/scan`, `/scan_gov`, `/analyze` (spends Claude credits), `/alert`, `/backtest_seed`. I found no order-placing commands.
- **Unverified:** whether the VPS has these set. Check now.
- Fix: refuse to start the webhook route unless the secret is set, and fail-closed when the chat id is empty.

**F-08 · Preview mode is non-functional in cloud mode, and its codes are published** [TEST + READ]
- Reproduced with FastAPI TestClient (`APP_ENV=cloud`): `POST /api/auth/preview {"code":"6969"}` returns 200 with **no token**; then `GET /api/admin/trading_status` returns 403 "Operator session required. Preview mode is read-only." Every data GET fails.
- The UI then treats the 403 as an expired session (F-09), so the "READ ONLY preview" cannot read anything on the VPS.
- `StartupGate.jsx:241` prints the codes to the user: "Enter a preview code: 6969 or 0209." The raw codes are also in source comments (`StartupGate.jsx:21-22`), and the hashes (unsalted SHA-256 of 4-digit PINs) are duplicated in `server.py:56-60`. The "keep raw codes out of the bundle" comment is contradicted by the UI text.
- Fix: either issue a scoped read-only token or remove preview from cloud builds; delete the hint text.

**F-09 · Cold load shows a false "session expired" error** [RUN]
- On a fresh load in cloud mode with no session, `waitForTerminalData` calls `GET /api/desktop/diagnostics` (`StartupGate.jsx:634`), which is not in `AUTH_EXEMPT_PATHS`, so it returns 403. The global interceptor (`StartupGate.jsx:80-96`) reacts by skipping the boot splash and displaying "Operator session expired after backend restart. Enter the access code again." Verified on desktop and 390px screenshots. The loop re-fires every 700 ms for 14 s.
- Fix: exempt `/api/desktop/diagnostics` (or make the probe use only `/api/status`), and only show the expiry message when a session token was actually present.

**F-10 · `data_quality.py:337` `NameError: logger`** [TEST]
- The module never defines `logger` (verified: `hasattr(data_quality, "logger") == False`). It is used inside the `except Exception as critical_exc:` branch that runs when the 12 s integration probe times out **and** the independent Alpaca probe fails, i.e. exactly when QC is degraded. The NameError escapes the handler and aborts the QC refresh that feeds the execution-gate truth snapshot.
- Fix: `logger = logging.getLogger(__name__)`.

**F-11 · `pnl_tracker.py:703` `NameError: today`** [TEST]
- `daily_pnl_curve()` uses `today` but never defines it (it is defined in other functions). Reproduced with a fake DB and price history: `NameError: name 'today' is not defined`. Feeds `GET /api/signals/curve` (`server.py:1035-1040`) and `daily_total_vs_spy_curve` (`pnl_tracker.py:791`), so the Performance curve and vs-SPY chart fail whenever any position has price history.
- Fix: `today = _today_et().isoformat()`.

**F-12 · Advertised Public caps are not enforced** [READ, grep-proven]
- `PUBLIC_MAX_ORDER_USD` (5) and `PUBLIC_MAX_ACCOUNT_USD` (100) are read and echoed by `/public/status` (`public_api.py:71-72, 92-93`) and nowhere else. `submit_equity_order` and `execute_pm_equity` never compare against them (same for `ROBINHOOD_*`).
- Mitigation that does exist: PM sizing is percent-of-Public-equity with gross and sector caps (`portfolio_manager.py:258-282, 590-620`), plus a buying-power check. So order size is bounded, but by different controls than the ones the config and status endpoint imply.
- Fix: enforce the caps in `submit_equity_order`, or stop advertising them.

**F-13 · Frontend fragility to partial/malformed payloads** [RUN, synthetic stub]
- With a stub returning `{}` for every endpoint: `/` (Command Center, the landing page), `/scanner`, `/learning`, `/performance`, `/settings` (and `/research`, which redirects to it) throw (`(t || []).map is not a function`, `p.map`, `Y.map`, `undefined.toUpperCase`, `undefined.map`) and fall into `TerminalErrorBoundary`.
- With HTTP 500 or connection-refused on every call: `/trade-floor`, `/portfolio-manager`, `/quality`, `/macro`, `/audit-logs`, `/truth-review` emit **unhandled promise rejections** (`Network Error` / `status code 500`).
- Caveat: `{}` is not a payload the real backend emits. But the backend does return `{"ok": false, ...}` shapes for failures, and `Array.isArray` guards are missing, so an error-shaped response to a list endpoint would take out the landing page.
- Fix: normalise responses at one boundary (`asArray`, `asObject`), add `.catch` with an error state on every page.

**F-14 · Failure renders as "no data / no positions"** [RUN + READ]
- `TradeFloorPage.jsx:30-37`: four `axios.get().then()` calls with no `.catch`. On failure the page shows `LIVE POS 0`, `CASH $0`, "No open positions.", and a red **"ALPACA NOT CONFIGURED — set APCA_API_KEY_ID..."** banner (reproduced under HTTP 500 and `{}`), because `acctReady` is falsy when the request merely failed.
- Backend does the same: `trade_floor.list_positions()/list_orders()` return `[]` and `get_account()` returns `None` on any error (`trade_floor.py:246-280`). Telegram `/positions` then replies "No open Trade Floor positions" (`telegram_service.py:826-830`) and `/account` uses the same Alpaca-only source. An outage is indistinguishable from flat.
- The page is titled "TRADE FLOOR · ALPACA PAPER" and lists Alpaca positions; Public positions are not shown here.
- Also: `TradeFloorPage.jsx` contains invalid UTF-8 (byte `0xb7`, lines 141/156/161). The shipped bundle contains `�`, so the UI renders "PENDING ORDERS � N" and "�" in empty cells. Same defect in the `legacy/` copy.
- Fix: distinguish `unavailable` from `empty` end-to-end; re-save the file as UTF-8.

**F-15 · Build and deploy are not reproducible** [RUN]
- `npm ci` (used by `bootstrap-ubuntu-24.sh`) fails with `ERESOLVE` (`react-day-picker@8.10.1` vs `date-fns@4`); it only works with `--legacy-peer-deps`. Under `set -e` the bootstrap aborts at the frontend step.
- `frontend/Dockerfile` runs `yarn install` with no lockfile and no legacy flag; `package.json` says `packageManager: yarn` but the only lock is `package-lock.json`.
- `REACT_APP_BACKEND_URL=` (bootstrap) and `REACT_APP_BACKEND_URL: ""` (docker-compose) are empty strings, which `config.js:4-7` treats as unset (`||`) and falls back to `http://129.121.101.96`. The same-origin `"/api"` branch (`config.js:8`) is unreachable. A local `docker compose up` frontend therefore talks to the **production VPS**, not the local backend.
- Fix: use `??` and an explicit sentinel; add `--legacy-peer-deps` (or fix the peer conflict) everywhere; commit one lockfile.

**F-16 · Auth model weaknesses** [READ + TEST]
- Access code hash is a single unsalted SHA-256 with a fixed prefix (`server.py:71-78`); signed tokens are stateless (no revocation), last 7 days, and the signing secret falls back to the password hash when `TERMINAL_SESSION_SECRET` is unset (`server.py:94-95`). Token is stored in `sessionStorage` and sent as a bearer header.
- Default is open: with `APP_ENV` unset and no access code configured, every mutating endpoint is unauthenticated (reproduced: `POST /api/admin/halt` in local mode reached the handler). `docker-compose.yml` publishes `8001` on all interfaces and does not set `APP_ENV=cloud`.
- CORS is `allow_credentials=True` with `*` outside cloud mode.
- Login rate limiting keys on `request.client.host` (`server.py:151-153`). Behind nginx that is the proxy address unless uvicorn's proxy-header handling is configured; **unverified** how the VPS behaves.
- Fix: bind compose to `127.0.0.1`, require `APP_ENV`+code at startup for non-loopback binds, use a KDF, add a `jti`/revocation list.

**F-17 · Session-clock and calendar inconsistencies** [READ]
- Scheduler uses the Alpaca `/v2/calendar` and fails closed (`scheduler.py:70-100`). Good. But it requires Alpaca credentials even though Alpaca equity is retired: if they lapse, every scheduled scan is held.
- `public_execution._public_session_now()` (`public_execution.py:707-712`) picks `CORE` from weekday + clock only; no holiday or early-close handling. `CrtShell.jsx:111-122` `getMarketStatus()` shows "NYSE · LIVE" on weekday holidays. `CrtShell.jsx:127` hard-codes `-04:00` (EDT), wrong by one hour in winter.
- Fix: one calendar service consumed by scheduler, Public session selection, and the UI.

**F-18 · Data layer scales with collection size** [READ]
- `postgres_store.py:296-345`: only equality-filter + single temporal sort + limit is executed in SQL. Anything else (`$` operators, dotted keys, non-temporal sort, no limit) runs `select payload ... where collection=$1` and filters in Python. SQL is parameterised (no injection found). Append-only evidence collections (per AGENTS.md) grow unbounded and there is no retention policy (carried over from prior audit F-27).
- Fix: retention/TTL by collection, and push more predicates into JSONB SQL.

**F-19 · Test suite is not hermetic and has blind spots** [RUN]
- `pymongo` is imported by tests (`test_execution_safety.py:28`, `test_v13_axiom_v52.py:20`, `conftest.py`) but is not in `requirements.txt`, so a clean install yields 2 failures (the duplicate-order-intent tests) and 1 collection error. Production code degrades gracefully (`execution_safety.py:97-101`); the tests don't.
- 148 external-integration and 5 Postgres tests are deselected by default; 0 tests are marked `live_destructive`; no frontend tests; no CI.
- The Public execution path (`execute_pm_equity`) is covered only by helper-level tests.

### LOW

| ID | Finding | Evidence |
|---|---|---|
| F-20 | Duplicate definitions shadow earlier ones in `pharma.py`: `fetch_pdufa_calendar` (252 shadowed by 517) and `_parse_pdufa_html` (313 shadowed by 437). Curated PDUFA seed fallback has every date on or before today; it is labelled `curated_seed`/`fallback_calendar`, but would present past events as upcoming if live sources fail. | flake8 F811 [TEST] |
| F-21 | Duplicate Telegram handlers: `/risk` (866, 1455) and `/contracts` (926, 1402). The second copies are unreachable. | grep [READ] |
| F-22 | No 404 route: unknown URLs render a blank page (text length 0). | browser [RUN] |
| F-23 | UI labels hard-coded: `BUILD 3.2.0 · STABLE` (`CrtShell.jsx:719`) vs Tauri `0.1.3`. | [READ] |
| F-24 | Pollers never pause on hidden tabs. `CrtShell` polls `execution_gate/overview` every 15 s per open tab (which triggers a data-truth computation); 75 silent `.catch(()=>...)` sites across 18 files. | [READ] |
| F-25 | Tauri: `"csp": null` (`tauri.conf.json`); `backend_request` interpolates `method` and `path` into the HTTP request line with no CR/LF validation (`lib.rs:109-120, 281-285`); the bridge does not forward `Authorization`, so a local desktop backend in cloud/auth mode returns 403. | [READ] |
| F-26 | 46k-line duplicate `frontend/legacy/pre-fable-frontend-20260802/` committed (not built, still greppable, and carries the same invalid-UTF-8 file). | [READ] |
| F-27 | `claim_execution_intent` expired-lease reclaim is find-then-update, not atomic (`execution_safety.py:74-114`); two workers could both reclaim. Very narrow window (24 h TTL). Deprecated `@app.on_event` hooks. | [READ] |
| F-28 | `setup-postgres.sh` interpolates the password into SQL and a DSN without escaping/URL-encoding. | [READ] |
| F-29 | Legacy Alpaca equity code (`trade_floor.py`, ~1.9k lines, `_alpaca_market_sell` in `trade_floor_phases.py`) is retained behind a single constant. Dead today; risky if that flag is ever flipped. | [READ] |

---

## 4. Documentation drift (affects how the next person reasons about safety)

- `AGENTS.md`: calls this a "paper-trading terminal" and says "Execution authority must use fresh Alpaca position and order data." In code, **Public is the sole equity broker** (real money; `PUBLIC_LIVE_EQUITY_ENABLED`) and Alpaca is options-only. Alpaca equity endpoints return 410. The doc should say which broker is authoritative for equity, and that Public has no paper mode.
- `docs/CASE_CAPITAL_SCHEDULE.md` (generated at `8ee0b39`) omits the 10:30 ET `stale_retry_scan_1030` full terminal cycle (`scheduler.py:45`); `schedule_control.py:38` label omits it too; "Position monitor: Alpaca positions" is stale.
- `docs/FULL_TERMINAL_AUDIT_2026-08-30.md` F-26 says Mongo is primary; the code is Postgres-backed with a Mongo-style shim.
- `.env.example` defaults `ENABLE_OPTIONS_EXECUTION=true` while equity defaults to `false`.

---

## 5. What is solid (verified)

- No secrets committed; `.gitignore` covers `.env`, `*.pem`, tokens (regex scan of tracked files found none).
- Options execution (`options_desk.execute`, 1657-1800) is genuinely layered: safety halt, execution gate, account-route guard, paper-only check, market-open check, execution-grade data, fresh preflight, midpoint-capped limit, per-ticket risk budget, daily premium cap, exposure block, duplicate client-order-id (local + broker), limit/day only.
- Public entry path has real controls: reconciliation health, DB halt, buy-permission, cash buying power, halts feed fail-closed, two quote-freshness checks with a 1% drift guard, protective-stop validation, preflight before place, idempotent client order id + intent claim.
- Public mutation is blocked unless both `PUBLIC_LIVE_EQUITY_ENABLED` is true and `PUBLIC_RESEARCH_ONLY` is false (`public_api.py:570-575`).
- SQL is parameterised; Telegram webhook secret uses `hmac.compare_digest`; access-code and token comparisons are constant-time; HTML parse-mode has a plain-text fallback.
- Scheduler jobs use `max_instances=1`, `coalesce`, `misfire_grace_time`; stock scans fail closed on calendar errors.
- All 148 static frontend API calls resolve to real routes; no horizontal drift at 390px on any of the 24 real routes in the empty state.

---

## 6. Recommended order of work

1. **Safety parity (F-01, F-02, F-12):** point the breaker and gate at Public; enforce or drop the caps. This is the only group that can lose money.
2. **Truthful UI (F-03, F-04, F-05, F-14):** real health bindings, error states, working close/halt feedback.
3. **Transport and access (F-06, F-07, F-08, F-09, F-16):** TLS, fail-closed Telegram, fix preview/cold-load.
4. **Two one-line crashers (F-10, F-11)** and the UTF-8 file (F-14).
5. **Reproducible build and CI (F-15, F-19):** `--legacy-peer-deps`/peer fix, `pymongo` in a dev requirements file, GitHub Actions running compile + pytest + build.
6. Everything else.

## 7. Before you trust this report: five checks to run on the VPS

```
git -C /opt/case-capital/stock-intel log -1 --oneline        # is it eb2c018 or later?
grep -c '^TELEGRAM_WEBHOOK_SECRET=.\+' backend/.env; grep -c '^TELEGRAM_CHAT_ID=.\+' backend/.env
grep '^APP_ENV=' backend/.env                                # must be cloud
curl -s http://127.0.0.1:8001/api/admin/trading_status       # which account does the breaker report?
curl -s http://127.0.0.1:8001/api/public/status              # caps advertised vs enforced
```
