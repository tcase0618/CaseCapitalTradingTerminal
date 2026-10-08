import React, { act } from "react";
import { createRoot } from "react-dom/client";
import IntelPage from "./IntelPage";
import axios from "axios";
import { displayResource } from "../hooks/useDisplayResource";

jest.mock("axios");
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("react-router-dom", () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
jest.mock("../hooks/useDisplayResource", () => ({ displayResource: jest.fn() }));
jest.mock("./CrtShell", () => ({
  tokens: { accent: "#d7bd68", accent2: "#7df7de", muted: "#7d8796", labelLight: "#ddd" },
  CrtShell: ({ children, headerRight }) => <div>{headerRight}{children}</div>,
  Card: ({ title, children, action }) => <section aria-label={title}>{action}{children}</section>,
  Stat: ({ label, value, sub }) => <div data-stat={label}>{value}|{sub}</div>,
}));

let root;
let container;
let fixtures;
let cache;
let failed;
const pathOf = url => url.slice(url.indexOf("/api") + 4);
const clickText = async text => act(async () => Array.from(container.querySelectorAll("button")).find(button => button.textContent === text).click());
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  failed = new Set();
  fixtures = {};
  cache = new Map();
  axios.get.mockImplementation(async url => { if (failed.has(pathOf(url))) throw new Error("feed down"); return { data: fixtures[pathOf(url)] ?? {} }; });
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

test("unknown FRED stays UNKNOWN and missing provider data is not zero or LIVE", async () => {
  await act(async () => root.render(<IntelPage />));
  const macro = container.querySelector('[aria-label="MACRO AND FEED HEALTH"]');
  expect(macro.textContent).toContain("FREDUNKNOWN");
  expect(macro.textContent).toContain("PM RISK-");
  expect(macro.textContent).toContain("SCAN ROWS-");
  expect(container.querySelector('[data-stat="DARK POOL"]').textContent).toMatch(/^-/);
  expect(container.querySelector('[aria-label="INSTITUTIONAL NEWSWIRE - FREE RSS EVENT TAPE"]').textContent).toContain("URGENT-");
  expect(container.querySelector('[aria-label="SOURCE FUSION MATRIX"]').textContent).toContain("ScannerUNKNOWN-");
  expect(container.textContent).not.toMatch(/NaN|undefined/);
});

test("all six lanes, stacks and malformed nested arrays render without issuing watch/order commands", async () => {
  fixtures["/scan/latest"] = { results: [null, { ticker: "SCAN", signals: {}, price: null }] };
  fixtures["/portfolio_manager/latest"] = { recommendations: [null, { ticker: "PM", action: "STARTER", pm_score: "85", signals: "bad", reasons: {}, cautions: null, allocation_usd: 0, risk_reward: 0 }], summary: {} };
  fixtures["/v32/conviction"] = { top3: [null, { ticker: "CONV", components: {} }] };
  fixtures["/v32/x_factor?days=14"] = [null, { ticker: "XF", triggers: {}, stocktwits: {} }];
  fixtures["/v32/x_factor/discoveries?days=7"] = [null, { ticker: "DISC", sources: {} }];
  fixtures["/georisk/live"] = { events: [null, { title: "Risk", severity: "HIGH", sectors: {}, tickers: "bad" }] };
  fixtures["/news_intel/latest?limit=80"] = { articles: [null, { title: "Headline", tickers: {}, urgency_terms: {}, age_minutes: null }] };
  await act(async () => root.render(<IntelPage />));
  await clickText("EXPAND");
  const lane = container.querySelector('[aria-label="Select intel action lane"]');
  for (const value of ["all", "act", "pm", "catalyst", "hidden", "watch"]) {
    await act(async () => { lane.value = value; lane.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  }
  await act(async () => { lane.value = "pm"; lane.dispatchEvent(new Event("change", { bubbles: true })); });
  await clickText("OPEN STACK");
  expect(container.querySelector('[aria-expanded="true"]')).not.toBeNull();
  expect(container.querySelector("#intel-action-lanes").textContent).toContain("ALLOC$0.00");
  expect(container.textContent).toContain("NO TIME");
  expect(axios.post).not.toHaveBeenCalled();
});

test("dark-pool partial marks do not produce fabricated full-tape notional or averages", async () => {
  fixtures["/v32/dark_horse?days=14"] = [
    { ticker: "A", block_volume: 100, close: 2, off_exchange_pct: 50, block_pct_of_adv: 10 },
    { ticker: "B", block_volume: null, close: null, off_exchange_pct: null },
  ];
  await act(async () => root.render(<IntelPage />));
  const dark = container.querySelector('[aria-label="DARK POOL TAPE - FINRA OFF-EXCHANGE PROXY"]');
  expect(dark.textContent).toContain("SHARES-");
  expect(dark.textContent).toContain("NOTIONAL-");
  expect(dark.textContent).toContain("AVG OFF-EX-");
  expect(dark.querySelector("table").parentElement.style.overflowX).toBe("auto");
});

test("failed refresh retains good evidence with stale label; no timer triggers backend reads", async () => {
  fixtures["/scan/latest"] = { results: [{ ticker: "KEEP" }] };
  fixtures["/v32/macro"] = { fred_available: false, events: [], imminent_warnings: [] };
  await act(async () => root.render(<IntelPage />));
  const initialCalls = axios.get.mock.calls.length;
  failed.add("/scan/latest");
  await clickText("REFRESH");
  expect(container.textContent).toContain("$KEEP");
  expect(container.textContent).toContain("retained values may be stale");
  expect(container.querySelector('[aria-label="SOURCE FUSION MATRIX"]').textContent).toContain("ScannerSTALE1");
  expect(container.querySelector('[aria-label="MACRO AND FEED HEALTH"]').textContent).toContain("FREDDEGRADED");
  expect(axios.get.mock.calls.length).toBe(initialCalls * 2);
  expect(axios.post).not.toHaveBeenCalled();
});

test("watch remains an explicit single watchlist POST", async () => {
  fixtures["/scan/latest"] = { results: [{ ticker: "WATCHME" }] };
  axios.post.mockResolvedValue({ data: {} });
  await act(async () => root.render(<IntelPage />));
  await clickText("EXPAND"); await clickText("WATCH");
  expect(axios.post).toHaveBeenCalledTimes(1);
  expect(axios.post.mock.calls[0]).toEqual([expect.stringContaining("/api/watchlist"), { ticker: "WATCHME" }]);
});

test("lanes expose names beyond the initial 24-card render limit", async () => {
  fixtures["/scan/latest"] = { results: Array.from({ length: 25 }, (_, index) => ({ ticker: `NAME${index}` })) };
  await act(async () => root.render(<IntelPage />));
  await clickText("EXPAND");
  expect(container.querySelectorAll('#intel-action-lanes button[aria-expanded="false"]')).toHaveLength(24);
  await clickText("SHOW MORE (1)");
  expect(container.querySelectorAll('#intel-action-lanes button[aria-expanded="false"]')).toHaveLength(25);
  expect(axios.post).not.toHaveBeenCalled();
});

test("unmount cancels component-owned provider GETs", async () => {
  await act(async () => root.render(<IntelPage />));
  const configs = axios.get.mock.calls.filter(([, config]) => config?.signal).map(([, config]) => config);
  expect(configs.length).toBeGreaterThan(0);
  await act(async () => root.unmount());
  expect(configs.every(config => config.signal.aborted)).toBe(true);
  root = createRoot(container);
});
