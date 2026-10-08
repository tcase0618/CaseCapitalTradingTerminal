import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import PerformancePage from "./PerformancePage";
import { displayResource } from "../hooks/useDisplayResource";

jest.mock("../config", () => ({ API: "/api" }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), defaults: { headers: { common: {} } } }));
jest.mock("sonner", () => ({ toast: jest.fn() }));
jest.mock("./CrtShell", () => ({
  tokens: { accent: "#ffffff", accent2: "#ffffff", dim: "#999999", muted: "#999999", labelLight: "#ffffff", hairline: "1px solid #333333", cardBg: "#111111" },
  CrtShell: ({ children, headerRight }) => <div>{headerRight}{children}</div>,
  Card: ({ title, children, action }) => <section><h2>{title}</h2>{action}{children}</section>,
  Stat: ({ label, value }) => <div>{label}: {value}</div>,
}));
jest.mock("./Institutional", () => ({ DataConfidenceStrip: () => null }));
jest.mock("recharts", () => {
  const React = require("react");
  const Chart = ({ children }) => React.createElement("div", null, children);
  const SvgChart = ({ children }) => React.createElement("svg", null, children);
  const Empty = () => null;
  return { ResponsiveContainer: Chart, AreaChart: SvgChart, LineChart: SvgChart, Area: Empty, Line: Empty,
    XAxis: Empty, YAxis: Empty, Tooltip: Empty, ReferenceLine: Empty, CartesianGrid: Empty, Legend: Empty };
});

const paths = [
  "/performance/summary", "/backtest/summary", "/signals/tracker?limit=200",
  "/signals/curve?days=90", "/signals/options_curve?days=90", "/signals/benchmark_curve?days=90",
  "/admin/price_source", "/edge/overview", "/signals/options_gap?limit=500&threshold_pct=50",
];
let container;
let root;
let visibility;
let serial = 0;
const flush = async () => { for (let step = 0; step < 80; step += 1) await Promise.resolve(); };
const fixture = url => {
  if (url.includes("/performance/summary")) return { signals: [], options: { by_crush_risk: [{ crush_risk: "LOW", n: 2, avg_return: 4, win_rate: 50 }] },
    proof: { signals_30d: [{ signal: "long_signal_label", n: 2, win_rate_pct: 50, expectancy_pct: 4 }] } };
  if (url.includes("/signals/tracker")) return { tracked: 7, total: 7, winners: 4, losers: 3, avg_gain_pct: 5, rows: [] };
  if (url.includes("/admin/price_source")) return { source: "finnhub", finnhub_available: true };
  if (url.includes("curve?days=")) return { curve: [{ date: "2026-10-08", avg_gain_pct: Number(url.split("days=")[1]), positions: 2 }] };
  return {};
};
const renderPage = async () => { await act(async () => { root.render(<PerformancePage />); await flush(); }); };
const click = async selector => { await act(async () => { container.querySelector(selector).click(); await flush(); }); };
const advance = async ms => { await act(async () => { jest.advanceTimersByTime(ms); await flush(); }); };

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date("2026-10-08T15:00:00Z"));
  global.IS_REACT_ACT_ENVIRONMENT = true;
  visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  sessionStorage.clear();
  axios.defaults.headers.common.Authorization = `performance-test-${serial++}`;
  displayResource("/performance-test-reset").dispose();
  axios.get.mockReset();
  axios.post.mockReset();
  axios.get.mockImplementation(url => Promise.resolve({ data: fixture(url) }));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root.unmount(); await flush(); });
  container.remove();
  visibility.mockRestore();
  jest.useRealTimers();
});

test("all nine display GETs use stable keys and poll at 30 seconds without mutations", async () => {
  await renderPage();
  expect(axios.get.mock.calls.map(([url]) => url).sort()).toEqual(paths.map(path => `/api${path}`).sort());
  expect(axios.get.mock.calls.every(([, config]) => config.signal && config.timeout === 12000)).toBe(true);
  await advance(29999);
  expect(axios.get).toHaveBeenCalledTimes(9);
  await advance(1);
  expect(axios.get).toHaveBeenCalledTimes(18);
  expect(axios.post).not.toHaveBeenCalled();
});

test("hidden browser tabs pause polling and visibility return refreshes stale reads", async () => {
  await renderPage();
  visibility.mockReturnValue("hidden");
  await advance(60000);
  expect(axios.get).toHaveBeenCalledTimes(9);
  visibility.mockReturnValue("visible");
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await flush(); });
  expect(axios.get).toHaveBeenCalledTimes(18);
  expect(axios.post).not.toHaveBeenCalled();
});

