import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import TickerPage from "./TickerPage";

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
