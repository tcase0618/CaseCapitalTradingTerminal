import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import ContractsPage from "./ContractsPage";
import MacroPage from "./MacroPage";
import GeoRiskPage from "./GeoRiskPage";

jest.mock("../config", () => ({ API: "/api" }));
jest.mock("axios", () => ({ get: jest.fn() }));
jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn(), Link: ({ children }) => <span>{children}</span> }));
jest.mock("./CrtShell", () => ({ tokens: {}, CrtShell: ({ children, headerRight }) => <main>{headerRight}{children}</main>, Card: ({ children }) => <section>{children}</section>, Stat: ({ label, value }) => <div>{label}{value}</div> }));
let root, container;
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
beforeEach(() => {
  axios.get.mockReset(); global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test.each([ContractsPage, MacroPage, GeoRiskPage])("research reads have finite timeouts and abort on route departure: %p", async Page => {
  axios.get.mockResolvedValue({ data: {} });
  await act(async () => { root.render(<Page />); await flush(); });
  const calls = axios.get.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  expect(calls.every(([, opts]) => opts.signal && opts.timeout > 0)).toBe(true);
  await act(async () => root.render(<div>Other route</div>));
  expect(calls.every(([, opts]) => opts.signal.aborted)).toBe(true);
});

test("Macro retains the successful calendar when overview fails", async () => {
  axios.get.mockImplementation(url => url.includes("/macro/overview") ? Promise.reject(new Error("offline")) : Promise.resolve({ data: { events: [{ name: "Fixture payroll", title: "Fixture payroll", date: "2026-10-09", impact: "HIGH" }] } }));
  await act(async () => { root.render(<MacroPage />); await flush(); });
  expect(container.textContent).toContain("Macro refresh incomplete");
  expect(container.textContent).toContain("EVENTS1");
  expect(container.textContent).toContain("UNAVAILABLE");
});

test("Contracts failure is explicit rather than a successful empty result", async () => {
  axios.get.mockRejectedValue(new Error("offline"));
  await act(async () => { root.render(<ContractsPage />); await flush(); });
  expect(container.textContent).toContain("Contract source unavailable");
});

test("GeoRisk malformed lists do not crash the diagram", async () => {
  axios.get.mockResolvedValue({ data: { events: {}, chokepoints: {}, live_alpaca: {}, db_positions: {} } });
  await act(async () => { root.render(<GeoRiskPage />); await flush(); });
  expect(container.textContent).toContain("GLOBAL CATALYST MAP");
});
