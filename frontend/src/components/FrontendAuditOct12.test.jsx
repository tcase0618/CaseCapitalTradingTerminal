import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { CrtShell } from "./CrtShell";
import WorkspaceSearch from "./WorkspaceSearch";
import { pages, prefetchPage } from "../lib/pageRegistry";
import useDisplayResource from "../hooks/useDisplayResource";
import axios from "axios";

const mockNavigate = jest.fn();
const mockLocation = { pathname: "/scanner" };

// Keep the real shell/search and registry, but do not load routes or contact APIs.
jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useLocation: () => mockLocation,
  useNavigate: () => mockNavigate,
}));
jest.mock("../lib/pageRegistry", () => ({
  ...jest.requireActual("../lib/pageRegistry"),
  prefetchPage: jest.fn(),
}));
jest.mock("../hooks/useDisplayResource", () => ({ __esModule: true, default: jest.fn() }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() }));

let container;
let root;
let fixtures;
let previousWidth;
let previousActEnvironment;
const refresh = jest.fn().mockResolvedValue(undefined);
const unavailable = () => ({ data: null, error: new Error("Local fixture unavailable"), loading: false, updatedAt: null, refresh });

beforeEach(() => {
  previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
  global.IS_REACT_ACT_ENVIRONMENT = true;
  previousWidth = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 1280 });
  localStorage.clear();
  mockLocation.pathname = "/scanner";
  fixtures = new Map();
  jest.clearAllMocks();
  refresh.mockResolvedValue(undefined);
  useDisplayResource.mockImplementation(url => {
    const entry = [...fixtures.entries()].find(([suffix]) => url.endsWith(suffix));
    return entry ? entry[1] : unavailable();
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  jest.restoreAllMocks();
  container.remove();
  localStorage.clear();
  window.innerWidth = previousWidth;
  global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
  expect(axios.get).not.toHaveBeenCalled();
  expect(axios.post).not.toHaveBeenCalled();
  expect(axios.put).not.toHaveBeenCalled();
  expect(axios.delete).not.toHaveBeenCalled();
});

