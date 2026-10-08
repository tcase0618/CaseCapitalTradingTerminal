import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import OptionsDeskPage from "./OptionsDeskPage";
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
jest.mock("./TradingViewMiniChart", () => () => <div>CHART</div>);
let container, root, visibility;
let serial = 0;
const flush = async () => { for (let i = 0; i < 100; i += 1) await Promise.resolve(); };
const mount = async () => { await act(async () => { root.render(<OptionsDeskPage />); await flush(); }); };
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
  axios.defaults.headers.common.Authorization = "OptionsDeskPageNAME-test-" + serial++;
  displayResource("/OptionsDeskPageNAME-test-reset").dispose();
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

const ticket = { ticker: "AAPL", candidate_id: "candidate-1", route: "OPTION", manual_fire_ready: true, instrument: {}, exit_policy: { tiers: "bad" }, blocked_reasons: {}, strategy_lane: { reasons: "bad" }, data_provider: 12 };
const fixture = url => {
  if (url.includes("/account")) return { ok: true, account: { equity: 20000, status: "ACTIVE" } };
  if (url.includes("/candidates")) return { candidates: [null, ticket], summary: {} };
  if (url.includes("/positions")) return { positions: [null] };
  if (url.includes("/orders")) return { orders: {} };
  if (url.includes("/risk")) return { checks: {} };
  if (url.includes("/trades")) return { trades: [null] };
  if (url.includes("/tail")) return { candidates: [null, { ...ticket, candidate_id: "tail-1", tail_gate: { reasons: {} } }], policy: {} };
  if (url.includes("/leaps")) return { holdings: [null, { ticker: "AAPL", kronos_1y: { cone_low_pct: null } }], candidates: {} };
  return { rows: [] };
};

test("all Options subtabs tolerate malformed lists and missing cone data", async () => {
  await mount();
  expect(container.textContent).toContain("AAPL");
  await click("TAIL HUNTER");
  expect(container.textContent).toContain("TAIL HUNTER QUEUE");
  await click("LEAPS SLEEVE");
  expect(container.textContent).toContain("1Y cone unavailable.");
  await click("OPTIONS DESK");
  expect(container.textContent).toContain("MANUAL EXECUTION TICKET");
  expect(axios.post).not.toHaveBeenCalled();
});

test("only persisted risk and explicit non-sync trades poll; hidden tabs pause", async () => {
  await mount();
  axios.get.mockClear();
  await advance(60000);
  expect(axios.get.mock.calls.map(([url]) => url).sort()).toEqual(["/api/options_desk/risk", "/api/options_desk/trades?sync_live=false"]);
  axios.get.mockClear();
  visibility.mockReturnValue("hidden");
  await advance(120000);
  expect(axios.get).not.toHaveBeenCalled();
  expect(axios.post).not.toHaveBeenCalled();
});

test("LSE context is cancelled when leaving DESK and uses encoded keys", async () => {
  const signals = [];
  axios.get.mockImplementation((url, config) => url.includes("/data/lse/")
    ? new Promise(() => { signals.push(config.signal); })
    : Promise.resolve({ data: url.includes("/candidates") ? { candidates: [{ ...ticket, ticker: "AAPL /?" }] } : fixture(url) }));
  await mount();
  expect(signals).toHaveLength(2);
  expect(axios.get.mock.calls.some(([url]) => url.includes("/options/AAPL%20%2F%3F?"))).toBe(true);
  await click("TAIL HUNTER");
  expect(signals.every(signal => signal.aborted)).toBe(true);
});

test("partial failures are named instead of becoming successful empty orders", async () => {
  axios.get.mockImplementation(url => url.endsWith("/orders")
    ? Promise.reject(new Error("offline")) : Promise.resolve({ data: fixture(url) }));
  await mount();
  expect(container.querySelector('[role="alert"]').textContent).toContain("orders");
});

test("manual paper execution retains endpoint and candidate payload", async () => {
  axios.post.mockResolvedValue({ data: { ok: true, order: { id: "paper-1" } } });
  await mount();
  expect(axios.post).not.toHaveBeenCalled();
  await click("MANUAL FIRE PAPER ORDER");
  expect(axios.post.mock.calls[0]).toEqual(["/api/options_desk/execute", { candidate_id: "candidate-1" }]);
  await click("TAIL HUNTER");
  await click("FIRE TAIL PAPER ORDER");
  expect(axios.post.mock.calls[1]).toEqual(["/api/options_desk/tail/execute", { candidate_id: "tail-1" }]);
});

test("all active mount reads abort on unmount without mutation", async () => {
  const signals = [];
  axios.get.mockImplementation((url, config) => new Promise(() => { signals.push(config.signal); }));
  await mount();
  expect(signals.length).toBeGreaterThan(6);
  await act(async () => { root.unmount(); await flush(); });
  expect(signals.every(signal => signal.aborted)).toBe(true);
  expect(axios.post).not.toHaveBeenCalled();
});

test("missing premiums and expectancy stay unavailable instead of zero", async () => {
  axios.get.mockImplementation(url => Promise.resolve({ data: url.includes("/expectancy") ? { grind: { expectancy_pct: null, avg_spread_cost: null } } : fixture(url) }));
  await mount();
  expect(container.textContent).toContain("GRIND EV-");
  expect(container.textContent).toContain("GRIND WIN-%");
  expect(container.textContent).not.toContain("GRIND EV0.0%");
  expect(container.textContent).toContain("SPREAD COST-");
});
