import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import PortfolioManagerPage from "./PortfolioManagerPage";
import { displayResource } from "../hooks/useDisplayResource";

jest.mock("../config", () => ({ API: "/api" }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), defaults: { headers: { common: {} } } }));
jest.mock("./CrtShell", () => ({
  tokens: { accent: "#c8a84b", accent2: "#5eead4", dim: "#888888", muted: "#999999", labelLight: "#ffffff", hairline: "1px solid #333333", cardBg: "#111111", cardBgHi: "#222222" },
  CrtShell: ({ children, headerRight }) => <main>{headerRight}{children}</main>,
  Card: ({ title, children }) => <section><h2>{title}</h2>{children}</section>,
  Stat: ({ label, value }) => <div>{label}: {value}</div>,
}));
jest.mock("./Institutional", () => ({ DataConfidenceStrip: () => null }));
jest.mock("./TradeJournalPage", () => ({ TradeJournalView: () => <div>JOURNAL CHILD</div> }));
jest.mock("recharts", () => {
  const React = require("react");
  const Chart = ({ children }) => React.createElement("div", null, children);
  const Empty = () => null;
  return { ResponsiveContainer: Chart, LineChart: Chart, PieChart: Chart, Pie: Chart, Cell: Empty,
    Line: Empty, XAxis: Empty, YAxis: Empty, Tooltip: Empty, ReferenceLine: Empty, CartesianGrid: Empty };
});
let container, root, visibility;
let serial = 0;
const flush = async () => { for (let i = 0; i < 100; i += 1) await Promise.resolve(); };
const mount = async () => { await act(async () => { root.render(<PortfolioManagerPage />); await flush(); }); };
const click = async text => {
  const button = [...container.querySelectorAll("button")].find(node => node.textContent.trim() === text);
  expect(button).toBeDefined();
  await act(async () => { button.click(); await flush(); });
};
const advance = async ms => { await act(async () => { jest.advanceTimersByTime(ms); await flush(); }); };
const change = async (node, value) => {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value").set;
    setter.call(node, value);
    node.dispatchEvent(new Event("change", { bubbles: true }));
    await flush();
  });
};
beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date("2026-10-08T15:00:00Z"));
  global.IS_REACT_ACT_ENVIRONMENT = true;
  visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  sessionStorage.clear();
  axios.defaults.headers.common.Authorization = "PortfolioManagerPage-test-" + serial++;
  displayResource("/PortfolioManagerPage-test-reset").dispose();
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

const fixture = url => {
  if (url.endsWith("/portfolio_manager/latest")) return { recommendations: [null, { ticker: "AAPL", action: "STARTER", reasons: {}, cautions: "bad" }], summary: { equity_basis: 1000, shock_tests: {}, equity_source: 12, regime: { status: 12 } }, exposure: { by_sector: {}, by_action: [null] } };
  if (url.endsWith("/options/latest")) return { candidates: [null, { ticker: "AAPL", route: "OPTION", data_provider: 12 }], summary: {} };
  if (url.includes("/learning/status")) return { phase: 12, action_stats: {}, sector_stats: [null], signal_stats: {}, latest_decisions: [null, { ticker: "AAPL", data_provider: 12 }], recommendations: {} };
  if (url.includes("/options/backtest")) return { method: 12, sample_rows: [null, { ticker: "AAPL", data_provider: 12 }], summary: {} };
  if (url.includes("/backtest")) return { action_stats: {}, ratchet_stats: [null], exit_simulation_by_ratchet: {}, sample_decisions: [null], summary: {} };
  if (url.includes("/benchmark_curve")) return { curve: [null, { date: "2026-10-08", terminal_total_pct: null, relative_pct: null, spy_return_pct: null }] };
  if (url.endsWith("/account")) return { ok: true, paper_only: true, account: { equity: 20000, status: 12 } };
  if (url.endsWith("/positions")) return { positions: {} };
  if (url.endsWith("/orders")) return { orders: [null] };
  if (url.endsWith("/rulesets")) return { rulesets: {} };
  return {};
};

