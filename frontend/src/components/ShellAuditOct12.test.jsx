import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { CrtShell } from "./CrtShell";
import WorkspaceSearch from "./WorkspaceSearch";

const mockNavigate = jest.fn();
let mockResource;
let mockPathname;
jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useLocation: () => ({ pathname: mockPathname }),
  useNavigate: () => mockNavigate,
}));
jest.mock("../hooks/useDisplayResource", () => () => mockResource);
jest.mock("../lib/pageRegistry", () => ({
  prefetchPage: jest.fn(),
  pages: Array.from({ length: 23 }, (_, index) => ({ path: `/workspace-${index}`, label: `Workspace ${index}`, group: "Research", aliases: index === 0 ? ["holdings", "qc", "filings", "evidence"] : [] })),
}));

let container, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
  localStorage.clear(); mockNavigate.mockClear();
  mockResource = { data: null, loading: false, error: "Unavailable", refresh: jest.fn() };
  mockPathname = "/scanner";
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.restoreAllMocks(); });
const key = async (target, value, shiftKey = false) => act(async () => target.dispatchEvent(new KeyboardEvent("keydown", { key: value, shiftKey, bubbles: true })));

test("compact navigation retains all 22 workspace names and refresh is labelled", async () => {
  window.innerWidth = 1200;
  localStorage.setItem("cc-compact-nav", "true");
  await act(async () => root.render(<CrtShell title="Scanner">Content</CrtShell>));
  const links = container.querySelectorAll("nav[aria-label='Workspaces'] a");
  expect(links).toHaveLength(22);
  expect(Array.from(links).every(link => link.getAttribute("aria-label"))).toBe(true);
  expect(container.querySelector('[aria-current="page"]').getAttribute("href")).toBe("/scanner");
  expect(container.querySelector('[data-testid="global-refresh-tab"]').getAttribute("aria-label")).toBeTruthy();
});

test("blocked preference storage does not crash the shell", async () => {
  window.innerWidth = 1200;
  jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Blocked storage"); });
  jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Blocked storage"); });
  await act(async () => root.render(<CrtShell title="Scanner">Content</CrtShell>));
  await act(async () => container.querySelector('[aria-label="Collapse navigation"]').click());
  expect(container.querySelector(".terminal-compact-nav")).not.toBeNull();
});

test("company profiles do not masquerade as Command Center", async () => {
  window.innerWidth = 390;
  mockPathname = "/ticker/AAPL";
  await act(async () => root.render(<CrtShell title="AAPL">Content</CrtShell>));
  expect(container.querySelector(".mobile-command-title").textContent).toContain("COMPANY PROFILE");
  expect(container.querySelector('nav[aria-label="Workspaces"] [aria-current="page"]')).toBeNull();
});

test("mobile drawer contains focus, makes the main view inert and restores focus on Escape", async () => {
  window.innerWidth = 390;
  await act(async () => root.render(<CrtShell title="Scanner">Content</CrtShell>));
  const trigger = container.querySelector('[data-testid="mobile-menu-toggle"]');
  trigger.focus();
  await act(async () => trigger.click());
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(container.querySelector("main").hasAttribute("inert")).toBe(true);
  const close = container.querySelector('[aria-label="Close navigation"]');
  expect(document.activeElement).toBe(close);
  await key(close, "Tab", true);
  expect(document.activeElement).toBe(Array.from(container.querySelectorAll("aside a[href], aside button")).pop());
  await key(document.activeElement, "Escape");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(trigger);
});

test("search traps Tab between input and close, scrolls selection and navigates without a mutation", async () => {
  const onClose = jest.fn();
  const scroll = jest.fn();
  HTMLElement.prototype.scrollIntoView = scroll;
  await act(async () => root.render(<WorkspaceSearch open onClose={onClose} />));
  const input = container.querySelector("input");
  expect(document.activeElement).toBe(input);
  expect(container.querySelectorAll('[role="option"][tabindex="-1"]')).toHaveLength(23);
  await key(input, "Tab", true);
  expect(document.activeElement.getAttribute("aria-label")).toBe("Close search");
  await key(document.activeElement, "Tab");
  expect(document.activeElement).toBe(input);
  await key(input, "ArrowDown");
  expect(input.getAttribute("aria-activedescendant")).toBe("workspace-result-1");
  expect(scroll).toHaveBeenCalledWith({ block: "nearest" });
  await key(input, "Enter");
  expect(mockNavigate).toHaveBeenCalledWith("/workspace-1");
  expect(onClose).toHaveBeenCalledTimes(1);
  delete HTMLElement.prototype.scrollIntoView;
});

test.each(["holdings", "qc", "filings", "evidence"])("search matches the existing page alias %s", async query => {
  await act(async () => root.render(<WorkspaceSearch open onClose={() => {}} />));
  const input = container.querySelector("input");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, query);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(container.querySelector('#workspace-result-0').textContent).toContain("Workspace 0");
  expect(container.querySelectorAll('[role="option"]')).toHaveLength(2);
});

test("missing discovery fields remain unavailable, while explicit empty arrays are zero", async () => {
  window.innerWidth = 1200;
  mockResource = { data: {}, loading: false, error: null, refresh: jest.fn() };
  await act(async () => root.render(<CrtShell title="Scanner">Content</CrtShell>));
  const counts = () => Array.from(container.querySelectorAll(".sidebar-alerts a")).map(link => link.lastElementChild.textContent);
  expect(counts()).toEqual(["--", "--", "--", "--"]);
  mockResource = { ...mockResource, data: { narrative_locks_14d: [], picks: [] } };
  await act(async () => root.render(<CrtShell title="Scanner">Content</CrtShell>));
  expect(counts()).toEqual(["--", "--", "0", "0"]);
});

test("empty search never selects a negative result or navigates", async () => {
  await act(async () => root.render(<WorkspaceSearch open onClose={() => {}} />));
  const input = container.querySelector("input");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "no matching workspace!");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await key(input, "ArrowDown");
  await key(input, "Enter");
  expect(input.hasAttribute("aria-activedescendant")).toBe(false);
  expect(container.querySelector('[role="status"]').textContent).toBe("No matching workspace");
  expect(mockNavigate).not.toHaveBeenCalled();
});
