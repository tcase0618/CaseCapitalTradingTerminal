import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import OptionsDeskPage from "./OptionsDeskPage";
import LotteryPage from "./LotteryPage";
import PharmaPage from "./PharmaPage";
import PortfolioManagerPage from "./PortfolioManagerPage";

jest.mock("../config", () => ({ API: "/api" }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), defaults: { headers: { common: {} } } }));
jest.mock("react-router-dom", () => ({ Link: ({ children, to }) => <a href={to}>{children}</a> }));
jest.mock("sonner", () => ({ toast: jest.fn() }));
jest.mock("./CrtShell", () => ({
  tokens: { accent: "#c8a84b", accent2: "#5eead4", dim: "#888888", muted: "#999999", labelLight: "#ffffff", hairline: "1px solid #333333", cardBg: "#111111", pageBg: "#000000" },
  CrtShell: ({ children, headerRight }) => <main>{headerRight}{children}</main>,
  Card: ({ title, children }) => <section><h2>{title}</h2>{children}</section>,
  Stat: ({ label, value, sub }) => <div data-stat={label}>{label}: {value}<small>{sub}</small></div>,
}));
jest.mock("./Institutional", () => ({ DataConfidenceStrip: () => null }));
jest.mock("./TradingViewMiniChart", () => () => <div />);
jest.mock("./TradeJournalPage", () => ({ TradeJournalView: () => <div /> }));
jest.mock("recharts", () => {
  const Chart = ({ children }) => <div>{children}</div>;
  const Empty = () => null;
  return { ResponsiveContainer: Chart, LineChart: Chart, PieChart: Chart, Pie: Chart, Cell: Empty,
    Line: Empty, XAxis: Empty, YAxis: Empty, Tooltip: Empty, ReferenceLine: Empty, CartesianGrid: Empty };
});

let container, root, serial = 0;
const flush = async () => { for (let i = 0; i < 100; i += 1) await Promise.resolve(); };
const mount = async Page => { await act(async () => { root.render(<Page />); await flush(); }); };
const click = async label => {
  const button = [...container.querySelectorAll("button")].find(node => node.textContent.trim() === label);
  expect(button).toBeDefined();
  await act(async () => { button.click(); await flush(); });
};
const change = async (node, value, event = "change") => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value").set.call(node, value);
    node.dispatchEvent(new Event(event, { bubbles: true }));
    await flush();
  });
};
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  axios.defaults.headers.common.Authorization = `oct12-page-audit-${serial++}`;
  axios.get.mockReset();
  axios.post.mockReset();
  axios.get.mockResolvedValue({ data: {} });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => { root.unmount(); await flush(); });
  container.remove();
  expect(axios.post).not.toHaveBeenCalled();
});

test("options loading and unavailable metrics never imply an armed account or zero premium", async () => {
  axios.get.mockImplementation(() => new Promise(() => {}));
  await mount(OptionsDeskPage);
  expect(container.textContent).toContain("Loading options display");
  expect(container.querySelector('[data-stat="DESK STATUS"]').textContent).toContain("UNAVAILABLE");
  expect(container.querySelector('[data-stat="DAILY PREMIUM"]').textContent).toContain("DAILY PREMIUM: -");
  expect(container.textContent).not.toContain("ARMED");
});

test("options zero buying power remains zero and absent fill status stays unknown", async () => {
  axios.get.mockImplementation(url => Promise.resolve({ data:
    url.endsWith("/account") ? { ok: true, account: { buying_power: 0 } } :
    url.endsWith("/positions") ? { positions: [{ symbol: "AAPL261218C00200000", unrealized_plpc: null }] } : {} }));
  await mount(OptionsDeskPage);
  expect(container.querySelector('[data-stat="BUYING POWER"]').textContent).toContain("$0.00");
  const profile = [...container.querySelectorAll("section")].find(node => node.textContent.includes("POSITION PROFILE"));
  expect(profile.textContent).toContain("UNKNOWN");
  expect(profile.textContent).not.toContain("LIVE");
  expect(profile.textContent).toContain("P/L-%");
});

test("options sorting and filtering do not alter source-selected execution ticket", async () => {
  const candidates = [
    { ticker: "ZZZ", candidate_id: "z", route: "OPTION", pm_score: null },
    { ticker: "AAA", candidate_id: "a", route: "BOTH", pm_score: 80 },
  ];
  axios.get.mockImplementation(url => Promise.resolve({ data: url.endsWith("/candidates") ? { candidates } : {} }));
  await mount(OptionsDeskPage);
  await change(container.querySelector('[aria-label="Sort options candidates"]'), "PM");
  const table = container.querySelector('[aria-label="Options candidate table"]');
  expect(table.querySelector("tbody tr").textContent).toContain("AAA");
  await change(container.querySelector('[aria-label="Filter options candidates"]'), "NO MATCH", "input");
  expect(table.textContent).toContain("No matching candidates.");
  const ticket = [...container.querySelectorAll("section")].find(node => node.textContent.includes("MANUAL EXECUTION TICKET"));
  expect(ticket.textContent).toContain("ZZZ");
});

