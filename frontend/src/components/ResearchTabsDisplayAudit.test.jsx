import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import QualityPage from "./QualityPage";
import TruthReviewPage from "./TruthReviewPage";
import KronosPage from "./KronosPage";
import { displayResource } from "../hooks/useDisplayResource";

jest.mock("../config", () => ({ API: "/api" }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), defaults: { headers: { common: {} } } }));
jest.mock("./TradingViewMiniChart", () => () => <div>Chart fixture</div>);
jest.mock("recharts", () => {
  const component = ({ children }) => <div>{children}</div>;
  return Object.fromEntries(["ResponsiveContainer", "AreaChart", "Area", "BarChart", "Bar", "CartesianGrid", "Cell", "LineChart", "Line", "XAxis", "YAxis", "Tooltip", "ReferenceLine"].map(name => [name, component]));
});
jest.mock("./CrtShell", () => ({
  tokens: { accent: "#c8a84b", accent2: "#5eead4", dim: "#555555", muted: "#999999", labelLight: "#dddddd", hairline: "1px solid #333333", cardBg: "#111111", pageBg: "#09090f" },
  CrtShell: ({ title, children, headerRight }) => <div>{title}{headerRight}{children}</div>,
  Card: ({ title, children }) => <section><h2>{title}</h2>{children}</section>,
  Stat: ({ label, value }) => <div>{label}{value}</div>,
}));
jest.mock("./Institutional", () => ({
  DataConfidenceStrip: ({ items }) => <div>{items.map(item => <span key={item.label}>{item.label}: {item.value}</span>)}</div>,
  InstitutionalEmpty: ({ title, detail }) => <div>{title}{detail}</div>,
}));

let container, root, visibility, fixtures;
let serial = 0;
const flush = async () => { for (let i = 0; i < 120; i += 1) await Promise.resolve(); };
const render = async Page => { await act(async () => { root.render(<Page />); await flush(); }); };
const advance = async ms => { await act(async () => { jest.advanceTimersByTime(ms); await flush(); }); };
const click = async label => {
  const button = [...container.querySelectorAll("button")].find(node => node.textContent.trim() === label);
  expect(button).toBeDefined();
  await act(async () => { button.click(); await flush(); });
};
const show = async () => { visibility.mockReturnValue("visible"); await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await flush(); }); };
const overviewCalls = path => axios.get.mock.calls.filter(([url]) => url === `/api${path}`);
const respond = url => Promise.resolve({ data: fixtures[url.slice(4)] ?? {} });

beforeEach(() => {
  jest.useFakeTimers(); jest.setSystemTime(new Date("2026-10-08T15:00:00Z"));
  global.IS_REACT_ACT_ENVIRONMENT = true;
  visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  sessionStorage.clear(); axios.defaults.headers.common.Authorization = `research-audit-${serial++}`;
  displayResource("/research-audit-reset").dispose();
  axios.get.mockReset(); axios.post.mockReset();
  fixtures = {
    "/data_quality/overview": { checks: [], trading_gate: { decision: "WATCH", blockers: [] }, remediation: { attempts: [] } },
    "/scheduler/overview": { rows: [], jobs: [], summary: {} },
    "/data_quality/events?limit=25": { events: [] },
    "/truth_review/overview": { overall: { score: 73, rating: "WATCH", holes: [] }, investor_packet: { proof_points: [], recommended_next_actions: [] }, systems: { scheduler: { jobs: [] } } },
    "/truth_review/ledger?limit=80": { events: [] }, "/truth_review/packets?limit=8": { packets: [] },
    "/kronos/forecast": { forecasts: [], generated_at: "2026-10-08T14:00:00Z" },
    "/kronos/status": {}, "/kronos/accuracy?limit=900": {}, "/kronos/learning?limit=900": {},
    "/kronos/disagreements?limit=250": { rows: [], summary: [] },
    "/kronos/candle_forecast/SPY": { timeframes: [] },
    "/kronos/calendar": { days: [], available_years: [2026] },
  };
  axios.get.mockImplementation(respond);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => { root.unmount(); await flush(); });
  expect(axios.post).not.toHaveBeenCalled(); container.remove(); visibility.mockRestore(); jest.useRealTimers();
});