const mount = async element => { await act(async () => root.render(element)); };
const shell = () => <CrtShell title="SCANNER"><div data-testid="audit-content">Local fixture content</div></CrtShell>;
const button = label => container.querySelector(`button[aria-label="${label}"]`);
const click = async element => { expect(element).not.toBeNull(); await act(async () => element.click()); };
const key = async (target, name, modifiers = {}) => {
  const event = new KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true, ...modifiers });
  await act(async () => target.dispatchEvent(event));
  return event;
};
const query = async value => {
  const input = container.querySelector('[role="combobox"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return input;
};
const loaded = data => ({ data, error: null, loading: false, updatedAt: Date.now(), refresh });

test("workspace navigation has a named landmark, unique valid routes, and one active page", async () => {
  await mount(shell());
  const nav = container.querySelector('nav[aria-label="Workspaces"]');
  expect(nav).not.toBeNull();
  const links = [...nav.querySelectorAll("a")];
  const paths = links.map(link => link.getAttribute("href"));
  expect(new Set(paths).size).toBe(paths.length);
  expect(paths.sort()).toEqual(pages.filter(page => !page.path.includes(":")).map(page => page.path).sort());
  expect(links.every(link => link.textContent.trim() || link.getAttribute("aria-label"))).toBe(true);
  expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  expect(nav.querySelector('[aria-current="page"]').getAttribute("href")).toBe("/scanner");
  expect(container.querySelector('img[alt="Case Capital Trading Terminal"]')).not.toBeNull();
});

test("navigation focus prefetches only the selected workspace", async () => {
  await mount(shell());
  await act(async () => container.querySelector('nav a[href="/portfolio-manager"]').focus());
  expect(prefetchPage).toHaveBeenCalledWith("/portfolio-manager");
});

test("search opens once via keyboard and returns focus to its trigger on Escape", async () => {
  await mount(shell());
  const trigger = button("Search workspaces");
  trigger.focus();
  const event = await key(window, "k", { ctrlKey: true });
  expect(event.defaultPrevented).toBe(true);
  expect(container.querySelectorAll('[role="dialog"]')).toHaveLength(1);
  const input = container.querySelector('[role="combobox"]');
  expect(document.activeElement).toBe(input);
  const list = container.querySelector(`#${input.getAttribute("aria-controls")}`);
  expect(list.getAttribute("role")).toBe("listbox");
  expect(container.querySelector('[role="dialog"]').getAttribute("aria-modal")).toBe("true");
  await key(input, "Escape");
  expect(container.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

test("search filters workspaces, resets selection, and navigates by Enter without mutations", async () => {
  const onClose = jest.fn();
  await mount(<WorkspaceSearch open onClose={onClose} />);
  const input = container.querySelector('[role="combobox"]');
  await key(input, "ArrowDown");
  expect(input.getAttribute("aria-activedescendant")).toBe("workspace-result-1");
  await query("portfolio manager");
  expect(container.querySelectorAll('[role="option"]')).toHaveLength(1);
  expect(input.getAttribute("aria-activedescendant")).toBe("workspace-result-0");
  await key(input, "Enter");
  expect(mockNavigate).toHaveBeenCalledWith("/portfolio-manager");
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("ticker search is a company-profile navigation, not an execution action", async () => {
  const onClose = jest.fn();
  await mount(<WorkspaceSearch open onClose={onClose} />);
  const input = await query("$aapl");
  expect(container.querySelector('[role="option"]').textContent).toContain("Company profile");
  await key(input, "Enter");
  expect(mockNavigate).toHaveBeenCalledWith("/ticker/AAPL");
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("invalid ticker input cannot create a profile route or execute an empty selection", async () => {
  await mount(<WorkspaceSearch open onClose={jest.fn()} />);
  const input = await query("../../order");
  expect(container.textContent).toContain("No matching workspace");
  expect(container.querySelectorAll('[role="option"]')).toHaveLength(0);
  await key(input, "ArrowDown");
  await key(input, "Enter");
  expect(input.hasAttribute("aria-activedescendant")).toBe(false);
  expect(mockNavigate).not.toHaveBeenCalled();
});

test("search ignores Enter while text input is composing", async () => {
  const onClose = jest.fn();
  await mount(<WorkspaceSearch open onClose={onClose} />);
  const input = await query("$aapl");
  await key(input, "Enter", { isComposing: true });
  expect(mockNavigate).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
  await key(input, "Enter");
  expect(mockNavigate).toHaveBeenCalledWith("/ticker/AAPL");
});

test("reopened search resets its query and stale active-result reference", async () => {
  await mount(shell());
  await click(button("Search workspaces"));
  const input = await query("../../order");
  await key(input, "Escape");
  await click(button("Search workspaces"));
  const reopened = container.querySelector('[role="combobox"]');
  expect(reopened.value).toBe("");
  expect(reopened.getAttribute("aria-activedescendant")).toBe("workspace-result-0");
  expect(container.querySelector('[role="option"][aria-selected="true"]').id).toBe("workspace-result-0");
});

test("search traps Tab at both dialog boundaries", async () => {
  await mount(<WorkspaceSearch open onClose={jest.fn()} />);
  const input = container.querySelector("input");
  const controls = container.querySelectorAll('input, button:not([tabindex="-1"])');
  const last = controls[controls.length - 1];
  const reverse = await key(input, "Tab", { shiftKey: true });
  expect(reverse.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(last);
  const forward = await key(last, "Tab");
  expect(forward.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(input);
});

test("clicking search content does not dismiss it but its backdrop does", async () => {
  const onClose = jest.fn();
  await mount(<WorkspaceSearch open onClose={onClose} />);
  await click(container.querySelector('[role="dialog"]'));
  expect(onClose).not.toHaveBeenCalled();
  await click(container.querySelector(".workspace-search-backdrop"));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("closed mobile navigation is hidden/inert and Escape removes its backdrop", async () => {
  window.innerWidth = 390;
  await mount(shell());
  const sidebar = container.querySelector(".terminal-sidebar");
  expect(sidebar.getAttribute("aria-hidden")).toBe("true");
  expect(sidebar.hasAttribute("inert")).toBe(true);
  expect(container.querySelector('[data-testid="drawer-backdrop"]')).toBeNull();
  await click(container.querySelector('[data-testid="mobile-menu-toggle"]'));
  expect(sidebar.hasAttribute("aria-hidden")).toBe(false);
  expect(sidebar.hasAttribute("inert")).toBe(false);
  expect(container.querySelectorAll('[data-testid="drawer-backdrop"]')).toHaveLength(1);
  await key(window, "Escape");
  expect(sidebar.getAttribute("aria-hidden")).toBe("true");
  expect(container.querySelector('[data-testid="drawer-backdrop"]')).toBeNull();
});

test("route change closes the mobile drawer so it cannot obscure the next workspace", async () => {
  window.innerWidth = 390;
  await mount(shell());
  await click(container.querySelector('[data-testid="mobile-menu-toggle"]'));
  mockLocation.pathname = "/settings";
  await mount(shell());
  expect(container.querySelector('[data-testid="drawer-backdrop"]')).toBeNull();
  expect(container.querySelector(".terminal-sidebar").getAttribute("aria-hidden")).toBe("true");
});

test("mobile drawer traps focus, makes the main region inert, and restores its trigger", async () => {
  window.innerWidth = 390;
  await mount(shell());
  const trigger = container.querySelector('[data-testid="mobile-menu-toggle"]');
  trigger.focus();
  await click(trigger);
  const dialog = container.querySelector('[role="dialog"][aria-label="Terminal navigation"]');
  expect(dialog).not.toBeNull();
  expect(dialog.getAttribute("aria-modal")).toBe("true");
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(document.getElementById(trigger.getAttribute("aria-controls"))).toBe(dialog);
  expect(container.querySelector("main").hasAttribute("inert")).toBe(true);
  const controls = dialog.querySelectorAll('a[href], button:not(:disabled)');
  const first = controls[0], last = controls[controls.length - 1];
  expect(document.activeElement).toBe(first);
  const reverse = await key(first, "Tab", { shiftKey: true });
  expect(reverse.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(last);
  const forward = await key(last, "Tab");
  expect(forward.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(first);
  await click(button("Close navigation"));
  expect(container.querySelector("main").hasAttribute("inert")).toBe(false);
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(trigger);
});

test("skip link focuses the unique workspace content target", async () => {
  await mount(shell());
  expect(container.querySelectorAll("#workspace-content")).toHaveLength(1);
  const content = container.querySelector("#workspace-content");
  await click(container.querySelector('a[href="#workspace-content"]'));
  expect(document.activeElement).toBe(content);
  expect(content.getAttribute("tabindex")).toBe("-1");
});

test("compact navigation preference survives remount and never creates a second main region", async () => {
  await mount(shell());
  await click(button("Collapse navigation"));
  expect(localStorage.getItem("cc-compact-nav")).toBe("true");
  expect(container.querySelector(".terminal-frame").classList.contains("terminal-compact-nav")).toBe(true);
  expect(container.querySelectorAll("main")).toHaveLength(1);
  await mount(null);
  await mount(shell());
  expect(button("Expand navigation")).not.toBeNull();
  await click(button("Expand navigation"));
  expect(localStorage.getItem("cc-compact-nav")).toBe("false");
});

test("focus view removes both tapes without removing page content and restores them", async () => {
  await mount(shell());
  expect(container.querySelectorAll('[data-testid="global-news-strip"]')).toHaveLength(2);
  await click(button("Focus view"));
  expect(localStorage.getItem("cc-focus-mode")).toBe("true");
  expect(button("Show news tapes").getAttribute("aria-pressed")).toBe("true");
  expect(container.querySelectorAll('[data-testid="global-news-strip"]')).toHaveLength(0);
  expect(container.querySelector('[data-testid="audit-content"]').textContent).toBe("Local fixture content");
  await click(button("Show news tapes"));
  expect(container.querySelectorAll('[data-testid="global-news-strip"]')).toHaveLength(2);
});

test("blocked preference storage does not prevent shell navigation and display controls", async () => {
  jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Storage blocked"); });
  jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage blocked"); });
  await mount(shell());
  await click(button("Collapse navigation"));
  expect(button("Expand navigation")).not.toBeNull();
  await click(button("Focus view"));
  expect(button("Show news tapes")).not.toBeNull();
  await click(button("Search workspaces"));
  expect(container.querySelector('[role="dialog"][aria-label="Find workspace or company"]')).not.toBeNull();
});

test("unavailable discovery counters remain unknown while verified empty counters display zero", async () => {
  await mount(shell());
  const alerts = () => [...container.querySelectorAll(".sidebar-alerts a")].map(link => link.lastElementChild.textContent);
  expect(alerts()).toEqual(["--", "--", "--", "--"]);
  fixtures.set("/v32/dark_horse?days=2", loaded([]));
  fixtures.set("/v32/x_factor?days=2", loaded([]));
  fixtures.set("/v32/conviction", loaded({ narrative_locks_14d: [] }));
  fixtures.set("/v32/lottery/current", loaded({ picks: [] }));
  await mount(shell());
  expect(alerts()).toEqual(["0", "0", "0", "0"]);
});

test("unavailable safety never reports enabled execution or loaded health", async () => {
  await mount(shell());
  expect(container.querySelector(".terminal-execution-strip").textContent).not.toContain("ENABLED");
  expect(container.querySelector(".sidebar-system-health").textContent).toContain("DISPLAY DEGRADED");
  expect(container.querySelector(".sidebar-system-health").textContent).toContain("UNKNOWN");
  expect(container.querySelector(".sidebar-system-health").textContent).not.toContain("STATUS LOADED");
});

test("failed refresh cannot resurrect stale PASS/enabled execution as current safety", async () => {
  const stale = data => ({ ...loaded(data), error: new Error("Local stale fixture"), updatedAt: Date.now() - 3600000 });
  fixtures.set("/admin/trading_status", stale({ trading_enabled: true }));
  fixtures.set("/execution_gate/overview", stale({ decision: "PASS" }));
  fixtures.set("/trade_floor/regime", stale({ status: "green" }));
  await mount(shell());
  const strip = container.querySelector(".terminal-execution-strip").textContent;
  expect(strip).not.toContain("ENABLED");
  expect(strip).not.toContain("PASS");
  expect(container.querySelector(".sidebar-system-health").textContent).toContain("DISPLAY DEGRADED");
});

test("failed news reads are marked down instead of live", async () => {
  await mount(shell());
  const tapes = container.querySelectorAll('[data-testid="global-news-strip"]');
  expect(tapes).toHaveLength(2);
  tapes.forEach(tape => {
    expect(tape.textContent).toContain("DOWN");
    expect(tape.textContent).not.toContain("LIVE");
  });
});
