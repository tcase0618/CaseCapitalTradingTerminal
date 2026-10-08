import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PharmaPage from "./PharmaPage";
import axios from "axios";
import { displayResource } from "../hooks/useDisplayResource";

jest.mock("axios");
jest.mock("sonner", () => ({ toast: jest.fn() }));
jest.mock("react-router-dom", () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
jest.mock("../hooks/useDisplayResource", () => ({ displayResource: jest.fn() }));
jest.mock("./CrtShell", () => ({
  tokens: { accent: "#d7bd68", accent2: "#7df7de", muted: "#7d8796", labelLight: "#ddd" },
  CrtShell: ({ children, headerRight }) => <div>{headerRight}{children}</div>,
  Card: ({ title, children }) => <section aria-label={title}>{children}</section>,
  Stat: ({ label, value, sub }) => <div data-stat={label}>{value}|{sub}</div>,
}));

let root;
let container;
let fixtures;
let cache;
const date = (year, month, day) => `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
const calendar = (year, month) => ({ year, month, days: [
  { date: date(year, month, 1), event_count: 1, events: [{ ticker: "FDA", drug: "Docket" }] },
  { date: date(year, month, 2), event_count: 0, events: [] },
], events: [], summary: { events: 1 } });
const clickText = async text => act(async () => Array.from(container.querySelectorAll("button")).find(button => button.textContent === text).click());

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  fixtures = {
    "/pharma/pdufa?days=90": { results: [] }, "/pharma/shocks?limit=50": { results: [] },
    "/pharma/active": { plays: [] }, "/pharma/track_record": { history: [], settled: 0 },
  };
  cache = new Map();
  axios.get.mockImplementation(async (url, config) => ({ data: url.endsWith("/pharma/fda_calendar") ? calendar(config.params.year, config.params.month) : fixtures[url.slice(url.indexOf("/api") + 4)] ?? {} }));
  displayResource.mockImplementation(url => {
    if (!cache.has(url)) {
      let state = { data: null, updatedAt: null, error: null };
      cache.set(url, { getSnapshot: () => state, refresh: jest.fn(async () => {
        try { state = { data: (await axios.get(url)).data, updatedAt: Date.now(), error: null }; }
        catch (error) { state = { ...state, error }; }
      }) });
    }
    return cache.get(url);
  });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("FDA selects an empty day without resurrecting another docket and labels controls", async () => {
  await act(async () => root.render(<PharmaPage />));
  const today = new Date();
  const emptyDate = date(today.getFullYear(), today.getMonth() + 1, 2);
  await act(async () => container.querySelector(`[aria-label="${emptyDate}: 0 FDA events"]`).click());
  expect(container.textContent).toContain(`No FDA/PDUFA events on ${emptyDate}`);
  expect(container.querySelector('select[aria-label="FDA calendar year"]')).not.toBeNull();
  const region = container.querySelector('[aria-label="FDA month grid"]');
  expect(region.style.overflowX).toBe("auto");
  expect(region.firstElementChild.style.minWidth).toBe("780px");
  expect(container.textContent).toContain("HOT -");
});

test("month requests ignore stale replies and do not reload unrelated feeds", async () => {
  await act(async () => root.render(<PharmaPage />));
  const pending = [];
  axios.get.mockImplementation((url, config) => new Promise(resolve => pending.push({ url, config, resolve })));
  const select = container.querySelector('[aria-label="FDA calendar month"]');
  const change = async month => act(async () => { select.value = String(month); select.dispatchEvent(new Event("change", { bubbles: true })); });
  await change(1); await change(2);
  expect(pending).toHaveLength(2);
  expect(pending[0].config.signal.aborted).toBe(true);
  await act(async () => pending[1].resolve({ data: calendar(pending[1].config.params.year, 2) }));
  await act(async () => pending[0].resolve({ data: { ...calendar(pending[0].config.params.year, 1), summary: { events: 999 } } }));
  expect(container.textContent).not.toContain("999");
  expect(container.querySelector('[aria-label="FDA calendar month"]').value).toBe("2");
});

test("Command and Track Record tolerate partial arrays and numeric strings with unavailable fields", async () => {
  fixtures["/pharma/pdufa?days=90"] = { results: [null, { ticker: "TEST", binary_event_score: "81", current_price: "0", days_until: null, trial: { phases: {} }, score_components: { missing: null, good: { points: 0, max: 10, note: "Evidence" } } }] };
  fixtures["/pharma/shocks?limit=50"] = { results: [null, { ticker: "TEST", shock_score: "90", bullish_terms: {}, bearish_terms: "bad" }] };
  fixtures["/pharma/active"] = { plays: [null, { ticker: "TEST", entry_score: "0", gain_pct: null }] };
  fixtures["/pharma/track_record"] = { history: [null, { ticker: "TEST", entry_price: "0", exit_price: "1.5", realized_pct: null }] };
  await act(async () => root.render(<PharmaPage />));
  await clickText("Command");
  expect(container.textContent).not.toMatch(/NaN|undefined/);
  expect(container.textContent).toContain("81/100");
  expect(container.querySelector('[aria-label="PDUFA calendar table"]').style.overflowX).toBe("auto");
  await act(async () => container.querySelector('[aria-label="Research TEST"]').click());
  expect(container.textContent).toContain("unavailable");
  expect(container.textContent).not.toContain("+0.0% base");
  await clickText("Track Record");
  expect(container.textContent).toContain("$0.00");
  expect(container.textContent).toContain("$1.50");
  expect(container.querySelector('[aria-label="Active pharma plays"]')).not.toBeNull();
});

test("malformed or failed feeds show unavailable rather than measured zero", async () => {
  fixtures["/pharma/pdufa?days=90"] = { results: {} };
  fixtures["/pharma/active"] = { plays: "invalid" };
  fixtures["/pharma/track_record"] = {};
  await act(async () => root.render(<PharmaPage />));
  expect(container.querySelector('[data-stat="PDUFA - 90D"]').textContent).toMatch(/^-/);
  expect(container.querySelector('[data-stat="ACTIVE PLAYS"]').textContent).toMatch(/^-/);
  expect(container.querySelector('[role="status"]').textContent).toContain("malformed");
  await clickText("Track Record");
  expect(container.textContent).toContain("Active plays unavailable.");
});

test("manual scan retains both POST endpoints and explicit forced FDA import", async () => {
  axios.post.mockResolvedValue({ data: { results: [], hot_count: 0 } });
  await act(async () => root.render(<PharmaPage />));
  await act(async () => container.querySelector('[data-testid="pharma-scan-btn"]').click());
  expect(axios.post.mock.calls.map(([url]) => url.slice(url.indexOf("/api") + 4))).toEqual(["/pharma/scan", "/pharma/shocks/scan"]);
  expect(axios.get.mock.calls.filter(([url, config]) => url.endsWith("/pharma/fda_calendar") && config.params.force_refresh)).toHaveLength(1);
  expect(cache.size).toBe(2);
});