describe.each([
  ["Quality", QualityPage, "/data_quality/overview", 30000, "quality-display-warning"],
  ["Truth", TruthReviewPage, "/truth_review/overview", 60000, "truth-display-warning"],
])("%s display lifecycle", (name, Page, path, interval, warningId) => {
  test("hidden mount/polling pause and visible return do not accelerate side-effecting overview", async () => {
    visibility.mockReturnValue("hidden"); await render(Page); await advance(interval * 2);
    expect(axios.get).not.toHaveBeenCalled();
    await show(); expect(overviewCalls(path)).toHaveLength(1);
    await show(); await show(); expect(overviewCalls(path)).toHaveLength(1);
    await advance(interval); expect(overviewCalls(path)).toHaveLength(2);
    if (name === "Truth") expect(overviewCalls(path)[0][1].params).toEqual({ persist: false });
    visibility.mockReturnValue("hidden"); await advance(interval * 2);
    expect(overviewCalls(path)).toHaveLength(2);
  });
  test("pending overview never overlaps; unmount aborts and late completion stays ignored", async () => {
    let resolveOverview;
    axios.get.mockImplementation(url => url === `/api${path}` ? new Promise(resolve => { resolveOverview = resolve; }) : respond(url));
    await render(Page); await advance(interval * 3); await show();
    expect(overviewCalls(path)).toHaveLength(1);
    const signal = overviewCalls(path)[0][1].signal;
    await act(async () => { root.unmount(); await flush(); });
    expect(signal.aborted).toBe(true);
    await act(async () => { resolveOverview({ data: fixtures[path] }); await flush(); });
    await advance(interval * 2); expect(overviewCalls(path)).toHaveLength(1); expect(container.textContent).toBe("");
    root = createRoot(container);
  });
  test("empty/malformed partial payloads render unavailable rather than invented success", async () => {
    fixtures[path] = name === "Quality" ? { checks: {}, trading_gate: { blockers: {} }, remediation: { attempts: [null, 9] } } : { overall: { holes: {} }, investor_packet: { proof_points: {}, recommended_next_actions: [null] }, systems: { scheduler: { jobs: {} } } };
    await render(Page);
    expect(container.querySelector(`[data-testid="${warningId}"]`).textContent).toContain("malformed lists");
    expect(container.textContent).toContain("UNAVAILABLE"); expect(container.textContent).not.toContain("NaN");
    if (name === "Quality") { await click("SCHEDULER"); expect(container.textContent).toContain("UNAVAILABLE"); expect(container.textContent).not.toContain("ARMED"); }
    else expect(container.textContent).not.toContain("+0.0%");
  });
  test("overview failure retains last data and warning clears on next successful cadence", async () => {
    await render(Page);
    axios.get.mockImplementation(url => url === `/api${path}` ? Promise.reject(new Error("offline")) : respond(url));
    await advance(interval);
    expect(container.querySelector(`[data-testid="${warningId}"]`).textContent).toContain("outdated");
    expect(container.textContent).toContain(name === "Quality" ? "WATCH" : "73.0 / 100");
    axios.get.mockImplementation(respond); await advance(interval);
    expect(container.querySelector(`[data-testid="${warningId}"]`)).toBeNull();
  });
  test("read-only proof failures are surfaced independently from healthy overview", async () => {
    const proof = name === "Quality" ? "/scheduler/overview" : "/truth_review/ledger?limit=80";
    axios.get.mockImplementation(url => url.endsWith(proof) ? Promise.reject(new Error("offline")) : respond(url));
    await render(Page);
    expect(container.querySelector(`[data-testid="${warningId}"]`).textContent).toContain("could not refresh");
    expect(overviewCalls(path)).toHaveLength(1);
  });
});

