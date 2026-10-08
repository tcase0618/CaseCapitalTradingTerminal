import React from "react";
import { renderToString } from "react-dom/server.node";
import { ScanFunnel, PositionHeat } from "./CommandCenterPage";

jest.mock("./CrtShell", () => ({ tokens: { accent: "#d7bd68", accent2: "#7df7de", muted: "#7d8796", labelLight: "#ddd" }, CrtShell: ({ children }) => <div>{children}</div> }));
jest.mock("sonner", () => ({ toast: jest.fn() }));
jest.mock("react-router-dom", () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));

const text = element => new DOMParser().parseFromString(renderToString(element), "text/html").body.textContent;

test("daily funnel never borrows latest-cycle counts for missing daily measurements", () => {
  const result = text(<ScanFunnel scanFunnel={{ counts: { executed: 0 } }} scan={{ results_count: 999 }} pmSummary={{ approved: 88, watch: 77, reject: 66 }} />);
  expect(result).toContain("SCANNED--");
  expect(result).toContain("PM APPROVED--");
  expect(result).toContain("EXECUTED0");
  expect(result).not.toContain("999");
});

test("invalid daily counts remain unknown and diagram widths stay bounded", () => {
  const html = renderToString(<ScanFunnel scanFunnel={{ counts: { scanned: -1, pm_approved: Infinity, pm_watch: 50, pm_rejected: 0, routed: 10 } }} />);
  expect(html).not.toContain("NaN");
  expect(html).not.toContain("Infinity");
  expect(html).toContain("width:100%");
  expect(html).not.toContain("width:500%");
});

test("missing position marks do not become fabricated portfolio totals", () => {
  const result = text(<PositionHeat positions={[{ symbol: "TEST", qty: 1, market_value: null, unrealized_pl: undefined }]} />);
  expect(result).toContain("FULL BOOK1 POS----");
  expect(result).not.toContain("$0.00");
});

test("unloaded and verified-empty portfolios remain distinct", () => {
  expect(text(<PositionHeat positions={[]} loaded={false} />)).toContain("FULL BOOK-- POS----");
  expect(text(<PositionHeat positions={[]} loaded />)).toContain("FULL BOOK0 POS$0.00$0.00");
});
