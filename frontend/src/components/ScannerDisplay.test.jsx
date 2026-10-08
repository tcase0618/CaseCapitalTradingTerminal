import React, { act } from "react";
import { createRoot } from "react-dom/client";
import Dashboard, { nextScanCountdown, ScannerSubtabPanel } from "./Dashboard";
import { displayResource } from "../hooks/useDisplayResource";
import axios from "axios";

jest.mock("../config", () => ({ API: "/api" }));
jest.mock("./CrtShell", () => ({
  CrtShell: ({ children, headerRight }) => <div>{headerRight}{children}</div>,
  tokens: {},
}));
jest.mock("react-router-dom", () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
jest.mock("sonner", () => ({ toast: jest.fn() }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), delete: jest.fn(), defaults: { headers: { common: {} } } }));
jest.mock("../hooks/useDisplayResource", () => ({ displayResource: jest.fn() }));

let container;
let root;
let fixtures;
let resources;
let visibility;
let identity = 0;
const flush = async () => { for (let step = 0; step < 80; step += 1) await Promise.resolve(); };
const row = ticker => ({ ticker, signal_score: 7, price: 10, signals: [], thesis: `${ticker} thesis` });
const click = async selector => {
  await act(async () => { container.querySelector(selector).click(); await flush(); });
};

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date("2026-10-08T13:45:00Z"));
  jest.clearAllMocks();
  axios.get.mockReset();
  global.IS_REACT_ACT_ENVIRONMENT = true;
  visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  axios.defaults.headers.common.Authorization = `scanner-test-${identity++}`;
  fixtures = {
    "/status": {}, "/scan/latest": { results: [row("AAA"), row("BBB")] },
    "/activity?limit=20": [], "/watchlist": [], "/alerts": [],
    "/contracts?days=90&min_amount=1000000": { contracts: [] },
    "/congress/recent?days=30": [], "/squeeze/leaderboard/top?limit=10": [],
    "/fy/status": {}, "/scan/preview": {}, "/scan/tabs": { tabs: {}, errors: {} },
    "/scan/replay_calendar?days=90": {},
    "/scheduler/overview": { jobs: [{ id: "ten_am_scan", next_run_time: "2026-10-08T10:00:00-04:00", pending: false }] },
  };
  resources = new Map();
  displayResource.mockImplementation(url => {
    if (!resources.has(url)) {
      const state = { data: fixtures[url.slice(4)] ?? null, updatedAt: Date.now(), error: null };
      const off = jest.fn();
      resources.set(url, { state, getSnapshot: () => state, refresh: jest.fn().mockResolvedValue(), subscribe: jest.fn(() => off), off });
    }
    return resources.get(url);
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root.unmount(); await flush(); });
  expect(axios.post).not.toHaveBeenCalled();
  expect(axios.delete).not.toHaveBeenCalled();
  container.remove();
  visibility.mockRestore();
  jest.useRealTimers();
});

test("countdown uses the earliest future Core runtime job, not 08:00 or specialist jobs", () => {
  const now = new Date("2026-10-08T13:45:00Z");
  const jobs = [
    { id: "morning_scan", next_run_time: "2026-10-08T08:00:00-04:00" },
    { id: "options_open_auto_execute_935", next_run_time: "2026-10-08T09:46:00-04:00" },
    { id: "midday_scan", next_run_time: "2026-10-08T12:00:00-04:00" },
    { id: "ten_am_scan", next_run_time: "2026-10-08T10:00:00-04:00" },
  ];
  expect(nextScanCountdown(now, { jobs })).toBe("00:15:00");
  expect(nextScanCountdown(new Date("2026-11-01T06:00:00Z"), { jobs: [{ id: "morning_scan", next_run_time: "2026-11-02T08:00:00-05:00" }] })).toBe("31:00:00");
});

test.each([
  null, {}, { jobs: [] }, { jobs: [{ id: "morning_scan", next_run_time: null }] },
  { jobs: [{ id: "morning_scan", next_run_time: "2026-10-08T15:00:00" }] },
  { jobs: [{ id: "morning_scan", next_run_time: "invalid" }] },
  { jobs: [{ id: "morning_scan", next_run_time: "2026-10-08T08:00:00-04:00" }] },
  { jobs: [{ id: "morning_scan", next_run_time: "2026-10-08T15:00:00Z", pending: true }] },
])("missing, invalid, paused, or elapsed scheduler data stays unavailable: %j", scheduler => {
  expect(nextScanCountdown(new Date("2026-10-08T13:45:00Z"), scheduler)).toBe("UNAVAILABLE");
});