test("Lottery open tickets are explicitly paper and unknown current price has no zero return", async () => {
  axios.get.mockResolvedValue({ data: { candidates: [], tickets: [{ ticker: "XYZ", ticket_id: "paper", entry_price: 10, current_price: null }] } });
  await mount(LotteryPage);
  await click("OPEN PAPER TICKETS");
  expect(container.textContent).toContain("OPEN PAPER TICKETS - 1");
  expect(container.textContent).not.toContain("LIVE TICKETS");
  expect(container.querySelector('[data-testid="lottery-ticket-XYZ"]').textContent).not.toContain("+0.0%");
});

test("Pharma score-sorted table puts missing scores last", async () => {
  axios.get.mockImplementation(url => Promise.resolve({ data:
    url.includes("/pdufa") ? { results: [{ ticker: "LOW", binary_event_score: null }, { ticker: "HIGH", binary_event_score: 90 }] } :
    url.includes("/fda_calendar") ? { days: [], events: [] } :
    url.includes("/track_record") ? { history: [] } :
    url.includes("/active") ? { plays: [] } : { results: [] } }));
  await mount(PharmaPage);
  await click("Command");
  expect([...container.querySelectorAll('[data-testid^="pdufa-"]')].map(node => node.dataset.testid)).toEqual(["pdufa-HIGH", "pdufa-LOW"]);
});

test("portfolio missing risk budgets cannot produce zero total planned risk", async () => {
  axios.get.mockImplementation(url => Promise.resolve({ data:
    url.endsWith("/portfolio_manager/latest") ? { summary: { equity_basis: 1000 }, recommendations: [] } :
    url.endsWith("/portfolio_manager/options/latest") ? { candidates: [{ manual_fire_ready: true, risk_budget: null }] } :
    url.endsWith("/options_desk/account") ? { account: { equity: 1000 } } : {} }));
  await mount(PortfolioManagerPage);
  expect(container.querySelector('[data-stat="TOTAL PLANNED RISK"]').textContent).toContain("--");
  expect(container.querySelector('[data-stat="TOTAL PLANNED RISK"]').textContent).not.toContain("$0");
});

test("PM calendar marks proxies and averages cumulative observations instead of adding them", async () => {
  const date = new Date();
  const prefix = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
  axios.get.mockImplementation(url => Promise.resolve({ data: url.includes("benchmark_curve") ? { curve: [
    { date: `${prefix}-01`, terminal_total_pct: 10, spy_return_pct: 2 },
    { date: `${prefix}-02`, terminal_total_pct: 20, spy_return_pct: 4 },
  ] } : {} }));
  await mount(PortfolioManagerPage);
  await click("P/L CALENDAR");
  expect(container.textContent).toContain("not realized fund P/L");
  expect(container.textContent).toContain("COMBINED PROXY");
  expect(container.querySelector(".pm-owned-calendar-weekdays")).not.toBeNull();
  const rail = container.querySelector(".pm-owned-week-rail");
  expect(rail.textContent).toContain("+15.0%");
  expect(rail.textContent).not.toContain("+30.0%");
  expect(container.querySelector('[data-stat="TOTAL FUND"]')).toBeNull();
});

test("PM missing options reads are unavailable and omitted paper mode remains unknown", async () => {
  axios.get.mockImplementation(url => url.endsWith("/positions") || url.endsWith("/orders")
    ? Promise.reject(new Error("offline")) : Promise.resolve({ data: url.endsWith("/account") ? { ok: true, account: {} } : {} }));
  await mount(PortfolioManagerPage);
  expect(container.querySelector('[data-stat="CONNECTION DIAGNOSTICS"]').textContent).toContain("NOT EXECUTION AUTHORIZATION");
  await click("OPTIONS");
  expect(container.querySelector('[data-stat="OPEN POS"]').textContent).toContain("OPEN POS: -");
  expect(container.textContent).toContain("Options positions unavailable.");
  expect(container.textContent).toContain("Options orders unavailable.");
  expect(container.textContent).toContain("UNKNOWN");
  expect(container.textContent).not.toContain("NOT PAPER");
  expect(container.textContent).not.toContain("PAPER READY");
});
