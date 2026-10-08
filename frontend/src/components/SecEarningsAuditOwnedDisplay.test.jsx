import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import SECPage from "./SECPage";
import EarningsPage from "./EarningsPage";
import AuditLogsPage from "./AuditLogsPage";
import useDisplayResource from "../hooks/useDisplayResource";

jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("../hooks/useDisplayResource", () => ({ __esModule: true, default: jest.fn() }));
jest.mock("react-router-dom", () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
jest.mock("./CrtShell", () => ({
  tokens: { accent: "#c8a84b", accent2: "#00f5d4", dim: "#777", muted: "#999", labelLight: "#ddd", hairline: "1px solid #333", cardBg: "#08090d" },
  CrtShell: ({ children, title, headerRight }) => <main><h1>{title}</h1>{headerRight}{children}</main>,
  Card: ({ children, title }) => <section><h2>{title}</h2>{children}</section>,
  Stat: ({ label, value }) => <div data-stat={label}>{label}: {value}</div>,
}));

let host, root, snapshot;
const refresh = jest.fn();
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  snapshot = { data: null, error: null, loading: true, refreshing: false, refresh };
  useDisplayResource.mockImplementation(() => snapshot);
  axios.get.mockResolvedValue({ data: { by_day: {}, total: 0, earnings_divergences: [], ok: true } });
  axios.post.mockResolvedValue({ data: { ok: true, routed: 2, divergence_count: 3 } });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const mount = async Component => { await act(async () => root.render(<Component />)); };
const click = async el => { await act(async () => el.dispatchEvent(new MouseEvent("click", { bubbles: true }))); };
const button = text => [...host.querySelectorAll("button")].find(el => el.textContent.includes(text));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test("SEC rejects malformed filing collections and labels data partial, not live", async () => {
  snapshot = { ...snapshot, data: { filings: { ticker: "BAD" } }, loading: false };
  await mount(SECPage);
  expect(host.textContent).toContain("SEC response is incomplete");
  expect(host.querySelector('[data-stat="SEC SOURCE"]').textContent).toContain("PARTIAL");
  expect(useDisplayResource).toHaveBeenCalledWith(expect.stringContaining("/sec/filings?days=7"), 60000);
  expect(axios.post).not.toHaveBeenCalled();
});

test("SEC detail guards nested arrays and displays missing metrics without zeros", async () => {
  snapshot = { ...snapshot, loading: false, data: { filings: [null, { ticker: "abc", form: "8-K", concurrent_signals: {}, accepted_at: 17 }] } };
  axios.get.mockResolvedValue({ data: { ticker: "ABC", history: [null, { form: "8-K", reaction_30d: {} }], risk_language: [null, { form: "8-K", terms: {} }], edgartools: { entity: { flags: [null, "issuer"] }, latest_filings: {} } } });
  await mount(SECPage);
  await click(host.querySelector('[data-testid="sec-abc"]'));
  expect(host.textContent).toContain("EXPECTED EFFECT");
  expect(host.textContent).not.toContain("+0.00%");
  await click(button("SEC BATTLE CARD"));
  expect(host.textContent).toContain("CLUSTER UNKNOWN");
  expect(host.textContent).toContain("ISSUER");
  expect(axios.get).toHaveBeenCalledWith(expect.stringContaining("/sec/battle_card/ABC"), expect.objectContaining({ signal: expect.anything() }));
});

test("SEC manual poll keeps POST and reports failure; battle card failure is retryable", async () => {
  snapshot = { ...snapshot, loading: false, data: { filings: [{ ticker: "ABC" }] } };
  axios.post.mockRejectedValue(new Error("offline"));
  axios.get.mockRejectedValue(new Error("offline"));
  await mount(SECPage);
  await click(button("POLL EDGAR"));
  expect(axios.post).toHaveBeenCalledWith(expect.stringContaining("/sec/poll"));
  expect(host.textContent).toContain("EDGAR poll failed");
  await click(host.querySelector('[data-testid="sec-ABC"]'));
  await click(button("SEC BATTLE CARD"));
  await click(button("SEC BATTLE CARD"));
  expect(axios.get).toHaveBeenCalledTimes(2);
});

test("SEC successful poll refreshes display and keyboard opens row", async () => {
  snapshot = { ...snapshot, loading: false, data: { filings: [{ ticker: "ABC", link: "javascript:alert(1)" }] } };
  await mount(SECPage);
  await click(button("POLL EDGAR"));
  expect(refresh).toHaveBeenCalledTimes(1);
  await act(async () => host.querySelector('[data-testid="sec-ABC"]').dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(button("SEC BATTLE CARD")).toBeDefined();
  expect(host.querySelector('a[href^="javascript:"]')).toBeNull();
});

test("audit refresh replaces selected payload with current event and supports keyboard selection", async () => {
  const event = { ts: "2026-10-01T12:00:00Z", source: "scanner", event_type: "activity", ref_id: "same", payload: { revision: 1 }, title: "ONE" };
  snapshot = { ...snapshot, loading: false, data: { events: [event] } };
  await mount(AuditLogsPage);
  expect(host.querySelector("pre").textContent).toContain('"revision": 1');
  snapshot = { ...snapshot, data: { events: [{ ...event, payload: { revision: 2 } }, { ...event, source: "system", title: "TWO", payload: { revision: 3 } }] } };
  await mount(AuditLogsPage);
  expect(host.querySelector("pre").textContent).toContain('"revision": 2');
  await act(async () => host.querySelectorAll("tbody tr")[1].dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true })));
  expect(host.querySelector("pre").textContent).toContain('"revision": 3');
  await click(button("REFRESH"));
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(useDisplayResource).toHaveBeenCalledWith(expect.stringContaining("/audit_logs?limit=300"), 60000);
  expect(axios.post).not.toHaveBeenCalled();
});