test("Scanner renders Core unchanged and ticks server-backed countdown without extra reads", async () => {
  await act(async () => { root.render(<Dashboard />); await flush(); });
  expect(container.querySelector('[data-testid="result-row-AAA"]')).not.toBeNull();
  expect(container.querySelector('[aria-label="grade 7.0 out of 10"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="run-scan-button"]').textContent).toContain("RUN SCAN");
  const reads = displayResource.mock.calls.length;
  expect(container.querySelector('[data-testid="next-scan-countdown"]').textContent).toBe("NEXT SCAN 00:15:00");
  await act(async () => { jest.advanceTimersByTime(1000); await flush(); });
  expect(container.querySelector('[data-testid="next-scan-countdown"]').textContent).toBe("NEXT SCAN 00:14:59");
  expect(displayResource).toHaveBeenCalledTimes(reads);
});

test("scheduler refresh failure suppresses cached countdown", async () => {
  await act(async () => { root.render(<Dashboard />); await flush(); });
  const resource = resources.get("/api/scheduler/overview");
  resource.state.error = new Error("scheduler offline");
  await act(async () => { jest.advanceTimersByTime(15000); await flush(); });
  expect(container.querySelector('[data-testid="next-scan-countdown"]').textContent).toBe("NEXT SCAN UNAVAILABLE");
  expect(container.textContent).toContain("Some scanner data could not refresh");
});

test("hidden mount issues no GETs and visible return refreshes using the real shared cache", async () => {
  const actual = jest.requireActual("../hooks/useDisplayResource").displayResource;
  displayResource.mockImplementation((url, interval) => actual(url, interval));
  axios.get.mockImplementation(async url => ({ data: fixtures[url.slice(4)] }));
  visibility.mockReturnValue("hidden");
  await act(async () => { root.render(<Dashboard />); await flush(); jest.advanceTimersByTime(30000); await flush(); });
  expect(axios.get).not.toHaveBeenCalled();
  visibility.mockReturnValue("visible");
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await flush(); });
  expect(axios.get).toHaveBeenCalledTimes(13);
  expect(container.querySelector('[data-testid="result-row-AAA"]')).not.toBeNull();
  expect(axios.get.mock.calls.every(([url]) => !url.includes("force"))).toBe(true);
});

test("Strict Mode remount publishes first results without waiting for the polling timer", async () => {
  const actual = jest.requireActual("../hooks/useDisplayResource").displayResource;
  displayResource.mockImplementation((url, interval) => actual(url, interval));
  axios.get.mockImplementation(async url => ({ data: fixtures[url.slice(4)] }));
  await act(async () => { root.render(<React.StrictMode><Dashboard /></React.StrictMode>); await flush(); });
  expect(container.querySelector('[data-testid="result-row-AAA"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="next-scan-countdown"]').textContent).toContain("00:15:00");
});

test("overlapping automatic refreshes coalesce and unmount releases all subscriptions", async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const factory = displayResource.getMockImplementation();
  displayResource.mockImplementation(url => {
    const resource = factory(url);
    resource.state.updatedAt = 0;
    resource.refresh.mockImplementation(() => pending);
    return resource;
  });
  await act(async () => { root.render(<Dashboard />); await flush(); });
  await act(async () => {
    jest.advanceTimersByTime(45000);
    document.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("visibilitychange"));
    await flush();
  });
  resources.forEach(resource => expect(resource.refresh).toHaveBeenCalledTimes(1));
  await act(async () => { root.unmount(); release(); await flush(); });
  resources.forEach(resource => expect(resource.off).toHaveBeenCalledTimes(1));
  root = createRoot(container);
});

test("selection switches abort old GETs, hide previous forecasts, and ignore late completions", async () => {
  const requests = [];
  axios.get.mockImplementation((url, options) => new Promise(resolve => requests.push({ url, options, resolve })));
  await act(async () => { root.render(<Dashboard />); await flush(); });
  await click('[data-testid="result-row-AAA"]');
  expect(requests[0].options.timeout).toBe(12000);
  await click('[data-testid="result-row-BBB"]');
  expect(requests[0].options.signal.aborted).toBe(true);
  await act(async () => { requests[1].resolve({ data: { battle_card: { ticker: "BBB", forecast_bias: "BBB_CURRENT", attribution: [], horizons: {} } } }); await flush(); });
  expect(container.textContent).toContain("BBB_CURRENT");
  await act(async () => { requests[0].resolve({ data: { battle_card: { ticker: "AAA", forecast_bias: "AAA_OBSOLETE" } } }); await flush(); });
  expect(container.textContent).not.toContain("AAA_OBSOLETE");
  expect(container.textContent).toContain("BBB_CURRENT");
  await click('[data-testid="result-row-AAA"]');
  expect(container.textContent).not.toContain("BBB_CURRENT");
  expect(container.textContent).toContain("LOADING KRONOS FORECAST");
  await click('[data-testid="result-row-AAA"]');
  expect(requests[2].options.signal.aborted).toBe(true);
  await act(async () => { requests[2].resolve({ data: { battle_card: { ticker: "AAA", forecast_bias: "DESELECTED" } } }); await flush(); });
  expect(container.textContent).not.toContain("DESELECTED");
});