test("Kronos checks every subtab with malformed list fixtures and no automatic mutation", async () => {
  fixtures["/kronos/forecast"] = { forecasts: {} };
  fixtures["/kronos/accuracy?limit=900"] = { by_timeframe: {}, by_regime: {}, recent: {} };
  fixtures["/kronos/disagreements?limit=250"] = { rows: {}, summary: {} };
  fixtures["/kronos/calendar"] = { days: {}, available_years: {} };
  fixtures["/kronos/candle_forecast/SPY"] = { timeframes: {} };
  await render(KronosPage);
  expect(container.querySelector('[data-testid="kronos-display-warning"]').textContent).toContain("malformed lists");
  for (const tab of ["SANDBOX", "CALENDAR", "PM DISAGREEMENTS", "FORECAST MEMORY", "FORECAST"]) await click(tab);
  expect(container.textContent).not.toContain("NaN");
  expect(axios.get.mock.calls.some(([url]) => url.endsWith("/options_desk/trades?sync_live=false"))).toBe(true);
  expect(axios.get.mock.calls.some(([url]) => url.includes("sync_live=true"))).toBe(false);
});

test("Kronos hidden mount makes no requests and guarded batch does not overlap", async () => {
  visibility.mockReturnValue("hidden"); await render(KronosPage); await advance(120000);
  expect(axios.get).not.toHaveBeenCalled();
  axios.get.mockImplementation((url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new Error("cancelled")), { once: true });
  }));
  await show(); const count = axios.get.mock.calls.length;
  await advance(120000);
  expect(overviewCalls("/portfolio_manager/latest")).toHaveLength(1);
  const batchSignals = axios.get.mock.calls.filter(([url]) => url === "/api/portfolio_manager/latest").map(([, options]) => options.signal);
  await act(async () => { root.unmount(); await flush(); });
  expect(batchSignals.every(signal => signal.aborted)).toBe(true);
  expect(axios.get.mock.calls.length).toBe(count);
  root = createRoot(container);
});

test("Kronos calendar period replacement aborts prior read and ignores its late result", async () => {
  const pending = [];
  axios.get.mockImplementation((url, options) => url.endsWith("/kronos/calendar") ? new Promise(resolve => pending.push({ resolve, options })) : respond(url));
  await render(KronosPage); await click("CALENDAR");
  expect(pending).toHaveLength(1);
  const select = container.querySelector("select");
  await act(async () => { select.value = "9"; select.dispatchEvent(new Event("change", { bubbles: true })); await flush(); });
  expect(pending).toHaveLength(2); expect(pending[0].options.signal.aborted).toBe(true);
  await act(async () => { pending[1].resolve({ data: { month: 9, year: 2026, days: [], available_years: [2026] } }); await flush(); });
  await act(async () => { pending[0].resolve({ data: { month: 10, year: 2026, days: [{ date: "2026-10-01", has_prediction: true, ticker: "OBSOLETE" }] } }); await flush(); });
  expect(select.value).toBe("9"); expect(container.textContent).not.toContain("OBSOLETE");
  await click("THIS MONTH"); expect(pending).toHaveLength(3);
  expect(pending[2].options.params).toEqual({ year: 2026, month: 10 });
});

test("Kronos leaving forecast cancels chart reads and ignores obsolete response", async () => {
  let release;
  axios.get.mockImplementation(url => url.includes("/kronos/candle_forecast/") ? new Promise(resolve => { release = resolve; }) : respond(url));
  await render(KronosPage);
  const signal = axios.get.mock.calls.find(([url]) => url.includes("/kronos/candle_forecast/"))[1].signal;
  await click("FORECAST MEMORY"); expect(signal.aborted).toBe(true);
  await act(async () => { release({ data: { timeframes: [{ ok: true, timeframe: "OBSOLETE" }] } }); await flush(); });
  expect(container.textContent).not.toContain("OBSOLETE");
});

test("Kronos disagreements paginate all persisted rows instead of silently truncating at eighty", async () => {
  fixtures["/kronos/disagreements?limit=250"] = { rows: Array.from({ length: 81 }, (_, index) => ({ ticker: `ROW${index}`, status: "OPEN_AUDIT" })), summary: [] };
  await render(KronosPage); await click("PM DISAGREEMENTS");
  expect(container.textContent).toContain("ROW79"); expect(container.textContent).not.toContain("ROW80");
  await click("Next"); expect(container.textContent).toContain("ROW80"); expect(container.textContent).not.toContain("ROW79");
});