test("audit malformed and failed reads never claim an empty matching stream", async () => {
  snapshot = { ...snapshot, loading: false, data: { events: {} }, error: new Error("offline") };
  await mount(AuditLogsPage);
  expect(host.textContent).toContain("Audit logs unavailable");
  expect(host.textContent).not.toContain("No audit events match");
  expect(host.querySelector('option[value="provider_health"]')).not.toBeNull();
  expect(host.querySelector('option[value="london_strategic_edge"]')).not.toBeNull();
});

test("earnings guards every board collection, detail arrays, and missing numbers", async () => {
  axios.get.mockImplementation(url => Promise.resolve({ data: url.includes("health") ? {} : { total: 1, by_day: { MONDAY: [null, { ticker: "ABC", earnings_date: "2020-01-01", earnings_setup_rating: "AVOID", avoid_flags: {}, option_strategy: { name: 7 }, battle_card: { bull_case: {}, bear_case: [null, "risk"] } }], TUESDAY: {} }, earnings_divergences: {} } }));
  await mount(EarningsPage);
  expect(host.textContent).toContain("Earnings response is incomplete");
  expect(host.textContent).toContain("LSE UNVERIFIED");
  expect(host.textContent).not.toContain("+0.0%");
  await click(button("OPEN"));
  const dialog = host.querySelector('[role="dialog"]');
  expect(dialog).not.toBeNull();
  expect(dialog.textContent).toContain("risk");
  expect(document.activeElement).toBe(dialog);
  await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(host.querySelector('[role="dialog"]')).toBeNull();
});

test("earnings week switch cancels old read, clears old board and ignores late response", async () => {
  const prior = deferred();
  const next = deferred();
  axios.get.mockImplementation(url => url.includes("health") ? Promise.resolve({ data: {} }) : axios.get.mock.calls.filter(([u]) => u.includes("earnings_week")).length === 1 ? prior.promise : next.promise);
  await mount(EarningsPage);
  const firstSignal = axios.get.mock.calls.find(([url]) => url.includes("earnings_week"))[1].signal;
  await click(button("NEXT WEEK"));
  expect(firstSignal.aborted).toBe(true);
  await act(async () => next.resolve({ data: { total: 1, by_day: { MONDAY: [{ ticker: "NEW" }] }, earnings_divergences: [] } }));
  await act(async () => prior.resolve({ data: { total: 1, by_day: { MONDAY: [{ ticker: "OLD" }] } } }));
  expect(host.querySelector('[data-testid="earnings-NEW"]')).not.toBeNull();
  expect(host.querySelector('[data-testid="earnings-OLD"]')).toBeNull();
  expect(axios.get.mock.calls.filter(([u]) => u.includes("earnings_week"))).toHaveLength(2);
});