test("strategy forecast panels have container-bounded tracks and wrapping summaries", async () => {
  const candidate = row("OPT");
  await act(async () => { root.render(<ScannerSubtabPanel view="options" rows={[candidate]} selected="OPT" kronosCard={{ battle_card: { ticker: "OPT", attribution: [], horizons: {} } }} />); await flush(); });
  const grid = container.querySelector('[data-testid="scanner-battle-grid"]');
  expect(grid.style.gridTemplateColumns).toBe("repeat(auto-fit, minmax(min(230px, 100%), 1fr))");
  const summary = container.querySelector('[data-testid="scanner-kronos-summary"]');
  expect(summary.style.minWidth).toBe("0");
  expect(summary.style.gridTemplateColumns).toBe("repeat(3, minmax(0, 1fr))");
  expect(summary.parentElement.style.flexWrap).toBe("wrap");
  expect(container.innerHTML).not.toContain("min-width: 280px");
});

test("tab switch aborts selection; request failures are visible without retry or mutation", async () => {
  let release;
  axios.get.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  await act(async () => { root.render(<Dashboard />); await flush(); });
  await click('[data-testid="result-row-AAA"]');
  const signal = axios.get.mock.calls[0][1].signal;
  await act(async () => {
    Array.from(container.querySelectorAll("button")).find(button => button.textContent.startsWith("Case Court")).click();
    await flush();
  });
  expect(signal.aborted).toBe(true);
  expect(container.textContent).toContain("CASE COURT IS NOT PROVIDED");
  await act(async () => {
    release({ data: { battle_card: { forecast_bias: "OFFSCREEN" } } });
    Array.from(container.querySelectorAll("button")).find(button => button.textContent.startsWith("Core")).click();
    await flush();
  });
  axios.get.mockRejectedValueOnce(new Error("forecast offline"));
  await click('[data-testid="result-row-BBB"]');
  expect(container.textContent).toContain("KRONOS DEGRADED: forecast offline");
  expect(container.textContent).not.toContain("OFFSCREEN");
  expect(axios.get).toHaveBeenCalledTimes(2);
  resources.forEach(resource => expect(resource.refresh.mock.calls.every(args => args[0] !== true)).toBe(true));
});

test("stored family rows paginate beyond 80 and reset when switching families", async () => {
  const rows = Array.from({ length: 83 }, (_, index) => row(`S${index}`));
  await act(async () => { root.render(<ScannerSubtabPanel view="lottery" rows={rows} />); await flush(); });
  expect(container.textContent).toContain("STORED SNAPSHOT");
  expect(container.textContent).not.toContain("LIVE ROUTING VIEW");
  expect(container.querySelectorAll('[data-testid^="scanner-family-row-"]')).toHaveLength(80);
  expect(container.textContent).toContain("1-80 OF 83");
  await click('[aria-label="Next scanner results"]');
  expect(container.querySelector('[data-testid="scanner-family-row-lottery-S82"]')).not.toBeNull();
  expect(container.textContent).toContain("81-83 OF 83");
  expect(container.querySelector('[aria-label="Next scanner results"]').disabled).toBe(true);
  await act(async () => { root.render(<ScannerSubtabPanel view="options" rows={rows} />); await flush(); });
  expect(container.textContent).toContain("1-80 OF 83");
});

test("unavailable snapshots and unprovided Case Court are distinct from empty results", async () => {
  await act(async () => { root.render(<ScannerSubtabPanel view="options" rows={[]} loaded={false} />); await flush(); });
  expect(container.textContent).toContain("SCAN SNAPSHOT UNAVAILABLE");
  await act(async () => { root.render(<ScannerSubtabPanel view="options" rows={[]} loaded />); await flush(); });
  expect(container.textContent).toContain("NO ROWS IN STORED SNAPSHOT");
  await act(async () => { root.render(<ScannerSubtabPanel view="court" rows={[]} />); await flush(); });
  expect(container.textContent).toContain("CASE COURT IS NOT PROVIDED BY THE SCANNER SNAPSHOT");
  expect(container.querySelector('a[href="/case-court"]')).not.toBeNull();
});