test("every owned PM view tolerates malformed arrays without automatic commands", async () => {
  await mount();
  expect(axios.get.mock.calls.some(([url]) => url.includes("/learning/status"))).toBe(false);
  for (const tab of ["EQUITIES", "OPTIONS", "TOTAL"]) await click(tab);
  await click("LEARNING");
  expect(container.textContent).toContain("ACTION PERFORMANCE");
  await click("OPTIONS");
  expect(container.textContent).toContain("OPTIONS LEARNING RECOMMENDATIONS");
  await click("BACKTEST");
  expect(container.textContent).toContain("SANDBOX RULE TESTER");
  await click("OPTIONS");
  expect(container.textContent).toContain("OPTIONS REPLAY SAMPLE");
  await click("P/L CALENDAR");
  expect(container.textContent).toContain("PM P/L CALENDAR");
  expect(container.textContent).not.toContain("+0.0%");
  await click("TRADE JOURNAL");
  expect(container.textContent).toContain("JOURNAL CHILD");
  expect(axios.post).not.toHaveBeenCalled();
});

test("learning uses cached cancellable keys, pauses while hidden, and detaches when leaving", async () => {
  await mount();
  await click("LEARNING");
  const learningCalls = () => axios.get.mock.calls.filter(([url]) => url.endsWith("/learning/status"));
  expect(learningCalls()).toHaveLength(1);
  expect(learningCalls()[0][1].signal).toBeDefined();
  visibility.mockReturnValue("hidden");
  await advance(600000);
  expect(learningCalls()).toHaveLength(1);
  visibility.mockReturnValue("visible");
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await flush(); });
  expect(learningCalls()).toHaveLength(2);
  await click("FUND");
  await advance(600000);
  expect(learningCalls()).toHaveLength(2);
});

test("failed calendar read does not loop and explicit refresh retries it", async () => {
  axios.get.mockImplementation(url => url.includes("/benchmark_curve")
    ? Promise.reject(new Error("offline")) : Promise.resolve({ data: fixture(url) }));
  await mount();
  await click("P/L CALENDAR");
  expect(container.querySelector('[role="alert"]').textContent).toContain("benchmark_curve");
  expect(axios.get.mock.calls.filter(([url]) => url.includes("/benchmark_curve"))).toHaveLength(1);
  await advance(600000);
  expect(axios.get.mock.calls.filter(([url]) => url.includes("/benchmark_curve"))).toHaveLength(1);
  await click("REFRESH P/L");
  expect(axios.get.mock.calls.filter(([url]) => url.includes("/benchmark_curve"))).toHaveLength(2);
});

test("failed plan reprice retains previous recommendations and names the failure", async () => {
  await mount();
  await click("EQUITIES");
  axios.get.mockImplementation(url => url.endsWith("/portfolio_manager/latest")
    ? Promise.reject(new Error("offline")) : Promise.resolve({ data: fixture(url) }));
  await click("REPRICE PLAN");
  expect(container.textContent).toContain("AAPL");
  expect(container.querySelector('[role="alert"]').textContent).toContain("portfolio_manager/latest");
  expect(axios.post).not.toHaveBeenCalled();
});

test("manual sandbox uses original numeric override params and no POST", async () => {
  await mount();
  await click("BACKTEST");
  axios.get.mockClear();
  await click("RUN SANDBOX");
  const [url, options] = axios.get.mock.calls[0];
  expect(url).toBe("/api/portfolio_manager/backtest");
  expect(options.params).toEqual({ limit_scans: 120, mode: "BALANCED", equity: 1000, max_position_pct: 0.08, max_single_name_risk_pct: 0.0125, max_gross_deployment_pct: 0.35, accumulate_score: 70, accumulate_rr: 1.8, starter_score: 58, starter_rr: 1.3, watch_score: 45 });
  expect(axios.post).not.toHaveBeenCalled();
});

test("root display reads abort on unmount", async () => {
  const signals = [];
  axios.get.mockImplementation((url, options) => new Promise(() => signals.push(options.signal)));
  await mount();
  expect(signals).toHaveLength(7);
  await act(async () => { root.unmount(); await flush(); });
  expect(signals.every(signal => signal.aborted)).toBe(true);
});

test("missing options capital is unavailable, never an invented 20000 balance", async () => {
  axios.get.mockImplementation(url => Promise.resolve({ data: url.endsWith("/account") ? { ok: false, account: {} } : fixture(url) }));
  await mount();
  expect(container.textContent).toContain("OPTIONS FUND: --");
  expect(container.textContent).toContain("TOTAL FUND CAPITAL: --");
  expect(container.textContent).not.toContain("NaN");
});