test("failed earnings read does not fabricate no earnings or provider source outcomes", async () => {
  axios.get.mockRejectedValue(new Error("offline"));
  await mount(EarningsPage);
  expect(host.textContent).toContain("Earnings unavailable for the selected week");
  expect(host.textContent).not.toContain("NO EARNINGS THIS WEEK");
  expect(host.textContent).not.toContain("returned no rows");
});

test("earnings manual PM and Telegram commands retain exact endpoint and parameters", async () => {
  await mount(EarningsPage);
  expect(axios.post).not.toHaveBeenCalled();
  await click(button("ROUTE TO PM"));
  await click(button("SEND TO TELEGRAM"));
  expect(axios.post).toHaveBeenCalledWith(expect.stringContaining("/v32/earnings_pm/route"), null, { params: { week_offset: 0, min_score: 58 } });
  expect(axios.post).toHaveBeenCalledWith(expect.stringContaining("/v32/earnings_divergences/dispatch"), null, { params: { week_offset: 0 } });
  expect(host.textContent).toContain("PM ROUTED 2");
  expect(host.textContent).toContain("SENT 3");
});

test("earnings failed new week clears a previously loaded board", async () => {
  axios.get.mockImplementation(url => url.includes("health") ? Promise.resolve({ data: {} }) : Promise.resolve({ data: { total: 1, by_day: { MONDAY: [{ ticker: "OLD" }] }, earnings_divergences: [] } }));
  await mount(EarningsPage);
  expect(host.querySelector('[data-testid="earnings-OLD"]')).not.toBeNull();
  axios.get.mockRejectedValue(new Error("offline"));
  await click(button("NEXT WEEK"));
  expect(host.querySelector('[data-testid="earnings-OLD"]')).toBeNull();
  expect(host.textContent).toContain("Earnings unavailable");
  expect(host.textContent).not.toContain("NO EARNINGS THIS WEEK");
});

test("late manual route result is not attributed to a different earnings week", async () => {
  const response = deferred();
  axios.post.mockReturnValue(response.promise);
  await mount(EarningsPage);
  await click(button("ROUTE TO PM"));
  await click(button("NEXT WEEK"));
  await act(async () => response.resolve({ data: { ok: true, routed: 7 } }));
  expect(host.textContent).not.toContain("PM ROUTED 7");
  expect(host.textContent).toContain("NOT ROUTED");
  expect(axios.post).toHaveBeenCalledWith(expect.stringContaining("/v32/earnings_pm/route"), null, { params: { week_offset: 0, min_score: 58 } });
});

test("audit partial events guard null entries, bad dates and absent payloads", async () => {
  snapshot = { ...snapshot, loading: false, data: { events: [null, [], { ts: "invalid", title: "PARTIAL" }] } };
  await mount(AuditLogsPage);
  expect(host.textContent).toContain("Audit response is incomplete");
  expect(host.textContent).not.toContain("Invalid Date");
  expect(host.querySelector("pre").textContent).toBe("Payload unavailable.");
});

test("earnings explicit zero observations remain zero rather than missing", async () => {
  axios.get.mockImplementation(url => Promise.resolve({ data: url.includes("health") ? { ok: false } : { total: 1, by_day: { MONDAY: [{ ticker: "ZERO", earnings_setup_score: 0, beat_probability_pct: 0, momentum_20d_pct: 0, options: { implied_move_pct: 0 } }] }, earnings_divergences: [] } }));
  await mount(EarningsPage);
  expect(host.querySelector('[data-testid="earnings-ZERO"]').textContent).toContain("+0.0%");
  expect(host.textContent).toContain("LSE DEGRADED");
});
