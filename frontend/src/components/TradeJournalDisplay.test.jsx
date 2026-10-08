import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import { TradeJournalView } from "./TradeJournalPage";

jest.mock("../config", () => ({ API: "/api" }));
jest.mock("axios", () => ({ get: jest.fn() }));
jest.mock("./CrtShell", () => ({ tokens: {}, CrtShell: ({ children }) => <main>{children}</main>, Card: ({ children, title }) => <section>{title}{children}</section>, Stat: ({ label, value }) => <div>{label}{value}</div> }));
jest.mock("./Institutional", () => ({ DataConfidenceStrip: ({ items }) => <div>{items.map(item => <span key={item.label}>{item.label}: {item.value}</span>)}</div> }));
let root, container;
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
beforeEach(() => { axios.get.mockReset(); global.IS_REACT_ACT_ENVIRONMENT = true; container = document.createElement("div"); document.body.append(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("journal tolerates malformed lists across all views without adding recurring requests", async () => {
  axios.get.mockResolvedValue({ data: { decision_time_capsules: {}, trade_dna: {}, orders: {}, candidates: {} } });
  await act(async () => { root.render(<TradeJournalView />); await flush(); });
  for (const name of ["GRAVEYARD", "ALT UNIVERSE", "EVIDENCE", "DNA", "CAPSULES", "OPTIONS", "EQUITIES"]) {
    const button = [...container.querySelectorAll("button")].find(node => node.textContent === name);
    expect(button).toBeDefined(); await act(async () => { button.click(); await flush(); });
  }
  expect(axios.get).toHaveBeenCalledTimes(3);
  expect(container.textContent).not.toContain("NaN");
  for (const [, options] of axios.get.mock.calls) expect(options.timeout).toBe(20000);
});

test("failed sources are visible and pending requests abort on unmount", async () => {
  axios.get.mockRejectedValue(new Error("offline"));
  await act(async () => { root.render(<TradeJournalView />); await flush(); });
  expect(container.textContent).toContain("JOURNAL SYNC DEGRADED");
  expect(container.textContent).toContain("PM Decisions: --");
  const signal = axios.get.mock.calls[0][1].signal;
  await act(async () => root.unmount());
  expect(signal.aborted).toBe(true);
  root = createRoot(container);
});