test("range changes replace only the three curve subscriptions", async () => {
  await renderPage();
  axios.get.mockClear();
  await click('[data-testid="benchmark-curve-range-30"]');
  expect(axios.get.mock.calls.map(([url]) => url).sort()).toEqual([
    "/api/signals/curve?days=30", "/api/signals/options_curve?days=30", "/api/signals/benchmark_curve?days=30",
  ].sort());
  expect(container.textContent).toContain("30D");
  expect(container.textContent).toContain("SIGNALS TRACKED: 7");
  expect(axios.post).not.toHaveBeenCalled();
});

test("manual display reload forces all current keys before their interval expires", async () => {
  await renderPage();
  await click('[data-testid="benchmark-curve-range-30"]');
  axios.get.mockClear();
  await click('[data-testid="performance-display-refresh"]');
  expect(axios.get).toHaveBeenCalledTimes(9);
  expect(axios.get.mock.calls.filter(([url]) => url.includes("days=")).every(([url]) => url.endsWith("days=30"))).toBe(true);
  expect(axios.post).not.toHaveBeenCalled();
});

test("partial failures name unavailable feeds and preserve last-good values", async () => {
  await renderPage();
  axios.get.mockImplementation(url => url.includes("/signals/tracker") || url.includes("/edge/overview")
    ? Promise.reject(new Error("offline")) : Promise.resolve({ data: fixture(url) }));
  await click('[data-testid="performance-display-refresh"]');
  expect(container.querySelector('[role="alert"]').textContent).toContain("2 PERFORMANCE DATA REQUEST(S) FAILED: Tracker, Edge");
  expect(container.querySelector('[role="alert"]').textContent).toContain("may be outdated");
  expect(container.textContent).toContain("SIGNALS TRACKED: 7");
  axios.get.mockImplementation(url => Promise.resolve({ data: fixture(url) }));
  await click('[data-testid="performance-display-refresh"]');
  expect(container.querySelector('[role="alert"]')).toBeNull();
});

test("unmount aborts active reads and removes queued reads and polling", async () => {
  const signals = [];
  axios.get.mockImplementation((url, { signal }) => new Promise((resolve, reject) => {
    signals.push(signal);
    signal.addEventListener("abort", () => { const error = new Error("canceled"); error.name = "CanceledError"; reject(error); }, { once: true });
  }));
  await renderPage();
  expect(axios.get).toHaveBeenCalledTimes(4);
  await act(async () => { root.unmount(); await flush(); });
  expect(signals.every(signal => signal.aborted)).toBe(true);
  await advance(60000);
  expect(axios.get).toHaveBeenCalledTimes(4);
  expect(axios.post).not.toHaveBeenCalled();
});

test("mutation endpoints and payloads remain explicit and refresh display data afterward", async () => {
  await renderPage();
  axios.post.mockResolvedValueOnce({ data: { written: 2, skipped: 0 } });
  await click('[data-testid="seed-backtest-btn"]');
  expect(axios.post).toHaveBeenNthCalledWith(1, "/api/backtest/seed");
  expect(axios.get).toHaveBeenCalledTimes(18);
  axios.post.mockResolvedValueOnce({ data: { first_seen_updated: 2, failures: 0, source: "massive" } });
  await click('[data-testid="refresh-prices-btn"]');
  expect(axios.post).toHaveBeenNthCalledWith(2, "/api/admin/refresh_prices", null, { timeout: 120000 });
  expect(axios.get).toHaveBeenCalledTimes(27);
});

test("responsive grid rules are scoped to Performance without changing metric values", async () => {
  await renderPage();
  const page = container.querySelector(".performance-display");
  expect(page.querySelectorAll(".performance-grid-six")).toHaveLength(2);
  expect(page.querySelector(".performance-grid-risk").textContent).toContain("WR 50%");
  expect(page.querySelector(".performance-grid-proof").textContent).toContain("long_signal_label");
  expect(page.querySelector("style").textContent).toContain(".performance-display .performance-grid-six");
  expect(page.querySelector("style").textContent).toContain("@media (max-width: 600px)");
  expect(page.textContent).toContain("AVG GAIN SINCE SIGNAL: +5.0%");
});

test.each([{}, { source: null }, { source: 42 }, { source: {} }, { source: "" }])(
  "malformed price-source payload remains unavailable without crashing: %j", async priceSource => {
    axios.get.mockImplementation(url => Promise.resolve({ data: url.includes("/admin/price_source") ? priceSource : fixture(url) }));
    await renderPage();
    expect(container.querySelector('[data-testid="price-source-badge"]').textContent).toContain("UNAVAILABLE");
    expect(container.textContent).toContain("SIGNALS TRACKED: 7");
    expect(axios.post).not.toHaveBeenCalled();
  }
);
