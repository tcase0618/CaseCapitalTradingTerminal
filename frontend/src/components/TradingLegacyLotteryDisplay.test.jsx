import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import LotteryPage from "./LotteryPage";
import TradeFloorPage from "./TradeFloorPage";
import TFEnginePage from "./TFEnginePage";
import useDisplayResource from "../hooks/useDisplayResource";

jest.mock("../config", () => ({ API: "/api" }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("../hooks/useDisplayResource", () => ({ __esModule: true, default: jest.fn() }));
jest.mock("sonner", () => ({ toast: Object.assign(jest.fn(), { error: jest.fn() }) }));
jest.mock("./CrtShell", () => ({ tokens: {}, CrtShell: ({ children, title, headerRight }) => <main>{title}{headerRight}{children}</main>, Card: ({ children, title }) => <section>{title}{children}</section>, Stat: ({ label, value }) => <div>{label}{value}</div> }));
let root, container;
const flush = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };
const click = async text => { const button = [...container.querySelectorAll("button")].find(node => node.textContent.trim() === text); expect(button).toBeDefined(); await act(async () => { button.click(); await flush(); }); };
beforeEach(() => {
  axios.get.mockReset(); axios.post.mockReset(); useDisplayResource.mockReset();
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); expect(axios.post).not.toHaveBeenCalled(); });

test("Lottery exposes every candidate by pagination and all six views tolerate partial lists", async () => {
  axios.get.mockResolvedValue({ data: { candidates: Array.from({ length: 65 }, (_, i) => ({ ticker: "TEST" + i, triggers: {}, penalties: {}, score: 50 })), tickets: {}, truth_board: { segments: { variant: {} }, latest_grades: {}, learning: { notes: {} }, learned_config: { retired_variants: {} } } } });
  await act(async () => { root.render(<LotteryPage />); await flush(); });
  expect(container.textContent).toContain("TEST29"); expect(container.textContent).not.toContain("TEST30");
  const next = container.querySelector('[aria-label="Next Lottery page"]');
  await act(async () => { next.click(); await flush(); });
  expect(container.textContent).toContain("TEST30"); expect(container.textContent).toContain("31-60 OF 65");
  for (const tab of ["LIVE TICKETS", "TRUTH BOARD", "LEARNING ENGINE", "VARIANT BOOK", "METHODOLOGY"]) await click(tab);
  expect(container.textContent).not.toContain("NaN");
});

test("legacy Trade Floor renders every tab on unavailable reads without claiming broker readiness", async () => {
  axios.get.mockRejectedValue(new Error("offline"));
  await act(async () => { root.render(<TradeFloorPage />); await flush(); });
  expect(container.textContent).toContain("LEGACY ALPACA EQUITY"); expect(container.textContent).toContain("refresh incomplete");
  for (const tab of ["SIGNAL FILTER", "ACCOUNT PERFORMANCE", "RISK DASHBOARD", "JOURNAL", "LIVE POSITIONS"]) await click(tab);
  expect(container.textContent).not.toContain("NaN");
});

test("legacy engine malformed combo payload is unavailable, not a real empty performance result", async () => {
  useDisplayResource.mockImplementation(url => ({ data: url.endsWith("/status") ? { phase: "pre_adjustment" } : { combos: {} }, error: null, refresh: jest.fn() }));
  await act(async () => { root.render(<TFEnginePage />); await flush(); });
  expect(container.textContent).toContain("Missing results are not zero performance");
  expect(container.textContent).not.toContain("NaN");
});
