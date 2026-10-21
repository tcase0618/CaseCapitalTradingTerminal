import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import TickerPage, { KronosTickerForecastBox } from "./TickerPage";
import { DISPLAY_REFRESH_EVENT } from "../hooks/useDisplayRefresh";

let mockTicker = "AAA";
jest.mock("react-router-dom", () => ({ useParams: () => ({ ticker: mockTicker }), Link: ({ children }) => <span>{children}</span> }));
jest.mock("../config", () => ({ API: "/api" }));
jest.mock("axios", () => ({ get: jest.fn(), CanceledError: class extends Error { constructor(message) { super(message); this.name = "CanceledError"; } } }));
jest.mock("./TradingViewMiniChart", () => () => <div>Chart fixture</div>);
jest.mock("./CrtShell", () => ({ tokens: {}, CrtShell: ({ children, headerRight }) => <main>{headerRight}{children}</main>, Card: ({ children }) => <section>{children}</section>, Stat: ({ label, value }) => <div>{label}{value}</div> }));

let root, container;
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
beforeEach(() => {
  mockTicker = "AAA";
  axios.get.mockReset();
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("numeric strings and malformed signal lists cannot crash a company profile", async () => {
  axios.get.mockImplementation(url => Promise.resolve({ data: url === "/api/ticker/AAA" ? { ticker: "AAA", price: "12.5", entry_low: "12", stop_loss: "11", signals: {} } : {} }));
  await act(async () => { root.render(<TickerPage />); await flush(); });
  expect(container.textContent).toContain("$12.50");
  expect(container.textContent).toContain("SIGNAL SCORE—");
  expect(axios.get.mock.calls.every(([, options]) => options.timeout === 12000 && options.signal)).toBe(true);
});

test("late old-company responses are ignored after a ticker change", async () => {
  let oldResolve;
  axios.get.mockImplementation(url => url === "/api/ticker/AAA" ? new Promise(resolve => { oldResolve = resolve; }) : Promise.resolve({ data: url === "/api/ticker/BBB" ? { ticker: "BBB", price: 20 } : {} }));
  await act(async () => { root.render(<TickerPage />); await flush(); });
  const oldSignal = axios.get.mock.calls[0][1].signal;
  mockTicker = "BBB";
  await act(async () => { root.render(<TickerPage />); await flush(); });
  await act(async () => { oldResolve({ data: { ticker: "AAA", price: 1 } }); await flush(); });
  expect(oldSignal.aborted).toBe(true);
  expect(container.textContent).toContain("$20.00");
  expect(container.textContent).not.toContain("$1.00");
});

test("failed company read is unavailable rather than a fabricated zero score", async () => {
  axios.get.mockRejectedValue(new Error("offline"));
  await act(async () => { root.render(<TickerPage />); await flush(); });
  expect(container.textContent).toContain("Company data unavailable");
  expect(container.textContent).toContain("CASE SCORE—");
});

test("same-company refresh preserves its profile during loading and failure", async () => {
  axios.get.mockImplementation(url => Promise.resolve({ data: url === "/api/ticker/AAA" ? { ticker: "AAA", price: 12.5 } : {} }));
  await act(async () => { root.render(<TickerPage />); await flush(); });
  let fail;
  axios.get.mockImplementation(url => url === "/api/ticker/AAA" ? new Promise((resolve, reject) => { fail = reject; }) : Promise.resolve({ data: {} }));
  await act(async () => { window.dispatchEvent(new Event(DISPLAY_REFRESH_EVENT)); await flush(); });
  expect(container.textContent).toContain("$12.50");
  expect(container.textContent).not.toContain("FETCHING INTELLIGENCE");
  await act(async () => { fail(new Error("offline")); await flush(); });
  expect(container.textContent).toContain("$12.50");
  expect(container.textContent).toContain("Prior displayed values may be outdated");
});

test("company forecasts display real array horizons without inventing a hold cone", async () => {
  await act(async () => root.render(<KronosTickerForecastBox data={{ price: 100, time_target: { low: 10, high: 20 } }} kronos={{ ok: true, battle_card: {
    input_asof: "2026-10-09T20:00:00Z", target_at: "2026-10-12T20:00:00Z",
    horizons: [{ horizon: "NEXT FULL RTH BAR", forecast_pct: 2, cone_low_pct: -3, cone_high_pct: 4 }],
  } }} />));
  expect(container.textContent).toContain("NEXT FULL RTH BAR");
  expect(container.textContent).toContain("-3.0%");
  expect(container.textContent).toContain("+4.0%");
  expect(container.textContent).toContain("2026-10-12T20:00:00Z");
  expect(container.textContent).not.toContain("1 DAY FORECAST CONE");
  expect(container.textContent).toContain("PM ALIGNMENTUNKNOWN");
});

test("failed or incomplete forecasts never produce locally synthesized ranges", async () => {
  await act(async () => root.render(<KronosTickerForecastBox data={{ price: 100 }} kronos={{ ok: false, battle_card: { forecast_pct: 7 } }} />));
  expect(container.textContent).toContain("Kronos unavailable");
  expect(container.textContent).not.toContain("+7.0%");
  await act(async () => root.render(<KronosTickerForecastBox data={{ price: 100 }} kronos={{ ok: true, battle_card: { forecast_pct: 7 } }} />));
  expect(container.textContent).toContain("No supplied forecast ranges available");
});
