import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server.node";
import { MemoryRouter } from "react-router-dom";
import { PositionHeat, ScanFunnel, buildCommandPositions, positionReturnPct } from "./CommandCenterPage";
import WorkspaceSearch from "./WorkspaceSearch";
import { pages } from "../lib/pageRegistry";

jest.mock("./CrtShell", () => ({ tokens: { accent: "#d7bd68", accent2: "#7df7de", muted: "#7d8796", labelLight: "#ddd" }, CrtShell: ({ children }) => <div>{children}</div> }));
jest.mock("sonner", () => ({ toast: jest.fn() }));
// CRA's Jest 27 resolver does not understand React Router 7's subpath exports.
// Routing itself is exercised in the browser smoke pass.
jest.mock("react-router-dom", () => ({ MemoryRouter: ({ children }) => <>{children}</>, Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>, useNavigate: () => jest.fn() }));
const render = element => renderToString(<MemoryRouter>{element}</MemoryRouter>);
const holdings = Array.from({ length: 15 }, (_, index) => ({ symbol: `STOCK${index}`, market_value: 10, unrealized_pl: 1, qty: 1, instrument: "EQUITY" }));

test("all 23 workspaces are independently loadable with unique paths", () => {
  expect(pages).toHaveLength(23);
  expect(new Set(pages.map(page => page.path)).size).toBe(23);
  expect(pages.every(page => typeof page.load === "function")).toBe(true);
});

test("funnel never substitutes held positions or PM approvals for executed/gated counts", () => {
  const html = render(<ScanFunnel scan={{ results: [] }} pmSummary={{ approved: 7 }} gateDecision="PASS" livePositions={holdings} />);
  const doc = new DOMParser().parseFromString(html, "text/html");
  expect(doc.body.textContent).toContain("GATED--");
  expect(doc.body.textContent).toContain("EXECUTED--");
});

test("funnel preserves measured zero executions", () => {
  const html = render(<ScanFunnel scanFunnel={{ counts: { executed: 0, gated: 0 } }} pmSummary={{}} livePositions={holdings} />);
  expect(new DOMParser().parseFromString(html, "text/html").body.textContent).toContain("EXECUTED0");
});

test("full book totals include positions beyond the first page", () => {
  const html = render(<PositionHeat positions={holdings} />);
  expect(html).toContain("$150.00");
  expect(html).toContain("+$15.00");
  expect(new DOMParser().parseFromString(html, "text/html").body.textContent).toContain("15 POS");
  expect(html).toContain("UNKNOWN");
  expect(html).toContain("NO STOP");
});

test("empty authoritative broker positions never resurrect fallback holdings", () => {
  expect(buildCommandPositions({ live_alpaca: holdings }, { equities: { positions: [] } })).toEqual([]);
});

test("percentage units use provider contracts, never magnitude guesses", () => {
  expect(positionReturnPct({ unrealized_plpc: 0.005 })).toBe(0.5);
  expect(positionReturnPct({ unrealized_plpc: 0.5, asset_class: "public_equity" })).toBe(0.5);
  expect(positionReturnPct({ unrealized_pct: 0.5 })).toBe(0.5);
  expect(positionReturnPct({ unrealized_plpc: 1.2 })).toBe(120);
  expect(positionReturnPct({})).toBeNull();
});

test("holdings filter and pagination work without network mutations", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<MemoryRouter><PositionHeat positions={holdings} /></MemoryRouter>));
  expect(container.querySelectorAll('a[href^="/ticker/"]')).toHaveLength(12);
  const next = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "Next");
  await act(async () => next.click());
  expect(container.querySelectorAll('a[href^="/ticker/"]')).toHaveLength(3);
  const input = container.querySelector("input");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "STOCK14");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(container.querySelectorAll('a[href^="/ticker/"]')).toHaveLength(1);
  expect(container.textContent).toContain("$150.00");
  await act(async () => root.unmount()); container.remove();
});

test("search presents a navigation dialog, not order commands", () => {
  const html = render(<WorkspaceSearch open onClose={() => {}} />);
  expect(html).toContain('role="dialog"');
  expect(html).toContain("Portfolio Manager");
  expect(html).not.toContain("HALT");
  expect(html).not.toContain("RESUME");
  expect(html).not.toContain("BUY");
});
